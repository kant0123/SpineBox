"""整理処理(CBZ 化 → 検証 → 確定 → 退避 → 元に戻す)。蔵書は一時ディレクトリに動的生成する。"""
import dataclasses
import io
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from spinebox import analysis, db
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.library import Library, VolumeNotFound, get_volume_row
from spinebox.organize import cbz, fs
from spinebox.organize.organizer import OrganizeError, Organizer
from spinebox.scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE, Scanner


@pytest.fixture
def env(make_settings, library):
    settings = make_settings(library)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    return SimpleNamespace(conn=conn, settings=settings, library=lib, root=library)


def organizer(env, **kw) -> Organizer:
    return Organizer(env.conn, env.settings, env.library, **kw)


def vid(env, rel, prefix="", container=None) -> int:
    return env.conn.execute(
        """SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
           WHERE s.rel_path = ? AND v.entry_prefix = ? AND v.container_path IS ?""",
        (rel, prefix, container)).fetchone()["id"]


def lib_snapshot(root: Path) -> dict:
    """作業場・退避フォルダ(.spinebox)を除いた蔵書の全ファイル。"""
    return {k: v for k, v in snapshot(root).items() if not k.startswith(".spinebox")}


def cbz_pages(path: Path) -> list[tuple[str, int, bytes]]:
    with zipfile.ZipFile(path) as z:
        return [(i.filename, i.compress_type, z.read(i)) for i in z.infolist()]


def set_progress(env, volume_id, page, finished=False):
    with db.transaction(env.conn) as c:
        c.execute("INSERT OR REPLACE INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, ?, ?)",
                  (volume_id, page, int(finished), db.now()))


def progress(env, volume_id):
    r = env.conn.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?", (volume_id,)).fetchone()
    return None if r is None else (r["page"], bool(r["finished"]))


def steps(env, op_id):
    return [(s["phase"], s["action"], s["state"]) for s in env.conn.execute(
        "SELECT * FROM organize_steps WHERE op_id = ? ORDER BY id", (op_id,))]


# --- 計画(プレビュー) -------------------------------------------------------

def test_plan_writes_nothing(env):
    before = snapshot(env.root)
    o = organizer(env)
    multi = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    folder = o.plan(volume_id=vid(env, "FolderBook"))
    nested = o.plan(volume_id=vid(env, "series/nested.zip", container="inner1.zip"))
    single = o.plan(volume_id=vid(env, "single.cbz"))
    assert snapshot(env.root) == before  # バイト列・サイズ・mtime が不変
    assert not (env.root / ".spinebox").exists()

    assert multi["state"] == "planned" and multi["op_id"]
    assert [(v["title"], v["output_rel_path"], v["page_count"]) for v in multi["volumes"]] == [
        ("Vol1", "Vol1.cbz", 2), ("Vol2", "Vol2.cbz", 1)]
    assert any("CBZ に入らない" in w for w in single["warnings"])  # info.txt
    assert any("画像以外" in w for w in folder["warnings"])  # notes.txt
    # 自分自身と同じ名前は連番で避ける
    assert single["volumes"][0]["output_rel_path"] == "single (2).cbz"
    # 読めない内側アーカイブ(7-Zip なしの rar)を含むソースは、1 巻も作らせない
    assert nested["state"] == "blocked" and nested["op_id"] is None
    assert any("inner3.rar" in p for p in nested["problems"])
    assert len(nested["volumes"]) == 3


def test_plan_output_names_are_sanitized_and_numbered(env):
    root = env.root
    write_zip(root / "odd.zip", [("題:名?/1.jpg", page_bytes("x")), ("CON/1.jpg", page_bytes("y"))])
    (root / "題_名_.cbz").write_bytes(b"existing")
    Scanner(env.conn, env.settings).scan()
    plan = organizer(env).plan(volume_id=vid(env, "odd.zip", "CON/"))
    assert sorted(v["output_rel_path"] for v in plan["volumes"]) == ["_CON.cbz", "題_名_ (2).cbz"]


def test_plan_refuses_folder_with_subfolders_and_root(env):
    (env.root / "FolderBook" / "extra").mkdir()
    plan = organizer(env).plan(volume_id=vid(env, "FolderBook"))
    assert plan["state"] == "blocked" and any("サブフォルダ" in p for p in plan["problems"])


# --- 往復 -------------------------------------------------------------------

def test_roundtrip_multi_volume_archive(env):
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    done = o.execute(plan["op_id"])
    assert done["state"] == "done"
    root = env.root
    assert not (root / "multi.zip").exists()
    assert cbz_pages(root / "Vol1.cbz") == [("0001.jpg", zipfile.ZIP_STORED, page_bytes("v1/001")),
                                            ("0002.jpg", zipfile.ZIP_STORED, page_bytes("v1/002"))]
    assert cbz_pages(root / "Vol2.cbz") == [("0001.jpg", zipfile.ZIP_STORED, page_bytes("v2/001"))]
    trashed = root / ".spinebox" / "trash" / plan["op_id"] / "original" / "multi.zip"
    assert trashed.read_bytes() == before["multi.zip"][0]
    assert not (root / ".spinebox" / "work" / plan["op_id"]).exists()
    assert [s for s in steps(env, plan["op_id"])] == [
        ("execute", "commit", "done"), ("execute", "commit", "done"), ("execute", "trash_original", "done")]

    undone = o.undo(plan["op_id"])
    assert undone["state"] == "undone"
    assert lib_snapshot(root) == before  # パス・バイト列・mtime まで実行前と一致
    created = root / ".spinebox" / "trash" / plan["op_id"] / "created"
    assert sorted(p.name for p in created.iterdir()) == ["Vol1.cbz", "Vol2.cbz"]  # 作った CBZ も消さない


def test_roundtrip_image_folder(env):
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "FolderBook"))
    o.execute(plan["op_id"])
    assert not (env.root / "FolderBook").exists()
    pages = cbz_pages(env.root / "FolderBook.cbz")
    # 今の並び順(表紙 → 自然順)を 0001.jpg 形式で固定する
    assert [(n, d) for n, _, d in pages] == [
        ("0001.jpg", page_bytes("folder/cover.JPG")), ("0002.jpg", page_bytes("folder/1.jpg")),
        ("0003.jpg", page_bytes("folder/2.jpg")), ("0004.jpg", page_bytes("folder/10.jpg"))]
    o.undo(plan["op_id"])
    assert lib_snapshot(env.root) == before


def test_new_volume_reads_same_pages(env):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "single.cbz"))
    o.execute(plan["op_id"])
    new_id = o.get(plan["op_id"])["volumes"][0]["new_volume_id"]
    with env.library.open(env.conn, new_id) as vol:
        assert [vol.read_page(i) for i in range(len(vol.page_names))] == [
            page_bytes(f"single/{n}") for n in ["1.jpg", "2.jpg", "10.jpg"]]


# --- 検証失敗 -----------------------------------------------------------------

def _rewrite_cbz(path: Path, transform) -> None:
    with zipfile.ZipFile(path) as z:
        entries = [(i.filename, z.read(i)) for i in z.infolist()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in transform(entries):
            z.writestr(name, data)
    path.write_bytes(buf.getvalue())


@pytest.mark.parametrize("transform, reason", [
    (lambda es: [(n, d + b"!" if i == 0 else d) for i, (n, d) in enumerate(es)], "ハッシュ"),
    (lambda es: es[:-1], "ページ数"),
])
def test_verification_failure_keeps_original(env, monkeypatch, transform, reason):
    original_write = cbz.write_cbz

    def tampered(path, names, read):
        records = original_write(path, names, read)
        if path.name == "0002.cbz":  # 2 巻目だけ壊す
            _rewrite_cbz(path, transform)
        return records

    monkeypatch.setattr(cbz, "write_cbz", tampered)
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    with pytest.raises(OrganizeError, match=reason):
        o.execute(plan["op_id"])
    assert lib_snapshot(env.root) == before  # 確定しない・元は元の場所に残る
    assert o.get(plan["op_id"])["state"] == "failed"
    assert steps(env, plan["op_id"]) == []  # 移動を 1 つも始めていない
    assert list((env.root / ".spinebox" / "work").iterdir()) == []
    assert get_volume_row(env.conn, vid(env, "multi.zip", "Vol1/"))  # 元の巻はそのまま見える


# --- 別ドライブ ---------------------------------------------------------------

def test_cross_drive_is_refused_before_writing(env):
    before = snapshot(env.root)
    other_drive = organizer(env, same_drive=lambda a, b: False)
    blocked = other_drive.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    assert blocked["state"] == "blocked" and any("別のドライブ" in p for p in blocked["problems"])
    # 計画の後に構成が変わった(作業場の置き場を別ドライブにした)場合も、実行の前に止める
    plan = organizer(env).plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    with pytest.raises(OrganizeError, match="別のドライブ"):
        other_drive.execute(plan["op_id"])
    assert snapshot(env.root) == before
    assert not (env.root / ".spinebox").exists()


def test_same_drive_on_real_paths(tmp_path):
    assert fs.same_drive(tmp_path, tmp_path / "not" / "yet" / "created")
    assert fs.nearest_existing(tmp_path / "a" / "b") == tmp_path


# --- 途中失敗からの巻き戻し -------------------------------------------------------

def test_rollback_when_trashing_original_fails(env):
    def move(src, dst):
        if "original" in Path(dst).parts:
            raise PermissionError("使用中")
        fs.move_no_overwrite(src, dst)

    before = lib_snapshot(env.root)
    o = organizer(env, move=move)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    with pytest.raises(OrganizeError, match="使用中"):
        o.execute(plan["op_id"])
    assert lib_snapshot(env.root) == before  # 確定済みの CBZ は作業場へ戻して片付け、元は元の場所
    assert o.get(plan["op_id"])["state"] == "failed"
    assert steps(env, plan["op_id"]) == [
        ("execute", "commit", "reverted"), ("execute", "commit", "reverted"), ("execute", "trash_original", "reverted")]


def test_rollback_when_db_update_fails(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("DB が壊れた")

    monkeypatch.setattr(Organizer, "_record_executed", boom)
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "FolderBook"))
    with pytest.raises(OrganizeError):
        o.execute(plan["op_id"])
    assert lib_snapshot(env.root) == before
    assert [s[2] for s in steps(env, plan["op_id"])] == ["reverted", "reverted"]
    assert get_volume_row(env.conn, vid(env, "FolderBook"))


def test_failed_rollback_is_recorded(env):
    calls = []

    def move(src, dst):
        calls.append(dst)
        if "original" in Path(dst).parts or len(calls) > 2:  # 退避も、確定の巻き戻しも失敗する
            raise PermissionError("使用中")
        fs.move_no_overwrite(src, dst)

    o = organizer(env, move=move)
    plan = o.plan(volume_id=vid(env, "single.cbz"))
    with pytest.raises(OrganizeError):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "rollback_failed"
    assert (env.root / "single.cbz").exists()  # 元は動いていない
    assert ("execute", "commit", "revert_failed") in steps(env, plan["op_id"])


# --- 複数巻のソース ---------------------------------------------------------------

def test_original_untouched_until_all_volumes_built_and_verified(env, monkeypatch):
    events = []
    real_write, real_verify = cbz.write_cbz, cbz.verify_cbz
    monkeypatch.setattr(cbz, "write_cbz", lambda p, n, r: events.append(("write", p.name)) or real_write(p, n, r))
    monkeypatch.setattr(cbz, "verify_cbz", lambda p, r: events.append(("verify", p.name)) or real_verify(p, r))

    def move(src, dst):
        events.append(("move", Path(src).name, Path(dst).name))
        fs.move_no_overwrite(src, dst)

    o = organizer(env, move=move)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol2/"))
    o.execute(plan["op_id"])
    assert events == [("write", "0001.cbz"), ("write", "0002.cbz"), ("verify", "0001.cbz"), ("verify", "0002.cbz"),
                      ("move", "0001.cbz", "Vol1.cbz"), ("move", "0002.cbz", "Vol2.cbz"),
                      ("move", "multi.zip", "multi.zip")]


def test_failure_on_later_volume_leaves_everything(env, monkeypatch):
    real_write = cbz.write_cbz

    def write(path, names, read):
        if path.name == "0002.cbz":
            raise OSError("ディスクがいっぱい")
        return real_write(path, names, read)

    monkeypatch.setattr(cbz, "write_cbz", write)
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    with pytest.raises(OrganizeError, match="ディスク"):
        o.execute(plan["op_id"])
    assert lib_snapshot(env.root) == before
    assert not (env.root / "Vol1.cbz").exists()


# --- スキャンとの関係 ---------------------------------------------------------------

def test_work_and_trash_are_not_scanned(env):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    o.execute(plan["op_id"])
    work = env.root / ".spinebox" / "work" / "leftover"
    work.mkdir(parents=True)
    (work / "1.jpg").write_bytes(page_bytes("w"))
    Scanner(env.conn, env.settings).scan()
    rels = {r["rel_path"] for r in env.conn.execute("SELECT rel_path FROM sources WHERE missing = 0")}
    assert not any(r.startswith(".spinebox") for r in rels)
    assert {"Vol1.cbz", "Vol2.cbz"} <= rels and "multi.zip" not in rels


def test_custom_organize_dir_is_not_scanned(make_settings, library):
    settings = dataclasses.replace(make_settings(library), organize_dir="_organize")
    assert settings.organize_base(library) == library / "_organize"
    (library / "_organize" / "trash").mkdir(parents=True)
    write_zip(library / "_organize" / "trash" / "x.cbz", [("1.jpg", page_bytes("t"))])
    (library / ".spinebox").mkdir()
    write_zip(library / ".spinebox" / "y.cbz", [("1.jpg", page_bytes("t"))])
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    rels = {r["rel_path"] for r in conn.execute("SELECT rel_path FROM sources")}
    assert not any(r.startswith(("_organize", ".spinebox")) for r in rels)


def test_rescan_after_execute_and_undo_keeps_ids(env):
    o = organizer(env)
    old_id = vid(env, "multi.zip", "Vol1/")
    plan = o.plan(volume_id=old_id)
    o.execute(plan["op_id"])
    new_id = o.get(plan["op_id"])["volumes"][0]["new_volume_id"]
    assert Scanner(env.conn, env.settings).scan().volumes == 7  # Vol1/Vol2 が CBZ の巻に置き換わっただけ
    assert get_volume_row(env.conn, new_id)
    o.undo(plan["op_id"])
    assert Scanner(env.conn, env.settings).scan().volumes == 7
    assert get_volume_row(env.conn, old_id)  # 同じ巻 id に戻る
    with pytest.raises(VolumeNotFound):
        get_volume_row(env.conn, new_id)


# --- 読書位置 -----------------------------------------------------------------

def test_reading_progress_follows_and_returns(env):
    o = organizer(env)
    old_id = vid(env, "multi.zip", "Vol1/")
    set_progress(env, old_id, 1)
    plan = o.plan(volume_id=old_id)
    o.execute(plan["op_id"])
    new_id = o.get(plan["op_id"])["volumes"][0]["new_volume_id"]
    assert progress(env, new_id) == (1, False)
    with pytest.raises(VolumeNotFound):
        get_volume_row(env.conn, old_id)  # 元の巻は消さずに「整理済み」として隠す
    assert env.conn.execute("SELECT organized_op_id FROM volumes WHERE id = ?", (old_id,)).fetchone()[0] == plan["op_id"]

    set_progress(env, new_id, 1, finished=True)  # 整理後に読み進めた
    o.undo(plan["op_id"])
    assert progress(env, old_id) == (1, True)
    assert get_volume_row(env.conn, old_id)


# --- 元に戻すの拒否 -------------------------------------------------------------

def test_undo_refuses_when_original_location_is_taken(env):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    o.execute(plan["op_id"])
    (env.root / "multi.zip").write_bytes(b"someone else's file")
    with pytest.raises(OrganizeError, match="元の場所"):
        o.undo(plan["op_id"])
    assert (env.root / "multi.zip").read_bytes() == b"someone else's file"
    assert (env.root / "Vol1.cbz").exists()
    assert o.get(plan["op_id"])["state"] == "done"


def test_undo_failure_midway_returns_to_executed_state(env):
    calls = []

    def move(src, dst):
        calls.append(dst)
        if len(calls) == 6:  # 元に戻す: CBZ 2 つを退避した後の、元を戻す移動で失敗
            raise PermissionError("使用中")
        fs.move_no_overwrite(src, dst)

    o = organizer(env, move=move)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    o.execute(plan["op_id"])
    after_execute = lib_snapshot(env.root)
    with pytest.raises(OrganizeError):
        o.undo(plan["op_id"])
    assert lib_snapshot(env.root) == after_execute
    assert o.get(plan["op_id"])["state"] == "done"


def test_stale_plan_is_refused(env):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "single.cbz"))
    path = env.root / "single.cbz"
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    with pytest.raises(OrganizeError, match="作り直して"):
        o.execute(plan["op_id"])
    assert not (env.root / "single (2).cbz").exists()
    with pytest.raises(OrganizeError):
        o.execute(plan["op_id"])  # 失敗した操作は再実行できない(計画から作り直す)


# --- 道具 -------------------------------------------------------------------

def test_move_no_overwrite(tmp_path):
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").write_bytes(b"b")
    with pytest.raises(FileExistsError):
        fs.move_no_overwrite(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b").read_bytes() == b"b"
    fs.move_no_overwrite(tmp_path / "a", tmp_path / "sub" / "c")
    assert (tmp_path / "sub" / "c").read_bytes() == b"a" and not (tmp_path / "a").exists()
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    with pytest.raises(FileExistsError):
        fs.move_no_overwrite(tmp_path / "d1", tmp_path / "d2")


def test_safe_stem():
    assert fs.safe_stem('a/b:c*?"<>|') == "a_b_c______"
    assert fs.safe_stem("nul") == "_nul"
    assert fs.safe_stem("末尾. ") == "末尾"
    assert len(fs.safe_stem("長" * 300)) == fs.MAX_STEM


def test_page_entry_names():
    assert cbz.page_entry_names(["a/cover.JPEG", "b.png"]) == ["0001.jpeg", "0002.png"]
    assert cbz.page_entry_names(["x.jpg"] * 10000)[-1] == "10000.jpg"


# --- API ---------------------------------------------------------------------

def test_api_plan_execute_history_undo(make_settings, library):
    settings = make_settings(library)
    c = TestClient(create_app(settings, frontend_dist=None))
    c.post("/api/scan")
    vols = {v["title"]: v for v in c.get("/api/volumes").json()}
    before = lib_snapshot(library)

    plan = c.post("/api/organize/plan", json={"volume_id": vols["Vol1"]["id"]}).json()
    assert plan["state"] == "planned" and len(plan["volumes"]) == 2
    assert c.get("/api/organize").json() == []  # 計画だけのものは履歴に出さない
    r = c.post(f"/api/organize/{plan['op_id']}/execute")
    assert r.status_code == 200 and r.json()["state"] == "done"
    titles = {v["title"] for v in c.get("/api/volumes").json()}
    assert "Vol1" in titles and len(titles) == 7  # 新しい CBZ の巻(名前は同じ)に置き換わった
    assert c.get(f"/api/volumes/{vols['Vol1']['id']}").status_code == 404  # 元の巻は整理済み
    assert [h["op_id"] for h in c.get("/api/organize").json()] == [plan["op_id"]]

    r = c.post(f"/api/organize/{plan['op_id']}/execute")
    assert r.status_code == 409 and r.json()["op"]["state"] == "done"
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert lib_snapshot(library) == before
    assert c.get(f"/api/volumes/{vols['Vol1']['id']}").status_code == 200

    assert c.post("/api/organize/nope/undo").status_code == 404
    assert c.post("/api/organize/plan", json={}).status_code == 422
    assert c.post("/api/organize/plan", json={"volume_id": 99999}).status_code == 404
    blocked = c.post("/api/organize/plan", json={"volume_id": vols["inner3"]["id"]}).json()
    assert blocked["state"] == "blocked"


def test_plan_problems_for_unusable_sources(env, tmp_path):
    o = organizer(env)
    # 元が消えている(スキャン前)
    gone = vid(env, "single.cbz")
    (env.root / "single.cbz").rename(tmp_path / "moved.cbz")
    assert any("見つかりません" in p for p in o.plan(volume_id=gone)["problems"])
    # 作業場の置き場が元のフォルダの中
    inside = organizer(SimpleNamespace(**{**vars(env), "settings": dataclasses.replace(
        env.settings, organize_dir=str(env.root / "FolderBook" / "_sb"))}))
    assert any("元のフォルダの中" in p for p in inside.plan(volume_id=vid(env, "FolderBook"))["problems"])
    # 蔵書ルート直下の画像
    (env.root / "top.jpg").write_bytes(page_bytes("top"))
    Scanner(env.conn, env.settings).scan()
    assert any("ルートそのもの" in p for p in o.plan(volume_id=vid(env, ""))["problems"])
    # ルートがオフライン
    multi = vid(env, "multi.zip", "Vol1/")
    env.root.rename(tmp_path / "unplugged")
    blocked = o.plan(volume_id=multi)
    assert blocked["state"] == "blocked" and any("オフライン" in p for p in blocked["problems"])


def test_undo_refuses_when_created_cbz_is_gone_or_not_done(env, tmp_path):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    with pytest.raises(OrganizeError, match="元に戻せる状態"):
        o.undo(plan["op_id"])  # まだ実行していない
    o.execute(plan["op_id"])
    (env.root / "Vol2.cbz").rename(tmp_path / "Vol2.cbz")
    with pytest.raises(OrganizeError, match="作った CBZ が見つかりません"):
        o.undo(plan["op_id"])
    assert not (env.root / "multi.zip").exists() and (env.root / "Vol1.cbz").exists()  # 何も動かしていない


# --- 分解済みの内側アーカイブ(#30 の子の巻) ------------------------------------

def _nested_set() -> bytes:
    """入れ子 3 段: set v01-03.zip → (v01.zip, v02-03.zip → (v02.zip, v03.zip))。"""
    v = {i: zip_bytes([(f"{p}.jpg", page_bytes(f"nv{i}/{p}")) for p in ("002", "001")]) for i in (1, 2, 3)}
    return zip_bytes([("v01.zip", v[1]), ("v02-03.zip", zip_bytes([("v02.zip", v[2]), ("v03.zip", v[3])]))])


@pytest.fixture
def nested_env(make_settings, tmp_path):
    root = tmp_path / "nested_lib"
    write_zip(root / "series.zip", [("set v01-03.zip", _nested_set())])
    settings = make_settings(root)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    return SimpleNamespace(conn=conn, settings=settings, library=lib, root=root)


def children(env) -> list[int]:
    return [r["id"] for r in env.conn.execute(
        "SELECT id FROM volumes WHERE parent_id IS NOT NULL AND missing = 0 ORDER BY title")]


def test_plan_requires_analysis_of_inner_archives_first(nested_env):
    env = nested_env
    parent = vid(env, "series.zip", container="set v01-03.zip")
    before = snapshot(env.root)
    plan = organizer(env).plan(volume_id=parent)
    # 中に何巻あるか分からないので、1 CBZ にまとめない。プレビューは何も書かない(解析もしない)
    assert plan["state"] == "blocked" and plan["op_id"] is None and plan["needs_analysis"] == [parent]
    assert any("中の巻をまだ調べていません" in p for p in plan["problems"])
    assert snapshot(env.root) == before and not (env.root / ".spinebox").exists()
    assert env.conn.execute("SELECT COUNT(*) FROM volumes WHERE parent_id IS NOT NULL").fetchone()[0] == 0
    assert env.conn.execute("SELECT COUNT(*) FROM organize_ops").fetchone()[0] == 0

    env.library.analyze(env.conn, parent)
    after = organizer(env).plan(volume_id=parent)
    assert after["state"] == "planned" and after["needs_analysis"] == []

    # 調べた後に元が変わったら、また調べ直しを求める
    set_mtime = (env.root / "series.zip").stat().st_mtime_ns + 5_000_000_000
    os.utime(env.root / "series.zip", ns=(set_mtime, set_mtime))
    stale = organizer(env).plan(volume_id=parent)
    assert stale["state"] == "blocked" and stale["needs_analysis"] == [parent]


def test_analysis_error_blocks_plan_with_reason(nested_env):
    env = nested_env
    parent = vid(env, "series.zip", container="set v01-03.zip")
    analysis.record_error(env.conn, parent, "壊れている")
    plan = organizer(env).plan(volume_id=parent)
    assert plan["state"] == "blocked" and any("壊れている" in p for p in plan["problems"])


def test_split_source_is_organized_per_child_and_roundtrips(nested_env):
    env = nested_env
    parent = vid(env, "series.zip", container="set v01-03.zip")
    env.library.analyze(env.conn, parent)
    kids = children(env)
    assert len(kids) == 3
    before = lib_snapshot(env.root)
    o = organizer(env)
    # 親(まとめ)の巻を指しても、子の巻ごとの計画になる(親と子の二重にならない)
    plan = o.plan(volume_id=parent)
    assert plan["state"] == "planned"
    assert sorted(v["volume_id"] for v in plan["volumes"]) == sorted(kids)
    assert [v["output_rel_path"] for v in plan["volumes"]] == ["v01.cbz", "v02.cbz", "v03.cbz"]
    assert lib_snapshot(env.root) == before

    # 2 巻目(v02)の途中まで読んだ読書位置を、CBZ の巻へ引き継ぐ
    v02 = next(k for k in kids if get_volume_row(env.conn, k)["title"] == "v02")
    set_progress(env, v02, 1)
    done = o.execute(plan["op_id"])
    assert done["state"] == "done"
    assert not (env.root / "series.zip").exists()
    for n in (1, 2, 3):
        assert cbz_pages(env.root / f"v0{n}.cbz") == [
            ("0001.jpg", zipfile.ZIP_STORED, page_bytes(f"nv{n}/001")),
            ("0002.jpg", zipfile.ZIP_STORED, page_bytes(f"nv{n}/002"))]
    new_ids = {v["volume_id"]: v["new_volume_id"] for v in done["volumes"]}
    assert progress(env, new_ids[v02]) == (1, False)
    # 一覧には CBZ の 3 巻だけ(元の親・子は整理済みで隠れる)
    listed = env.conn.execute("SELECT COUNT(*) " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE).fetchone()[0]
    assert listed == 3
    # 再スキャンしても CBZ の巻が読め、同じ id のまま
    Scanner(env.conn, env.settings).scan()
    for old, new in new_ids.items():
        with env.library.open(env.conn, new) as opened:
            assert len(opened.page_names) == 2

    assert o.undo(plan["op_id"])["state"] == "undone"
    assert lib_snapshot(env.root) == before
    assert progress(env, v02) == (1, False)
    Scanner(env.conn, env.settings).scan()
    assert sorted(children(env)) == sorted(kids)  # 子の巻の id は変わらない
    with env.library.open(env.conn, v02) as opened:
        assert len(opened.page_names) == 2
