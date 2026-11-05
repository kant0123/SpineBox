"""整理処理(1 ソース → 巻ごとの CBZ)の計画・実行・元に戻す。

蔵書に書き込む唯一の経路(CLAUDE.md「蔵書を扱う不変条件」の例外規定)。ユーザーが明示実行するときだけ動く。

- 計画(プレビュー): DB と stat と外側アーカイブの目次だけを読み、蔵書にも作業場にも何も書かない
- 実行: 作業場(`<organize_base>/work/<操作ID>/`)に全巻の CBZ を作る → 全巻を検証 → 改名で確定(作業場 → 出力先)
  → 元を退避フォルダ(`<organize_base>/trash/<操作ID>/original/<元の相対パス>`)へ改名で移す。
  1 ソースに複数巻があっても、全巻の CBZ 化と検証が済むまで元にも出力先にも触らない
- 元に戻す: 作った CBZ を `trash/<操作ID>/created/` へ移し、元を元の場所へ戻す。どちらも消さない
- 移動は 1 つずつ操作ジャーナル(organize_steps)に pending → done で記録し、途中で失敗したら done の手順を逆順に戻す。
  出力のために作ったフォルダも mkdir として記録し、巻き戻し・元に戻すで空なら消す(蔵書には何も残さない)
- 出力名は巻の表示名(naming="title")か命名テンプレート(naming="template"。template.py)
- 改名・移動だけの整理(kind="rename"): 既に 1 巻 = 1 CBZ の巻を、作品単位でテンプレートの名前へ移す。
  巻 id を変えずにソースのパスを付け替えるので、読書位置・作品の割り当て・指紋・重複の却下はそのまま残る
- 範囲指定の整理(kind="range"): 1 巻 = 1 CBZ は改名・移動するだけ。include_cbz=True なら、それ以外の元
  (複数巻のアーカイブ・入れ子・画像フォルダ・CBZ でない 1 巻のアーカイブ)も読める巻ごとにテンプレートの
  名前の CBZ にする(#133)。CBZ 化する元は全巻を作業場で作って検証してから確定し、元は退避フォルダへ移す
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import db, works
from ..archive import ArchiveError, list_entries, open_archive
from ..archive.base import file_reader_available, inner_reader_problem
from ..cache import CacheTooLarge
from ..config import Settings
from ..library import Library, VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row
from ..pages import is_image, natural_key
from ..naming import parse_number
from ..scanner import (LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, SCAN_VERSION, Scanner, VolumeSpec, _fs_name_bytes,
                       resolve_source_path, root_is_accessible)
from .. import bookmarks, page_marks
from ..works import WorkNotFound, root_of, transfer_assignment, transfer_volume_fields
from . import cbz, fs
from .cleanup import clean_stem, split_ext
from .template import NameTemplate, NameValues, RangeTemplate, TemplateError, ValueMissing, check_path_length

NAMINGS = ("title", "template")
KIND_LABELS = {"manga": "漫画", "novel": "小説"}
# 改名・移動だけの整理の種類。rename = 作品単位(name_template)、range = 範囲指定(整理画面のテンプレート・ファイル名の整理)
RENAME_KINDS = ("rename", "range")
# 改名・移動の計画の各巻の扱い。cbz = CBZ 化する(範囲指定の整理で include_cbz=True のとき)
ITEM_MOVE, ITEM_UNCHANGED, ITEM_EXCLUDED, ITEM_CONFLICT, ITEM_SKIPPED, ITEM_CBZ = (
    "move", "unchanged", "excluded", "conflict", "skipped", "cbz")


def _smaller_than_top(group: list[dict]) -> list[tuple[dict, list[dict]]]:
    """同じ出力名になる巻の組から、ファイル数(ページ数)が最多より少ない巻を (その巻, 最多の巻たち) で返す(#143)。

    既定では多い方を残す。最多が同数ならそれらは自動では決めない(衝突のまま利用者に選ばせる)。ページ数が不明な巻が
    いる組も決めない。既にその名前で置かれている巻(unchanged)は動かせないので、外す側には入れない。
    """
    if len(group) < 2 or any(it["page_count"] is None for it in group):
        return []
    most = max(it["page_count"] for it in group)
    top = [it for it in group if it["page_count"] == most]
    return [(it, top) for it in group if it["page_count"] < most and it["status"] != ITEM_UNCHANGED]


def _mark_superseded(item: dict, top: list[dict]) -> None:
    """ファイル数の多い巻に譲って外す。superseded_by が「残す方を選び直す」の手がかり。"""
    names = "・".join(f"「{t['title']}」" for t in top)
    item.update(status=ITEM_SKIPPED, superseded_by=[t["volume_id"] for t in top],
                reason=f"同じ名前の{names}({top[0]['page_count']} ファイル)のほうがファイル数が多いため、整理しません"
                       f"(この巻は {item['page_count']} ファイル)")


class OrganizeError(Exception):
    """整理処理を始められない・失敗した(失敗時は巻き戻し済み)。"""

    def __init__(self, message: str, op: dict | None = None):
        super().__init__(message)
        self.op = op


class OperationNotFound(Exception):
    """その ID の操作が無い。"""


class RangeError(ValueError):
    """範囲の指定(蔵書フォルダの中のパス)が使えない。メッセージは利用者向け。"""


class RangeNotFound(LookupError):
    """範囲に指定した蔵書フォルダが無い(受け入れフォルダ・無効なものを含む)。"""


@dataclass(frozen=True)
class _Step:
    """巻き戻せるように控えた手順。kind は move / mkdir / rmdir(フォルダは src = dst)。"""
    step_id: int
    kind: str
    src: Path
    dst: Path

    def __iter__(self):
        return iter((self.step_id, self.kind, self.src, self.dst))


@dataclass
class _Built:
    volume: dict
    work_path: Path
    output_path: Path
    records: list[cbz.PageRecord] = field(default_factory=list)
    # 移動だけの受け入れ(mode="move")では CBZ を作らないので、ページ数と形式は計画から持ってくる
    page_count: int | None = None
    format: str | None = None


def new_op_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def fingerprint(path: Path) -> str:
    """計画から実行までに元が変わっていないかを見る値。フォルダは直下の各エントリのサイズ・mtime から作る。"""
    st = path.stat()
    if not path.is_dir():
        return f"file:{st.st_size}:{st.st_mtime_ns}"
    h = hashlib.sha256()
    for e in sorted(os.scandir(path), key=lambda e: e.name):
        est = e.stat(follow_symlinks=False)
        h.update(f"{e.name}\0{e.is_dir(follow_symlinks=False)}\0{est.st_size}\0{est.st_mtime_ns}\n".encode(
            "utf-8", "surrogateescape"))
    return f"dir:{st.st_mtime_ns}:{h.hexdigest()}"


def _rel_posix(root: Path, path: Path) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


class Organizer:
    """same_drive と move は差し替えられる(テストで別ドライブ構成・途中失敗を作るため)。"""

    def __init__(self, conn: sqlite3.Connection, settings: Settings, library: Library, *,
                 same_drive: Callable[[Path, Path], bool] = fs.same_drive,
                 move: Callable[[Path, Path], None] = fs.move_no_overwrite):
        self.conn = conn
        self.settings = settings
        self.library = library
        self.same_drive = same_drive
        self.move = move

    # --- 計画 ---------------------------------------------------------------

    def plan(self, *, volume_id: int | None = None, source_id: int | None = None, naming: str = "title") -> dict:
        """計画を作る。問題が無ければ操作として保存して op_id を付ける。蔵書にも作業場にも書かない。

        naming="template" なら出力先を命名テンプレートで決める。値が無い巻・衝突する巻が 1 つでもあれば実行させない
        (1 ソースの全巻をまとめて CBZ にするため、一部の巻だけ外すことはできない)。
        """
        if naming not in NAMINGS:
            raise ValueError(f"naming は {' / '.join(NAMINGS)}")
        if source_id is None:
            row = self.conn.execute("SELECT source_id, missing, organized_op_id FROM volumes WHERE id = ?",
                                    (volume_id,)).fetchone()
            if row is None or row["missing"] or row["organized_op_id"] is not None:
                raise VolumeNotFound(volume_id)
            source_id = row["source_id"]
        src = self.conn.execute(
            """SELECT s.*, r.path AS root_path, r.online AS root_online, r.enabled AS root_enabled
               FROM sources s JOIN library_roots r ON r.id = s.root_id WHERE s.id = ?""", (source_id,)).fetchone()
        if src is None or src["missing"] or not src["root_enabled"]:
            raise VolumeNotFound(volume_id)
        root = Path(src["root_path"])
        rel = src["rel_path"]
        base = self.settings.organize_base(root)
        problems: list[str] = []
        warnings: list[str] = []
        # 整理の単位は「読める巻」= 見えている子を持たない巻。分解済みの内側アーカイブは、親(まとめ)ではなく
        # 子の巻ごとに 1 CBZ にする(親と子を両方 CBZ にすると同じページが二重になる)
        all_rows = [dict(v) for v in self.conn.execute(
            """SELECT v.*, EXISTS (SELECT 1 FROM volumes c WHERE c.parent_id = v.id AND c.missing = 0) AS has_children
               FROM volumes v WHERE v.source_id = ? AND v.missing = 0 AND v.organized_op_id IS NULL""", (source_id,))]
        vols = [v for v in all_rows if not v["has_children"]]
        vols.sort(key=lambda v: (natural_key(v["entry_prefix"]), natural_key(v["container_path"] or ""),
                                 natural_key(v["inner_path"] or "")))
        plan = {
            "source_id": source_id, "root_id": src["root_id"], "root_path": str(root), "source_rel_path": rel,
            "source_kind": src["kind"], "organize_base": str(base), "fingerprint": None,
            "volumes": [], "warnings": warnings, "problems": problems, "needs_analysis": [],
            "kind": "cbz", "naming": naming, "template": self.settings.name_template if naming == "template" else None,
        }
        if src["offline"] or not root_is_accessible(root):
            problems.append("蔵書フォルダが今オフラインです")
            return self._finish_plan(plan, vols)
        if rel == "":
            problems.append("蔵書ルートそのものは整理できません(画像をフォルダに入れてください)")
            return self._finish_plan(plan, vols)
        source_path = resolve_source_path(root, rel)
        if not source_path.exists():
            problems.append("元のファイル・フォルダが見つかりません(再スキャンしてください)")
            return self._finish_plan(plan, vols)
        if any(v["format"] for v in vols):
            problems.append("小説(EPUB・テキスト)は CBZ に整理できません")
            return self._finish_plan(plan, vols)
        if not vols:
            problems.append("整理できる巻がありません")
        plan["needs_analysis"] = self._unanalyzed(all_rows, problems)
        problems.extend(self._drive_problems(source_path, base))
        if fs.is_within(base, source_path):
            problems.append("作業場・退避フォルダが元のフォルダの中にあります")
        if src["kind"] == "folder":
            self._inspect_folder(source_path, problems, warnings)
        else:
            self._inspect_archive(source_path, vols, problems, warnings)
        plan["fingerprint"] = fingerprint(source_path)
        if naming == "template":
            self._template_outputs(root, base, source_path, vols, problems)
            return self._finish_plan(plan, vols)
        try:
            taken = {n.casefold() for n in os.listdir(source_path.parent)}
        except OSError as e:
            problems.append(f"出力先のフォルダを読めません: {e}")
            taken = set()
        for v in vols:
            stem = fs.safe_stem(v["title"])
            name, n = f"{stem}.cbz", 2
            while name.casefold() in taken:
                name, n = f"{stem} ({n}).cbz", n + 1
            taken.add(name.casefold())
            v["output_rel_path"] = _rel_posix(root, source_path.parent / name)
        return self._finish_plan(plan, vols)

    def _template_outputs(self, root: Path, base: Path, source_path: Path, vols: list[dict],
                          problems: list[str]) -> None:
        """CBZ 化の出力先をテンプレートで決める。値が無い・衝突する巻は連番で逃がさず問題として出す。"""
        template = NameTemplate(self.settings.name_template)
        values = self.name_values([v["id"] for v in vols])
        seen: dict[str, str] = {}
        for v in vols:
            try:
                rel = template.render(values[v["id"]])
                check_path_length(root, rel)
            except ValueMissing as e:
                problems.append(f"「{v['title']}」はテンプレートの名前にできません: {e}")
                continue
            reason = self._target_problem(root, base, rel, source_path)
            if reason is None and rel.casefold() in seen:
                reason = f"「{seen[rel.casefold()]}」と同じ名前になります"
            if reason:
                problems.append(f"「{v['title']}」の出力先 {rel}: {reason}")
                continue
            seen[rel.casefold()] = v["title"]
            v["output_rel_path"] = rel

    def _target_problem(self, root: Path, base: Path, rel: str, source_path: Path) -> str | None:
        """テンプレートで決めた出力先に置けない理由。置けるなら None。"""
        try:
            target = resolve_source_path(root, rel)
        except ValueError:
            return "蔵書フォルダの外を指しています"
        if fs.is_within(target, base):
            return "作業場・退避フォルダの中になります"
        if source_path.is_dir() and fs.is_within(target, source_path):
            return "元のフォルダの中になります(元はフォルダごと退避するため)"
        if os.path.lexists(target):
            return "既にファイル・フォルダがあります"
        try:
            if not self.same_drive(source_path, target.parent):
                return "元と別のドライブです。同じドライブの中でしか整理しません"
        except OSError as e:
            return f"ドライブを判定できません: {e}"
        return None

    def name_values(self, volume_ids: list[int]) -> dict[int, NameValues]:
        """巻ごとのテンプレートの値。作品の項目は統合されていれば吸収先のもの。番号は名前から読んだ候補。

        範囲指定の整理は蔵書ルート全体の巻をまとめて渡すので、巻ごとに SQL を発行しない(まとめて引く)。
        """
        out: dict[int, NameValues] = {}
        fields_cache: dict[int, dict] = {}
        roots: dict[int, int | None] = {}  # 作品 id → 統合の吸収先(作品ごとに 1 回)
        ids = list(dict.fromkeys(volume_ids))
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""SELECT v.id, v.number_kind, v.number_start, v.number_end, v.format, wv.work_id FROM volumes v
                    LEFT JOIN work_volumes wv ON wv.volume_id = v.id WHERE v.id IN ({marks})""", chunk).fetchall()
            # レーベル(label)・発売年は巻の書誌(volume_fields)。発売日は「2020-05-01」「2020」などの先頭の 4 桁
            vfields: dict[int, dict] = {}
            for r in self.conn.execute(
                    f"SELECT volume_id, field, value FROM volume_fields WHERE volume_id IN ({marks}) "
                    "AND field IN ('label', 'released')", chunk):
                vfields.setdefault(r["volume_id"], {})[r["field"]] = r["value"]
            for row in rows:
                vid = row["id"]
                work_id = None
                if row["work_id"] is not None:
                    if row["work_id"] not in roots:
                        roots[row["work_id"]] = root_of(self.conn, row["work_id"])
                    work_id = roots[row["work_id"]]
                fields: dict = {}
                if work_id is not None:
                    if work_id not in fields_cache:
                        fields_cache[work_id] = {r["field"]: r["value"] for r in self.conn.execute(
                            "SELECT field, value FROM work_fields WHERE work_id = ?", (work_id,))}
                    fields = dict(fields_cache[work_id])
                # 種別は作品の項目(manga / novel)を日本語にする。未決でも小説の巻(EPUB・テキスト)は小説と分かる
                kind = fields.get(works.FIELD_KIND) or ("novel" if row["format"] else None)
                fields["kind"] = KIND_LABELS.get(kind)
                if fields.get("author"):  # 複数著者は保存値の「、」ではなく「・」でつなぐ(出力名だけ。保存値は変えない)
                    fields["author"] = "・".join(a.strip() for a in fields["author"].split(works.AUTHOR_SEPARATOR))
                vf = vfields.get(vid, {})
                fields["label"] = vf.get("label")
                year = re.match(r"\s*(\d{4})", vf.get("released") or "")
                fields["year"] = year.group(1) if year else None
                out[vid] = NameValues(fields, row["number_kind"], row["number_start"], row["number_end"])
        return out

    def _unanalyzed(self, rows: list[dict], problems: list[str]) -> list[int]:
        """中の巻をまだ調べていない(または調べた後に元が変わった・調べられなかった)内側アーカイブの巻の id。

        その巻が中に複数の巻を持つかは調べるまで分からず、1 CBZ にまとめてよいか決められない
        (分解済みなら子ごとに 1 CBZ)。プレビューは書き込まない約束なので、ここで解析せず、先に調べるよう促して
        整理を始めさせない。調べ方は一覧の「中の巻を調べる」(`POST /api/volumes/{id}/analyze`)。
        """
        needs = []
        for v in rows:
            if v["container_path"] is None or v["parent_id"] is not None or v["inner_path"] is not None:
                continue  # 分解の対象になる内側アーカイブの巻(親を持たない)だけ
            row = get_volume_row(self.conn, v["id"])
            current = self.library.current_analysis_version(row)
            if v["analyzed_version"] is None or v["analysis_error"] is not None or v["analyzed_version"] != current:
                needs.append(v["id"])
                reason = f"(前回は調べられませんでした: {v['analysis_error']})" if v["analysis_error"] else ""
                problems.append(f"内側アーカイブ「{v['title']}」の中の巻をまだ調べていません{reason}。"
                                "先に「中の巻を調べる」を実行してください(1 つの中に複数の巻があると、"
                                "巻ごとの CBZ にするため)")
        return needs

    def _drive_problems(self, source_path: Path, base: Path, *, out_base: Path | None = None,
                        out_root: Path | None = None) -> list[str]:
        """元と、作業場・退避フォルダ・出力先が同じドライブか。受け入れ(out_root あり)は退避・作業場・書庫を別々に見る。"""
        problems = []
        targets = ((("作業場・退避フォルダ", base), ("出力先", source_path.parent)) if out_root is None else
                   (("退避フォルダ", base), ("作業場", out_base or base), ("書庫", out_root)))
        for label, target in targets:
            try:
                ok = self.same_drive(source_path, target)
            except OSError as e:
                problems.append(f"{label}のドライブを判定できません: {e}")
                continue
            if not ok:
                problems.append(f"{label}が元と別のドライブにあります({target})。同じドライブの中でしか整理しません")
        return problems

    def _inspect_folder(self, path: Path, problems: list[str], warnings: list[str]) -> None:
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            problems.append(f"元のフォルダを読めません: {e}")
            return
        subdirs = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
        if subdirs:
            # 元のフォルダごと退避するので、別の巻(サブフォルダ)まで一緒に動いてしまう
            problems.append(f"サブフォルダを含むフォルダは整理できません: {', '.join(sorted(subdirs)[:5])}")
        others = [e.name for e in entries if not e.is_dir(follow_symlinks=False) and not is_image(e.name)]
        if others:
            warnings.append(f"画像以外のファイル {len(others)} 件は CBZ に入らず、元のフォルダごと退避フォルダへ移ります")

    def _inspect_archive(self, path: Path, vols: list[dict], problems: list[str], warnings: list[str]) -> None:
        sevenzip = self.settings.sevenzip_path
        if not file_reader_available(path, sevenzip):
            problems.append("この形式は今読めません(7-Zip が見つからない、または未対応)")
            return
        try:
            with open_archive(path, sevenzip_path=sevenzip) as reader:
                listing = list_entries(reader, self.settings.limits)
                by_name = {}
                for e in listing.entries:
                    by_name.setdefault(e.name, e)  # 同名なら最初の 1 つ(スキャンが巻にしたもの)
                # 内側アーカイブは取り出した後と同じく先頭の中身で判定する(#62)
                for container in dict.fromkeys(v["container_path"] for v in vols if v["container_path"]):
                    entry = by_name.get(container)
                    problem = (inner_reader_problem(reader, entry, sevenzip) if entry is not None
                               else "アーカイブの中に見つからない")
                    if problem:
                        problems.append(f"内側アーカイブを読めません: {container}: {problem}")
        except ArchiveError as e:
            problems.append(f"アーカイブを読めません: {e}")
            return
        prefixes = {v["entry_prefix"] for v in vols if v["container_path"] is None}
        containers = {v["container_path"] for v in vols if v["container_path"]}
        dropped = [e.name for e in listing.entries
                   if not ((is_image(e.name) and e.parent in prefixes) or e.name in containers)]
        if dropped:
            warnings.append(f"CBZ に入らないエントリが {len(dropped)} 件あります(元のアーカイブごと退避フォルダに残ります)")
        if listing.skipped:
            warnings.append(f"危険な名前のため読まなかったエントリが {len(listing.skipped)} 件あります")

    def _finish_plan(self, plan: dict, vols: list[dict]) -> dict:
        plan["volumes"] = [{"volume_id": v["id"], "title": v["title"], "page_count": v["page_count"],
                            "output_rel_path": v.get("output_rel_path")} for v in vols]
        return self._save_plan(plan)

    def _save_plan(self, plan: dict) -> dict:
        if plan["problems"]:
            return {**self._view_plan(plan), "op_id": None, "state": "blocked"}
        op_id = new_op_id()
        now = db.now()
        with db.transaction(self.conn) as c:
            c.execute("""INSERT INTO organize_ops (id, kind, source_id, state, plan_json, created_at, updated_at)
                         VALUES (?, ?, ?, 'planned', ?, ?, ?)""",
                      (op_id, plan.get("kind", "cbz"), plan["source_id"], json.dumps(plan, ensure_ascii=False),
                       now, now))
            c.executemany("""INSERT INTO organize_volumes (op_id, old_volume_id, output_rel_path, page_count)
                             VALUES (?, ?, ?, ?)""",
                          [(op_id, v["volume_id"], v["output_rel_path"], v["page_count"]) for v in plan["volumes"]])
        return self.get(op_id)

    # --- 実行 ---------------------------------------------------------------

    def execute(self, op_id: str) -> dict:
        op, plan = self._load(op_id)
        if op["state"] != "planned":
            raise OrganizeError(f"この操作は実行できる状態ではありません({op['state']})", self.get(op_id))
        if plan.get("kind") in RENAME_KINDS:
            if plan.get("include_cbz"):
                return self._execute_range_cbz(op_id, plan)
            return self._execute_rename(op_id, plan)
        root, base, out_root, out_base = self._roots_of(plan)
        move_only = plan.get("mode") == "move"
        work_dir = out_base / "work" / op_id
        trash_path = base / "trash" / op_id / "original" / plan["source_rel_path"]
        try:
            source_path = self._check_before_execute(plan, root, base, out_root, out_base)
        except OrganizeError as e:
            self._set_state(op_id, "failed", error=str(e))
            raise OrganizeError(str(e), self.get(op_id)) from e

        self._set_state(op_id, "running")
        done_steps: list[_Step] = []
        built: list[_Built] = []
        try:
            if move_only:
                # 受け入れで、既に 1 巻 = 1 つのファイル(CBZ・EPUB・テキスト)のものは、中身をそのまま
                # テンプレートの名前で書庫へ改名で移す
                for v in plan["volumes"]:
                    b = _Built(v, work_dir, out_root / v["output_rel_path"], page_count=v["page_count"],
                               format=v.get("format"))
                    built.append(b)
                    self._mkdir_steps(op_id, "execute", out_root, out_base, b.output_path, done_steps)
                    self._move_step(op_id, "execute", "move_in", source_path, b.output_path, done_steps)
                self._record_executed(op_id, plan, out_root, built)
                return self.get(op_id)
            work_dir.mkdir(parents=True, exist_ok=False)
            # 1. 全巻の CBZ を作業場に作る(元にも出力先にもまだ触らない)
            for i, v in enumerate(plan["volumes"]):
                b = _Built(v, work_dir / f"{i + 1:04d}.cbz", out_root / v["output_rel_path"])
                built.append(b)
                with self.library.open(self.conn, v["volume_id"]) as opened:
                    if not opened.page_names:
                        raise OrganizeError(f"ページの無い巻は整理できません: {v['title']}")
                    b.records = cbz.write_cbz(b.work_path, opened.page_names, opened.read_page)
            # 2. 全巻を読み直して検証する
            for b in built:
                cbz.verify_cbz(b.work_path, b.records)
            # 3. 改名で確定 → 4. 元を退避
            for b in built:
                self._mkdir_steps(op_id, "execute", out_root, out_base, b.output_path, done_steps)
                self._move_step(op_id, "execute", "commit", b.work_path, b.output_path, done_steps)
            self._move_step(op_id, "execute", "trash_original", source_path, trash_path, done_steps)
            # 5. DB: 新しい CBZ の巻を登録し、読書位置を引き継ぎ、元の巻を「整理済み」にする
            self._record_executed(op_id, plan, out_root, built)
        except BaseException as e:
            ok = self._revert(done_steps)
            for b in built:
                if b.work_path.is_file():
                    # 作業場に残った CBZ は確定前(または巻き戻し後)の中間生成物で、蔵書ではないので片付ける。
                    # 確定の巻き戻しに失敗したものは出力先に残っていて、ここには無い
                    _unlink_quietly(b.work_path)
            _rmdir_quietly(work_dir)
            message = _describe(e)
            self._set_state(op_id, "failed" if ok else "rollback_failed",
                            error=message if ok else f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        _rmdir_quietly(work_dir)
        return self.get(op_id)

    @staticmethod
    def _roots_of(plan: dict) -> tuple[Path, Path, Path, Path]:
        """(元のあるルート, 元の退避の置き場, 出力先のルート, 作業場・作った CBZ の退避の置き場)。

        整理は元と出力先が同じルート。受け入れ(kind="intake")は元が受け入れフォルダ、出力先が書庫のルートで、
        元の退避は受け入れフォルダの側、作業場は書庫の側に置く(どちらも同じドライブの中)。
        """
        root, base = Path(plan["root_path"]), Path(plan["organize_base"])
        return (root, base, Path(plan.get("out_root_path") or plan["root_path"]),
                Path(plan.get("out_base") or plan["organize_base"]))

    def _check_before_execute(self, plan: dict, root: Path, base: Path, out_root: Path | None = None,
                              out_base: Path | None = None) -> Path:
        """書き込みを始める前の確認。どれかに当たれば何も書かずに止める。"""
        intake = out_root is not None and out_root != root
        out_root = out_root or root
        if not root_is_accessible(root):
            raise OrganizeError("受け入れフォルダが今オフラインです" if intake else "蔵書フォルダが今オフラインです")
        if intake and not root_is_accessible(out_root):
            raise OrganizeError("書庫が今オフラインです")
        source_path = resolve_source_path(root, plan["source_rel_path"])
        if not source_path.exists():
            raise OrganizeError("元のファイル・フォルダが見つかりません")
        problems = (self._drive_problems(source_path, base, out_base=out_base, out_root=out_root) if intake
                    else self._drive_problems(source_path, base))
        if problems:
            raise OrganizeError(problems[0])
        if fingerprint(source_path) != plan["fingerprint"]:
            raise OrganizeError("計画を作った後に元が変わりました。計画を作り直してください")
        for v in plan["volumes"]:
            out = out_root / v["output_rel_path"]
            if os.path.lexists(out):
                raise OrganizeError(f"出力先に既にファイルがあります: {v['output_rel_path']}")
            if not self.same_drive(source_path, out):
                # テンプレートの出力先は元と別のフォルダになりうる(計画後にドライブを付け替えた場合)
                raise OrganizeError(f"出力先が元と別のドライブです: {v['output_rel_path']}")
            row = self.conn.execute("SELECT missing, organized_op_id FROM volumes WHERE id = ?",
                                    (v["volume_id"],)).fetchone()
            if row is None or row["missing"] or row["organized_op_id"] is not None:
                raise OrganizeError(f"巻が変わりました。計画を作り直してください: {v['title']}")
        return source_path

    def _record_executed(self, op_id: str, plan: dict, root: Path, built: list[_Built]) -> None:
        """root は出力先のルート(受け入れでは書庫)。新しい巻はそこのソースとして登録する。"""
        scanner = Scanner(self.conn, self.settings)
        now = db.now()
        with db.transaction(self.conn) as c:
            for b in built:
                rel = _rel_posix(root, b.output_path)
                st = b.output_path.stat()
                pages = len(b.records) if b.records else b.page_count
                # スキャナーがこの CBZ から作るのと同じ巻(直下の画像 = entry_prefix '')として登録する
                src = scanner._upsert_source(c, plan.get("out_root_id", plan["root_id"]), rel, "archive")
                stem = b.output_path.stem
                scanner._replace_volumes(c, src["id"], [VolumeSpec(
                    None, "", stem, _fs_name_bytes(b.output_path.name), "filesystem", pages, b.format)])
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, now, src["id"]))
                new_vid = c.execute("""SELECT id FROM volumes WHERE source_id = ? AND container_path IS NULL
                                       AND entry_prefix = ''""", (src["id"],)).fetchone()["id"]
                old_vid = b.volume["volume_id"]
                transfer_volume_state(c, old_vid, new_vid, pages)
                c.execute("UPDATE volumes SET organized_op_id = ?, page_count = ?, updated_at = ? WHERE id = ?",
                          (op_id, pages, now, old_vid))
                c.execute("""UPDATE organize_volumes SET new_volume_id = ?, page_count = ?
                             WHERE op_id = ? AND old_volume_id = ?""", (new_vid, pages, op_id, old_vid))
            # 元は退避フォルダへ移ったので、スキャンが見るのと同じく missing にする(行は消さない)
            c.execute("UPDATE sources SET missing = 1 WHERE id = ?", (plan["source_id"],))
            c.execute("UPDATE organize_ops SET state = 'done', error = NULL, executed_at = ?, updated_at = ? WHERE id = ?",
                      (now, now, op_id))

    # --- 元に戻す -------------------------------------------------------------

    def undo(self, op_id: str) -> dict:
        op, plan = self._load(op_id)
        if op["state"] != "done":
            raise OrganizeError(f"この操作は元に戻せる状態ではありません({op['state']})", self.get(op_id))
        if plan.get("kind") in RENAME_KINDS:
            if plan.get("include_cbz"):
                return self._undo_range_cbz(op_id, plan)
            return self._undo_rename(op_id, plan)
        root, base, out_root, out_base = self._roots_of(plan)
        if plan.get("mode") == "move":
            return self._undo_move_in(op_id, plan, root, out_root)
        steps = {s["action"]: s for s in self.conn.execute(
            "SELECT * FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND state = 'done'", (op_id,))}
        trash = steps.get("trash_original")
        if trash is None:
            raise OrganizeError("退避の記録が見つかりません", self.get(op_id))
        original, trashed = Path(trash["from_path"]), Path(trash["to_path"])
        outputs = [Path(s["to_path"]) for s in self.conn.execute(
            """SELECT to_path FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND action = 'commit'
               AND state = 'done' ORDER BY id""", (op_id,))]
        created_dir = out_base / "trash" / op_id / "created"
        problem = None
        if not root_is_accessible(root) or not root_is_accessible(out_root):
            problem = "蔵書フォルダ(受け入れ・書庫)が今オフラインです"
        elif os.path.lexists(original):
            problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {_rel_posix(root, original)}"
        elif not trashed.exists():
            problem = f"退避した元が見つかりません: {trashed}"
        else:
            for out in outputs:
                if not out.is_file():
                    problem = f"作った CBZ が見つかりません(移動・改名された?): {_rel_posix(out_root, out)}"
                    break
        if problem:
            raise OrganizeError(problem, self.get(op_id))

        self._set_state(op_id, "undoing")
        done_steps: list[_Step] = []
        try:
            for out in outputs:
                self._move_step(op_id, "undo", "trash_created", out, created_dir / _rel_posix(out_root, out),
                                done_steps)
            self._move_step(op_id, "undo", "restore_original", trashed, original, done_steps)
            self._rmdir_created(op_id, done_steps)
            self._record_undone(op_id, plan, out_root, outputs)
        except BaseException as e:
            ok = self._revert(done_steps)
            message = _describe(e)
            if ok:
                # 元に戻す前の状態(実行済み)に戻った
                self._set_state(op_id, "done", error=f"元に戻せませんでした: {message}")
            else:
                self._set_state(op_id, "undo_failed",
                                error=f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        return self.get(op_id)

    def _undo_move_in(self, op_id: str, plan: dict, root: Path, out_root: Path) -> dict:
        """受け入れで移しただけ(mode="move")のものを、元の場所へ戻す。移した先のファイルが移したときのままのときだけ。"""
        step = self.conn.execute(
            """SELECT * FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND action = 'move_in'
               AND state = 'done'""", (op_id,)).fetchone()
        if step is None:
            raise OrganizeError("移動の記録が見つかりません", self.get(op_id))
        original, moved = Path(step["from_path"]), Path(step["to_path"])
        problem = None
        if not root_is_accessible(root) or not root_is_accessible(out_root):
            problem = "蔵書フォルダ(受け入れ・書庫)が今オフラインです"
        elif os.path.lexists(original):
            problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {_rel_posix(root, original)}"
        elif not moved.is_file():
            problem = f"移した先のファイルが見つかりません(移動・改名された?): {_rel_posix(out_root, moved)}"
        elif fingerprint(moved) != plan["fingerprint"]:
            problem = f"移した後にファイルが変わったので戻しません: {_rel_posix(out_root, moved)}"
        if problem:
            raise OrganizeError(problem, self.get(op_id))
        self._set_state(op_id, "undoing")
        done_steps: list[_Step] = []
        try:
            self._move_step(op_id, "undo", "restore_original", moved, original, done_steps)
            self._rmdir_created(op_id, done_steps)
            self._record_undone(op_id, plan, out_root, [moved])
        except BaseException as e:
            ok = self._revert(done_steps)
            message = _describe(e)
            if ok:
                self._set_state(op_id, "done", error=f"元に戻せませんでした: {message}")
            else:
                self._set_state(op_id, "undo_failed",
                                error=f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        return self.get(op_id)

    def _record_undone(self, op_id: str, plan: dict, root: Path, outputs: list[Path]) -> None:
        """root は出力先のルート(受け入れでは書庫)。受け入れ(kind="intake")の元の巻は受け入れフォルダの待ち行列なので、
        書庫で付いた作品の割り当て・読書位置は写し戻さない(受け入れフォルダの巻が作品に入ってしまう)。"""
        now = db.now()
        with db.transaction(self.conn) as c:
            for m in c.execute("SELECT * FROM organize_volumes WHERE op_id = ?", (op_id,)).fetchall():
                if m["new_volume_id"] is not None and plan.get("kind") != "intake":
                    transfer_volume_state(c, m["new_volume_id"], m["old_volume_id"], m["page_count"])
                c.execute("UPDATE volumes SET organized_op_id = NULL, updated_at = ? WHERE id = ?",
                          (now, m["old_volume_id"]))
            c.execute("UPDATE sources SET missing = 0 WHERE id = ?", (plan["source_id"],))
            # 作った CBZ は退避フォルダへ移ったので、そのソースは missing にする(行・読書位置は消さない)
            c.executemany("UPDATE sources SET missing = 1 WHERE root_id = ? AND rel_path = ?",
                          [(plan.get("out_root_id", plan["root_id"]), _rel_posix(root, out)) for out in outputs])
            c.execute("UPDATE organize_ops SET state = 'undone', error = NULL, undone_at = ?, updated_at = ? WHERE id = ?",
                      (now, now, op_id))

    # --- ジャーナル -----------------------------------------------------------

    def _journal(self, op_id: str, phase: str, action: str, src: str, dst: str) -> int:
        with db.transaction(self.conn) as c:
            cur = c.execute("""INSERT INTO organize_steps (op_id, phase, action, from_path, to_path, state, updated_at)
                               VALUES (?, ?, ?, ?, ?, 'pending', ?)""", (op_id, phase, action, src, dst, db.now()))
        return cur.lastrowid

    def _move_step(self, op_id: str, phase: str, action: str, src: Path, dst: Path,
                   done_steps: list[_Step]) -> None:
        step_id = self._journal(op_id, phase, action, str(src), str(dst))
        # 移動が例外で終わっても、実は済んでいた場合に巻き戻せるよう、先に控える
        done_steps.append(_Step(step_id, "move", src, dst))
        self.move(src, dst)
        self._set_step(step_id, "done")

    def _mkdir_steps(self, op_id: str, phase: str, root: Path, base: Path, dst: Path,
                     done_steps: list[_Step]) -> None:
        """dst の親のうち、まだ無い蔵書のフォルダを上から作り、1 つずつ mkdir として記録する。

        テンプレートの出力先(`<作品名>/`)は今は無いフォルダになりうる。記録しておかないと、巻き戻し・元に戻すの後に
        空のフォルダが蔵書に残り、実行前と一致しなくなる。作業場・退避フォルダの中は記録しない(蔵書ではない)。
        """
        missing = []
        p = dst.parent
        while not os.path.lexists(p) and fs.is_within(p, root) and p != Path(root):
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            if fs.is_within(d, base):
                continue
            step_id = self._journal(op_id, phase, "mkdir", "", str(d))
            done_steps.append(_Step(step_id, "mkdir", d, d))
            d.mkdir()
            self._set_step(step_id, "done")

    def _rmdir_created(self, op_id: str, done_steps: list[_Step]) -> None:
        """実行で作ったフォルダを、深い方から空なら消す(元に戻すの最後)。中に何かあれば残す(蔵書を消さない)。"""
        dirs = [Path(s["to_path"]) for s in self.conn.execute(
            """SELECT to_path FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND action = 'mkdir'
               AND state = 'done' ORDER BY id DESC""", (op_id,))]
        for d in dirs:
            try:
                if not d.is_dir() or any(os.scandir(d)):
                    continue
            except OSError:
                continue
            step_id = self._journal(op_id, "undo", "rmdir", "", str(d))
            done_steps.append(_Step(step_id, "rmdir", d, d))
            d.rmdir()
            self._set_step(step_id, "done")

    def _revert(self, done_steps: list[_Step]) -> bool:
        """記録した移動・フォルダの作成と削除を逆順に戻す。全部戻せたら True。"""
        ok = True
        for step_id, kind, src, dst in reversed(done_steps):
            try:
                if kind == "mkdir":
                    if os.path.isdir(dst):
                        dst.rmdir()  # 空でなければ失敗する(中身を消さない)
                    self._set_step(step_id, "reverted")
                    continue
                if kind == "rmdir":
                    dst.mkdir(parents=True, exist_ok=True)
                    self._set_step(step_id, "reverted")
                    continue
                moved = os.path.lexists(dst) and not os.path.lexists(src)
                not_moved = os.path.lexists(src) and not os.path.lexists(dst)
                if moved:
                    self.move(dst, src)
                elif not not_moved:
                    raise OSError(f"移動の前後どちらの状態でもない: {src} / {dst}")
                self._set_step(step_id, "reverted")
            except Exception:
                ok = False
                self._set_step(step_id, "revert_failed")
        return ok

    def _set_step(self, step_id: int, state: str) -> None:
        with db.transaction(self.conn) as c:
            c.execute("UPDATE organize_steps SET state = ?, updated_at = ? WHERE id = ?", (state, db.now(), step_id))

    def _set_state(self, op_id: str, state: str, *, error: str | None = None) -> None:
        with db.transaction(self.conn) as c:
            c.execute("UPDATE organize_ops SET state = ?, error = ?, updated_at = ? WHERE id = ?",
                      (state, error, db.now(), op_id))

    def _load(self, op_id: str) -> tuple[sqlite3.Row, dict]:
        op = self.conn.execute("SELECT * FROM organize_ops WHERE id = ?", (op_id,)).fetchone()
        if op is None:
            raise OperationNotFound(op_id)
        return op, json.loads(op["plan_json"])

    # --- 改名・移動だけの整理(作品単位) ------------------------------------------

    _RENAME_SELECT = """SELECT v.*, s.rel_path, s.kind AS source_kind, s.offline AS source_offline, s.root_id,
                      r.path AS root_path,
                      (SELECT COUNT(*) FROM volumes o WHERE o.source_id = v.source_id AND o.missing = 0
                         AND o.organized_op_id IS NULL AND o.parent_id IS NULL) AS source_volumes
               """ + LISTED_VOLUMES_JOIN

    def plan_rename(self, work_id: int, *, exclude_volume_ids: tuple[int, ...] | list[int] = ()) -> dict:
        """作品の全巻をテンプレートの名前へ改名・移動する計画。蔵書には何も書かない。

        各巻を move(改名する)/ unchanged(既にその名前)/ excluded(対象外。理由つき)/ conflict(衝突。理由つき)/
        skipped(利用者が外した)に振り分ける。衝突は自動で連番にせず、どれかを外して計画を作り直してもらう。
        """
        root_work = root_of(self.conn, work_id)
        if root_work is None:
            raise WorkNotFound(work_id)
        template = NameTemplate(self.settings.name_template)
        rows = [dict(r) for r in self.conn.execute(
            self._RENAME_SELECT + " JOIN work_volumes wv ON wv.volume_id = v.id "
            + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?", (root_work,))]
        rows.sort(key=lambda r: (r["number_start"] if r["number_start"] is not None else 10 ** 9,
                                 natural_key(r["title"])))
        values = self.name_values([r["id"] for r in rows])
        items = self._classify_renames(rows, set(exclude_volume_ids), self._rename_ineligible,
                                       lambda r: (template.render(values[r["id"]]), []))
        plan = {"kind": "rename", "naming": "template", "template": self.settings.name_template, "work_id": root_work}
        return self._save_rename_plan(plan, items, "この作品には一覧に出ている巻がありません")

    def plan_range(self, root_id: int, path: str = "", *, file_template: str = "", folder_template: str = "",
                   exclude_volume_ids: tuple[int, ...] | list[int] = (), include_cbz: bool = False) -> dict:
        """蔵書ルートの中の範囲(path。空ならルート全体、サブフォルダも含む)の巻を、整理画面のテンプレートで
        改名・移動する計画(#84)。蔵書には何も書かない。

        ファイル名テンプレートが空なら今の名前を整理した名前(cleanup.py)、フォルダーテンプレートが空なら元と同じフォルダ。
        フォルダーテンプレートは範囲のパスを起点にする。CBZ 化はしない(複数巻のまとめ・入れ子・画像フォルダは対象外にして
        CBZ 化の画面へ案内する)。include_cbz=True なら、それらの元も読める巻ごとにテンプレートの名前の CBZ にする(#133)。
        振り分け・実行・元に戻すは作品単位の改名・移動(plan_rename)と同じ。
        """
        template = RangeTemplate(file_template, folder_template)
        root_row = self.conn.execute(
            "SELECT id, path, online, enabled, inbox FROM library_roots WHERE id = ?", (root_id,)).fetchone()
        if root_row is None or not root_row["enabled"] or root_row["inbox"]:
            raise RangeNotFound(f"蔵書フォルダが見つかりません: {root_id}")
        root = Path(root_row["path"])
        plan = {"kind": "range", "naming": "template", "template": None, "work_id": None,
                "include_cbz": bool(include_cbz), "cbz_sources": [],
                "scope": {"root_id": root_id, "root_path": str(root), "path": "", "file_template": template.file_text,
                          "folder_template": template.folder_text}}
        if not root_row["online"] or not root_is_accessible(root):
            if include_cbz:
                return self._save_range_plan(plan, [], [], "蔵書フォルダが今オフラインです")
            return self._save_rename_plan(plan, [], "蔵書フォルダが今オフラインです")
        sub = self.range_path(root, path)
        plan["scope"]["path"] = sub
        prefix = f"{sub}/" if sub else ""
        rows = [dict(r) for r in self.conn.execute(
            self._RENAME_SELECT + LISTED_VOLUMES_WHERE + " AND s.root_id = ?", (root_id,))
            if r["rel_path"].startswith(prefix)]
        rows.sort(key=lambda r: (natural_key(r["rel_path"]), natural_key(r["entry_prefix"] or "")))
        values = self.name_values([r["id"] for r in rows])
        if include_cbz:
            return self._plan_range_cbz(root, sub, rows, values, template, set(exclude_volume_ids), plan)

        def target(r: dict) -> tuple[str, list[str]]:
            parent, _, name = r["rel_path"].rpartition("/")
            stem, ext = split_ext(name)
            changes: list[str] = []
            file_stem = template.render_file(values[r["id"]])
            if file_stem is None:
                cleaned = clean_stem(stem)
                file_stem, changes = cleaned.name, list(cleaned.changes)
            folder = template.render_folder(values[r["id"]])
            dirs = (parent.split("/") if parent else []) if folder is None else [*(sub.split("/") if sub else []),
                                                                               *folder]
            return "/".join([*dirs, file_stem + ext]), changes

        items = self._classify_renames(rows, set(exclude_volume_ids), self._range_ineligible, target)
        return self._save_rename_plan(plan, items, "範囲の中に一覧に出ている巻がありません")

    def _plan_range_cbz(self, root: Path, sub: str, rows: list[dict], values: dict, template: RangeTemplate,
                        excluded_ids: set[int], plan: dict) -> dict:
        """範囲指定の整理で、1 巻 = 1 CBZ でない元も CBZ にする計画(#133)。

        1 巻 = 1 CBZ の巻は従来どおり改名・移動する。それ以外は読める巻ごとに CBZ にし、元(1 つのファイル・
        フォルダ)は全巻を作って検証してから退避フォルダへ移す。元の一部の巻だけを CBZ にすると、退避した元に
        残る巻の読めない状態ができるので、元の全巻を CBZ にできるときだけ対象にする(1 巻でも駄目なら元全体を衝突にする)。
        """
        base = self.settings.organize_base(root)
        problems: list[str] = []
        warnings: list[str] = []
        plan["needs_analysis"] = self._unanalyzed(rows, problems)
        items: list[dict] = []
        by_source: dict[int, list[dict]] = {}
        source_rows: dict[int, dict] = {}

        def move_target(r: dict) -> tuple[str, list[str]]:
            parent, _, name = r["rel_path"].rpartition("/")
            stem, ext = split_ext(name)
            changes: list[str] = []
            file_stem = template.render_file(values[r["id"]])
            if file_stem is None:
                cleaned = clean_stem(stem)
                file_stem, changes = cleaned.name, list(cleaned.changes)
            folder = template.render_folder(values[r["id"]])
            dirs = (parent.split("/") if parent else []) if folder is None else [*(sub.split("/") if sub else []),
                                                                               *folder]
            return "/".join([*dirs, file_stem + ext]), changes

        # 1 巻 = 1 CBZ の巻はまとめて振り分ける(巻ごとに _classify_renames を呼ぶと、その中の
        # sources のキャッシュが効かず、同じ SELECT を巻数ぶん発行してしまう)
        folder_sources: dict[int, set[str]] = {}
        source_paths: dict[int, set[str]] = {}
        single_rows = [r for r in rows
                       if self._single_file(r) and r["rel_path"].lower().endswith(".cbz")]
        single_items = self._classify_renames(single_rows, excluded_ids, self._range_ineligible, move_target,
                                              folder_sources=folder_sources, source_paths=source_paths, dedupe=False)
        for it in single_items:
            it["conversion"] = False
        single_by_id = {it["volume_id"]: it for it in single_items}

        for r in rows:
            if r["id"] in single_by_id:
                items.append(single_by_id[r["id"]])
                continue
            it = self._range_item(r)
            it["conversion"] = True
            items.append(it)
            if r["id"] in excluded_ids:
                it.update(status=ITEM_SKIPPED, reason="利用者が整理しない巻にしました")
                by_source.setdefault(r["source_id"], []).append(it)
                source_rows.setdefault(r["source_id"], r)
                continue
            if r["format"]:
                it.update(status=ITEM_EXCLUDED, reason="小説(EPUB・テキスト)は CBZ にできません(名前は変えません)")
                continue
            by_source.setdefault(r["source_id"], []).append(it)
            source_rows.setdefault(r["source_id"], r)
            try:
                source_path = resolve_source_path(root, r["rel_path"])
            except ValueError:
                it.update(status=ITEM_EXCLUDED, reason="元のファイル・フォルダが見つかりません(再スキャンしてください)")
                continue
            if not source_path.exists():
                it.update(status=ITEM_EXCLUDED, reason="元のファイル・フォルダが見つかりません(再スキャンしてください)")
                continue
            try:
                rel, changes = self._range_cbz_target(r, template, values, sub)
                check_path_length(root, rel)
            except ValueMissing as e:
                it.update(status=ITEM_EXCLUDED, reason=str(e))
                continue
            it["output_rel_path"] = rel
            it["changes"] = changes
            problem = self._range_cbz_target_problem(r, root, base, rel, source_path, folder_sources)
            if problem:
                it.update(status=ITEM_CONFLICT, reason=problem)

        # 同じ名前になる巻はファイル数の多い方を残す(#143)。外すのは 1 巻でも元全体なので、先に済ませる
        self._supersede_smaller_outputs(items, by_source)

        # 元(1 つのファイル・フォルダ)は全巻まとめて CBZ にする。1 巻でも外した・対象外・衝突なら元全体を止める
        for source_id, src_items in by_source.items():
            r = source_rows[source_id]
            if any(it["status"] == ITEM_SKIPPED for it in src_items):
                for it in src_items:
                    if it.get("superseded_by"):
                        continue  # ファイル数で譲った巻は、理由と出力名を残す(画面の衝突の組に出す)
                    it.update(status=ITEM_SKIPPED, reason="整理しません(同じ元の別の巻を整理しないため、元全体を整理しません)",
                              output_rel_path=None)
                continue
            bad = next((it for it in src_items if it["status"] != ITEM_CBZ), None)
            if bad is not None:
                reason = bad["reason"] or "CBZ 化できません"
                for it in src_items:
                    it.update(status=ITEM_CONFLICT,
                              reason=f"同じ元の巻を CBZ 化できないため、この元全体を CBZ 化しません: {reason}")
                continue
            source_path = resolve_source_path(root, r["rel_path"])
            try:
                if r["source_kind"] == "folder":
                    problem, more = self._folder_cbz_problem(r["root_id"], r["rel_path"], source_path)
                else:
                    more = []
                    found: list[str] = []
                    self._inspect_archive(source_path, [row for row in rows if row["source_id"] == source_id],
                                          found, more)
                    problem = found[0] if found else None
            except OSError as e:
                problem, more = f"元を読めません: {e}", []
            if problem:
                for it in src_items:
                    it.update(status=ITEM_CONFLICT, reason=problem)
                continue
            warnings.extend(f"「{r['rel_path']}」: {w}" for w in more)
            try:
                source_fingerprint = fingerprint(source_path)
            except OSError as e:
                for it in src_items:
                    it.update(status=ITEM_CONFLICT, reason=f"元を読めません: {e}")
                continue
            plan["cbz_sources"].append({
                "source_id": source_id, "rel_path": r["rel_path"], "root_id": r["root_id"],
                "root_path": r["root_path"], "source_kind": r["source_kind"],
                "fingerprint": source_fingerprint,
                "volume_ids": [it["volume_id"] for it in src_items],
                "volumes": [{"volume_id": it["volume_id"], "title": it["title"],
                             "before_title": it["before"]["title"]} for it in src_items],
            })

        self._mark_duplicate_range_outputs(items)
        # 名前の衝突で 1 巻だけ衝突にした場合も、その元全体を CBZ 化しない(全巻そろわないと元を退避できない)
        for src_items in by_source.values():
            if all(it["status"] in (ITEM_CBZ, ITEM_SKIPPED) for it in src_items):
                continue
            for it in src_items:
                if it["status"] in (ITEM_CBZ, ITEM_EXCLUDED):
                    it.update(status=ITEM_CONFLICT,
                              reason="同じ元の別の巻と出力先が競合するため、この元全体を CBZ 化しません")
        still = {it["volume_id"] for it in items if it["status"] == ITEM_CBZ}
        plan["cbz_sources"] = [s for s in plan["cbz_sources"] if all(v in still for v in s["volume_ids"])]
        return self._save_range_plan(plan, items, plan["cbz_sources"],
                                     "範囲の中に一覧に出ている巻がありません", problems, warnings)

    def _range_item(self, r: dict) -> dict:
        return {"volume_id": r["id"], "title": r["title"], "page_count": r["page_count"],
                "from_rel_path": r["rel_path"], "output_rel_path": None, "status": ITEM_CBZ, "reason": None,
                "cbz_volume_id": None, "source_id": r["source_id"], "root_id": r["root_id"],
                "root_path": str(Path(r["root_path"])), "entry_prefix": r["entry_prefix"], "changes": [],
                "conversion": True, "before": {"title": r["title"]}}

    def _range_cbz_target(self, r: dict, template: RangeTemplate, values: dict, sub: str) -> tuple[str, list[str]]:
        """CBZ 化の出力先。ファイル名テンプレートが空なら巻の表示名を整理したもの。"""
        parent, _, _ = r["rel_path"].rpartition("/")
        changes: list[str] = []
        file_stem = template.render_file(values[r["id"]])
        if file_stem is None:
            cleaned = clean_stem(r["title"])
            file_stem, changes = cleaned.name, list(cleaned.changes)
        folder = template.render_folder(values[r["id"]])
        dirs = (parent.split("/") if parent else []) if folder is None else [*(sub.split("/") if sub else []), *folder]
        return "/".join([*dirs, file_stem + ".cbz"]), changes

    def _range_cbz_target_problem(self, r: dict, root: Path, base: Path, rel: str, source_path: Path,
                                  folder_sources: dict[int, set[str]]) -> str | None:
        reason = self._target_problem(root, base, rel, source_path)
        if reason:
            return reason
        # 出力先に missing の sources 行が残っていてもよい。CBZ 化の実行は _upsert_source でその行を
        # 再利用する(改名・移動は sources.rel_path を直接 UPDATE するので、別の行があると衝突する)。
        folders = folder_sources.get(r["root_id"])
        if folders is None:
            folders = {f["rel_path"] for f in self.conn.execute(
                "SELECT rel_path FROM sources WHERE root_id = ? AND kind = 'folder' AND missing = 0",
                (r["root_id"],))}
            folder_sources[r["root_id"]] = folders
        parts = rel.split("/")[:-1]
        inside = next((a for a in ("/".join(parts[:i]) for i in range(1, len(parts) + 1))
                       if a in folders), None)
        if inside is not None:
            return f"画像フォルダの巻「{inside}」の中になります"
        return None

    def _folder_cbz_problem(self, root_id: int, rel_path: str, path: Path) -> tuple[str | None, list[str]]:
        """画像フォルダを CBZ にできるか。フォルダごと退避するので、中の別の本・サブフォルダは動かせない。"""
        try:
            entries = list(os.scandir(path))
        except OSError as e:
            return f"元のフォルダを読めません: {e}", []
        subdirs = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
        if subdirs:
            return f"サブフォルダを含む画像フォルダは CBZ にできません: {', '.join(sorted(subdirs)[:5])}", []
        prefix = rel_path + "/"
        nested = self.conn.execute(
            "SELECT rel_path FROM sources WHERE root_id = ? AND missing = 0 AND substr(rel_path, 1, ?) = ?",
            (root_id, len(prefix), prefix)).fetchall()
        if nested:
            return "このフォルダの中に別の本があるため、フォルダごと CBZ にできません", []
        others = [e.name for e in entries if not e.is_dir(follow_symlinks=False) and not is_image(e.name)]
        return None, ([f"画像以外のファイル {len(others)} 件は CBZ に入らず、元のフォルダごと退避フォルダへ移ります"]
                      if others else [])

    @staticmethod
    def _supersede_smaller_outputs(items: list[dict], by_source: dict[int, list[dict]]) -> None:
        """同じ出力名の巻のうちファイル数の少ない方を外す(#143)。CBZ 化する巻は元全体を外す。

        元を外すと、その元の別の巻が持っていた名前も空くので、変化がなくなるまで組み直す。
        """
        while True:
            groups: dict[tuple[int, str], list[dict]] = {}
            for it in items:
                if it["status"] in (ITEM_MOVE, ITEM_UNCHANGED, ITEM_CBZ) and it["output_rel_path"]:
                    groups.setdefault((it["root_id"], it["output_rel_path"].casefold()), []).append(it)
            # 同じ元の中の名前の重複は、片方を外すと元全体を外すことになり、勝つ側も巻き込む。決めずに衝突のまま残す
            losers = [(loser, top) for group in groups.values() for loser, top in _smaller_than_top(group)
                      if not (loser.get("conversion") and any(t["source_id"] == loser["source_id"] for t in top))]
            if not losers:
                return
            for loser, top in losers:
                _mark_superseded(loser, top)
            for loser, _ in losers:
                if not loser.get("conversion"):
                    continue
                for sibling in by_source.get(loser["source_id"], []):
                    if sibling["status"] != ITEM_SKIPPED:
                        sibling.update(status=ITEM_SKIPPED, output_rel_path=None,
                                       reason="整理しません(同じ元の別の巻を整理しないため、元全体を整理しません)")

    @staticmethod
    def _mark_duplicate_range_outputs(items: list[dict]) -> None:
        groups: dict[tuple[int, str], list[dict]] = {}
        for it in items:
            if it["status"] in (ITEM_MOVE, ITEM_UNCHANGED, ITEM_CBZ) and it["output_rel_path"]:
                groups.setdefault((it["root_id"], it["output_rel_path"].casefold()), []).append(it)
        for group in groups.values():
            if len(group) < 2:
                continue
            for it in group:
                if it["status"] == ITEM_UNCHANGED:
                    continue  # 既にその名前の巻はそのまま。変更する側だけを衝突にする
                others = "・".join(f"「{o['title']}」" for o in group if o is not it)
                it.update(status=ITEM_CONFLICT, reason=f"{others}と同じ名前になります。残す方を選んでください")

    def _save_range_plan(self, head: dict, items: list[dict], cbz_sources: list[dict], empty_problem: str,
                         problems: list[str] | None = None, warnings: list[str] | None = None) -> dict:
        moving = [it for it in items if it["status"] == ITEM_MOVE]
        converting = [it for it in items if it["status"] == ITEM_CBZ]
        first = moving[0] if moving else (converting[0] if converting else (items[0] if items else None))
        problems = list(problems or [])
        warnings = list(warnings or [])
        if not moving and not converting:
            problems.append("改名・移動・CBZ 化する巻がありません" if items else empty_problem)
        plan = {
            **head,
            "source_id": first["source_id"] if first else None, "root_id": first["root_id"] if first else None,
            "root_path": first["root_path"] if first else None, "source_rel_path": "", "source_kind": "archive",
            "organize_base": str(self.settings.organize_base(Path(first["root_path"]))) if first else None,
            "fingerprint": None, "needs_analysis": head.get("needs_analysis", []),
            "items": items, "warnings": warnings, "problems": problems, "cbz_sources": cbz_sources,
            "volumes": [{"volume_id": it["volume_id"], "title": it["title"], "page_count": it["page_count"],
                         "output_rel_path": it["output_rel_path"]} for it in [*moving, *converting]],
        }
        if not problems and any(it["status"] == ITEM_CONFLICT for it in items):
            plan["warnings"].append("競合している巻は改名・CBZ 化しません(残す方を選ぶか、作品・巻の情報を直して計画を作り直してください)")
        if not problems and converting:
            plan["warnings"].append(
                "CBZ 化する元は、全巻を作って検証してから確定します。元は退避フォルダへ移します(削除しません)")
        return self._save_plan(plan)

    def range_path(self, root: Path, path: str) -> str:
        """範囲のパスを蔵書ルートからの相対(`/` 区切り。ルート全体は "")にする。ルートの絶対パスで書いてもよい。"""
        norm = (path or "").strip().replace("\\", "/")
        root_norm = str(root).replace("\\", "/").rstrip("/")
        if norm.casefold() == root_norm.casefold() or norm.casefold().startswith(root_norm.casefold() + "/"):
            norm = norm[len(root_norm):]
        elif re.match(r"^[A-Za-z]:", norm):
            raise RangeError("範囲のパスは選んだ蔵書フォルダの中を指してください")
        norm = norm.strip("/")
        if not norm:
            return ""
        if any(part.strip() in ("", ".", "..") for part in norm.split("/")):
            raise RangeError("範囲のパスに空・「.」・「..」の区切りは使えません")
        try:
            full = resolve_source_path(root, norm)
        except ValueError:
            raise RangeError("範囲のパスは選んだ蔵書フォルダの中を指してください") from None
        if fs.is_within(full, self.settings.organize_base(root)):
            raise RangeError("作業場・退避フォルダは範囲にできません")
        if not full.is_dir():
            raise RangeError(f"範囲のフォルダが見つかりません: {norm}")
        return self._disk_case(root, norm)

    @staticmethod
    def _disk_case(root: Path, norm: str) -> str:
        """区切りごとにディスク上の綴り(大文字小文字)へ直す。Windows は綴りが違っても同じフォルダを指すが、
        DB の相対パスとの前方一致や出力先のフォルダ名は綴りが合っている必要がある。ぴったり同じ名前があればそれを使う
        (大文字小文字を区別するファイルシステムで別のフォルダを取り違えない)。"""
        cur, parts = root, []
        for part in norm.split("/"):
            name = part
            try:
                names = [e.name for e in os.scandir(cur)]
            except OSError:
                names = []
            if part not in names:
                name = next((n for n in sorted(names) if n.casefold() == part.casefold()), part)
            parts.append(name)
            cur = cur / name
        return "/".join(parts)

    def range_folders(self, root_id: int, path: str = "") -> dict:
        """範囲を選ぶための、フォルダの直下のフォルダ名(読むだけ)。作業場・退避フォルダと「.」で始まるものは出さない。"""
        row = self.conn.execute("SELECT path, online, enabled, inbox FROM library_roots WHERE id = ?",
                                (root_id,)).fetchone()
        if row is None or not row["enabled"] or row["inbox"]:
            raise RangeNotFound(f"蔵書フォルダが見つかりません: {root_id}")
        root = Path(row["path"])
        if not row["online"] or not root_is_accessible(root):
            raise RangeError("蔵書フォルダが今オフラインです")
        sub = self.range_path(root, path)
        full = resolve_source_path(root, sub)
        base = self.settings.organize_base(root)
        try:
            names = sorted((e.name for e in os.scandir(full)
                            if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                            and not fs.is_within(Path(e.path), base)), key=natural_key)
        except OSError as e:
            raise RangeError(f"フォルダを読めません: {e}") from e
        return {"root_id": root_id, "path": sub, "parent": sub.rpartition("/")[0] if sub else None, "folders": names}

    def _classify_renames(self, rows: list[dict], excluded_ids: set[int], ineligible, target, *,
                          folder_sources: dict[int, set[str]] | None = None,
                          source_paths: dict[int, set[str]] | None = None, dedupe: bool = True) -> list[dict]:
        """各巻を move / unchanged / excluded / conflict / skipped に振り分ける。target(r) は (出力の相対パス, 整理の説明)。

        folder_sources / source_paths は同じ計画の中で使い回すキャッシュ。範囲の全巻を渡すときは
        呼び出し側で用意して、巻ごとに同じ SELECT を発行しないようにする。
        dedupe=False なら同じ名前になる巻の判定(ファイル数の多い方を残す・同数は衝突)をしない。CBZ 化する元と
        いっしょに判定する呼び出し側(`_plan_range_cbz`)が、あとでまとめて行う。ここで単巻どうしだけ先に決めると、
        同じ名前の CBZ 化の元と比べられない(#143)。
        """
        items: list[dict] = []
        folder_sources = {} if folder_sources is None else folder_sources
        source_paths = {} if source_paths is None else source_paths
        for r in rows:
            root = Path(r["root_path"])
            item = {"volume_id": r["id"], "title": r["title"], "page_count": r["page_count"],
                    "from_rel_path": r["rel_path"], "output_rel_path": None, "status": ITEM_MOVE, "reason": None,
                    "cbz_volume_id": None, "source_id": r["source_id"], "root_id": r["root_id"],
                    "root_path": str(root), "entry_prefix": r["entry_prefix"], "changes": []}
            items.append(item)
            if r["id"] in excluded_ids:
                item.update(status=ITEM_SKIPPED, reason="利用者が整理しない巻にしました")
                continue
            reason = ineligible(r, root)
            if reason:
                item.update(status=ITEM_EXCLUDED, reason=reason)
                if "CBZ に整理" in reason:
                    item["cbz_volume_id"] = r["id"]
                continue
            try:
                rel, changes = target(r)
                check_path_length(root, rel)
            except ValueMissing as e:
                item.update(status=ITEM_EXCLUDED, reason=str(e))
                continue
            item["output_rel_path"] = rel
            item["changes"] = changes
            if rel == r["rel_path"]:
                item.update(status=ITEM_UNCHANGED, reason="既にテンプレートどおりの名前です")
            elif rel.casefold() == r["rel_path"].casefold():
                item.update(status=ITEM_EXCLUDED, reason="大文字・小文字だけの違いは改名しません")
            else:
                source_path = resolve_source_path(root, r["rel_path"])
                problem = self._target_problem(root, self.settings.organize_base(root), rel, source_path)
                if problem is None:
                    paths = source_paths.get(r["root_id"])
                    if paths is None:
                        paths = {row["rel_path"] for row in self.conn.execute(
                            "SELECT rel_path FROM sources WHERE root_id = ?", (r["root_id"],))}
                        source_paths[r["root_id"]] = paths
                    if rel in paths:
                        problem = "この場所にあったファイルの記録が残っています(同じ名前のファイルを以前スキャンしました)"
                if problem is None:
                    if r["root_id"] not in folder_sources:
                        folder_sources[r["root_id"]] = {f["rel_path"] for f in self.conn.execute(
                            "SELECT rel_path FROM sources WHERE root_id = ? AND kind = 'folder' AND missing = 0",
                            (r["root_id"],))}
                    parts = rel.split("/")[:-1]
                    inside = next((a for a in ("/".join(parts[:i]) for i in range(1, len(parts) + 1))
                                   if a in folder_sources[r["root_id"]]), None)
                    if inside is not None:
                        # 画像フォルダの巻の中へ入れると、次のスキャンでその巻の添え物・別の巻と取り違えうる
                        problem = f"画像フォルダの巻「{inside}」の中になります"
                if problem:
                    item.update(status=ITEM_CONFLICT, reason=problem)
                else:
                    item["fingerprint"] = fingerprint(source_path)
                    item["before"] = {k: r[k] for k in ("title", "name_encoding", "number_kind", "number_start",
                                                        "number_end", "number_evidence")}
                    item["before"]["name_raw"] = r["name_raw"].hex() if r["name_raw"] is not None else None
        if not dedupe:
            return items
        # 同じ名前になる巻どうし(別スキャンの重複など)は、どちらも衝突にして利用者に選ばせる
        groups: dict[tuple[int, str], list[dict]] = {}
        for it in items:
            if it["status"] in (ITEM_MOVE, ITEM_UNCHANGED):
                groups.setdefault((it["root_id"], it["output_rel_path"].casefold()), []).append(it)
        for group in groups.values():
            # ファイル数の多い方を残す(#143)。同数・不明は従来どおり衝突として利用者に選ばせる
            for loser, top in _smaller_than_top(group):
                _mark_superseded(loser, top)
            group = [it for it in group if it["status"] != ITEM_SKIPPED]
            if len(group) > 1:
                for it in group:
                    if it["status"] == ITEM_UNCHANGED:
                        continue  # 既にその名前の巻はそのまま。改名してくる側だけを衝突にする
                    others = "・".join(f"「{o['title']}」" for o in group if o is not it)
                    it.update(status=ITEM_CONFLICT, reason=f"{others}と同じ名前になります。残す方を選んでください")
        return items

    def _save_rename_plan(self, head: dict, items: list[dict], empty_problem: str) -> dict:
        moving = [it for it in items if it["status"] == ITEM_MOVE]
        first = moving[0] if moving else (items[0] if items else None)
        problems = []
        if not moving:
            problems.append("改名・移動する巻がありません" if items else empty_problem)
        plan = {
            **head,
            "source_id": first["source_id"] if first else None, "root_id": first["root_id"] if first else None,
            "root_path": first["root_path"] if first else None, "source_rel_path": "", "source_kind": "archive",
            "organize_base": None, "fingerprint": None, "needs_analysis": [],
            "items": items, "warnings": [], "problems": problems,
            "volumes": [{"volume_id": it["volume_id"], "title": it["title"], "page_count": it["page_count"],
                         "output_rel_path": it["output_rel_path"]} for it in moving],
        }
        if not problems and any(it["status"] == ITEM_CONFLICT for it in items):
            plan["warnings"].append("競合している巻は改名しません(残す方を選ぶか、作品・巻の情報を直して計画を作り直してください)")
        if not problems and any(it["entry_prefix"] for it in moving):
            plan["warnings"].append("CBZ の中のフォルダ名を巻の名前にしている巻は、一覧の名前は変わりません")
        return self._save_plan(plan)

    def _rename_ineligible(self, r: dict, root: Path) -> str | None:
        """改名・移動の対象外の理由(1 巻 = 1 CBZ でない・オフラインなど)。対象なら None。"""
        if r["source_offline"] or not root_is_accessible(root):
            return "蔵書フォルダが今オフラインです"
        if r["format"]:  # 小説(.epub / .txt)。CBZ ではなく、テンプレートの .cbz とも合わない
            return "小説(EPUB・テキスト)は整理の対象外です(名前は変えません)"
        if not self._single_file(r) or not r["rel_path"].lower().endswith(".cbz"):
            return "1 巻 = 1 CBZ ではありません(先に「CBZ に整理」をテンプレートの名前で実行してください)"
        return self._missing_file(r, root)

    def _range_ineligible(self, r: dict, root: Path) -> str | None:
        """範囲の整理の対象外の理由。1 巻 = 1 ファイル(アーカイブ・小説)なら拡張子を保って動かせる。"""
        if r["source_offline"] or not root_is_accessible(root):
            return "蔵書フォルダが今オフラインです"
        if not self._single_file(r):
            if r["source_kind"] == "folder":
                return "画像フォルダの巻です(この画面では動かしません。先に「CBZ に整理」してください)"
            return ("1 つのファイルに複数の巻・入れ子の巻があります(この画面では動かしません。"
                    "先に「CBZ に整理」してください)")
        return self._missing_file(r, root)

    @staticmethod
    def _single_file(r: dict) -> bool:
        return (r["source_kind"] == "archive" and r["container_path"] is None and r["parent_id"] is None
                and r["inner_path"] is None and r["source_volumes"] == 1)

    @staticmethod
    def _missing_file(r: dict, root: Path) -> str | None:
        try:
            if not resolve_source_path(root, r["rel_path"]).is_file():
                return "元のファイルが見つかりません(再スキャンしてください)"
        except ValueError:
            return "元のファイルが見つかりません(再スキャンしてください)"
        return None

    def _execute_rename(self, op_id: str, plan: dict) -> dict:
        moving = [it for it in plan["items"] if it["status"] == ITEM_MOVE]
        try:
            for it in moving:
                self._check_rename_item(it)
        except OrganizeError as e:
            self._set_state(op_id, "failed", error=str(e))
            raise OrganizeError(str(e), self.get(op_id)) from e
        self._set_state(op_id, "running")
        done_steps: list[_Step] = []
        try:
            for it in moving:
                root = Path(it["root_path"])
                src, dst = root / it["from_rel_path"], root / it["output_rel_path"]
                self._mkdir_steps(op_id, "execute", root, self.settings.organize_base(root), dst, done_steps)
                self._move_step(op_id, "execute", "rename", src, dst, done_steps)
            now = db.now()
            with db.transaction(self.conn) as c:
                for it in moving:
                    self._relocate(c, it, it["output_rel_path"], None)
                    c.execute("""UPDATE organize_volumes SET new_volume_id = old_volume_id
                                 WHERE op_id = ? AND old_volume_id = ?""", (op_id, it["volume_id"]))
                c.execute("""UPDATE organize_ops SET state = 'done', error = NULL, executed_at = ?, updated_at = ?
                             WHERE id = ?""", (now, now, op_id))
        except BaseException as e:
            ok = self._revert(done_steps)
            message = _describe(e)
            self._set_state(op_id, "failed" if ok else "rollback_failed",
                            error=message if ok else f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        return self.get(op_id)

    def _check_rename_item(self, it: dict) -> None:
        """改名を始める前の確認。どれかに当たれば何も動かさずに止める。"""
        root = Path(it["root_path"])
        if not root_is_accessible(root):
            raise OrganizeError("蔵書フォルダが今オフラインです")
        src = root / it["from_rel_path"]
        if not src.is_file():
            raise OrganizeError(f"元のファイルが見つかりません: {it['from_rel_path']}")
        if fingerprint(src) != it["fingerprint"]:
            raise OrganizeError(f"計画を作った後に元が変わりました。計画を作り直してください: {it['from_rel_path']}")
        if os.path.lexists(root / it["output_rel_path"]):
            raise OrganizeError(f"出力先に既にファイルがあります: {it['output_rel_path']}")
        if not self.same_drive(src, root / it["output_rel_path"]):
            raise OrganizeError(f"出力先が元と別のドライブです: {it['output_rel_path']}")
        row = self.conn.execute(
            """SELECT v.title, v.missing, v.organized_op_id, s.rel_path FROM volumes v
               JOIN sources s ON s.id = v.source_id WHERE v.id = ?""", (it["volume_id"],)).fetchone()
        if (row is None or row["missing"] or row["organized_op_id"] is not None
                or row["rel_path"] != it["from_rel_path"] or row["title"] != it["before"]["title"]):
            raise OrganizeError(f"巻が変わりました。計画を作り直してください: {it['title']}")
        if self.conn.execute("SELECT 1 FROM sources WHERE root_id = ? AND rel_path = ?",
                             (it["root_id"], it["output_rel_path"])).fetchone():
            raise OrganizeError(f"出力先の記録が既にあります。計画を作り直してください: {it['output_rel_path']}")

    def _relocate(self, c, it: dict, rel_path: str, before: dict | None) -> None:
        """ソースのパスを付け替え、巻の名前をスキャナーが新しい名前から作るものに合わせる(before があればその値に戻す)。

        巻 id は変えない(読書位置・作品の割り当て・指紋・重複の却下が巻 id に付いているため)。ファイルの中身・
        サイズ・mtime は改名で変わらないので、次のスキャンは読み直さず、同じ巻のまま続く。
        """
        now = db.now()
        c.execute("UPDATE sources SET rel_path = ? WHERE id = ?", (rel_path, it["source_id"]))
        if it["entry_prefix"]:
            return  # 巻の名前は CBZ の中のフォルダ名から来ている。ファイル名を変えても変わらない
        if before is None:
            name = rel_path.rsplit("/", 1)[-1]
            title = Path(name).stem
            num = parse_number(title)
            numbers = (num.kind, num.start, num.end, num.evidence) if num else (None, None, None, None)
            c.execute("""UPDATE volumes SET title = ?, name_raw = ?, name_encoding = 'filesystem', number_kind = ?,
                         number_start = ?, number_end = ?, number_evidence = ?, updated_at = ? WHERE id = ?""",
                      (title, _fs_name_bytes(name), *numbers, now, it["volume_id"]))
        else:
            raw = bytes.fromhex(before["name_raw"]) if before["name_raw"] is not None else None
            c.execute("""UPDATE volumes SET title = ?, name_raw = ?, name_encoding = ?, number_kind = ?,
                         number_start = ?, number_end = ?, number_evidence = ?, updated_at = ? WHERE id = ?""",
                      (before["title"], raw, before["name_encoding"], before["number_kind"], before["number_start"],
                       before["number_end"], before["number_evidence"], now, it["volume_id"]))

    def _undo_rename(self, op_id: str, plan: dict) -> dict:
        moved = {s["to_path"]: s for s in self.conn.execute(
            """SELECT * FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND action = 'rename'
               AND state = 'done'""", (op_id,))}
        moving = [it for it in plan["items"] if it["status"] == ITEM_MOVE]
        problem = None
        for it in moving:
            root = Path(it["root_path"])
            src, dst = root / it["from_rel_path"], root / it["output_rel_path"]
            if not root_is_accessible(root):
                problem = "蔵書フォルダが今オフラインです"
            elif str(dst) not in moved:
                problem = f"改名の記録が見つかりません: {it['output_rel_path']}"
            elif os.path.lexists(src):
                problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {it['from_rel_path']}"
            elif not dst.is_file():
                problem = f"改名したファイルが見つかりません(移動・改名された?): {it['output_rel_path']}"
            elif fingerprint(dst) != it["fingerprint"]:
                problem = f"改名した後にファイルが変わったので戻しません: {it['output_rel_path']}"
            else:
                row = self.conn.execute("SELECT id FROM sources WHERE root_id = ? AND rel_path = ?",
                                        (it["root_id"], it["from_rel_path"])).fetchone()
                if row is not None and row["id"] != it["source_id"]:
                    problem = f"元の場所の記録が別にできています(再スキャンした?): {it['from_rel_path']}"
            if problem:
                raise OrganizeError(problem, self.get(op_id))
        self._set_state(op_id, "undoing")
        done_steps: list[_Step] = []
        try:
            for it in reversed(moving):
                root = Path(it["root_path"])
                self._move_step(op_id, "undo", "restore", root / it["output_rel_path"], root / it["from_rel_path"],
                                done_steps)
            self._rmdir_created(op_id, done_steps)
            now = db.now()
            with db.transaction(self.conn) as c:
                for it in moving:
                    self._relocate(c, it, it["from_rel_path"], it["before"])
                c.execute("""UPDATE organize_ops SET state = 'undone', error = NULL, undone_at = ?, updated_at = ?
                             WHERE id = ?""", (now, now, op_id))
        except BaseException as e:
            ok = self._revert(done_steps)
            message = _describe(e)
            if ok:
                self._set_state(op_id, "done", error=f"元に戻せませんでした: {message}")
            else:
                self._set_state(op_id, "undo_failed",
                                error=f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        return self.get(op_id)

    # --- 範囲指定の整理で CBZ 化もまとめて行う(#133) --------------------------------

    def _execute_range_cbz(self, op_id: str, plan: dict) -> dict:
        """1 巻 = 1 CBZ の改名・移動と、CBZ 化を 1 つの操作として実行する。

        先に CBZ 化する全巻を作業場で作って検証し、その後に改名で確定 → 元のファイル・フォルダを退避、の順に進む。
        途中で失敗したら、この操作で動かしたすべてを逆順に戻す。
        """
        moving = [it for it in plan["items"] if it["status"] == ITEM_MOVE]
        converting = [it for it in plan["items"] if it["status"] == ITEM_CBZ]
        sources = plan.get("cbz_sources") or []
        try:
            for it in moving:
                self._check_rename_item(it)
            for it in converting:
                self._check_range_cbz_item(it)
            for src in sources:
                self._check_cbz_source(src)
        except OrganizeError as e:
            self._set_state(op_id, "failed", error=str(e))
            raise OrganizeError(str(e), self.get(op_id)) from e
        self._set_state(op_id, "running")
        root = Path(plan["scope"]["root_path"])
        base = self.settings.organize_base(root)
        work_dir = base / "work" / op_id
        done_steps: list[_Step] = []
        built: list[_Built] = []
        try:
            work_dir.mkdir(parents=True, exist_ok=False)
            # 1. CBZ 化する全巻を作業場に作る(元にも出力先にもまだ触らない)
            for i, it in enumerate(converting):
                b = _Built(it, work_dir / f"{i + 1:04d}.cbz", root / it["output_rel_path"],
                           page_count=it["page_count"])
                built.append(b)
                with self.library.open(self.conn, it["volume_id"]) as opened:
                    if not opened.page_names:
                        raise OrganizeError(f"ページの無い巻は CBZ 化できません: {it['title']}")
                    b.records = cbz.write_cbz(b.work_path, opened.page_names, opened.read_page)
            # 2. 全巻を読み直して検証する
            for b in built:
                cbz.verify_cbz(b.work_path, b.records)
            # 3. 改名で確定する
            for b in built:
                self._mkdir_steps(op_id, "execute", root, base, b.output_path, done_steps)
                self._move_step(op_id, "execute", "commit", b.work_path, b.output_path, done_steps)
            for it in moving:
                src, dst = root / it["from_rel_path"], root / it["output_rel_path"]
                self._mkdir_steps(op_id, "execute", root, base, dst, done_steps)
                self._move_step(op_id, "execute", "rename", src, dst, done_steps)
            # 4. CBZ 化した元を退避フォルダへ移す(削除しない)
            for src in sources:
                original = resolve_source_path(root, src["rel_path"])
                trashed = base / "trash" / op_id / "original" / src["rel_path"]
                self._move_step(op_id, "execute", "trash_original", original, trashed, done_steps)
            # 5. DB: 新しい CBZ の巻を登録し、読書位置・作品の割り当てを引き継ぐ
            self._record_range_executed(op_id, plan, root, moving, built, sources)
        except BaseException as e:
            ok = self._revert(done_steps)
            for b in built:
                if b.work_path.is_file():
                    _unlink_quietly(b.work_path)
            _rmdir_quietly(work_dir)
            message = _describe(e)
            self._set_state(op_id, "failed" if ok else "rollback_failed",
                            error=message if ok else f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        _rmdir_quietly(work_dir)
        return self.get(op_id)

    def _check_range_cbz_item(self, it: dict) -> None:
        """CBZ 化する巻を始める前の確認。出力先・巻の記録・ドライブを見る。"""
        root = Path(it["root_path"])
        if not root_is_accessible(root):
            raise OrganizeError("蔵書フォルダが今オフラインです")
        target = root / it["output_rel_path"]
        original = root / it["from_rel_path"]
        if os.path.lexists(target):
            raise OrganizeError(f"出力先に既にファイルがあります: {it['output_rel_path']}")
        if not self.same_drive(original, target):
            raise OrganizeError(f"出力先が元と別のドライブです: {it['output_rel_path']}")
        row = self.conn.execute(
            """SELECT v.title, v.missing, v.organized_op_id, s.rel_path FROM volumes v
               JOIN sources s ON s.id = v.source_id WHERE v.id = ?""", (it["volume_id"],)).fetchone()
        if (row is None or row["missing"] or row["organized_op_id"] is not None
                or row["rel_path"] != it["from_rel_path"] or row["title"] != it["before"]["title"]):
            raise OrganizeError(f"巻が変わりました。計画を作り直してください: {it['title']}")
        # 出力先に missing の sources 行が残っていてもよい(実行の _upsert_source が再利用する)。
        # ディスク上にファイルがある場合は上の lexists で止まる。

    def _check_cbz_source(self, src: dict) -> None:
        """CBZ 化する元(1 つのファイル・フォルダ)が計画のときのままか。"""
        root = Path(src["root_path"])
        if not root_is_accessible(root):
            raise OrganizeError("蔵書フォルダが今オフラインです")
        try:
            original = resolve_source_path(root, src["rel_path"])
        except ValueError as e:
            raise OrganizeError(f"元のファイル・フォルダが見つかりません: {src['rel_path']}") from e
        if not original.exists():
            raise OrganizeError(f"元のファイル・フォルダが見つかりません: {src['rel_path']}")
        if fingerprint(original) != src["fingerprint"]:
            raise OrganizeError(f"計画を作った後に元が変わりました。計画を作り直してください: {src['rel_path']}")

    def _record_range_executed(self, op_id: str, plan: dict, root: Path, moving: list[dict],
                               built: list[_Built], sources: list[dict]) -> None:
        scanner = Scanner(self.conn, self.settings)
        now = db.now()
        with db.transaction(self.conn) as c:
            for b in built:
                rel = _rel_posix(root, b.output_path)
                st = b.output_path.stat()
                pages = len(b.records) if b.records else b.page_count
                src = scanner._upsert_source(c, plan["scope"]["root_id"], rel, "archive")
                scanner._replace_volumes(c, src["id"], [VolumeSpec(
                    None, "", b.output_path.stem, _fs_name_bytes(b.output_path.name), "filesystem", pages, None)])
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, now, src["id"]))
                new_vid = c.execute("""SELECT id FROM volumes WHERE source_id = ? AND container_path IS NULL
                                       AND entry_prefix = ''""", (src["id"],)).fetchone()["id"]
                old_vid = b.volume["volume_id"]
                transfer_volume_state(c, old_vid, new_vid, pages)
                c.execute("UPDATE volumes SET organized_op_id = ?, page_count = ?, updated_at = ? WHERE id = ?",
                          (op_id, pages, now, old_vid))
                c.execute("""UPDATE organize_volumes SET new_volume_id = ?, page_count = ?
                             WHERE op_id = ? AND old_volume_id = ?""", (new_vid, pages, op_id, old_vid))
            for it in moving:
                self._relocate(c, it, it["output_rel_path"], None)
                c.execute("""UPDATE organize_volumes SET new_volume_id = old_volume_id
                             WHERE op_id = ? AND old_volume_id = ?""", (op_id, it["volume_id"]))
            # CBZ 化した元は退避フォルダへ移ったので、スキャンが見るのと同じく missing にする(行は消さない)
            for src in sources:
                c.execute("UPDATE sources SET missing = 1 WHERE id = ?", (src["source_id"],))
            c.execute("UPDATE organize_ops SET state = 'done', error = NULL, executed_at = ?, updated_at = ? WHERE id = ?",
                      (now, now, op_id))

    def _undo_range_cbz(self, op_id: str, plan: dict) -> dict:
        """CBZ 化と改名・移動をまとめて元に戻す。作った CBZ は退避の created/ へ、元は元の場所へ戻す。"""
        root = Path(plan["scope"]["root_path"])
        base = self.settings.organize_base(root)
        moving = [it for it in plan["items"] if it["status"] == ITEM_MOVE]
        converting = [it for it in plan["items"] if it["status"] == ITEM_CBZ]
        sources = plan.get("cbz_sources") or []
        steps = self.conn.execute(
            "SELECT * FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND state = 'done'",
            (op_id,)).fetchall()
        commits = {s["to_path"]: s for s in steps if s["action"] == "commit"}
        renames = {s["to_path"]: s for s in steps if s["action"] == "rename"}
        trashes = {s["from_path"]: s for s in steps if s["action"] == "trash_original"}
        problem = None
        if not root_is_accessible(root):
            problem = "蔵書フォルダが今オフラインです"
        else:
            for it in moving:
                src, dst = root / it["from_rel_path"], root / it["output_rel_path"]
                if os.path.lexists(src):
                    problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {it['from_rel_path']}"
                elif str(dst) not in renames:
                    problem = f"改名の記録が見つかりません: {it['output_rel_path']}"
                elif not dst.is_file():
                    problem = f"改名したファイルが見つかりません(移動・改名された?): {it['output_rel_path']}"
                elif fingerprint(dst) != it["fingerprint"]:
                    problem = f"改名した後にファイルが変わったので戻しません: {it['output_rel_path']}"
                else:
                    row = self.conn.execute("SELECT id FROM sources WHERE root_id = ? AND rel_path = ?",
                                             (it["root_id"], it["from_rel_path"])).fetchone()
                    if row is not None and row["id"] != it["source_id"]:
                        problem = f"元の場所の記録が別にできています(再スキャンした?): {it['from_rel_path']}"
                if problem:
                    break
        if problem is None:
            for it in converting:
                dst = root / it["output_rel_path"]
                if str(dst) not in commits:
                    problem = f"CBZ 化の記録が見つかりません: {it['output_rel_path']}"
                elif not dst.is_file():
                    problem = f"作った CBZ が見つかりません(移動・改名された?): {it['output_rel_path']}"
                if problem:
                    break
        if problem is None:
            for src in sources:
                original = resolve_source_path(root, src["rel_path"])
                step = trashes.get(str(original))
                if os.path.lexists(original):
                    problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {src['rel_path']}"
                elif step is None:
                    problem = f"退避の記録が見つかりません: {src['rel_path']}"
                elif not Path(step["to_path"]).exists():
                    problem = f"退避した元が見つかりません: {step['to_path']}"
                elif fingerprint(Path(step["to_path"])) != src["fingerprint"]:
                    problem = f"退避した元が変わったので戻しません: {src['rel_path']}"
                if problem:
                    break
        if problem:
            raise OrganizeError(problem, self.get(op_id))

        self._set_state(op_id, "undoing")
        created_dir = base / "trash" / op_id / "created"
        done_steps: list[_Step] = []
        try:
            for it in converting:
                out = root / it["output_rel_path"]
                self._move_step(op_id, "undo", "trash_created", out, created_dir / _rel_posix(root, out),
                                done_steps)
            for src in sources:
                original = resolve_source_path(root, src["rel_path"])
                self._move_step(op_id, "undo", "restore_original", Path(trashes[str(original)]["to_path"]), original,
                                done_steps)
            for it in reversed(moving):
                self._move_step(op_id, "undo", "restore", root / it["output_rel_path"], root / it["from_rel_path"],
                                done_steps)
            self._rmdir_created(op_id, done_steps)
            self._record_range_undone(op_id, plan, root, moving, converting, sources)
        except BaseException as e:
            ok = self._revert(done_steps)
            message = _describe(e)
            if ok:
                self._set_state(op_id, "done", error=f"元に戻せませんでした: {message}")
            else:
                self._set_state(op_id, "undo_failed",
                                error=f"{message}(巻き戻しに失敗した手順があります。ジャーナルを確認してください)")
            if isinstance(e, Exception):
                raise OrganizeError(message, self.get(op_id)) from e
            raise
        return self.get(op_id)

    def _record_range_undone(self, op_id: str, plan: dict, root: Path, moving: list[dict],
                             converting: list[dict], sources: list[dict]) -> None:
        now = db.now()
        with db.transaction(self.conn) as c:
            mapping = {m["old_volume_id"]: m for m in c.execute(
                "SELECT * FROM organize_volumes WHERE op_id = ?", (op_id,))}
            for it in converting:
                m = mapping.get(it["volume_id"])
                if m is not None and m["new_volume_id"] is not None:
                    transfer_volume_state(c, m["new_volume_id"], it["volume_id"], m["page_count"])
                c.execute("UPDATE volumes SET organized_op_id = NULL, updated_at = ? WHERE id = ?",
                          (now, it["volume_id"]))
            for it in moving:
                self._relocate(c, it, it["from_rel_path"], it["before"])
            for src in sources:
                c.execute("UPDATE sources SET missing = 0 WHERE id = ?", (src["source_id"],))
            # 作った CBZ は退避フォルダへ移ったので、そのソースは missing にする(行・読書位置は消さない)
            c.executemany("UPDATE sources SET missing = 1 WHERE root_id = ? AND rel_path = ?",
                          [(plan["scope"]["root_id"], _rel_posix(root, root / it["output_rel_path"]))
                           for it in converting])
            c.execute("UPDATE organize_ops SET state = 'undone', error = NULL, undone_at = ?, updated_at = ? WHERE id = ?",
                      (now, now, op_id))

    # --- 参照 ---------------------------------------------------------------

    @staticmethod
    def _view_plan(plan: dict) -> dict:
        view = {"kind": plan.get("kind", "cbz"), "naming": plan.get("naming", "title"),
                "template": plan.get("template"), "include_cbz": bool(plan.get("include_cbz", False)),
                "source_id": plan["source_id"], "source_rel_path": plan["source_rel_path"],
                "source_kind": plan["source_kind"], "volumes": plan["volumes"],
                "warnings": plan["warnings"], "problems": plan["problems"],
                "needs_analysis": plan.get("needs_analysis", [])}
        if plan.get("kind") == "intake":
            view.update(mode=plan.get("mode"), work=plan.get("work"), skipped=plan.get("skipped", []),
                        inbox_path=plan.get("root_path"), library_path=plan.get("out_root_path"))
        if plan.get("kind") in RENAME_KINDS:
            view["work_id"] = plan.get("work_id")
            view["items"] = [{**{k: it[k] for k in ("volume_id", "title", "from_rel_path", "output_rel_path", "status",
                                                    "reason", "cbz_volume_id")},
                              "changes": it.get("changes", []), "conversion": bool(it.get("conversion", False)),
                              "page_count": it.get("page_count"), "superseded_by": list(it.get("superseded_by", []))}
                             for it in plan["items"]]
        if plan.get("kind") == "range":
            view["scope"] = plan["scope"]
            view["cbz_sources"] = [{"source_id": s["source_id"], "rel_path": s["rel_path"],
                                    "volume_ids": list(s.get("volume_ids", [])),
                                    "source_kind": s.get("source_kind")}
                                   for s in plan.get("cbz_sources", [])]
        return view

    def get(self, op_id: str) -> dict:
        op, plan = self._load(op_id)
        mapping = {m["old_volume_id"]: m for m in self.conn.execute(
            "SELECT * FROM organize_volumes WHERE op_id = ?", (op_id,))}
        view = self._view_plan(plan)
        view["volumes"] = [{**v, "new_volume_id": mapping[v["volume_id"]]["new_volume_id"]
                            if v["volume_id"] in mapping else None,
                            "page_count": (mapping[v["volume_id"]]["page_count"]
                                           if v["volume_id"] in mapping else v["page_count"])}
                           for v in plan["volumes"]]
        return {"op_id": op["id"], "state": op["state"], "error": op["error"], "created_at": op["created_at"],
                "executed_at": op["executed_at"], "undone_at": op["undone_at"], **view}

    def history(self) -> list[dict]:
        """実行を試みた操作(計画だけのものは除く)を新しい順に。"""
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM organize_ops WHERE state != 'planned' ORDER BY created_at DESC, id DESC")]
        return [self.get(i) for i in ids]


def transfer_volume_state(c: sqlite3.Connection, from_id: int, to_id: int, page_count: int | None) -> None:
    """巻に紐づくユーザーの記録(読書位置・作品の割り当て・巻の書誌・ブックマーク)を別の巻へ写す。手動編集メタデータを足したらここで一緒に写す。

    ページの並びは CBZ 化の前後で同じ(今の並び順で固定する)なので、ページ番号はそのまま使える。
    作品の割り当ては手動の決定も含めてそのまま写す(整理で作品が変わらない。元に戻すときは逆向きに写す)。
    """
    transfer_assignment(c, from_id, to_id)
    transfer_volume_fields(c, from_id, to_id)  # 手動で直した出版社・ISBN などを、整理しても失わない
    bookmarks.transfer_volume(c, from_id, to_id)  # 印は写さず移す(元に戻すと元の巻へ戻る)
    page_marks.transfer_volume(c, from_id, to_id, page_count)  # しおり(#136)も移す(元に戻すと戻る)
    row = c.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?", (from_id,)).fetchone()
    if row is None:
        return
    page = row["page"] if page_count is None else min(row["page"], max(page_count - 1, 0))
    c.execute("""INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, ?, ?)
                 ON CONFLICT (volume_id) DO UPDATE SET page = excluded.page, finished = excluded.finished,
                 updated_at = excluded.updated_at""", (to_id, page, row["finished"], db.now()))


def _describe(e: BaseException) -> str:
    if isinstance(e, (OrganizeError, cbz.VerificationFailed)):
        return str(e)
    if isinstance(e, VolumeOffline):
        return "巻のある蔵書フォルダが今オフラインです"
    if isinstance(e, (VolumeUnreadable, CacheTooLarge)):
        return f"巻を読めません: {e}"
    if isinstance(e, VolumeNotFound):
        return "巻が見つかりません(再スキャンしてください)"
    return f"{type(e).__name__}: {e}"


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _rmdir_quietly(path: Path) -> None:
    """空のときだけ消す(中身があれば残す)。"""
    try:
        path.rmdir()
    except OSError:
        pass
