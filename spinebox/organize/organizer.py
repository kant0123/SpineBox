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
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import db
from ..archive import ArchiveError, list_entries, open_archive
from ..archive.base import reader_available
from ..cache import CacheTooLarge
from ..config import Settings
from ..library import Library, VolumeNotFound, VolumeOffline, VolumeUnreadable, get_volume_row
from ..pages import is_image, natural_key
from ..naming import parse_number
from ..scanner import (LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, SCAN_VERSION, Scanner, VolumeSpec, _fs_name_bytes,
                       resolve_source_path, root_is_accessible)
from ..works import WorkNotFound, root_of, transfer_assignment
from . import cbz, fs
from .template import NameTemplate, NameValues, ValueMissing, check_path_length

NAMINGS = ("title", "template")
# 改名・移動の計画の各巻の扱い
ITEM_MOVE, ITEM_UNCHANGED, ITEM_EXCLUDED, ITEM_CONFLICT, ITEM_SKIPPED = (
    "move", "unchanged", "excluded", "conflict", "skipped")


class OrganizeError(Exception):
    """整理処理を始められない・失敗した(失敗時は巻き戻し済み)。"""

    def __init__(self, message: str, op: dict | None = None):
        super().__init__(message)
        self.op = op


class OperationNotFound(Exception):
    """その ID の操作が無い。"""


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
        """巻ごとのテンプレートの値。作品の項目は統合されていれば吸収先のもの。番号は名前から読んだ候補。"""
        out: dict[int, NameValues] = {}
        fields_cache: dict[int, dict] = {}
        for vid in volume_ids:
            row = self.conn.execute(
                """SELECT v.number_kind, v.number_start, v.number_end, wv.work_id FROM volumes v
                   LEFT JOIN work_volumes wv ON wv.volume_id = v.id WHERE v.id = ?""", (vid,)).fetchone()
            fields: dict = {}
            work_id = root_of(self.conn, row["work_id"]) if row["work_id"] is not None else None
            if work_id is not None:
                if work_id not in fields_cache:
                    fields_cache[work_id] = {r["field"]: r["value"] for r in self.conn.execute(
                        "SELECT field, value FROM work_fields WHERE work_id = ?", (work_id,))}
                fields = fields_cache[work_id]
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

    def _drive_problems(self, source_path: Path, base: Path) -> list[str]:
        problems = []
        for label, target in (("作業場・退避フォルダ", base), ("出力先", source_path.parent)):
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
        if not reader_available(path.name, sevenzip):
            problems.append("この形式は今読めません(7-Zip が見つからない、または未対応)")
            return
        for v in vols:
            if v["container_path"] and not reader_available(v["container_path"], sevenzip):
                problems.append(f"内側アーカイブを読めません(7-Zip が見つからない、または未対応): {v['container_path']}")
        try:
            with open_archive(path, sevenzip_path=sevenzip) as reader:
                listing = list_entries(reader, self.settings.limits)
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
        if plan.get("kind") == "rename":
            return self._execute_rename(op_id, plan)
        root = Path(plan["root_path"])
        base = Path(plan["organize_base"])
        work_dir = base / "work" / op_id
        trash_path = base / "trash" / op_id / "original" / plan["source_rel_path"]
        try:
            source_path = self._check_before_execute(plan, root, base)
        except OrganizeError as e:
            self._set_state(op_id, "failed", error=str(e))
            raise OrganizeError(str(e), self.get(op_id)) from e

        self._set_state(op_id, "running")
        done_steps: list[_Step] = []
        built: list[_Built] = []
        try:
            work_dir.mkdir(parents=True, exist_ok=False)
            # 1. 全巻の CBZ を作業場に作る(元にも出力先にもまだ触らない)
            for i, v in enumerate(plan["volumes"]):
                b = _Built(v, work_dir / f"{i + 1:04d}.cbz", root / v["output_rel_path"])
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
                self._mkdir_steps(op_id, "execute", root, base, b.output_path, done_steps)
                self._move_step(op_id, "execute", "commit", b.work_path, b.output_path, done_steps)
            self._move_step(op_id, "execute", "trash_original", source_path, trash_path, done_steps)
            # 5. DB: 新しい CBZ の巻を登録し、読書位置を引き継ぎ、元の巻を「整理済み」にする
            self._record_executed(op_id, plan, root, built)
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

    def _check_before_execute(self, plan: dict, root: Path, base: Path) -> Path:
        """書き込みを始める前の確認。どれかに当たれば何も書かずに止める。"""
        if not root_is_accessible(root):
            raise OrganizeError("蔵書フォルダが今オフラインです")
        source_path = resolve_source_path(root, plan["source_rel_path"])
        if not source_path.exists():
            raise OrganizeError("元のファイル・フォルダが見つかりません")
        problems = self._drive_problems(source_path, base)
        if problems:
            raise OrganizeError(problems[0])
        if fingerprint(source_path) != plan["fingerprint"]:
            raise OrganizeError("計画を作った後に元が変わりました。計画を作り直してください")
        for v in plan["volumes"]:
            out = root / v["output_rel_path"]
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
        scanner = Scanner(self.conn, self.settings)
        now = db.now()
        with db.transaction(self.conn) as c:
            for b in built:
                rel = _rel_posix(root, b.output_path)
                st = b.output_path.stat()
                # スキャナーがこの CBZ から作るのと同じ巻(直下の画像 = entry_prefix '')として登録する
                src = scanner._upsert_source(c, plan["root_id"], rel, "archive")
                stem = b.output_path.stem
                scanner._replace_volumes(c, src["id"], [VolumeSpec(
                    None, "", stem, _fs_name_bytes(b.output_path.name), "filesystem", len(b.records))])
                c.execute("UPDATE sources SET size = ?, mtime_ns = ?, scan_version = ?, scanned_at = ? WHERE id = ?",
                          (st.st_size, st.st_mtime_ns, SCAN_VERSION, now, src["id"]))
                new_vid = c.execute("""SELECT id FROM volumes WHERE source_id = ? AND container_path IS NULL
                                       AND entry_prefix = ''""", (src["id"],)).fetchone()["id"]
                old_vid = b.volume["volume_id"]
                transfer_volume_state(c, old_vid, new_vid, len(b.records))
                c.execute("UPDATE volumes SET organized_op_id = ?, page_count = ?, updated_at = ? WHERE id = ?",
                          (op_id, len(b.records), now, old_vid))
                c.execute("""UPDATE organize_volumes SET new_volume_id = ?, page_count = ?
                             WHERE op_id = ? AND old_volume_id = ?""", (new_vid, len(b.records), op_id, old_vid))
            # 元は退避フォルダへ移ったので、スキャンが見るのと同じく missing にする(行は消さない)
            c.execute("UPDATE sources SET missing = 1 WHERE id = ?", (plan["source_id"],))
            c.execute("UPDATE organize_ops SET state = 'done', error = NULL, executed_at = ?, updated_at = ? WHERE id = ?",
                      (now, now, op_id))

    # --- 元に戻す -------------------------------------------------------------

    def undo(self, op_id: str) -> dict:
        op, plan = self._load(op_id)
        if op["state"] != "done":
            raise OrganizeError(f"この操作は元に戻せる状態ではありません({op['state']})", self.get(op_id))
        if plan.get("kind") == "rename":
            return self._undo_rename(op_id, plan)
        root = Path(plan["root_path"])
        base = Path(plan["organize_base"])
        steps = {s["action"]: s for s in self.conn.execute(
            "SELECT * FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND state = 'done'", (op_id,))}
        trash = steps.get("trash_original")
        if trash is None:
            raise OrganizeError("退避の記録が見つかりません", self.get(op_id))
        original, trashed = Path(trash["from_path"]), Path(trash["to_path"])
        outputs = [Path(s["to_path"]) for s in self.conn.execute(
            """SELECT to_path FROM organize_steps WHERE op_id = ? AND phase = 'execute' AND action = 'commit'
               AND state = 'done' ORDER BY id""", (op_id,))]
        created_dir = base / "trash" / op_id / "created"
        problem = None
        if not root_is_accessible(root):
            problem = "蔵書フォルダが今オフラインです"
        elif os.path.lexists(original):
            problem = f"元の場所に別のファイル・フォルダがあるので戻しません: {_rel_posix(root, original)}"
        elif not trashed.exists():
            problem = f"退避した元が見つかりません: {trashed}"
        else:
            for out in outputs:
                if not out.is_file():
                    problem = f"作った CBZ が見つかりません(移動・改名された?): {_rel_posix(root, out)}"
                    break
        if problem:
            raise OrganizeError(problem, self.get(op_id))

        self._set_state(op_id, "undoing")
        done_steps: list[_Step] = []
        try:
            for out in outputs:
                self._move_step(op_id, "undo", "trash_created", out, created_dir / _rel_posix(root, out), done_steps)
            self._move_step(op_id, "undo", "restore_original", trashed, original, done_steps)
            self._rmdir_created(op_id, done_steps)
            self._record_undone(op_id, plan, root, outputs)
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

    def _record_undone(self, op_id: str, plan: dict, root: Path, outputs: list[Path]) -> None:
        now = db.now()
        with db.transaction(self.conn) as c:
            for m in c.execute("SELECT * FROM organize_volumes WHERE op_id = ?", (op_id,)).fetchall():
                if m["new_volume_id"] is not None:
                    transfer_volume_state(c, m["new_volume_id"], m["old_volume_id"], m["page_count"])
                c.execute("UPDATE volumes SET organized_op_id = NULL, updated_at = ? WHERE id = ?",
                          (now, m["old_volume_id"]))
            c.execute("UPDATE sources SET missing = 0 WHERE id = ?", (plan["source_id"],))
            # 作った CBZ は退避フォルダへ移ったので、そのソースは missing にする(行・読書位置は消さない)
            c.executemany("UPDATE sources SET missing = 1 WHERE root_id = ? AND rel_path = ?",
                          [(plan["root_id"], _rel_posix(root, out)) for out in outputs])
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
            """SELECT v.*, s.rel_path, s.kind AS source_kind, s.offline AS source_offline, s.root_id,
                      r.path AS root_path,
                      (SELECT COUNT(*) FROM volumes o WHERE o.source_id = v.source_id AND o.missing = 0
                         AND o.organized_op_id IS NULL AND o.parent_id IS NULL) AS source_volumes
               """ + LISTED_VOLUMES_JOIN + " JOIN work_volumes wv ON wv.volume_id = v.id "
            + LISTED_VOLUMES_WHERE + " AND wv.work_id = ?", (root_work,))]
        rows.sort(key=lambda r: (r["number_start"] if r["number_start"] is not None else 10 ** 9,
                                 natural_key(r["title"])))
        values = self.name_values([r["id"] for r in rows])
        excluded_ids = set(exclude_volume_ids)
        items: list[dict] = []
        for r in rows:
            root = Path(r["root_path"])
            item = {"volume_id": r["id"], "title": r["title"], "page_count": r["page_count"],
                    "from_rel_path": r["rel_path"], "output_rel_path": None, "status": ITEM_MOVE, "reason": None,
                    "cbz_volume_id": None, "source_id": r["source_id"], "root_id": r["root_id"],
                    "root_path": str(root), "entry_prefix": r["entry_prefix"]}
            items.append(item)
            if r["id"] in excluded_ids:
                item.update(status=ITEM_SKIPPED, reason="外しました")
                continue
            reason = self._rename_ineligible(r, root)
            if reason:
                item.update(status=ITEM_EXCLUDED, reason=reason)
                if "CBZ に整理" in reason:
                    item["cbz_volume_id"] = r["id"]
                continue
            try:
                rel = template.render(values[r["id"]])
                check_path_length(root, rel)
            except ValueMissing as e:
                item.update(status=ITEM_EXCLUDED, reason=str(e))
                continue
            item["output_rel_path"] = rel
            if rel == r["rel_path"]:
                item.update(status=ITEM_UNCHANGED, reason="既にテンプレートどおりの名前です")
            elif rel.casefold() == r["rel_path"].casefold():
                item.update(status=ITEM_EXCLUDED, reason="大文字・小文字だけの違いは改名しません")
            else:
                source_path = resolve_source_path(root, r["rel_path"])
                problem = self._target_problem(root, self.settings.organize_base(root), rel, source_path)
                if problem is None and self.conn.execute(
                        "SELECT 1 FROM sources WHERE root_id = ? AND rel_path = ?", (r["root_id"], rel)).fetchone():
                    problem = "この場所にあったファイルの記録が残っています(同じ名前のファイルを以前スキャンしました)"
                if problem:
                    item.update(status=ITEM_CONFLICT, reason=problem)
                else:
                    item["fingerprint"] = fingerprint(source_path)
                    item["before"] = {k: r[k] for k in ("title", "name_encoding", "number_kind", "number_start",
                                                        "number_end", "number_evidence")}
                    item["before"]["name_raw"] = r["name_raw"].hex() if r["name_raw"] is not None else None
        # 同じ名前になる巻どうし(別スキャンの重複など)は、どちらも衝突にして利用者に選ばせる
        groups: dict[tuple[int, str], list[dict]] = {}
        for it in items:
            if it["status"] in (ITEM_MOVE, ITEM_UNCHANGED):
                groups.setdefault((it["root_id"], it["output_rel_path"].casefold()), []).append(it)
        for group in groups.values():
            if len(group) > 1:
                for it in group:
                    if it["status"] == ITEM_UNCHANGED:
                        continue  # 既にその名前の巻はそのまま。改名してくる側だけを衝突にする
                    others = "・".join(f"「{o['title']}」" for o in group if o is not it)
                    it.update(status=ITEM_CONFLICT, reason=f"{others}と同じ名前になります。どれかを外してください")
        moving = [it for it in items if it["status"] == ITEM_MOVE]
        first = moving[0] if moving else (items[0] if items else None)
        problems = []
        if not moving:
            problems.append("改名・移動する巻がありません" if items else "この作品には一覧に出ている巻がありません")
        plan = {
            "kind": "rename", "naming": "template", "template": self.settings.name_template, "work_id": root_work,
            "source_id": first["source_id"] if first else None, "root_id": first["root_id"] if first else None,
            "root_path": first["root_path"] if first else None, "source_rel_path": "", "source_kind": "archive",
            "organize_base": None, "fingerprint": None, "needs_analysis": [],
            "items": items, "warnings": [], "problems": problems,
            "volumes": [{"volume_id": it["volume_id"], "title": it["title"], "page_count": it["page_count"],
                         "output_rel_path": it["output_rel_path"]} for it in moving],
        }
        if not problems and any(it["status"] == ITEM_CONFLICT for it in items):
            plan["warnings"].append("衝突している巻は改名しません(外すか、作品・巻の情報を直して計画を作り直してください)")
        if not problems and any(it["entry_prefix"] for it in moving):
            plan["warnings"].append("CBZ の中のフォルダ名を巻の名前にしている巻は、一覧の名前は変わりません")
        return self._save_plan(plan)

    def _rename_ineligible(self, r: dict, root: Path) -> str | None:
        """改名・移動の対象外の理由(1 巻 = 1 CBZ でない・オフラインなど)。対象なら None。"""
        if r["source_offline"] or not root_is_accessible(root):
            return "蔵書フォルダが今オフラインです"
        single = (r["source_kind"] == "archive" and r["container_path"] is None and r["parent_id"] is None
                  and r["inner_path"] is None and r["source_volumes"] == 1)
        if not single or not r["rel_path"].lower().endswith(".cbz"):
            return "1 巻 = 1 CBZ ではありません(先に「CBZ に整理」をテンプレートの名前で実行してください)"
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

    # --- 参照 ---------------------------------------------------------------

    @staticmethod
    def _view_plan(plan: dict) -> dict:
        view = {"kind": plan.get("kind", "cbz"), "naming": plan.get("naming", "title"),
                "template": plan.get("template"),
                "source_id": plan["source_id"], "source_rel_path": plan["source_rel_path"],
                "source_kind": plan["source_kind"], "volumes": plan["volumes"],
                "warnings": plan["warnings"], "problems": plan["problems"],
                "needs_analysis": plan.get("needs_analysis", [])}
        if plan.get("kind") == "rename":
            view["work_id"] = plan["work_id"]
            view["items"] = [{k: it[k] for k in ("volume_id", "title", "from_rel_path", "output_rel_path", "status",
                                                 "reason", "cbz_volume_id")} for it in plan["items"]]
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
    """巻に紐づくユーザーの記録(読書位置・作品の割り当て)を別の巻へ写す。手動編集メタデータを足したらここで一緒に写す。

    ページの並びは CBZ 化の前後で同じ(今の並び順で固定する)なので、ページ番号はそのまま使える。
    作品の割り当ては手動の決定も含めてそのまま写す(整理で作品が変わらない。元に戻すときは逆向きに写す)。
    """
    transfer_assignment(c, from_id, to_id)
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
