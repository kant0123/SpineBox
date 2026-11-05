"""受け入れ(受け入れフォルダ → 同期 → 整理案のプレビュー → 承認で書庫へ)。蔵書は一時ディレクトリに動的生成する。"""
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip
from spinebox import db, works
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.config import ConfigError, InboxSetting, parse_settings
from spinebox.library import Library
from spinebox.organize import fs
from spinebox.organize.intake import Decision, Intake
from spinebox.organize.organizer import OrganizeError, Organizer
from spinebox.scanner import Scanner

FOO = "Foo Series"
BAR = "Bar Story"


def cbz(path: Path, label: str, n: int = 2) -> Path:
    return write_zip(path, [(f"{i:03d}.jpg", page_bytes(f"{label}/{i}")) for i in range(1, n + 1)])


def tree(root: Path) -> tuple[dict, set]:
    """作業場・退避フォルダ(.spinebox)を除いた全ファイル(バイト列・サイズ・mtime)と全フォルダ。"""
    files = {k: v for k, v in snapshot(root).items() if not k.startswith(".spinebox")}
    dirs = {str(Path(d).relative_to(root)) for d, _, _ in os.walk(root)}
    return files, {d for d in dirs if not d.startswith(".spinebox")}


@pytest.fixture
def env(tmp_path, make_settings):
    lib, inbox = tmp_path / "lib", tmp_path / "inbox"
    cbz(lib / FOO / f"{FOO} 第01巻.cbz", "foo1")
    cbz(lib / FOO / f"{FOO} 第02巻.cbz", "foo2")
    inbox.mkdir()
    settings = replace(make_settings(lib), inboxes=(InboxSetting(inbox, lib),))
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    works.sync(conn)
    library = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    return SimpleNamespace(conn=conn, settings=settings, library=library, lib=lib, inbox=inbox)


def intake(env, **kw) -> Intake:
    return Intake(Organizer(env.conn, env.settings, env.library, **kw))


def sync(env):
    Scanner(env.conn, env.settings).scan_inbox()


def items(env, **kw) -> dict:
    return {i["name"]: i for i in intake(env, **kw).list_items()["items"]}


def put_inbox(env):
    """4 通り: 既存作品の新しい巻 / 新しい作品 / 既に持っている巻の別スキャン / 壊れたアーカイブ。"""
    cbz(env.inbox / f"{FOO} 第03巻.cbz", "foo3")
    write_zip(env.inbox / f"{BAR} 第01巻.zip", [("001.jpg", page_bytes("bar1/1")), ("002.jpg", page_bytes("bar1/2"))])
    cbz(env.inbox / f"{FOO} 2巻.cbz", "foo2-other", 3)
    (env.inbox / "broken.zip").write_bytes(b"this is not a zip")
    sync(env)


def work_titles(env) -> dict[str, int]:
    return {w["title"]: w["volume_count"] if "volume_count" in w else len(w.get("volumes", []))
            for w in works.list_works(env.conn)["works"]}


def visible_titles(env) -> set[str]:
    from spinebox.scanner import LISTED_VOLUMES_JOIN, LISTED_VOLUMES_WHERE
    return {r["title"] for r in env.conn.execute("SELECT v.title " + LISTED_VOLUMES_JOIN + LISTED_VOLUMES_WHERE)}


# --- 設定 -----------------------------------------------------------------------

def test_inbox_dirs_setting(tmp_path):
    lib, lib2, inbox = tmp_path / "lib", tmp_path / "lib2", tmp_path / "inbox"
    s = parse_settings({"library_roots": [str(lib)], "inbox_dirs": [str(inbox)]}, tmp_path)
    assert s.inboxes == (InboxSetting(inbox, lib),)
    s = parse_settings({"library_roots": [str(lib), str(lib2)],
                        "inbox_dirs": [{"path": str(inbox), "library": str(lib2)}]}, tmp_path)
    assert s.inboxes[0].library == lib2 and s.inbox_for(str(inbox)) is s.inboxes[0] and s.inbox_for(lib) is None
    assert parse_settings({}, tmp_path).inboxes == ()


@pytest.mark.parametrize("data, message", [
    ({"library_roots": ["lib", "lib2"], "inbox_dirs": ["inbox"]}, "書庫.*決まりません"),
    ({"library_roots": ["lib"], "inbox_dirs": [{"path": "inbox", "library": "other"}]}, "library_roots のどれか"),
    ({"library_roots": ["lib"], "inbox_dirs": [{"path": "lib/inbox"}]}, "重ねられません"),
    ({"library_roots": ["lib/books"], "inbox_dirs": ["lib"]}, "重ねられません"),
    ({"library_roots": ["lib"], "inbox_dirs": ["lib"]}, "重ねられません"),
    ({"library_roots": ["lib"], "inbox_dirs": ["a", "a/b"]}, "重ねられません"),
    ({"library_roots": ["lib"], "inbox_dirs": "inbox"}, "配列"),
    ({"library_roots": ["lib"], "inbox_dirs": [3]}, "文字列か"),
    ({"library_roots": ["lib"], "inbox_dirs": [{"path": ""}]}, "path"),
    ({"library_roots": ["lib"], "inbox_dirs": [{"path": "a", "extra": 1}]}, "path と library だけ"),
])
def test_invalid_inbox_dirs(tmp_path, data, message):
    with pytest.raises(ConfigError, match=message):
        parse_settings(data, tmp_path)


# --- 検出と整理案 ---------------------------------------------------------------

def test_four_kinds_are_classified(env):
    put_inbox(env)
    got = items(env)
    new_volume = got[f"{FOO} 第03巻.cbz"]
    assert (new_volume["category"], new_volume["status"], new_volume["mode"]) == ("existing_work", "ready", "move")
    assert new_volume["work"]["match"] == "existing" and new_volume["work"]["title"] == FOO
    assert new_volume["volumes"][0]["output_rel_path"] == f"{FOO}/{FOO} 第03巻.cbz"
    new_work = got[f"{BAR} 第01巻.zip"]
    assert (new_work["category"], new_work["status"], new_work["mode"]) == ("new_work", "ready", "cbz")
    assert new_work["work"]["match"] == "new" and new_work["work"]["title"] == BAR
    assert new_work["volumes"][0]["output_rel_path"] == f"{BAR}/{BAR} 第01巻.cbz"
    dup = got[f"{FOO} 2巻.cbz"]
    assert (dup["category"], dup["status"]) == ("duplicate", "choose")
    assert dup["volumes"][0]["duplicate_of"][0]["title"] == f"{FOO} 第02巻"
    assert "既に持っている巻" in dup["reasons"][0]
    broken = got["broken.zip"]
    assert (broken["category"], broken["status"]) == ("unreadable", "blocked") and broken["reasons"]


def test_sync_and_preview_write_nothing(env):
    cbz(env.inbox / "sub" / f"{FOO} 第03巻.cbz", "foo3")
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "bar1")
    (env.inbox / "broken.zip").write_bytes(b"x")
    before = (tree(env.lib), tree(env.inbox))
    sync(env)
    it = intake(env)
    it.list_items()
    result = it.preview([Decision(r["id"]) for r in env.conn.execute(
        "SELECT s.id FROM sources s JOIN library_roots r ON r.id = s.root_id WHERE r.inbox = 1")])
    assert len(result["items"]) == 3
    assert (tree(env.lib), tree(env.inbox)) == before  # 1 バイト・mtime・フォルダも変わらない
    assert not (env.lib / ".spinebox").exists() and not (env.inbox / ".spinebox").exists()


def test_inbox_volumes_are_not_part_of_the_library(env):
    put_inbox(env)
    before = visible_titles(env)
    assert before == {f"{FOO} 第01巻", f"{FOO} 第02巻"}  # 受け入れ待ちは蔵書の一覧に出ない
    works.sync(env.conn)
    assert {w["title"] for w in works.list_works(env.conn)["works"]} == {FOO}
    # 蔵書のスキャンは受け入れフォルダの記録に触らず、受け入れの同期も蔵書の記録に触らない
    Scanner(env.conn, env.settings).scan()
    assert len(items(env)) == 4 and visible_titles(env) == before
    assert env.conn.execute("SELECT COUNT(*) FROM library_roots WHERE inbox = 1 AND enabled = 1").fetchone()[0] == 1


def test_scan_issues_are_separated_per_scan_kind(env):
    (env.inbox / "broken.zip").write_bytes(b"x")
    (env.lib / "broken-lib.zip").write_bytes(b"y")
    inbox_result = Scanner(env.conn, env.settings).scan_inbox()
    lib_result = Scanner(env.conn, env.settings).scan()
    assert [i["path"].rsplit("/", 1)[-1] for i in inbox_result.issues] == ["broken.zip"]
    assert [i["path"].rsplit("/", 1)[-1] for i in lib_result.issues] == ["broken-lib.zip"]


def test_not_approved_items_stay_and_show_up_again(env):
    put_inbox(env)
    it = intake(env)
    target = next(i for i in it.list_items()["items"] if i["name"] == f"{FOO} 第03巻.cbz")
    res = it.approve([Decision(target["source_id"], expect_outputs=target["expect_outputs"])])
    assert res["results"][0]["ok"], res
    sync(env)
    got = items(env)
    assert f"{FOO} 第03巻.cbz" not in got  # 承認したものは受け入れフォルダから出た
    assert {f"{BAR} 第01巻.zip", f"{FOO} 2巻.cbz", "broken.zip"} <= set(got)  # 承認しなかったものは残る
    for name in (f"{BAR} 第01巻.zip", f"{FOO} 2巻.cbz", "broken.zip"):
        assert (env.inbox / name).is_file()


# --- 承認と元に戻す ---------------------------------------------------------------

def approve_all_ready(env, it: Intake | None = None):
    it = it or intake(env)
    ready = [i for i in it.list_items()["items"] if i["status"] == "ready"]
    return ready, it.approve([Decision(i["source_id"], expect_outputs=i["expect_outputs"]) for i in ready])


def test_approve_then_undo_restores_both_sides_exactly(env):
    put_inbox(env)
    before = (tree(env.lib), tree(env.inbox))
    ready, res = approve_all_ready(env)
    assert {i["name"] for i in ready} == {f"{FOO} 第03巻.cbz", f"{BAR} 第01巻.zip"}
    assert all(r["ok"] for r in res["results"]), res
    # 書庫に入って、通常の巻として一覧・作品に出る
    assert visible_titles(env) == {f"{FOO} 第01巻", f"{FOO} 第02巻", f"{FOO} 第03巻", f"{BAR} 第01巻"}
    assert (env.lib / FOO / f"{FOO} 第03巻.cbz").is_file() and (env.lib / BAR / f"{BAR} 第01巻.cbz").is_file()
    listed = {w["title"]: w for w in works.list_works(env.conn)["works"]}
    assert set(listed) == {FOO, BAR}
    assert (listed[FOO]["volume_count"], listed[BAR]["volume_count"]) == (3, 1)
    # 受け入れフォルダからは出て、元は退避フォルダにある(消えていない)
    assert not (env.inbox / f"{BAR} 第01巻.zip").exists() and not (env.inbox / f"{FOO} 第03巻.cbz").exists()
    assert (env.inbox / ".spinebox" / "trash").is_dir()
    # 新しい巻の読書位置は新規
    assert env.conn.execute("SELECT COUNT(*) FROM reading_progress").fetchone()[0] == 0

    org = Organizer(env.conn, env.settings, env.library)
    for r in res["results"]:
        undone = org.undo(r["op"]["op_id"])
        assert undone["state"] == "undone"
    assert (tree(env.lib), tree(env.inbox)) == before  # パス・バイト列・mtime・フォルダまで実行前と一致
    assert visible_titles(env) == {f"{FOO} 第01巻", f"{FOO} 第02巻"}
    sync(env)
    assert {f"{FOO} 第03巻.cbz", f"{BAR} 第01巻.zip"} <= set(items(env))  # 元に戻したものは再び受け入れ待ち
    # 元に戻して一覧に見える巻が無くなった作品(Bar Story)は、受け入れの候補にも「既にある作品」にもならない
    # (作品の一覧に出ない作品を選ばせない)。最初と同じ「新しい作品」の整理案に戻る
    again = items(env)[f"{BAR} 第01巻.zip"]
    assert (again["status"], again["category"]) == ("ready", "new_work") and again["work"]["candidates"] == []


def test_cbz_mode_converts_each_volume_and_keeps_original(env):
    write_zip(env.inbox / f"{BAR}.zip", [
        ("Bar Story 1巻/001.jpg", page_bytes("a1")), ("Bar Story 1巻/002.jpg", page_bytes("a2")),
        ("Bar Story 2巻/001.jpg", page_bytes("b1")),
    ])
    sync(env)
    item = items(env)[f"{BAR}.zip"]
    assert (item["mode"], item["status"], item["category"]) == ("cbz", "ready", "new_work")
    assert [v["output_rel_path"] for v in item["volumes"]] == [f"{BAR}/{BAR} 第01巻.cbz", f"{BAR}/{BAR} 第02巻.cbz"]
    original = (env.inbox / f"{BAR}.zip").read_bytes()
    before = (tree(env.lib), tree(env.inbox))
    ready, res = approve_all_ready(env)
    assert res["results"][0]["ok"], res
    assert sorted(p.name for p in (env.lib / BAR).iterdir()) == [f"{BAR} 第01巻.cbz", f"{BAR} 第02巻.cbz"]
    trashed = list((env.inbox / ".spinebox" / "trash").rglob(f"{BAR}.zip"))
    assert len(trashed) == 1 and trashed[0].read_bytes() == original  # 元は退避フォルダへ移すだけ
    assert {w["title"] for w in works.list_works(env.conn)["works"]} == {FOO, BAR}
    Organizer(env.conn, env.settings, env.library).undo(res["results"][0]["op"]["op_id"])
    assert (tree(env.lib), tree(env.inbox)) == before


def test_choose_when_work_is_uncertain_and_user_decides(env):
    # 巻自身の名前に作品名が無く、外側のフォルダ名から推測するだけ → 自動では決めない
    cbz(env.inbox / BAR / "第2巻.cbz", "bar2")
    sync(env)
    item = items(env)["第2巻.cbz"]
    assert item["status"] == "choose" and item["work"]["confident"] is False
    assert "外側の名前" in item["work"]["reason"] and item["work"]["title"] == BAR
    it = intake(env)
    res = it.approve([Decision(item["source_id"])])
    assert res["results"][0]["ok"] is False  # 確認が済むまで承認できない
    assert (env.inbox / BAR / "第2巻.cbz").is_file()
    # 既存の作品を選ぶ
    foo_id = works.list_works(env.conn)["works"][0]["id"]
    decided = it.evaluate(Decision(item["source_id"], work_id=foo_id), state=works._State(env.conn), inspect=True)[0]
    assert decided["work"]["decided"] and decided["work"]["title"] == FOO
    assert decided["status"] == "choose" and decided["volumes"][0]["duplicate_of"]  # 第02巻は既に持っている
    # 新しい作品として入れる
    ok = it.evaluate(Decision(item["source_id"], new_title="Baz World"), state=works._State(env.conn), inspect=True)[0]
    assert ok["status"] == "ready" and ok["volumes"][0]["output_rel_path"] == "Baz World/Baz World 第02巻.cbz"
    res = it.approve([Decision(item["source_id"], new_title="Baz World", expect_outputs=ok["expect_outputs"])])
    assert res["results"][0]["ok"], res
    titles = {w["title"] for w in works.list_works(env.conn)["works"]}
    assert titles == {FOO, "Baz World"}
    # 名前から作られた同名の作品を空のまま残さない(作品の候補に同じ名前が 2 つ出てしまう)
    assert env.conn.execute("""SELECT COUNT(*) FROM works w WHERE w.merged_into IS NULL
                               AND NOT EXISTS (SELECT 1 FROM work_volumes v WHERE v.work_id = w.id)""").fetchone()[0] == 0


def test_unreadable_number_and_duplicate_choices(env):
    cbz(env.inbox / f"{FOO} 番外編.cbz", "extra")
    cbz(env.inbox / f"{FOO} 2巻.cbz", "foo2-other")
    sync(env)
    got = items(env)
    extra = got[f"{FOO} 番外編.cbz"]
    assert extra["status"] == "choose" and "番号" in extra["volumes"][0]["reason"]
    # 名前の似た既存の作品は同じ作品かもしれないので、自動では決めない(候補に出す)
    assert extra["work"]["candidates"][0]["title"] == FOO and extra["work"]["confident"] is False
    it = intake(env)
    foo_id = extra["work"]["candidates"][0]["work_id"]
    fixed = it.evaluate(Decision(extra["source_id"], work_id=foo_id, numbers={extra["volumes"][0]["volume_id"]: 9}),
                        state=works._State(env.conn), inspect=True)[0]
    assert fixed["status"] == "ready" and fixed["volumes"][0]["output_rel_path"] == f"{FOO}/{FOO} 第09巻.cbz"
    assert fixed["volumes"][0]["number"]["source"] == "manual"
    # 重複は取り込まないと決めれば、取り込む巻が無いので承認できない(受け入れフォルダに残る)
    dup = got[f"{FOO} 2巻.cbz"]
    skipped = it.evaluate(Decision(dup["source_id"], skip_volume_ids=frozenset({dup["volumes"][0]["volume_id"]})),
                          state=works._State(env.conn), inspect=True)[0]
    assert skipped["status"] == "blocked" and "取り込む巻がありません" in skipped["reasons"][0]
    # 番号を直せば別の巻として取り込める
    other = it.evaluate(Decision(dup["source_id"], numbers={dup["volumes"][0]["volume_id"]: 7}),
                        state=works._State(env.conn), inspect=True)[0]
    assert other["status"] == "ready" and other["volumes"][0]["output_rel_path"] == f"{FOO}/{FOO} 第07巻.cbz"


def test_partly_duplicate_source_skips_duplicates_only(env):
    write_zip(env.inbox / f"{FOO} 2-3巻まとめ.zip", [
        (f"{FOO} 2巻/001.jpg", page_bytes("d2")), (f"{FOO} 3巻/001.jpg", page_bytes("d3")),
    ])
    sync(env)
    item = items(env)[f"{FOO} 2-3巻まとめ.zip"]
    dup_id = next(v["volume_id"] for v in item["volumes"] if v["duplicate_of"])
    assert item["status"] == "choose" and item["category"] == "duplicate"
    it = intake(env)
    chosen = it.evaluate(Decision(item["source_id"], skip_volume_ids=frozenset({dup_id})),
                         state=works._State(env.conn), inspect=True)[0]
    assert chosen["status"] == "ready" and chosen["warnings"] and "退避フォルダに残り" in chosen["warnings"][-1]
    before = (tree(env.lib), tree(env.inbox))
    res = it.approve([Decision(item["source_id"], skip_volume_ids=frozenset({dup_id}),
                               expect_outputs=chosen["expect_outputs"])])
    assert res["results"][0]["ok"], res
    assert (env.lib / FOO / f"{FOO} 第03巻.cbz").is_file() and not (env.lib / FOO / f"{FOO} 第02巻 (2).cbz").exists()
    Organizer(env.conn, env.settings, env.library).undo(res["results"][0]["op"]["op_id"])
    assert (tree(env.lib), tree(env.inbox)) == before


def test_duplicate_names_inside_one_batch_are_flagged(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    cbz(env.inbox / "other" / f"{BAR} 1巻.cbz", "b")
    sync(env)
    got = intake(env).list_items()["items"]
    first, second = (i for i in got if i["name"].startswith(BAR))
    assert first["status"] == "ready" and second["status"] == "choose" and "同じ" in second["reasons"][0]


def test_novel_is_moved_with_its_extension(env):
    (env.inbox / f"{BAR} 第01巻.txt").write_text("吾輩は猫である。\n" * 5, encoding="utf-8")
    sync(env)
    item = items(env)[f"{BAR} 第01巻.txt"]
    assert (item["mode"], item["status"]) == ("move", "ready")
    assert item["volumes"][0]["output_rel_path"] == f"{BAR}/{BAR} 第01巻.txt"
    before = (tree(env.lib), tree(env.inbox))
    ready, res = approve_all_ready(env)
    assert res["results"][0]["ok"], res
    assert (env.lib / BAR / f"{BAR} 第01巻.txt").is_file()
    row = env.conn.execute("SELECT v.format FROM volumes v WHERE v.title = ? AND v.missing = 0 AND v.organized_op_id IS NULL",
                           (f"{BAR} 第01巻",)).fetchone()
    assert row["format"] == "text"
    Organizer(env.conn, env.settings, env.library).undo(res["results"][0]["op"]["op_id"])
    assert (tree(env.lib), tree(env.inbox)) == before


def test_read_position_of_accepted_volume_is_new_and_survives_rescan(env):
    cbz(env.inbox / f"{FOO} 第03巻.cbz", "foo3", 4)
    sync(env)
    ready, res = approve_all_ready(env)
    new_vid = res["results"][0]["op"]["volumes"][0]["new_volume_id"]
    assert env.conn.execute("SELECT * FROM reading_progress WHERE volume_id = ?", (new_vid,)).fetchone() is None
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    row = env.conn.execute("SELECT id FROM volumes WHERE title = ? AND missing = 0 AND organized_op_id IS NULL",
                           (f"{FOO} 第03巻",)).fetchone()
    assert row["id"] == new_vid  # 書庫のスキャンで同じ巻のまま


# --- 失敗・拒否 -------------------------------------------------------------------

def test_different_drive_is_refused(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    sync(env)
    it = intake(env, same_drive=lambda a, b: False)
    item = it.list_items()["items"][0]
    assert item["status"] == "blocked" and any("別のドライブ" in r for r in item["reasons"])
    assert any("別のドライブ" in p for p in it.list_items()["inboxes"][0]["problems"])
    before = (tree(env.lib), tree(env.inbox))
    res = it.approve([Decision(item["source_id"])])
    assert res["results"][0]["ok"] is False and (tree(env.lib), tree(env.inbox)) == before


def test_drive_changed_between_preview_and_approve_stops_before_writing(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    sync(env)
    plan_it = intake(env)
    item, plan = plan_it.evaluate(Decision(plan_it.list_items()["items"][0]["source_id"]),
                                  state=works._State(env.conn), inspect=True)
    assert plan is not None
    op = plan_it.org._save_plan(plan)
    before = (tree(env.lib), tree(env.inbox))
    with pytest.raises(OrganizeError, match="別のドライブ"):
        Organizer(env.conn, env.settings, env.library, same_drive=lambda a, b: False).execute(op["op_id"])
    assert (tree(env.lib), tree(env.inbox)) == before


def test_source_changed_after_preview_is_refused(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    sync(env)
    it = intake(env)
    item, plan = it.evaluate(Decision(it.list_items()["items"][0]["source_id"]), state=works._State(env.conn),
                             inspect=True)
    op = it.org._save_plan(plan)
    (env.inbox / f"{BAR} 第01巻.cbz").write_bytes(b"changed")
    with pytest.raises(OrganizeError, match="元が変わりました"):
        it.org.execute(op["op_id"])
    assert (env.inbox / f"{BAR} 第01巻.cbz").read_bytes() == b"changed" and not (env.lib / BAR).exists()


def test_move_failure_rolls_back_both_sides(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    write_zip(env.inbox / f"{BAR} 第02巻.zip", [("001.jpg", page_bytes("b"))])
    sync(env)
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("ディスクの異常")
        fs.move_no_overwrite(src, dst)

    it = intake(env, move=flaky)
    before = (tree(env.lib), tree(env.inbox))
    ready = [i for i in it.list_items()["items"] if i["status"] == "ready"]
    res = it.approve([Decision(i["source_id"]) for i in ready])
    assert [r["ok"] for r in res["results"]] == [True, False]  # 1 件目は成功、2 件目は巻き戻し済みで受け入れフォルダに残る
    assert "ディスクの異常" in res["results"][1]["error"]
    assert (env.inbox / f"{BAR} 第02巻.zip").is_file() and not (env.lib / BAR / f"{BAR} 第02巻.cbz").exists()
    Organizer(env.conn, env.settings, env.library).undo(res["results"][0]["op"]["op_id"])
    assert (tree(env.lib), tree(env.inbox)) == before


def test_expected_outputs_mismatch_is_refused(env):
    cbz(env.inbox / f"{FOO} 第03巻.cbz", "a")
    sync(env)
    it = intake(env)
    item = it.list_items()["items"][0]
    res = it.approve([Decision(item["source_id"], expect_outputs=[f"{FOO}/{FOO} 第09巻.cbz"])])
    assert res["results"][0]["ok"] is False and "出力先が変わりました" in res["results"][0]["error"]
    assert (env.inbox / f"{FOO} 第03巻.cbz").is_file()


def test_same_name_reappearing_after_accept_is_explained(env):
    cbz(env.inbox / f"{FOO} 第03巻.cbz", "a")
    sync(env)
    ready, res = approve_all_ready(env)
    cbz(env.inbox / f"{FOO} 第03巻.cbz", "again", 3)  # 同じ名前の本をまた置いた
    sync(env)
    item = items(env)[f"{FOO} 第03巻.cbz"]
    assert item["status"] == "blocked" and "以前に受け入れた" in item["reasons"][0]


def test_output_already_present_is_blocked_not_overwritten(env):
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "a")
    sync(env)
    cbz(env.lib / BAR / f"{BAR} 第01巻.cbz", "existing-but-not-scanned")  # 書庫にまだ記録の無いファイル
    item = intake(env).list_items()["items"][0]
    assert item["status"] == "blocked" and "既にファイル" in "".join(item["reasons"])


def test_unanalyzed_inner_archive_needs_analysis(env):
    from conftest import zip_bytes
    inner = zip_bytes([(f"{BAR} 1巻/001.jpg", page_bytes("i1"))])
    write_zip(env.inbox / f"{BAR}.zip", [("inner.zip", inner)])
    sync(env)
    item = items(env)[f"{BAR}.zip"]
    assert item["status"] == "blocked" and item["needs_analysis"] and "中の巻をまだ調べていません" in item["reasons"][0]


def test_shelf_not_scanned_yet_is_explained(tmp_path, make_settings):
    lib, inbox = tmp_path / "lib", tmp_path / "inbox"
    lib.mkdir()
    cbz(inbox / f"{BAR} 第01巻.cbz", "a")
    settings = replace(make_settings(lib), inboxes=(InboxSetting(inbox, lib),))
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan_inbox()
    it = Intake(Organizer(conn, settings, Library(settings, ExtractionCache(settings.cache_dir, 1 << 30))))
    out = it.list_items()
    assert "スキャンされていません" in out["items"][0]["reasons"][0]
    assert "スキャンされていません" in out["inboxes"][0]["problems"][0]


# --- HTTP ---------------------------------------------------------------------

@pytest.fixture
def client(env):
    env.conn.close()
    with TestClient(create_app(env.settings, frontend_dist=None)) as c:
        yield c


def test_api_flow_sync_preview_approve_undo(env, client):
    assert client.get("/api/inbox").json()["items"] == []
    cbz(env.inbox / "サブ" / f"{FOO} 第03巻.cbz", "foo3")
    cbz(env.inbox / f"{BAR} 第01巻.cbz", "bar1")
    untouched = (tree(env.lib), tree(env.inbox))
    synced = client.post("/api/inbox/sync").json()
    assert synced["volumes"] == 2 and [i["name"] for i in synced["items"]] == [f"{BAR} 第01巻.cbz", f"{FOO} 第03巻.cbz"]
    assert (tree(env.lib), tree(env.inbox)) == untouched  # 同期は読み取りだけ
    assert client.get("/api/volumes").json().__len__() == 2  # 受け入れ待ちは蔵書の一覧に出ない
    item = synced["items"][1]
    preview = client.post("/api/inbox/preview", json={"items": [{"source_id": item["source_id"]}]}).json()
    assert preview["items"][0]["status"] == "ready" and (tree(env.lib), tree(env.inbox)) == untouched
    res = client.post("/api/inbox/approve", json={"items": [
        {"source_id": item["source_id"], "expect_outputs": preview["items"][0]["expect_outputs"]}]}).json()
    assert res["results"][0]["ok"] is True
    assert not (env.inbox / "サブ").exists()  # 受け入れ後、空になった受け入れ元のサブフォルダは削除される
    op_id = res["results"][0]["op"]["op_id"]
    assert len(client.get("/api/volumes").json()) == 3
    hist = client.get("/api/organize").json()
    assert hist[0]["kind"] == "intake" and hist[0]["state"] == "done" and hist[0]["mode"] == "move"
    assert client.post(f"/api/organize/{op_id}/undo").json()["state"] == "undone"
    assert len(client.get("/api/volumes").json()) == 2
    assert (tree(env.lib), tree(env.inbox)) == untouched
    jobs = client.get("/api/jobs").json()["history"]
    assert [(j["kind"], j["origin"], j["status"]) for j in jobs[:3]] == [
        ("organize_undo", "button", "succeeded"),
        ("inbox_approve", "button", "succeeded"),
        ("inbox_sync", "button", "succeeded")]


def test_api_validation_and_numbers(env, client):
    assert client.post("/api/inbox/preview", json={"items": [{"source_id": 1, "numbers": {"x": 1}}]}).status_code == 422
    assert client.post("/api/inbox/preview", json={"items": [{"source_id": 1, "numbers": {"1": 99999}}]}).status_code == 422
    assert client.post("/api/inbox/preview", json={"items": [{"source_id": "a"}]}).status_code == 422
    missing = client.post("/api/inbox/preview", json={"items": [{"source_id": 99999}]}).json()["items"][0]
    assert missing["status"] == "blocked"


def test_approve_busy_while_scanning(env, client):
    app = client.app
    assert app.state.scan_lock.acquire(blocking=False)
    try:
        assert client.post("/api/inbox/approve", json={"items": []}).status_code == 409
        assert client.post("/api/inbox/sync").status_code == 409
    finally:
        app.state.scan_lock.release()
