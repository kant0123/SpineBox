"""`[metadata]` 設定と、生応答の保存テーブル(マイグレーション)。"""
import pytest

from spinebox import db
from spinebox.config import ConfigError, MetadataSettings, parse_settings
from pathlib import Path


def parse(table: dict) -> MetadataSettings:
    return parse_settings({"metadata": table} if table is not None else {}, Path.cwd()).metadata


def test_defaults():
    m = parse_settings({}, Path.cwd()).metadata
    assert m.sources == ("ndl", "mediaarts", "bangumi")
    assert (m.cache_ttl_days, m.timeout_seconds, m.max_retries) == (30.0, 10.0, 2)
    assert m.interval("ndl") == 1.0 and m.interval("mediaarts") == 1.0 and m.interval("bangumi") == 1.0
    assert m.keys == {} and m.contact == ""


def test_overrides_and_partial_interval_table():
    m = parse({"sources": ["ndl"], "cache_ttl_days": 7, "timeout_seconds": 3.5, "max_retries": 0,
               "candidate_limit": 5, "detail_limit": 1, "contact": " me@example.test ",
               "min_interval_seconds": {"mediaarts": 4, "rakuten": 0.5},
               "keys": {"rakuten_application_id": "abc"}})
    assert m.sources == ("ndl",) and m.cache_ttl_days == 7.0 and m.timeout_seconds == 3.5 and m.max_retries == 0
    assert (m.candidate_limit, m.detail_limit, m.contact) == (5, 1, "me@example.test")
    assert m.interval("mediaarts") == 4.0 and m.interval("ndl") == 1.0 and m.interval("rakuten") == 0.5  # 既定に上書き
    assert m.keys == {"rakuten_application_id": "abc"} and "abc" not in repr(m)


def test_linking_settings_defaults_and_overrides():
    m = parse(None)
    assert (m.background, m.background_interval_seconds, m.recheck_days, m.retry_hours) == (False, 120, 30.0, 6.0)
    m = parse({"background": True, "background_interval_seconds": 30, "recheck_days": 7, "retry_hours": 0.5})
    assert (m.background, m.background_interval_seconds, m.recheck_days, m.retry_hours) == (True, 30, 7.0, 0.5)


def test_japanese_display_settings_defaults_and_overrides():
    m = parse(None)
    assert m.untranslated_terms == "hide"
    m = parse({"untranslated_terms": "keep"})
    assert m.untranslated_terms == "keep"


@pytest.mark.parametrize("value", ["fold", "hide", "show", "", True])
def test_retired_english_synopsis_is_ignored_with_a_warning(value, caplog):
    """廃止した `english_synopsis`(#82)は、どんな値が残っていても起動を止めない。無視してログで知らせる。"""
    with caplog.at_level("WARNING", logger="spinebox.config"):
        m = parse({"english_synopsis": value, "untranslated_terms": "keep"})
    assert m.untranslated_terms == "keep" and not hasattr(m, "english_synopsis")
    assert len([r for r in caplog.records if "english_synopsis" in r.getMessage()]) == 1


@pytest.mark.parametrize("table", [
    {"untranslated_terms": "show"}, {"untranslated_terms": True},
])
def test_invalid_japanese_display_settings_are_rejected(table):
    with pytest.raises(ConfigError):
        parse(table)


@pytest.mark.parametrize("table", [
    {"background": "yes"}, {"background": 1}, {"background_interval_seconds": 5}, {"background_interval_seconds": 60.5},
    {"recheck_days": 0}, {"retry_hours": 0}, {"retry_hours": "6"},
])
def test_invalid_linking_settings_are_rejected(table):
    with pytest.raises(ConfigError):
        parse(table)


def test_sources_can_be_empty_to_turn_metadata_off():
    assert parse({"sources": []}).sources == ()


@pytest.mark.parametrize("table", [
    {"sources": "ndl"}, {"sources": [1]}, {"sources": [""]},
    {"cache_ttl_days": -1}, {"cache_ttl_days": "30"}, {"cache_ttl_days": True},
    {"timeout_seconds": 0}, {"max_retries": -1}, {"max_retries": 1.5}, {"candidate_limit": 0},
    {"min_interval_seconds": [1]}, {"min_interval_seconds": {"ndl": -1}}, {"min_interval_seconds": {"ndl": "1"}},
    {"keys": {"a": 1}}, {"keys": []}, {"contact": 1},
])
def test_invalid_values_are_rejected(table):
    with pytest.raises(ConfigError):
        parse(table)


def test_metadata_must_be_a_table():
    with pytest.raises(ConfigError):
        parse_settings({"metadata": "x"}, Path.cwd())


def test_example_config_parses():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "config.example.toml").read_text(encoding="utf-8"))
    assert parse_settings(data, root).metadata.sources == ("ndl", "mediaarts", "bangumi")


def test_metadata_responses_table_is_created_and_unique_per_request(tmp_path):
    conn = db.open_db(tmp_path / "m.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(metadata_responses)")}
    assert cols == {"id", "source", "request_key", "request", "status", "body", "fetched_at"}
    row = ("ndl", "k", "{}", 200, b"x", "2026-11-01T00:00:00+00:00")
    insert = "INSERT INTO metadata_responses (source, request_key, request, status, body, fetched_at) VALUES (?,?,?,?,?,?)"
    conn.execute(insert, row)
    with pytest.raises(Exception):
        conn.execute(insert, row)                       # 同じ (取得元, 要求の鍵) は 1 行
    conn.execute(insert, ("mediaarts", "k", "{}", 200, b"x", "2026-11-01T00:00:00+00:00"))


def test_store_put_overwrites_and_get_by_key(tmp_path):
    from spinebox.metadata.http import SourceRequest
    from spinebox.metadata.store import ResponseStore
    conn = db.open_db(tmp_path / "m.db")
    store = ResponseStore(conn)
    request = SourceRequest("ndl", "GET", "https://example.test/", (("title", "日本語"),))
    store.put(request, 200, b"one")
    store.put(request, 200, b"two")
    assert store.get(request).body == b"two"
    assert conn.execute("SELECT COUNT(*) FROM metadata_responses").fetchone()[0] == 1
    assert store.get_by_key("ndl", request.key()).body == b"two"
    assert store.get_by_key("mediaarts", request.key()) is None
    store.delete(request)
    assert store.get(request) is None
