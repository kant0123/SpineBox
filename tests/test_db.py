import sqlite3

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


def test_migration_2_keeps_existing_volumes_and_progress(tmp_path):
    """#30 の移行: 版 1 の DB(フェーズ 1 のスキーマ)にある巻・読書位置がそのまま残り、新しい列は NULL。"""
    path = tmp_path / "old.db"
    conn = db.connect(path)
    with db.transaction(conn) as c:
        for statement in db.MIGRATIONS[0]:
            c.execute(statement)
        c.execute("PRAGMA user_version = 1")
        c.execute("INSERT INTO library_roots (id, path, online) VALUES (1, 'E:/Books', 1)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, size, mtime_ns, scan_version) "
                  "VALUES (1, 1, 'a/v01-03.7z', 'archive', 10, 20, 1)")
        c.execute("INSERT INTO volumes (id, source_id, container_path, entry_prefix, title, created_at, updated_at) "
                  "VALUES (7, 1, 'v01-03.rar', '', 'v01-03', 't', 't')")
        c.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (7, 5, 0, 't')")
    conn.close()

    conn = db.open_db(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    row = conn.execute("SELECT * FROM volumes WHERE id = 7").fetchone()
    assert row["title"] == "v01-03" and row["container_path"] == "v01-03.rar"
    assert all(row[k] is None for k in ("parent_id", "inner_path", "number_kind", "number_start", "number_end",
                                        "number_evidence", "analyzed_version", "analysis_error"))
    assert tuple(conn.execute("SELECT page, finished FROM reading_progress WHERE volume_id = 7").fetchone()) == (5, 0)
    # 子の巻は親と同じ (source, container, prefix) でも inner_path が違えば入る。同じ inner_path は重複として弾く
    insert = ("INSERT INTO volumes (source_id, container_path, entry_prefix, inner_path, parent_id, title, created_at,"
              " updated_at) VALUES (1, 'v01-03.rar', '', ?, 7, 'x', 't', 't')")
    conn.execute(insert, ("[]",))
    conn.execute(insert, ('["v01.zip"]',))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ('["v01.zip"]',))
    conn.close()
