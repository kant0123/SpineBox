"""命名テンプレートでの整理(CBZ 化の出力先・改名と移動だけ)。蔵書は一時ディレクトリに動的生成する。"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip
from spinebox import db, works
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.config import ConfigError, parse_settings
from spinebox.library import Library
from spinebox.organize import fs
from spinebox.organize.organizer import OrganizeError, Organizer
from spinebox.organize.template import (DEFAULT_NAME_TEMPLATE, MAX_PATH, NameTemplate, NameValues, TemplateError,
                                        ValueMissing, check_path_length)
from spinebox.scanner import Scanner

TITLE = "お気楽領主の楽しい領地防衛"


# --- テンプレートの展開と無害化 ----------------------------------------------------

def render(template: str = DEFAULT_NAME_TEMPLATE, *, title=TITLE, kind="volume", start=1, end=None, **fields):
    return NameTemplate(template).render(NameValues({"title": title, **fields}, kind, start,
                                                    start if end is None else end))


def test_default_template_volume_and_episode():
    assert render(start=3) == f"{TITLE}/{TITLE} 第03巻.cbz"
    # 番号の直後の「巻」は、話単位の巻では「話」になる
    assert render(kind="episode", start=12) == f"{TITLE}/{TITLE} 第12話.cbz"
    assert render("<作品名> 第NN話.cbz", kind="volume", start=1) == f"{TITLE} 第01巻.cbz"
    # まとめ(範囲)は 01-03、桁数の指定と単位の変数
    assert render(start=1, end=3) == f"{TITLE}/{TITLE} 第01-03巻.cbz"
    assert render("<作品名>/<番号:3><単位>.cbz", kind="episode", start=7) == f"{TITLE}/007話.cbz"
    assert render("<著者>/<作品名>/<出版社> <巻番号>.cbz", author="作者 太郎", publisher="出版社X", start=120) == \
        f"作者 太郎/{TITLE}/出版社X 120.cbz"


@pytest.mark.parametrize("fields, kind, start, reason", [
    ({"title": None}, "volume", 1, "作品に割り当てられていません"),
    ({"title": TITLE}, None, None, "番号を読めません"),
    ({"title": "  "}, "volume", 1, "作品に割り当てられていません"),
])
def test_missing_values_are_reasons(fields, kind, start, reason):
    with pytest.raises(ValueMissing, match=reason):
        NameTemplate(DEFAULT_NAME_TEMPLATE).render(NameValues(fields, kind, start, start))
    with pytest.raises(ValueMissing, match="著者が空欄"):
        render("<著者>/<作品名> NN.cbz")
    with pytest.raises(ValueMissing, match="巻か話か"):
        NameTemplate("<作品名> <番号><単位>.cbz").render(NameValues({"title": TITLE}, None, 1, 1))


def test_values_are_sanitized_into_one_segment():
    # 値の / \ や Windows で使えない文字は _ に。全角の記号はそのまま使える
    assert render(title="Fate/Zero: 前編?") == "Fate_Zero_ 前編_/Fate_Zero_ 前編_ 第01巻.cbz"
    assert render(title="全角：記号／は＊残る") == "全角：記号／は＊残る/全角：記号／は＊残る 第01巻.cbz"
    assert render(title="a\\b") == "a_b/a_b 第01巻.cbz"
    # .. や末尾のドット・空白でフォルダを外へ出したり、Windows で開けない名前にしたりしない
    assert render("<作品名>/x NN.cbz", title="..") == "_/x 01.cbz"
    assert render("<作品名>/x NN.cbz", title="作品. ") == "作品/x 01.cbz"
    # 予約名は先頭に _
    assert render("<作品名>/<作品名>.cbz", title="CON") == "_CON/_CON.cbz"
    assert render("<作品名>/<作品名>.cbz", title="nul.txt") == "_nul.txt/_nul.txt.cbz"
    rel = render(title="あ" * 400)
    folder, name = rel.split("/")
    assert len(folder) == fs.MAX_STEM and name.endswith(".cbz") and len(name) == fs.MAX_STEM + 4


def test_long_path_is_reason(tmp_path):
    check_path_length(tmp_path, "a/b.cbz")
    long_rel = "/".join(["あ" * 100] * 3) + ".cbz"
    with pytest.raises(ValueMissing, match=f"上限 {MAX_PATH}"):
        check_path_length(tmp_path, long_rel)


@pytest.mark.parametrize("template, message", [
    ("", "空でない"),
    ("/abs/<作品名>.cbz", "相対パス"),
    ("C:/漫画/<作品名>.cbz", "相対パス"),
    ("C:<作品名>.cbz", "相対パス"),
    ("../<作品名>.cbz", "「..」"),
    ("<作品名>/../x.cbz", "「..」"),
    ("<作品名>//x.cbz", "空"),
    ("<作品名>.zip", ".cbz"),
    ("<作品名>/<題名>.cbz", "使えません"),
    ("<作品名>/<番号:9>.cbz", "桁数"),
    ("<作品名>/a?b.cbz", "使えない文字"),
    ("<作品名/x.cbz", "使えない文字"),
])
def test_invalid_templates(template, message):
    with pytest.raises(TemplateError, match=message):
        NameTemplate(template)


def test_template_setting(tmp_path):
    assert parse_settings({}, tmp_path).name_template == DEFAULT_NAME_TEMPLATE
    s = parse_settings({"organize": {"name_template": "漫画\\<作品名>\\<作品名> NNN.cbz"}}, tmp_path)
    assert NameTemplate(s.name_template).render(NameValues({"title": "X"}, "volume", 2, 2)) == "漫画/X/X 002.cbz"
    with pytest.raises(ConfigError, match="相対パス"):
        parse_settings({"organize": {"name_template": "D:/x.cbz"}}, tmp_path)
    with pytest.raises(ConfigError):
        parse_settings({"organize": "x"}, tmp_path)


# --- 合成蔵書 -------------------------------------------------------------------

def cbz(path: Path, label: str, n: int = 2) -> Path:
    return write_zip(path, [(f"{i:03d}.jpg", page_bytes(f"{label}/{i}")) for i in range(1, n + 1)])


@pytest.fixture
def lib(tmp_path) -> Path:
    root = tmp_path / "lib"
    cbz(root / "受け入れ" / f"{TITLE} 1巻.cbz", "v1")
    cbz(root / f"{TITLE} 2巻.cbz", "v2")
    cbz(root / "別スキャン" / f"{TITLE} 第2巻.cbz", "v2-other", 3)  # 同じ巻の別スキャン → 衝突
    write_zip(root / f"{TITLE} 3巻.zip", [("001.jpg", page_bytes("v3/1"))])  # CBZ ではない → 対象外
    cbz(root / TITLE / f"{TITLE} 第04巻.cbz", "v4")  # 既にテンプレートどおり
    cbz(root / "ある作品 第5話.cbz", "ep5")
    cbz(root / "番外編集.cbz", "extra")  # 番号が無い
    return root


@pytest.fixture
def env(make_settings, lib):
    settings = make_settings(lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    works.sync(conn)
    library = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    return SimpleNamespace(conn=conn, settings=settings, library=library, root=lib)


def organizer(env, **kw) -> Organizer:
    return Organizer(env.conn, env.settings, env.library, **kw)


def full_snapshot(root: Path) -> tuple[dict, set]:
    """作業場・退避フォルダ(.spinebox)を除いた蔵書の全ファイル(バイト列・サイズ・mtime)と全フォルダ。"""
    files = {k: v for k, v in snapshot(root).items() if not k.startswith(".spinebox")}
    dirs = {str(Path(d).relative_to(root)) for d, _, _ in os.walk(root)}
    return files, {d for d in dirs if not d.startswith(".spinebox")}


def vid_of(env, rel: str) -> int:
    return env.conn.execute("""SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
                               WHERE s.rel_path = ?""", (rel,)).fetchone()["id"]


def work_of(env, volume_id: int) -> int:
    return env.conn.execute("SELECT work_id FROM work_volumes WHERE volume_id = ?", (volume_id,)).fetchone()["work_id"]


def items_by_title(plan) -> dict:
    return {it["title"]: it for it in plan["items"]}


def set_progress(env, volume_id, page, finished=False):
    with db.transaction(env.conn) as c:
        c.execute("INSERT OR REPLACE INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, ?, ?, ?)",
                  (volume_id, page, int(finished), db.now()))


def progress(env, volume_id):
    r = env.conn.execute("SELECT page, finished FROM reading_progress WHERE volume_id = ?", (volume_id,)).fetchone()
    return None if r is None else (r["page"], bool(r["finished"]))


# --- 改名・移動だけの整理 ------------------------------------------------------------

def test_rename_plan_writes_nothing_and_explains_each_volume(env):
    before = full_snapshot(env.root)
    work = work_of(env, vid_of(env, f"{TITLE} 2巻.cbz"))
    plan = organizer(env).plan_rename(work)
    assert full_snapshot(env.root) == before  # プレビューでは蔵書を 1 バイトも変えない
    assert not (env.root / ".spinebox").exists()
    assert plan["state"] == "planned" and plan["kind"] == "rename" and plan["template"] == DEFAULT_NAME_TEMPLATE
    items = items_by_title(plan)
    assert items[f"{TITLE} 1巻"]["status"] == "move"
    assert items[f"{TITLE} 1巻"]["output_rel_path"] == f"{TITLE}/{TITLE} 第01巻.cbz"
    # 同じ名前になる別スキャンの重複は、自動で連番にせず両方を衝突として出す
    for t in (f"{TITLE} 2巻", f"{TITLE} 第2巻"):
        assert items[t]["status"] == "conflict" and "同じ名前" in items[t]["reason"]
    assert items[f"{TITLE} 3巻"]["status"] == "excluded" and "1 巻 = 1 CBZ ではありません" in items[f"{TITLE} 3巻"]["reason"]
    assert items[f"{TITLE} 3巻"]["cbz_volume_id"] == vid_of(env, f"{TITLE} 3巻.zip")
    assert items[f"{TITLE} 第04巻"]["status"] == "unchanged"
    assert [v["title"] for v in plan["volumes"]] == [f"{TITLE} 1巻"]  # 実行で動くのは move だけ
    assert any("衝突" in w for w in plan["warnings"])


def test_rename_plan_reasons_for_missing_values(env):
    o = organizer(env)
    extra = o.plan_rename(work_of(env, vid_of(env, "番外編集.cbz")))
    assert extra["state"] == "blocked" and extra["op_id"] is None
    assert extra["items"][0]["status"] == "excluded" and "番号を読めません" in extra["items"][0]["reason"]
    # 作品の著者が空欄なら、著者を使うテンプレートでは対象外
    env.settings = parse_settings({"library_roots": [str(env.root)], "data_dir": str(env.settings.data_dir),
                                   "organize": {"name_template": "<著者>/<作品名> NN.cbz"}}, env.root)
    plan = organizer(env).plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    assert plan["items"][0]["status"] == "excluded" and "著者が空欄" in plan["items"][0]["reason"]


def test_rename_roundtrip_keeps_state_on_same_volume(env):
    root = env.root
    before = full_snapshot(root)
    v1, v2, v2b = vid_of(env, f"受け入れ/{TITLE} 1巻.cbz"), vid_of(env, f"{TITLE} 2巻.cbz"), \
        vid_of(env, f"別スキャン/{TITLE} 第2巻.cbz")
    ep = vid_of(env, "ある作品 第5話.cbz")
    work = work_of(env, v1)
    set_progress(env, v1, 1)
    set_progress(env, v2b, 2, True)
    with db.transaction(env.conn) as c:  # 重複の却下(巻 id に付いている)
        c.execute("""INSERT INTO duplicate_candidates (volume_a, volume_b, rule, matched_pages, compared_pages, evidence,
                     computed_at, dismissed_at) VALUES (?, ?, 'r', 1, 1, '{}', ?, ?)""",
                  (min(v2, v2b), max(v2, v2b), db.now(), db.now()))
    o = organizer(env)
    # 衝突の片方(v2)を外して作り直すと、もう片方は改名の対象になる
    plan = o.plan_rename(work, exclude_volume_ids=[v2])
    items = items_by_title(plan)
    assert items[f"{TITLE} 2巻"]["status"] == "skipped"
    assert items[f"{TITLE} 第2巻"]["status"] == "move"
    done = o.execute(plan["op_id"])
    assert done["state"] == "done"
    assert sorted(p.name for p in (root / TITLE).iterdir()) == [
        f"{TITLE} 第01巻.cbz", f"{TITLE} 第02巻.cbz", f"{TITLE} 第04巻.cbz"]
    assert (root / TITLE / f"{TITLE} 第02巻.cbz").read_bytes() == before[0][str(Path("別スキャン") / f"{TITLE} 第2巻.cbz")][0]
    assert not (root / "受け入れ" / f"{TITLE} 1巻.cbz").exists()
    assert (root / "受け入れ").is_dir()  # 空になった元のフォルダも消さない
    # 巻 id はそのまま。読書位置・作品・重複の却下が残り、名前と番号は新しいファイル名のもの
    assert vid_of(env, f"{TITLE}/{TITLE} 第01巻.cbz") == v1
    assert progress(env, v1) == (1, False) and progress(env, v2b) == (2, True)
    assert env.conn.execute("SELECT dismissed_at FROM duplicate_candidates").fetchone()["dismissed_at"]
    row = env.conn.execute("SELECT title, number_start FROM volumes WHERE id = ?", (v1,)).fetchone()
    assert (row["title"], row["number_start"]) == (f"{TITLE} 第01巻", 1)
    # 作品の更新・再スキャンをしても同じ作品・同じ巻のまま(中身・サイズ・mtime は改名で変わらない)
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    assert work_of(env, v1) == work_of(env, v2b) == work
    assert vid_of(env, f"{TITLE}/{TITLE} 第01巻.cbz") == v1
    assert work_of(env, ep) != work

    undone = o.undo(plan["op_id"])
    assert undone["state"] == "undone"
    assert full_snapshot(root) == before  # パス・バイト列・mtime・フォルダまで実行前と一致
    assert vid_of(env, f"受け入れ/{TITLE} 1巻.cbz") == v1
    row = env.conn.execute("SELECT title, number_start FROM volumes WHERE id = ?", (v1,)).fetchone()
    assert (row["title"], row["number_start"]) == (f"{TITLE} 1巻", 1)
    assert progress(env, v1) == (1, False)
    Scanner(env.conn, env.settings).scan()
    assert vid_of(env, f"受け入れ/{TITLE} 1巻.cbz") == v1


def test_rename_episode_creates_and_removes_folder(env):
    before = full_snapshot(env.root)
    o = organizer(env)
    plan = o.plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    assert plan["items"][0]["output_rel_path"] == "ある作品/ある作品 第05話.cbz"
    o.execute(plan["op_id"])
    assert (env.root / "ある作品" / "ある作品 第05話.cbz").is_file()
    o.undo(plan["op_id"])
    assert full_snapshot(env.root) == before
    actions = [(s["phase"], s["action"], s["state"]) for s in env.conn.execute(
        "SELECT * FROM organize_steps WHERE op_id = ? ORDER BY id", (plan["op_id"],))]
    assert actions == [("execute", "mkdir", "done"), ("execute", "rename", "done"),
                       ("undo", "restore", "done"), ("undo", "rmdir", "done")]


def test_undo_keeps_folder_that_got_other_files(env):
    o = organizer(env)
    plan = o.plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    o.execute(plan["op_id"])
    (env.root / "ある作品" / "メモ.txt").write_text("後から置いた")
    o.undo(plan["op_id"])
    assert (env.root / "ある作品" / "メモ.txt").is_file()  # 蔵書(利用者のファイル)を消さない
    assert (env.root / "ある作品 第5話.cbz").is_file()


def test_rename_rollback_when_move_fails_midway(env):
    before = full_snapshot(env.root)
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("ディスクの異常")
        fs.move_no_overwrite(src, dst)

    o = organizer(env, move=flaky)
    work = work_of(env, vid_of(env, f"{TITLE} 2巻.cbz"))
    plan = o.plan_rename(work, exclude_volume_ids=[vid_of(env, f"{TITLE} 2巻.cbz")])
    assert len(plan["volumes"]) == 2
    with pytest.raises(OrganizeError, match="ディスクの異常"):
        o.execute(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "failed"
    assert full_snapshot(env.root) == before  # 先に動かした巻・作ったフォルダも戻っている


def test_rename_stale_plan_and_taken_target_are_refused(env):
    o = organizer(env)
    work = work_of(env, vid_of(env, "ある作品 第5話.cbz"))
    plan = o.plan_rename(work)
    os.utime(env.root / "ある作品 第5話.cbz", ns=(1, 1))
    before = full_snapshot(env.root)
    with pytest.raises(OrganizeError, match="計画を作った後に元が変わりました"):
        o.execute(plan["op_id"])
    assert full_snapshot(env.root) == before
    # 出力先が既に埋まっていれば衝突(上書きしない)
    cbz(env.root / "ある作品" / "ある作品 第05話.cbz", "other")
    plan = o.plan_rename(work)
    assert plan["state"] == "blocked" and plan["items"][0]["status"] == "conflict"
    assert "既にファイル" in plan["items"][0]["reason"]


def test_rename_undo_refuses_when_original_location_is_taken(env):
    o = organizer(env)
    plan = o.plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    o.execute(plan["op_id"])
    cbz(env.root / "ある作品 第5話.cbz", "new")
    with pytest.raises(OrganizeError, match="元の場所に別の"):
        o.undo(plan["op_id"])
    assert o.get(plan["op_id"])["state"] == "done"
    assert (env.root / "ある作品" / "ある作品 第05話.cbz").is_file()


def test_rename_cross_drive_is_conflict(env):
    plan = organizer(env, same_drive=lambda a, b: False).plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    assert plan["state"] == "blocked" and "別のドライブ" in plan["items"][0]["reason"]


# --- CBZ 化 + テンプレート ---------------------------------------------------------

def test_cbz_with_template_roundtrip(env):
    root = env.root
    before = full_snapshot(root)
    old = vid_of(env, f"{TITLE} 3巻.zip")
    work = work_of(env, old)
    set_progress(env, old, 0, True)
    o = organizer(env)
    plan = o.plan(volume_id=old, naming="template")
    assert full_snapshot(root) == before
    assert plan["state"] == "planned" and plan["naming"] == "template"
    assert plan["volumes"][0]["output_rel_path"] == f"{TITLE}/{TITLE} 第03巻.cbz"
    o.execute(plan["op_id"])
    new = vid_of(env, f"{TITLE}/{TITLE} 第03巻.cbz")
    assert progress(env, new) == (0, True) and work_of(env, new) == work
    assert not (root / f"{TITLE} 3巻.zip").exists()
    o.undo(plan["op_id"])
    assert full_snapshot(root) == before
    assert progress(env, old) == (0, True)


def test_cbz_with_template_creates_folder_and_undo_removes_it(env):
    before = full_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid_of(env, "ある作品 第5話.cbz"), naming="template")
    assert plan["volumes"][0]["output_rel_path"] == "ある作品/ある作品 第05話.cbz"
    o.execute(plan["op_id"])
    assert (env.root / "ある作品" / "ある作品 第05話.cbz").is_file()
    o.undo(plan["op_id"])
    assert full_snapshot(env.root) == before  # 実行で作った「ある作品/」も残らない


def test_cbz_with_template_blocks_on_missing_values_and_conflicts(env):
    o = organizer(env)
    extra = o.plan(volume_id=vid_of(env, "番外編集.cbz"), naming="template")
    assert extra["state"] == "blocked" and any("番号を読めません" in p for p in extra["problems"])
    # 既にテンプレートどおりの CBZ を CBZ 化すると、出力先が自分自身と重なる(連番で逃がさない)
    v4 = o.plan(volume_id=vid_of(env, f"{TITLE}/{TITLE} 第04巻.cbz"), naming="template")
    assert v4["state"] == "blocked" and any("既にファイル" in p for p in v4["problems"])
    # 名前の決め方を選ばなければ従来どおり(表示名 + 連番)
    assert o.plan(volume_id=vid_of(env, "番外編集.cbz"))["volumes"][0]["output_rel_path"] == "番外編集 (2).cbz"
    with pytest.raises(ValueError):
        o.plan(volume_id=vid_of(env, "番外編集.cbz"), naming="other")


def test_cbz_template_multi_volume_source_conflict(make_settings, tmp_path):
    root = tmp_path / "lib2"
    write_zip(root / "まとめ.zip", [("作品A 1巻/", None), ("作品A 1巻/1.jpg", page_bytes("a")),
                                   ("作品A 第1巻/", None), ("作品A 第1巻/1.jpg", page_bytes("b"))])
    settings = make_settings(root)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    works.sync(conn)
    lib = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    v = conn.execute("SELECT id FROM volumes WHERE entry_prefix = '作品A 1巻/'").fetchone()["id"]
    plan = Organizer(conn, settings, lib).plan(volume_id=v, naming="template")
    assert plan["state"] == "blocked" and any("同じ名前" in p for p in plan["problems"])


# --- API ------------------------------------------------------------------------

def test_api_rename_plan_execute_undo(make_settings, lib):
    c = TestClient(create_app(make_settings(lib), frontend_dist=None))
    c.post("/api/scan")
    before = full_snapshot(lib)
    vols = {v["title"]: v for v in c.get("/api/volumes").json()}
    work = vols["ある作品 第5話"]["work_id"]
    plan = c.post("/api/organize/rename-plan", json={"work_id": work}).json()
    assert plan["state"] == "planned" and plan["items"][0]["status"] == "move"
    assert c.post(f"/api/organize/{plan['op_id']}/execute").json()["state"] == "done"
    assert [h["kind"] for h in c.get("/api/organize").json()] == ["rename"]
    assert c.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    assert full_snapshot(lib) == before
    assert c.post("/api/organize/rename-plan", json={"work_id": 99999}).status_code == 404
    tpl = c.post("/api/organize/plan", json={"volume_id": vols[f"{TITLE} 3巻"]["id"], "naming": "template"}).json()
    assert tpl["volumes"][0]["output_rel_path"] == f"{TITLE}/{TITLE} 第03巻.cbz"
    assert c.post("/api/organize/plan", json={"volume_id": 1, "naming": "x"}).status_code == 422


def test_rename_execute_rechecks_target_and_undo_rechecks_file(env):
    o = organizer(env)
    work = work_of(env, vid_of(env, "ある作品 第5話.cbz"))
    plan = o.plan_rename(work)
    cbz(env.root / "ある作品" / "ある作品 第05話.cbz", "late")  # 計画の後に出力先が埋まった
    with pytest.raises(OrganizeError, match="出力先に既にファイル"):
        o.execute(plan["op_id"])
    assert (env.root / "ある作品 第5話.cbz").is_file()
    (env.root / "ある作品" / "ある作品 第05話.cbz").unlink()
    (env.root / "ある作品").rmdir()
    plan = o.plan_rename(work)
    o.execute(plan["op_id"])
    moved = env.root / "ある作品" / "ある作品 第05話.cbz"
    os.utime(moved, ns=(5, 5))  # 改名した後に中身が変わった(上書きされた)とみなす
    with pytest.raises(OrganizeError, match="改名した後にファイルが変わった"):
        o.undo(plan["op_id"])
    assert moved.is_file() and o.get(plan["op_id"])["state"] == "done"


def test_rename_undo_failure_midway_returns_to_executed_state(env, monkeypatch):
    o = organizer(env)
    plan = o.plan_rename(work_of(env, vid_of(env, "ある作品 第5話.cbz")))
    o.execute(plan["op_id"])
    after = full_snapshot(env.root)
    v = vid_of(env, "ある作品/ある作品 第05話.cbz")

    def broken(*a, **kw):
        raise RuntimeError("DB の更新に失敗")

    monkeypatch.setattr(Organizer, "_relocate", broken)
    with pytest.raises(OrganizeError, match="DB の更新に失敗"):
        o.undo(plan["op_id"])
    op = o.get(plan["op_id"])
    assert op["state"] == "done" and "元に戻せませんでした" in op["error"]
    assert full_snapshot(env.root) == after  # 戻したファイルも、消したフォルダも実行済みの状態へ戻る
    assert vid_of(env, "ある作品/ある作品 第05話.cbz") == v


def test_cbz_with_template_execute_rechecks_drive(env):
    plan = organizer(env).plan(volume_id=vid_of(env, "ある作品 第5話.cbz"), naming="template")
    before = full_snapshot(env.root)
    # 計画の後に出力先(テンプレートで決めた別のフォルダ)だけが別ドライブになった
    o = organizer(env, same_drive=lambda a, b: "ある作品/" not in str(b).replace(os.sep, "/"))
    with pytest.raises(OrganizeError, match="別のドライブ"):
        o.execute(plan["op_id"])
    assert full_snapshot(env.root) == before
