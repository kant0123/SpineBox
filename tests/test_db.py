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
