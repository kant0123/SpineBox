import pytest

from spinebox import db


def test_migrate_sets_user_version_and_is_idempotent(tmp_path):
    conn = db.open_db(tmp_path / "x.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert db.migrate(conn) == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"library_roots", "sources", "volumes", "reading_progress", "scan_issues"} <= tables


def test_newer_schema_is_refused():
    conn = db.connect(":memory:")
    conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) + 1}")
    with pytest.raises(db.SchemaTooNew):
        db.migrate(conn)


def test_transaction_rolls_back_on_error():
    conn = db.open_db(":memory:")
    with pytest.raises(RuntimeError):
        with db.transaction(conn) as c:
            c.execute("INSERT INTO library_roots (path) VALUES ('a')")
            raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM library_roots").fetchone()[0] == 0


def test_migrate_from_v1_keeps_records(tmp_path):
    """版 1(フェーズ 1)の DB に整理処理の表を足しても、蔵書レコード・読書位置はそのまま残る。"""
    path = tmp_path / "old.db"
    conn = db.connect(path)
    with db.transaction(conn) as c:
        for statement in db.MIGRATIONS[0]:
            c.execute(statement)
        c.execute("PRAGMA user_version = 1")
        c.execute("INSERT INTO library_roots (id, path) VALUES (1, 'E:/Books')")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind) VALUES (1, 1, 'a.zip', 'archive')")
        c.execute("""INSERT INTO volumes (id, source_id, title, created_at, updated_at)
                     VALUES (1, 1, 'a', 'now', 'now')""")
        c.execute("INSERT INTO reading_progress (volume_id, page, updated_at) VALUES (1, 3, 'now')")
    conn.close()

    conn = db.open_db(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"organize_ops", "organize_steps", "organize_volumes"} <= tables
    row = conn.execute("SELECT v.title, v.organized_op_id, p.page FROM volumes v "
                       "JOIN reading_progress p ON p.volume_id = v.id").fetchone()
    assert tuple(row) == ("a", None, 3)
