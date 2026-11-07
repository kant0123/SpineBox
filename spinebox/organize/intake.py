"""受け入れ(GOAL.md フェーズ 4): 受け入れフォルダ → 同期で検出 → 整理案のプレビュー → 承認で書庫へ。

- 検出(同期): 受け入れフォルダは蔵書と同じスキャナーで走査する(読み取りだけ。内側アーカイブは取り出さない)。
  `library_roots.inbox = 1` の巻は一覧・作品・検索・重複・結線の対象にならない
- 整理案: 受け入れフォルダの 1 ソース(ファイル・フォルダ)ごとに、書庫のどこに何という名前で入るか(命名テンプレート)、
  CBZ にするか(`mode="cbz"`)移すだけか(`mode="move"`。1 巻 = 1 CBZ のファイルと小説)、既存の作品の何巻か・新しい作品か、
  既に持っている巻か、対象外の理由、を出す。**確信度が低い名寄せ・番号が読めない巻・重複は自動で決めず**、
  利用者が作品・番号・取り込まないを選ぶまで承認できない(`status="choose"`)
- 承認: 選んだものだけを `Organizer` の既存の仕組み(`kind="intake"`)で書庫へ入れる。元は受け入れフォルダの退避フォルダへ
  移すだけで消さず、履歴から元に戻せる。承認しなかったものは受け入れフォルダに残り、次の同期でも出る
- 同期とプレビューは蔵書にも受け入れフォルダにも書かない(DB への記録だけ)。自動では何も動かさない
"""
from __future__ import annotations

import difflib
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import works
from ..archive.base import file_reader_available
from ..config import InboxSetting, Settings, _path_key
from ..naming import normalize_title
from ..pages import natural_key
from ..scanner import resolve_source_path, root_is_accessible
from .organizer import OrganizeError, Organizer, fingerprint
from .template import NameTemplate, NameValues, ValueMissing, check_path_length

log = logging.getLogger(__name__)

# 項目の状態: ready = 承認できる / choose = 利用者の確認(作品・番号・取り込まない)が要る / blocked = 取り込めない(理由つき)
READY, CHOOSE, BLOCKED = "ready", "choose", "blocked"
# 分類: 既存作品の巻 / 新しい作品 / 既に持っている巻(の疑い)/ 読めない(壊れたアーカイブなど)/ 対象外 / 作品が決まらない
EXISTING_WORK, NEW_WORK, DUPLICATE, UNREADABLE, UNSUPPORTED, UNKNOWN_WORK = (
    "existing_work", "new_work", "duplicate", "unreadable", "unsupported", "unknown_work")
SIMILAR_MIN = 0.8        # 名前がこの割合以上似た既存の作品は、同じ作品かもしれないものとして確認を求める
MAX_CANDIDATES = 6
MAX_NUMBER = 9999


@dataclass
class Decision:
    """利用者が選んだこと。省略したものは自動(確信度が高いときだけ決まる)。"""
    source_id: int
    work_id: int | None = None                # 既存の作品に入れる
    new_title: str | None = None              # 新しい作品として入れる(作品名)
    numbers: dict[int, int] = field(default_factory=dict)  # 巻 id → 巻の番号(読めない・直したい巻)
    skip_volume_ids: frozenset[int] = frozenset()          # 取り込まない巻
    expect_outputs: list[str] | None = None   # 承認で、プレビューで見た出力先。違っていたら実行しない


@dataclass
class _Work:
    kind: str                # existing / new / unknown
    work_id: int | None
    title: str
    decided: bool            # 利用者が選んだ
    confident: bool          # 自動で決めてよい確信度
    reason: str
    candidates: list[dict]
    fields: dict
    confidence: float = 0.0


def _volume_dict(v: dict, number: dict | None) -> dict:
    return {"volume_id": v["id"], "title": v["title"], "page_count": v["page_count"], "number": number,
            "output_rel_path": None, "duplicate_of": [], "skipped": False, "reason": None}


def _overlaps(a: dict, b: dict) -> bool:
    return (a["kind"] == b["kind"] and a["start"] <= (b["end"] if b["end"] is not None else b["start"])
            and b["start"] <= (a["end"] if a["end"] is not None else a["start"]))


class Intake:
    """organizer を通して書庫へ入れる。sync_works は新しい巻を作品へ割り当て直す処理(結線の反映を含められる)。"""

    def __init__(self, organizer: Organizer, sync_works: Callable[[], None] | None = None):
        self.org = organizer
        self.conn: sqlite3.Connection = organizer.conn
        self.settings: Settings = organizer.settings
        self.sync_works = sync_works or (lambda: works.sync(self.conn))

    # --- 受け入れフォルダの設定と状態 ----------------------------------------------------

    def inboxes(self) -> list[dict]:
        out = []
        for cfg in self.settings.inboxes:
            problems = []
            if not root_is_accessible(Path(cfg.path)):
                problems.append("受け入れフォルダが見えません(オフライン)")
            shelf = self._shelf_row(cfg)
            if shelf is None:
                problems.append("書庫がまだスキャンされていません。先に一覧のスキャンを実行してください")
            elif not root_is_accessible(Path(cfg.library)):
                problems.append("書庫が見えません(オフライン)")
            if not problems:
                try:
                    if not self.org.same_drive(Path(cfg.path), Path(cfg.library)):
                        problems.append("受け入れフォルダと書庫が別のドライブにあります。同じドライブの中でしか受け入れません")
                except OSError as e:
                    problems.append(f"ドライブを判定できません: {e}")
            row = self.conn.execute("SELECT last_checked_at FROM library_roots WHERE inbox = 1 AND path = ?",
                                    (str(Path(cfg.path)),)).fetchone()
            out.append({"path": str(cfg.path), "library": str(cfg.library), "problems": problems,
                        "synced_at": row["last_checked_at"] if row else None})
        return out

    def _shelf_row(self, cfg: InboxSetting) -> sqlite3.Row | None:
        key = _path_key(cfg.library)
        for r in self.conn.execute("SELECT * FROM library_roots WHERE inbox = 0 AND enabled = 1"):
            if _path_key(r["path"]) == key:
                return r
        return None

    def _source_rows(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            """SELECT s.id, s.rel_path FROM sources s JOIN library_roots r ON r.id = s.root_id
               WHERE r.inbox = 1 AND r.enabled = 1 AND s.missing = 0""").fetchall()
        return sorted(rows, key=lambda r: natural_key(r["rel_path"]))

    # --- 一覧・プレビュー ---------------------------------------------------------------

    def list_items(self, *, inspect: bool = False) -> dict:
        """受け入れフォルダにあるもの(DB の記録)とその整理案。ファイルは読むだけ(inspect=False では目次も開かない)。"""
        return self.preview([Decision(r["id"]) for r in self._source_rows()], inspect=inspect)

    def preview(self, decisions: list[Decision], *, inspect: bool = True) -> dict:
        state = works._State(self.conn)
        taken: set[str] = set()
        items = [self.evaluate(d, state=state, inspect=inspect, taken=taken)[0] for d in decisions]
        return {"inboxes": self.inboxes(), "items": items}

    def evaluate(self, d: Decision, *, state, inspect: bool, taken: set[str] | None = None) -> tuple[dict, dict | None]:
        """1 ソースの整理案(項目)と、承認できる(status="ready")ときだけ実行用の計画を返す。何も書かない。"""
        src = self.conn.execute(
            """SELECT s.*, r.path AS root_path, r.online AS root_online FROM sources s
               JOIN library_roots r ON r.id = s.root_id WHERE s.id = ? AND r.inbox = 1""", (d.source_id,)).fetchone()
        if src is None:
            item = self._item({"id": d.source_id, "rel_path": "", "kind": "archive", "root_path": ""})
            return self._blocked(item, UNSUPPORTED, "受け入れフォルダの本ではありません"), None
        item = self._item(src)
        self._drop_unlisted_works(state)
        cfg = self.settings.inbox_for(src["root_path"])
        if cfg is None:
            return self._blocked(item, UNSUPPORTED, "受け入れフォルダが設定から外れています"), None
        shelf = self._shelf_row(cfg)
        root, shelf_path = Path(src["root_path"]), Path(cfg.library)
        if shelf is None:
            return self._blocked(item, UNSUPPORTED, "書庫がまだスキャンされていません。先に一覧のスキャンを実行してください"), None
        if src["offline"] or not root_is_accessible(root):
            return self._blocked(item, UNSUPPORTED, "受け入れフォルダが今オフラインです"), None
        if not root_is_accessible(shelf_path):
            return self._blocked(item, UNSUPPORTED, "書庫が今オフラインです"), None
        rel = src["rel_path"]
        if rel == "":
            return self._blocked(item, UNSUPPORTED, "受け入れフォルダそのものは受け入れられません(画像をフォルダに入れてください)"), None
        try:
            source_path = resolve_source_path(root, rel)
        except ValueError:
            return self._blocked(item, UNSUPPORTED, "受け入れフォルダの外を指しています"), None
        if not source_path.exists():
            return self._blocked(item, UNSUPPORTED, "元のファイル・フォルダが見つかりません(同期し直してください)"), None

        all_rows = [dict(v) for v in self.conn.execute(
            """SELECT v.*, EXISTS (SELECT 1 FROM volumes c WHERE c.parent_id = v.id AND c.missing = 0) AS has_children
               FROM volumes v WHERE v.source_id = ? AND v.missing = 0""", (d.source_id,))]
        if not all_rows:
            issues = [r["reason"] for r in self.conn.execute(
                "SELECT reason FROM scan_issues WHERE source_id = ? ORDER BY id", (d.source_id,))]
            return self._blocked(item, UNREADABLE, *(issues or ["巻にできるもの(画像のあるフォルダ・アーカイブ)が見つかりません"])), None
        listed = [v for v in all_rows if v["organized_op_id"] is None]
        if not listed:
            op = all_rows[0]["organized_op_id"]
            return self._blocked(item, UNSUPPORTED, f"以前に受け入れた本と同じ名前です(操作 {op})。"
                                 "履歴で元に戻すか、名前を変えてください"), None
        vols = [v for v in listed if not v["has_children"]]
        vols.sort(key=lambda v: (natural_key(v["entry_prefix"]), natural_key(v["container_path"] or ""),
                                 natural_key(v["inner_path"] or "")))
        item["issues"] = [r["reason"] for r in self.conn.execute(
            "SELECT reason FROM scan_issues WHERE source_id = ? ORDER BY id", (d.source_id,))]
        problems: list[str] = []
        warnings: list[str] = []

        # 取り込み方: 1 巻 = 1 つのファイル(CBZ・小説)は中身を変えずに移すだけ。それ以外は 1 巻ずつ CBZ にする
        novel = any(v["format"] in ("epub", "text") for v in vols)
        suffix = Path(rel).suffix.lower()
        single_cbz = (src["kind"] == "archive" and suffix == ".cbz" and len(vols) == 1 and not novel
                      and vols[0]["container_path"] is None and vols[0]["entry_prefix"] == ""
                      and vols[0]["parent_id"] is None and vols[0]["inner_path"] is None)
        mode = "move" if (single_cbz or (novel and len(vols) == 1)) else "cbz"
        item["mode"] = mode
        if novel and len(vols) != 1:
            problems.append("小説(EPUB・テキスト)と他のものが混ざっています")
        needs = self.org._unanalyzed(listed, problems) if mode == "cbz" else []
        item["needs_analysis"] = needs
        if mode == "cbz":
            if inspect:
                if src["kind"] == "folder":
                    self.org._inspect_folder(source_path, problems, warnings)
                else:
                    self.org._inspect_archive(source_path, vols, problems, warnings)
            elif src["kind"] == "archive" and not file_reader_available(source_path, self.settings.sevenzip_path):
                problems.append("この形式は今読めません(7-Zip が見つからない、または未対応)")
        base = self.settings.organize_base(root)
        out_base = self.settings.organize_base(shelf_path)
        problems.extend(self.org._drive_problems(source_path, base, out_base=out_base, out_root=shelf_path))
        try:
            fp = fingerprint(source_path)
        except OSError as e:
            return self._blocked(item, UNSUPPORTED, f"元を読めません: {e}"), None
        item["fingerprint"] = fp

        # 作品
        w = self._match_work(state, vols, rel, d)
        item["work"] = {"match": w.kind, "work_id": w.work_id, "title": w.title, "decided": w.decided,
                        "confident": w.confident, "confidence": w.confidence, "reason": w.reason,
                        "candidates": w.candidates}
        # 巻ごとの番号・重複・出力先
        template = NameTemplate(self.settings.name_template)
        library_numbers = self._library_numbers(w.work_id) if w.work_id is not None else []
        unresolved: list[str] = []
        seen: dict[str, str] = {}
        taken = taken if taken is not None else set()
        outputs: list[dict] = []
        for v in vols:
            number = self._number(v, d)
            vd = _volume_dict(v, number)
            item["volumes"].append(vd)
            if v["id"] in d.skip_volume_ids:
                vd.update(skipped=True, reason="取り込まない")
            vd["duplicate_of"] = [{"volume_id": o["id"], "title": o["title"], "rel_path": o["rel_path"]}
                                  for o in library_numbers if number is not None and _overlaps(number, o["number"])]
            if vd["skipped"]:
                continue
            if vd["duplicate_of"]:
                names = "・".join(f"「{o['title']}」" for o in vd["duplicate_of"][:3])
                vd["reason"] = f"書庫の{names}と同じ巻かもしれません。取り込まないか、番号を直してください"
                unresolved.append(f"「{v['title']}」は既に持っている巻かもしれません")
                continue
            if number is None:
                vd["reason"] = "名前から巻・話の番号を読めません。番号を入力してください"
                unresolved.append(f"「{v['title']}」の番号が読めません")
                continue
            if w.kind == "unknown":
                continue  # 作品が決まるまで出力先は決められない
            values = NameValues(w.fields, number["kind"], number["start"],
                                number["end"] if number["end"] is not None else number["start"])
            try:
                out_rel = template.render(values)
                if mode == "move" and suffix != ".cbz":
                    out_rel = out_rel[:-4] + suffix  # 小説は元の拡張子のまま(テンプレートは .cbz で終わる)
                check_path_length(shelf_path, out_rel)
            except ValueMissing as e:
                vd["reason"] = str(e)
                problems.append(f"「{v['title']}」はテンプレートの名前にできません: {e}")
                continue
            bad = self.org._target_problem(shelf_path, out_base, out_rel, source_path)
            if bad is None:
                key = out_rel.casefold()
                if key in seen:
                    bad = f"「{seen[key]}」と同じ名前になります。どちらかを取り込まないにしてください"
                    unresolved.append(f"「{v['title']}」の出力先が「{seen[key]}」と同じです")
                elif key in taken:
                    bad = "ほかの受け入れ待ちの本と同じ名前になります"
                    unresolved.append(f"「{v['title']}」の出力先がほかの本と同じです")
                else:
                    seen[key] = v["title"]
                    taken.add(key)
                    outputs.append({"volume_id": v["id"], "title": v["title"], "page_count": v["page_count"],
                                    "output_rel_path": out_rel, "format": v["format"]})
            else:
                problems.append(f"「{v['title']}」の出力先 {out_rel}: {bad}")
            vd["output_rel_path"] = out_rel
            if bad:
                vd["reason"] = bad
        item["warnings"] = warnings
        skipped = [vd for vd in item["volumes"] if vd["skipped"] or vd["duplicate_of"]]
        dup = any(vd["duplicate_of"] for vd in item["volumes"])

        # 状態
        reasons = list(problems)
        if reasons:
            status = BLOCKED
        else:
            if w.kind == "unknown" or not (w.decided or w.confident):
                unresolved.insert(0, w.reason or "作品を確かめてください")
            if unresolved:
                status, reasons = CHOOSE, unresolved
            elif not outputs:
                status, reasons = BLOCKED, ["取り込む巻がありません(すべて取り込まない)"]
            else:
                status = READY
        item["status"] = status
        item["reasons"] = reasons
        item["category"] = (UNSUPPORTED if (status == BLOCKED and problems) else DUPLICATE if dup
                            else UNKNOWN_WORK if w.kind == "unknown" else EXISTING_WORK if w.kind == "existing"
                            else NEW_WORK)
        item["expect_outputs"] = sorted(o["output_rel_path"] for o in outputs)
        if status != READY:
            return item, None
        if skipped and mode == "cbz":
            warnings.append("取り込まない巻は書庫に入りません(元のファイルごと退避フォルダに残り、元に戻せます)")
        plan = {
            "kind": "intake", "mode": mode, "naming": "template", "template": self.settings.name_template,
            "source_id": d.source_id, "root_id": src["root_id"], "root_path": str(root), "source_rel_path": rel,
            "source_kind": src["kind"], "organize_base": str(base), "out_root_id": shelf["id"],
            "out_root_path": str(shelf_path), "out_base": str(out_base), "fingerprint": fp,
            "volumes": outputs,
            "skipped": [{"volume_id": vd["volume_id"], "title": vd["title"], "reason": vd["reason"]} for vd in skipped],
            "work": {"kind": w.kind, "work_id": w.work_id, "title": w.title, "decided": w.decided},
            "warnings": warnings, "problems": [], "needs_analysis": [],
        }
        return item, plan

    @staticmethod
    def _item(src) -> dict:
        rel = src["rel_path"]
        return {"source_id": src["id"], "inbox": src["root_path"], "rel_path": rel, "name": Path(rel).name or rel,
                "kind": src["kind"], "status": BLOCKED, "category": UNSUPPORTED, "mode": None, "reasons": [],
                "warnings": [], "issues": [], "work": None, "volumes": [], "needs_analysis": [],
                "fingerprint": None, "expect_outputs": []}

    @staticmethod
    def _blocked(item: dict, category: str, *reasons: str) -> dict:
        item.update(status=BLOCKED, category=category, reasons=list(reasons))
        return item

    # --- 作品の名寄せ ------------------------------------------------------------------

    def _match_work(self, st, vols: list[dict], rel: str, d: Decision) -> _Work:
        """既存の作品の名前と突き合わせる(`works` の名寄せと同じ規則)。決められないときは利用者に選ばせる。"""
        if d.work_id is not None:
            wid = works.root_of(self.conn, d.work_id)
            if wid is not None and wid in st.works:
                return _Work("existing", wid, st.works[wid].title, True, True, "あなたが選んだ作品です",
                             [], self._work_fields(wid), 1.0)
            return _Work("unknown", None, "", False, False, "選んだ作品が見つかりません", [], {})
        if d.new_title is not None and d.new_title.strip():
            title = d.new_title.strip()
            norm = normalize_title(title)
            if not norm:
                return _Work("unknown", None, "", False, False, "作品名に使える文字がありません", [], {})
            same = st.find_work(norm)
            if same is not None:
                return _Work("unknown", None, title, False, False,
                             f"「{st.works[same].title}」という作品が既にあります。その作品を選んでください",
                             [{"work_id": same, "title": st.works[same].title}], {})
            return _Work("new", None, title, True, True, "あなたが決めた新しい作品です", [], {"title": title}, 1.0)
        owns = [works.own_name(self._row(v, rel)) for v in vols]
        named = [o for o in owns if o is not None]
        if not named:
            stem = Path(rel).stem or rel
            return _Work("unknown", None, stem, False, False,
                         "名前から作品名を読めません。作品を選ぶか、作品名を入力してください", [], {})
        first = named[0]
        norm = first.name.norm
        cands = [i for i in st.by_norm.get(norm, ()) if i in st.works]
        similar = [] if cands else self._similar(st, norm)
        why = ""
        if len(named) != len(owns) or len({o.name.norm for o in named}) > 1:
            why = "この中の巻の名前から読んだ作品名が揃っていません(別の作品が混じっている?)"
        elif any(o.level > 0 for o in named):
            why = f"巻自身の名前に作品名が無く、外側の名前「{first.text}」から推測しました"
        elif len(cands) > 1:
            why = "同じ名前に当たる既存の作品が複数あります"
        elif similar:
            why = "名前の似た既存の作品があります。同じ作品かもしれません"
        shown = [{"work_id": i, "title": st.works[i].title} for i in cands]
        shown += [{"work_id": i, "title": st.works[i].title} for i in similar]
        authors = works.author_text(first.name.authors)
        if len(cands) == 1 and not why:
            wid = cands[0]
            return _Work("existing", wid, st.works[wid].title, False, True,
                         f"巻の名前から既存の作品「{st.works[wid].title}」と判断しました", shown[:MAX_CANDIDATES],
                         self._work_fields(wid), first.confidence)
        if len(cands) == 1:
            wid = cands[0]
            return _Work("existing", wid, st.works[wid].title, False, False, why, shown[:MAX_CANDIDATES],
                         self._work_fields(wid), first.confidence)
        fields = {"title": first.name.title, **({"author": authors} if authors else {})}
        if not why:
            return _Work("new", None, first.name.title, False, True,
                         f"既存の作品に同じ名前がありません。新しい作品「{first.name.title}」として入れます", [],
                         fields, first.confidence)
        return _Work("new", None, first.name.title, False, False, why, shown[:MAX_CANDIDATES], fields, first.confidence)

    @staticmethod
    def _row(v: dict, rel: str) -> dict:
        return {**v, "rel_path": rel}

    def _drop_unlisted_works(self, st) -> None:
        """一覧に見えている巻を持たない作品(取り込みを元に戻した後に残る作品など)は、受け入れ先の候補にしない。
        作品の一覧に出ない作品を「既にある」と言われても、選べない・確かめられない。レコードは消さない。"""
        if getattr(st, "_intake_pruned", False):
            return
        live = {works.root_of(self.conn, a["work_id"]) for vid, a in st.assign.items() if vid in st.listed}
        for wid in [i for i in st.works if i not in live]:
            del st.works[wid]
        st._intake_pruned = True

    def _work_fields(self, work_id: int) -> dict:
        return {r["field"]: r["value"] for r in self.conn.execute(
            "SELECT field, value FROM work_fields WHERE work_id = ?", (work_id,))}

    @staticmethod
    def _similar(st, norm: str) -> list[int]:
        """名前が似た既存の作品(別名を含む)。ローマ字と日本語のような別の言語の名前は、ここでは見つからない。"""
        if len(norm) < 3:
            return []
        scored = []
        for w in st.works.values():
            best = 0.0
            for other, n in w.names.items():
                if n.source == works.SOURCE_CONTAINER or other == norm:
                    continue
                sm = difflib.SequenceMatcher(None, norm, other)
                if sm.real_quick_ratio() >= SIMILAR_MIN and sm.quick_ratio() >= SIMILAR_MIN:
                    best = max(best, sm.ratio())
            if best >= SIMILAR_MIN:
                scored.append((-best, w.id))
        return [i for _, i in sorted(scored)][:MAX_CANDIDATES]

    # --- 巻の番号と重複 ---------------------------------------------------------------

    @staticmethod
    def _number(v: dict, d: Decision) -> dict | None:
        """巻の番号。利用者が入れた番号(manual)が優先、無ければ名前から読んだ候補(name)。"""
        n = d.numbers.get(v["id"])
        if n is not None:
            return {"kind": v["number_kind"] or "volume", "start": n, "end": n, "source": "manual"}
        if v["number_start"] is None:
            return None
        return {"kind": v["number_kind"], "start": v["number_start"],
                "end": v["number_end"] if v["number_end"] is not None else v["number_start"], "source": "name"}

    def _library_numbers(self, work_id: int) -> list[dict]:
        """書庫(一覧に出ている巻)でその作品に割り当てられている巻と、名前から読んだ番号。"""
        from ..scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE
        out = []
        for r in self.conn.execute(
                "SELECT v.id, v.title, v.number_kind, v.number_start, v.number_end, s.rel_path " + LISTED_VOLUMES_JOIN
                + " JOIN work_volumes wv ON wv.volume_id = v.id " + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?",
                (work_id,)):
            if r["number_start"] is None:
                continue
            out.append({"id": r["id"], "title": r["title"], "rel_path": r["rel_path"],
                        "number": {"kind": r["number_kind"], "start": r["number_start"],
                                   "end": r["number_end"] if r["number_end"] is not None else r["number_start"]}})
        return out

    # --- 承認 --------------------------------------------------------------------------

    def approve(self, decisions: list[Decision], *, ctx=None) -> dict:
        """選ばれたものを順に書庫へ入れる。1 件の失敗(巻き戻し済み)で残りを止めない。"""
        results = []
        if ctx is not None and decisions:
            ctx.step("受け入れの承認")
        for i, d in enumerate(decisions):
            state = works._State(self.conn)  # 前の件で作品が増えたかもしれない
            item, plan = self.evaluate(d, state=state, inspect=True)
            if ctx is not None:
                ctx.progress(i, len(decisions), item.get("rel_path") or item.get("name") or f"source {d.source_id}")
            if plan is None:
                results.append({"source_id": d.source_id, "ok": False, "item": item,
                                "error": "取り込めません: " + ("、".join(item["reasons"]) or "確認が要ります")})
                continue
            if d.expect_outputs is not None and sorted(d.expect_outputs) != item["expect_outputs"]:
                results.append({"source_id": d.source_id, "ok": False, "item": item,
                                "error": "プレビューを見た後に出力先が変わりました。もう一度確かめてください"})
                continue
            op = self.org._save_plan(plan)
            try:
                done = self.org.execute(op["op_id"], ctx=ctx)
            except OrganizeError as e:
                results.append({"source_id": d.source_id, "ok": False, "item": item, "op": e.op, "error": str(e)})
                continue
            warning = self._assign(done, plan["work"])
            results.append({"source_id": d.source_id, "ok": True, "item": item, "op": done,
                            **({"warning": warning} if warning else {})})
            if ctx is not None:
                ctx.progress(i + 1, len(decisions), item.get("rel_path") or item.get("name"))
        return {"results": results}

    def _assign(self, op: dict, work: dict) -> str | None:
        """新しい巻を作品へ入れる。利用者が選んだ作品は手動の決定にし、自動のものは名前からの割り当てを確かめる。"""
        ids = [v["new_volume_id"] for v in op["volumes"] if v["new_volume_id"] is not None]
        try:
            self.sync_works()
            if work["decided"]:
                if work["work_id"] is not None:
                    works.assign_volumes(self.conn, ids, work["work_id"])
                else:
                    # 巻の名前から同じ作品名の作品が既にできていれば、それを手動の割り当てにする
                    # (新しく作ると、同名の空の作品が残って作品の候補に 2 つ出る)
                    same = {self._work_of(vid) for vid in ids}
                    found = next(iter(same)) if len(same) == 1 else None
                    if found is not None and normalize_title(self._work_fields(found).get("title", "")) == normalize_title(work["title"]):
                        works.assign_volumes(self.conn, ids, found)
                    else:
                        works.assign_volumes(self.conn, ids, None, work["title"])
            elif work["work_id"] is not None:
                target = works.root_of(self.conn, work["work_id"])
                wrong = [vid for vid in ids if self._work_of(vid) != target]
                if wrong:
                    works.assign_volumes(self.conn, wrong, work["work_id"])
            self.sync_works()
        except Exception as e:  # noqa: BLE001 (ファイルは移動済み。作品への割り当ては作品画面から直せる)
            log.exception("受け入れた巻の作品への割り当てに失敗しました")
            return f"書庫には入りましたが、作品への割り当てに失敗しました: {type(e).__name__}: {e}"
        return None

    def _work_of(self, volume_id: int) -> int | None:
        row = self.conn.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (volume_id,)).fetchone()
        return works.root_of(self.conn, row["work_id"]) if row else None
