"""起動時の復旧(#36): 実行・元に戻すの途中でプロセスが落ちた操作を、起動時にジャーナルとファイルの有無から戻す。

落ちた場面は、移動の途中で例外を投げ、その後の巻き戻し(_revert)も途中で落として作る。
どちらも BaseException なので、例外処理(巻き戻し)の外へ抜け、操作は running / undoing のまま残る。
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from conftest import page_bytes, snapshot, write_zip
from spinebox import db
from spinebox.cache import ExtractionCache
from spinebox.library import Library
from spinebox.organize import fs
from spinebox.organize import organizer as organizer_module
from spinebox.organize.organizer import Organizer
from spinebox.scanner import Scanner


class SimulatedCrash(BaseException):
    """プロセスが落ちた場面。巻き戻しの except に捕まらないよう BaseException にする。"""


@contextmanager
def power_cut_during_rollback():
    """巻き戻し(_revert)の途中で落ちた状態。操作は元の状態(running / undoing)のまま残る。"""
    def die(self, steps):
        raise SimulatedCrash()

    with mock.patch.object(Organizer, "_revert", die):
        yield


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


def steps(env, op_id):
    return [(s["phase"], s["action"], s["state"]) for s in env.conn.execute(
        "SELECT * FROM organize_steps WHERE op_id = ? ORDER BY id", (op_id,))]


def state_of(env, op_id) -> str:
    return organizer(env).get(op_id)["state"]


def crash_on_second_commit(env):
    """多巻のアーカイブ(multi.zip)の 2 巻目の確定の移動に入る前に落ちる。1 巻目は確定済み。"""
    calls = []

    def move(src, dst):
        calls.append(Path(dst).name)
        if len(calls) == 2:
            raise SimulatedCrash()
        fs.move_no_overwrite(src, dst)

    return organizer(env, move=move)


def test_startup_rolls_back_execute_interrupted_mid_commit(env):
    before = lib_snapshot(env.root)
    o = crash_on_second_commit(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    op = plan["op_id"]
    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        o.execute(op)
    assert state_of(env, op) == "running"
    assert lib_snapshot(env.root) != before  # 落ちた時点で 1 巻目は確定している

    recovered = organizer(env).recover_interrupted()

    assert [(r["op_id"], r["state"]) for r in recovered] == [(op, "failed")]
    assert lib_snapshot(env.root) == before
    assert not (env.root / ".spinebox" / "work" / op).exists()
    assert steps(env, op) == [("execute", "commit", "reverted"), ("execute", "commit", "reverted")]
    assert state_of(env, op) == "failed"
    assert env.conn.execute("SELECT organized_op_id FROM volumes WHERE id = ?",
                            (vid(env, "multi.zip", "Vol1/"),)).fetchone()[0] is None


def test_startup_rolls_back_move_done_but_not_journaled(env):
    """退避の移動は済んだが、記録を done にする前に落ちた。ファイルの有無で移動の済みを判断して戻す。"""
    before = lib_snapshot(env.root)

    def move(src, dst):
        fs.move_no_overwrite(src, dst)
        if "original" in Path(dst).parts:
            raise SimulatedCrash()

    o = organizer(env, move=move)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    op = plan["op_id"]
    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        o.execute(op)
    assert not (env.root / "multi.zip").exists()  # 退避は済んでいる

    organizer(env).recover_interrupted()

    assert lib_snapshot(env.root) == before
    assert steps(env, op) == [("execute", "commit", "reverted"), ("execute", "commit", "reverted"),
                              ("execute", "trash_original", "reverted")]
    assert state_of(env, op) == "failed"


def test_startup_returns_interrupted_undo_to_done_and_it_can_be_undone(env):
    before = lib_snapshot(env.root)
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    op = plan["op_id"]
    o.execute(op)
    executed = lib_snapshot(env.root)
    calls = []

    def move(src, dst):
        calls.append(dst)
        if len(calls) == 2:  # 元に戻す途中、2 つ目の CBZ を移す前に落ちる
            raise SimulatedCrash()
        fs.move_no_overwrite(src, dst)

    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        organizer(env, move=move).undo(op)
    assert state_of(env, op) == "undoing"

    recovered = organizer(env).recover_interrupted()

    # 元に戻す前(実行済み)の状態へ戻り、もう一度元に戻せる
    assert [(r["op_id"], r["state"]) for r in recovered] == [(op, "done")]
    assert lib_snapshot(env.root) == executed
    organizer(env).undo(op)
    assert state_of(env, op) == "undone"
    assert lib_snapshot(env.root) == before


def test_offline_root_is_left_untouched_and_retried_on_next_start(env):
    before = lib_snapshot(env.root)
    o = crash_on_second_commit(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    op = plan["op_id"]
    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        o.execute(op)
    crashed = lib_snapshot(env.root)

    with mock.patch.object(organizer_module, "root_is_accessible", return_value=False):
        assert organizer(env).recover_interrupted() == []

    assert lib_snapshot(env.root) == crashed  # 何も動かさず、状態も running のまま
    assert state_of(env, op) == "running"

    recovered = organizer(env).recover_interrupted()  # 次の起動で戻す
    assert [(r["op_id"], r["state"]) for r in recovered] == [(op, "failed")]
    assert lib_snapshot(env.root) == before


def test_nothing_to_recover_on_a_clean_start(env):
    o = organizer(env)
    plan = o.plan(volume_id=vid(env, "single.cbz"))
    o.execute(plan["op_id"])
    assert organizer(env).recover_interrupted() == []
    assert state_of(env, plan["op_id"]) == "done"


def test_startup_rolls_back_range_cbz_interrupted_mid_trash(make_settings, tmp_path):
    """範囲指定の CBZ 化(改名・CBZ 化・退避をまとめた操作)が、退避の途中で落ちた。"""
    root = tmp_path / "lib"
    write_zip(root / "漫画" / "まとめ.zip", [("v1/001.jpg", page_bytes("a1")), ("v2/001.jpg", page_bytes("a2"))])
    write_zip(root / "漫画" / "単巻 1.cbz", [("001.jpg", page_bytes("b1"))])
    settings = make_settings(root)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    env = SimpleNamespace(conn=conn, settings=settings, root=root,
                          library=Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes)))
    before = lib_snapshot(root)
    root_id = conn.execute("SELECT id FROM library_roots").fetchone()["id"]

    def move(src, dst):
        if "original" in Path(dst).parts:  # 退避の移動に入る前に落ちる
            raise SimulatedCrash()
        fs.move_no_overwrite(src, dst)

    o = organizer(env, move=move)
    plan = o.plan_range(root_id, "漫画", include_cbz=True)
    assert plan["state"] == "planned"
    op = plan["op_id"]
    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        o.execute(op)
    created = set(lib_snapshot(root)) - set(before)
    assert any(k.endswith(".cbz") for k in created)  # 確定までは済んでいる(CBZ が出力先にある)

    recovered = organizer(env).recover_interrupted()

    assert [(r["op_id"], r["state"]) for r in recovered] == [(op, "failed")]
    assert lib_snapshot(root) == before
    assert not (env.settings.organize_base(root) / "work" / op).exists()
    assert state_of(env, op) == "failed"


def test_app_startup_recovers_interrupted_operation(env):
    """アプリの起動(lifespan)で、途中で止まった操作が戻る。"""
    from spinebox.app import create_app

    before = lib_snapshot(env.root)
    o = crash_on_second_commit(env)
    plan = o.plan(volume_id=vid(env, "multi.zip", "Vol1/"))
    op = plan["op_id"]
    with power_cut_during_rollback(), pytest.raises(SimulatedCrash):
        o.execute(op)

    from fastapi.testclient import TestClient
    with TestClient(create_app(env.settings, frontend_dist=None)) as client:
        assert client.get(f"/api/organize/{op}").json()["state"] == "failed"
    assert lib_snapshot(env.root) == before
