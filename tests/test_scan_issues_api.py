"""管理「読めなかったもの」API (/api/scan-issues) のテスト(#364)。"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from spinebox import db
from spinebox.app import create_app
from spinebox.config import InboxSetting, Settings
from spinebox.scanner import list_all_issues


def _setup_data(settings: Settings) -> None:
    conn = db.open_db(settings.db_path)
    with db.transaction(conn) as c:
        c.execute("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (1, 'E:/Books', 1, 1, 0)")
        c.execute("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (2, 'F:/Removed', 1, 0, 0)")
        c.execute("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (3, 'E:/Inbox', 1, 1, 1)")

        # active / offline / missing / disabled / inbox の各ソース
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (1, 1, 'a/broken.zip', 'archive', 0, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (2, 1, 'b/unplugged.rar', 'archive', 1, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (3, 1, 'c/deleted.cbz', 'archive', 0, 1)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (4, 2, 'd/disabled.zip', 'archive', 0, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (5, 3, 'e/intake.zip', 'archive', 0, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (6, 1, 'f/nested.7z', 'archive', 0, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind, offline, missing) VALUES (7, 1, 'g/old_nested.7z', 'archive', 0, 0)")

        # scan_issues (source_id IS NULL の通常パス・inbox パス含む)
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(1, NULL, '../evil.jpg', '親ディレクトリ参照', '2026-11-20T05:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(NULL, NULL, 'E:/Books/locked', '読めないフォルダ: PermissionError', '2026-11-20T04:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(NULL, NULL, 'E:/Inbox/locked', '読めないフォルダ: PermissionError', '2026-11-20T03:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(2, NULL, NULL, '未対応の形式', '2026-11-19T10:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(3, NULL, NULL, '展開上限超過', '2026-11-18T10:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(4, NULL, NULL, '壊れた ZIP', '2026-11-17T10:00:00Z')"
        )
        c.execute(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES "
            "(5, NULL, NULL, '巻にできるものが無い', '2026-11-16T10:00:00Z')"
        )

        # volumes.analysis_error (有効 1 件 + missing 1 件)
        c.execute(
            "INSERT INTO volumes (id, source_id, container_path, entry_prefix, title, missing, analysis_error, created_at, updated_at) "
            "VALUES (10, 6, 'v01.rar', '', 'v01', 0, '7-Zip CLI が必要です', '2026-11-15T00:00:00Z', '2026-11-20T02:00:00Z')"
        )
        c.execute(
            "INSERT INTO volumes (id, source_id, container_path, entry_prefix, title, missing, analysis_error, created_at, updated_at) "
            "VALUES (11, 7, 'v02.rar', '', 'v02', 1, '古い解析エラー', '2026-11-14T00:00:00Z', '2026-11-15T02:00:00Z')"
        )
    conn.close()


def test_list_all_issues_covers_all_statuses_and_matches_stats(tmp_path):
    settings = Settings(
        library_roots=(tmp_path / "books",),
        data_dir=tmp_path / "data",
        inboxes=(InboxSetting(path=Path("E:/Inbox"), library="main"),),
    )
    _setup_data(settings)
    client = TestClient(create_app(settings, frontend_dist=None))

    res = client.get("/api/scan-issues")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 9
    assert body["counts"] == {"all": 9, "active": 2, "analysis": 1, "inactive": 4, "inbox": 2}
    assert sum(body["counts"][k] for k in ("active", "analysis", "inactive", "inbox")) == body["counts"]["all"]

    # 統計の scan_issues 件数は active の scan 件数と一致する
    stats_body = client.get("/api/stats").json()
    assert stats_body["scan_issues"] == body["counts"]["active"]

    # recorded_at 降順に並んでいる
    times = [item["recorded_at"] for item in body["items"]]
    assert times == sorted(times, reverse=True)
    # entry_name が source_id=NULL の行では None になる
    assert body["items"][1]["path"] == "E:/Books/locked" and body["items"][1]["entry_name"] is None


def test_list_all_issues_filters_scope_and_query(tmp_path):
    settings = Settings(
        library_roots=(tmp_path / "books",),
        data_dir=tmp_path / "data",
        inboxes=(InboxSetting(path=Path("E:/Inbox"), library="main"),),
    )
    _setup_data(settings)
    client = TestClient(create_app(settings, frontend_dist=None))

    assert [i["status"] for i in client.get("/api/scan-issues?scope=active").json()["items"]] == ["active", "active"]
    analysis = client.get("/api/scan-issues?scope=analysis").json()["items"]
    assert len(analysis) == 1 and analysis[0]["kind"] == "analysis" and analysis[0]["container_path"] == "v01.rar"

    inactive = client.get("/api/scan-issues?scope=inactive").json()["items"]
    assert {i["status"] for i in inactive} == {"offline", "missing", "disabled"}
    assert len(inactive) == 4  # offline, missing, disabled, missing volume analysis_error

    inbox = client.get("/api/scan-issues?scope=inbox").json()["items"]
    assert len(inbox) == 2 and all(i["status"] == "inbox" for i in inbox)

    # 空白区切り AND 検索(path / container_path / entry_name / reason をまたいで大文字小文字無視)
    searched = client.get("/api/scan-issues", params={"q": "broken.zip ../evil"}).json()
    assert searched["total"] == 1
    assert searched["items"][0]["path"] == "E:/Books/a/broken.zip"
    assert searched["counts"]["all"] == 9  # 全体件数は維持される

    searched_analysis = client.get("/api/scan-issues", params={"q": "V01.RAR 7-zip"}).json()
    assert searched_analysis["total"] == 1 and searched_analysis["items"][0]["kind"] == "analysis"


def test_list_all_issues_pagination_over_200(tmp_path):
    settings = Settings(library_roots=(), data_dir=tmp_path / "data")
    conn = db.open_db(settings.db_path)
    with db.transaction(conn) as c:
        c.execute("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (1, 'E:/Books', 1, 1, 0)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind) VALUES (1, 1, 'pack.zip', 'archive')")
        c.executemany(
            "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES (?, NULL, ?, ?, ?)",
            [(1, f"e{i:03d}.jpg", f"不正な名前 {i}", f"2026-11-20T00:{i // 60:02d}:{i % 60:02d}Z") for i in range(210)],
        )
    conn.close()

    client = TestClient(create_app(settings, frontend_dist=None))
    p1 = client.get("/api/scan-issues?limit=100&offset=0").json()
    p2 = client.get("/api/scan-issues?limit=100&offset=100").json()
    p3 = client.get("/api/scan-issues?limit=100&offset=200").json()
    assert p1["total"] == 210 and len(p1["items"]) == 100
    assert len(p2["items"]) == 100 and len(p3["items"]) == 10
    ids = [x["id"] for x in p1["items"] + p2["items"] + p3["items"]]
    assert len(set(ids)) == 210


def test_list_all_issues_rejects_invalid_params(tmp_path):
    settings = Settings(library_roots=(), data_dir=tmp_path / "data")
    db.open_db(settings.db_path).close()
    client = TestClient(create_app(settings, frontend_dist=None))
    assert client.get("/api/scan-issues?scope=unknown").status_code == 422
    assert client.get("/api/scan-issues?limit=0").status_code == 422
    assert client.get("/api/scan-issues?limit=501").status_code == 422
    assert client.get("/api/scan-issues?offset=-1").status_code == 422


def test_list_all_issues_query_count_is_constant():
    def measure(n: int) -> list[str]:
        conn = db.open_db(":memory:")
        with db.transaction(conn) as c:
            c.execute("INSERT INTO library_roots (id, path, online, enabled, inbox) VALUES (1, 'E:/Books', 1, 1, 0)")
            c.execute("INSERT INTO sources (id, root_id, rel_path, kind) VALUES (1, 1, 'a.zip', 'archive')")
            c.executemany(
                "INSERT INTO scan_issues (source_id, container_path, entry_name, reason, recorded_at) VALUES (1, NULL, ?, 'bad', 'x')",
                [(f"p{i}",) for i in range(n)],
            )
        sqls: list[str] = []
        conn.set_trace_callback(sqls.append)
        list_all_issues(conn)
        conn.set_trace_callback(None)
        conn.close()
        return sqls

    small = measure(10)
    large = measure(100)
    assert len(small) == len(large) == 2
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in large)
