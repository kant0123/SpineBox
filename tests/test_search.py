"""作品の全文検索と絞り込み(#56)。蔵書は一時ディレクトリに動的生成した合成データ。"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
from novels import epub_bytes
from spinebox import db, search, works
from spinebox.app import create_app

OKIRAKU_JA = "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第01巻.cbz"
OKIRAKU_ROMAJI = "Okiraku ryoshu no tanoshi ryochi v01.cbz"
KAKUKAKU = "[東村アキコ] かくかくしかじか 第01巻.cbz"
OTHER = "Other story v01.cbz"


def cbz(lib, name, pages=3):
    write_zip(lib / name, [(f"{i:03d}.jpg", page_bytes(f"{name}/{i}")) for i in range(pages)])


@pytest.fixture
def env(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for f in (OKIRAKU_JA, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第02巻.cbz", OKIRAKU_ROMAJI, KAKUKAKU,
              "[東村アキコ] かくかくしかじか 第02巻.cbz", OTHER):
        cbz(lib, f)
    settings = make_settings(lib)
    client = TestClient(create_app(settings, frontend_dist=None))
    client.post("/api/scan")
    return settings, client


def titles(client, **params):
    r = client.get("/api/works", params=params)
    assert r.status_code == 200, r.text
    return sorted(w["title"] for w in r.json()["works"])


def work_id(client, title):
    return next(w["id"] for w in client.get("/api/works").json()["works"] if w["title"] == title)


def vid(client, title):
    return next(v["id"] for v in client.get("/api/volumes").json() if v["title"] == title)


JA = "お気楽領主の楽しい領地防衛"
ROMAJI = "Okiraku ryoshu no tanoshi ryochi"
KAKU = "かくかくしかじか"


# --- 受け入れ基準の語 ------------------------------------------------------------------------

def test_acceptance_terms_find_the_work_including_alias_and_author(env):
    settings, client = env
    assert titles(client, q="お気楽") == [JA]
    assert titles(client, q="領地防衛") == [JA]
    assert titles(client, q="Okiraku") == [ROMAJI]
    assert titles(client, q="かくかく") == [KAKU]
    assert titles(client, q="東村") == [KAKU]  # 著者経由・2 文字
    # ローマ字の作品を日本語の作品へ統合すると、ローマ字名(別名になる)でも日本語の作品が出る
    client.post(f"/api/works/{work_id(client, JA)}/merge", json={"other_id": work_id(client, ROMAJI)})
    assert titles(client, q="Okiraku") == [JA]
    assert titles(client, q="tanoshi ryochi") == [JA]


def test_short_terms_use_like_and_still_match(env):
    _, client = env
    assert titles(client, q="領主") == [JA]   # 2 文字
    assert titles(client, q="楽") == [JA]     # 1 文字
    assert titles(client, q="宗") == [JA]     # 著者の 1 文字
    assert titles(client, q="zz") == []


def test_normalization_width_case_and_kana(env):
    _, client = env
    assert titles(client, q="ＯＫＩＲＡＫＵ") == [ROMAJI]   # 全角英字
    assert titles(client, q="okiraku") == [ROMAJI]          # 大小文字
    assert titles(client, q="カクカク") == [KAKU]            # カタカナ → ひらがな
    assert titles(client, q="ｶｸｶｸ") == [KAKU]              # 半角カタカナ
    assert titles(client, q="ryoshu_no") == [ROMAJI]         # 記号・空白は落とす
    assert titles(client, q="・・") == titles(client)         # 正規化で空になる語は絞らない


def test_multiple_terms_are_and(env):
    _, client = env
    assert titles(client, q="お気楽 防衛") == [JA]
    assert titles(client, q="お気楽　防衛") == [JA]  # 全角の空白も区切り
    assert titles(client, q="お気楽 東村") == []
    assert titles(client, q="第01巻") == sorted([JA, KAKU])  # 巻の名前も索引にある


def test_empty_result_reports_total(env):
    _, client = env
    body = client.get("/api/works", params={"q": "存在しない作品"}).json()
    assert body["works"] == [] and body["total"] == 4


# --- 索引の追従 ------------------------------------------------------------------------------

def test_manual_edits_are_reflected_immediately(env):
    _, client = env
    wid = work_id(client, KAKU)
    client.patch(f"/api/works/{wid}", json={"title": "描け描け", "author": "別の著者"})
    assert titles(client, q="描け描け") == ["描け描け"]
    assert titles(client, q="別の著者") == ["描け描け"]
    assert titles(client, author="東村アキコ") == []  # 著者を直したので前の著者では絞れない
    assert titles(client, author="別の著者") == ["描け描け"]
    assert titles(client, q="東村") == ["描け描け"]  # 巻の名前(`[東村アキコ] …`)は索引に残る
    assert titles(client, q="かくかく") == ["描け描け"]  # 直す前の作品名は別名に残る(名寄せの規則)

    detail = client.post(f"/api/works/{wid}/aliases", json={"alias": "Kakukaku Shikajika"}).json()
    assert titles(client, q="shikajika") == ["描け描け"]
    alias_id = next(a["id"] for a in detail["aliases"] if a["alias"] == "Kakukaku Shikajika")
    client.delete(f"/api/works/{wid}/aliases/{alias_id}")
    assert titles(client, q="shikajika") == []  # 外した別名は引かない


def test_reassigned_volume_moves_its_name_to_the_new_work(env):
    _, client = env
    client.post("/api/works/assign", json={"volume_ids": [vid(client, "Other story v01")], "work_id": work_id(client, KAKU)})
    assert titles(client, q="other") == [KAKU]


def test_volume_title_change_and_merge_mark_the_index_dirty(env):
    settings, client = env
    client.get("/api/works")  # 索引を追いつかせる
    conn = db.connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM work_search_dirty").fetchone()[0] == 0
        v = vid(client, "Other story v01")
        with db.transaction(conn) as c:
            c.execute("UPDATE volumes SET title = 'Renamed thing v01' WHERE id = ?", (v,))
        assert conn.execute("SELECT COUNT(*) FROM work_search_dirty").fetchone()[0] == 1
    finally:
        conn.close()
    assert titles(client, q="renamed") == ["Other story"]
    keep, drop = work_id(client, JA), work_id(client, ROMAJI)
    client.post(f"/api/works/{keep}/merge", json={"other_id": drop})
    client.get("/api/works")
    conn = db.connect(settings.db_path)
    try:
        ids = {r[0] for r in conn.execute("SELECT rowid FROM work_search")}
        assert keep in ids and drop not in ids  # 統合された作品の行は消える
    finally:
        conn.close()


def test_rebuild_matches_incremental_index(env):
    settings, client = env
    client.get("/api/works")
    conn = db.open_db(settings.db_path)
    try:
        before = sorted(tuple(r) for r in conn.execute("SELECT rowid, body FROM work_search"))
        assert search.rebuild(conn) == 4
        assert sorted(tuple(r) for r in conn.execute("SELECT rowid, body FROM work_search")) == before
        assert search.uses_fts(conn)
    finally:
        conn.close()


def test_without_trigram_falls_back_to_like_with_the_same_results(env, monkeypatch):
    settings, client = env
    expected = {q: titles(client, q=q) for q in ("お気楽", "東村", "Okiraku", "楽", "お気楽 防衛")}
    monkeypatch.setattr(search, "trigram_available", lambda c: False)
    conn = db.open_db(settings.db_path)
    try:
        search.rebuild(conn)
        assert not search.uses_fts(conn)
    finally:
        conn.close()
    assert {q: titles(client, q=q) for q in expected} == expected
    wid = work_id(client, KAKU)
    client.patch(f"/api/works/{wid}", json={"title": "描け描け"})
    assert titles(client, q="描け描け") == ["描け描け"]  # 普通の表でも追従する


def test_trigram_is_available_in_this_sqlite():
    conn = db.connect(":memory:")
    assert search.trigram_available(conn)  # CI・サーバー機の Python 3.12 は SQLite 3.34 以上を同梱する


def test_rebuild_command(env, monkeypatch, capsys):
    from spinebox import __main__ as entry

    settings, _ = env
    monkeypatch.setattr(entry, "load_settings", lambda: settings)
    assert entry.main(["--rebuild-search"]) == 0
    assert "4 作品" in capsys.readouterr().out


# --- 絞り込み --------------------------------------------------------------------------------

def test_reading_state_and_author_filters_combine_with_query(env):
    _, client = env
    first = vid(client, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第01巻")
    second = vid(client, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第02巻")
    k1 = vid(client, "[東村アキコ] かくかくしかじか 第01巻")
    k2 = vid(client, "[東村アキコ] かくかくしかじか 第02巻")
    client.put(f"/api/volumes/{first}/progress", json={"page": 1, "finished": False})
    for v in (k1, k2):
        client.put(f"/api/volumes/{v}/progress", json={"page": 2, "finished": True})

    states = {w["title"]: w["reading_state"] for w in client.get("/api/works").json()["works"]}
    assert states == {JA: "reading", ROMAJI: "unread", KAKU: "finished", "Other story": "unread"}
    assert titles(client, status="reading") == [JA]
    assert titles(client, status="finished") == [KAKU]
    assert titles(client, status="unread") == sorted([ROMAJI, "Other story"])
    assert titles(client, status="unread", q="okiraku") == [ROMAJI]
    assert titles(client, status="reading", q="okiraku") == []

    assert titles(client, author="東村アキコ") == [KAKU]
    assert titles(client, author="赤池宗") == [JA]          # 複数著者の 1 人
    assert titles(client, author="赤池") == []              # 著者の絞り込みは名前の一致(部分一致は q で)
    assert titles(client, author="赤池宗", q="かくかく") == []
    assert titles(client, author="赤池宗", status="reading", q="防衛") == [JA]

    client.put(f"/api/volumes/{second}/progress", json={"page": 2, "finished": True})
    client.put(f"/api/volumes/{first}/progress", json={"page": 2, "finished": True})
    assert titles(client, status="finished") == sorted([JA, KAKU])


def test_facets_list_authors_from_all_listed_works(env):
    _, client = env
    body = client.get("/api/works", params={"q": "かくかく"}).json()
    assert body["facets"]["author"] == sorted(["東村アキコ", "青色まろ", "赤池宗"])  # 絞る前の作品から
    assert body["facets"]["kind"] == body["facets"]["genre"] == body["facets"]["tag"] == []


def test_invalid_filters_are_422(env):
    _, client = env
    assert client.get("/api/works", params={"status": "done"}).status_code == 422
    with pytest.raises(search.InvalidFilter):
        search.check_filters({"label": "x"})


def test_kind_genre_tag_filters_use_the_public_db_fields(env):
    """種別・ジャンル・タグ。公開 DB の結線(#50)が work_fields に足す行(kind / genres / tags。配列は JSON)で、
    絞り込みと検索が効く。"""
    settings, client = env
    wid = work_id(client, KAKU)
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            for field, value in (("kind", "manga"), ("genres", '["エッセイ", "コメディ"]'), ("tags", '["美大", "宮崎"]'),
                                 ("publisher", "集英社"), ("total_volumes", "5"), ("synopsis", "漫画家の半生")):
                c.execute("""INSERT INTO work_fields (work_id, field, value, source, manual, updated_at)
                             VALUES (?, ?, ?, 'public_db', 0, ?)""", (wid, field, value, db.now()))
    finally:
        conn.close()
    assert titles(client, kind="manga") == [KAKU]
    assert titles(client, kind="漫画") == [KAKU]     # 日本語の表記でも同じ
    assert titles(client, kind="novel") == [] and titles(client, kind="小説") == []
    assert titles(client, genre="コメディ") == [KAKU]
    assert titles(client, genre="ｺﾒﾃﾞｨ") == [KAKU]  # 値の比較も同じ正規化
    assert titles(client, genre="エッセイ、コメディ") == []  # 配列は 1 要素ずつ。つないだ文字列は 1 つの値ではない
    assert titles(client, tag="美大", status="unread") == [KAKU]
    assert titles(client, tag="美大", genre="コメディ", kind="manga", author="東村アキコ") == [KAKU]
    assert titles(client, tag="美大", genre="ホラー") == []
    assert titles(client, q="集英社") == [KAKU]      # 項目は索引に入る
    assert titles(client, q="エッセイ") == [KAKU]
    assert titles(client, q="漫画", tag="宮崎") == [KAKU]  # 種別の日本語表記でも引ける
    assert titles(client, q="半生") == []            # 廃止したあらすじ(#82)の行は残っていても索引に入れない
    assert titles(client, q="大宮") == []            # 配列の要素をまたいで語が当たらない(「美大」「宮崎」)
    assert titles(client, q="5") == []               # 全巻数の数値は索引に入れない
    facets = client.get("/api/works").json()["facets"]
    assert facets["kind"] == ["manga"] and facets["genre"] == ["エッセイ", "コメディ"]
    assert facets["tag"] == ["宮崎", "美大"]


def test_fields_edited_through_the_work_api_are_searchable_and_filterable(env):
    """#50 の書き込み経路(PATCH /api/works/{id}。公開 DB の取得 apply_work も同じ work_fields に書く)で足した
    種別・ジャンル・タグ・出版社が、手を加えずに検索と絞り込みへ追従する。"""
    settings, client = env
    wid = work_id(client, KAKU)
    assert titles(client, genre="コメディ") == []
    r = client.patch(f"/api/works/{wid}", json={"kind": "manga", "genres": ["エッセイ", "コメディ"], "tags": ["美大"],
                                                 "publisher": "集英社"})
    assert r.status_code == 200, r.text
    assert titles(client, genre="コメディ") == [KAKU]
    assert titles(client, tag="美大", kind="漫画", author="東村アキコ") == [KAKU]
    assert titles(client, q="集英社 エッセイ") == [KAKU]
    assert titles(client, q="エッセイ", tag="なし") == []
    facets = client.get("/api/works").json()["facets"]
    assert facets["kind"] == ["manga"] and facets["genre"] == ["エッセイ", "コメディ"] and facets["tag"] == ["美大"]
    # 手で空にすると索引からも絞り込みからも消える
    assert client.patch(f"/api/works/{wid}", json={"genres": []}).status_code == 200
    assert titles(client, genre="コメディ") == [] and titles(client, q="コメディ") == []


def test_novel_volumes_are_searchable_by_name_and_filter_by_kind(env):
    """小説(EPUB・テキスト。#57)の巻も作品になり、作品名・巻の名前で引ける。種別を手で足せば小説で絞れる。"""
    settings, client = env
    lib = settings.library_roots[0]
    (lib / "[夏目漱石] 吾輩は猫である 第1巻.epub").write_bytes(epub_bytes(["<p>吾輩は猫である。</p>"]))
    (lib / "[夏目漱石] 吾輩は猫である 第2巻.txt").write_bytes("名前はまだ無い。".encode("utf-8"))
    assert client.post("/api/scan").status_code == 200
    assert titles(client, q="吾輩") == ["吾輩は猫である"]
    assert titles(client, q="夏目") == ["吾輩は猫である"]      # 著者
    assert titles(client, q="第2巻") == ["吾輩は猫である"]     # 巻の名前(txt)
    wid = work_id(client, "吾輩は猫である")
    assert client.patch(f"/api/works/{wid}", json={"kind": "novel"}).status_code == 200
    assert titles(client, kind="小説") == ["吾輩は猫である"]
    assert titles(client, kind="漫画") == []


def test_split_values_reads_json_arrays_and_separated_strings():
    assert search.split_values('["a", " b ", "", 3]') == ["a", "b"]
    assert search.split_values("a、b, c") == ["a", "b", "c"]
    assert search.split_values("[壊れた JSON") == ["[壊れた JSON"]  # 配列として読めなければ区切りで分ける
    assert search.split_values(None) == []


def test_list_works_function_without_api(env):
    settings, _ = env
    conn = db.open_db(settings.db_path)
    try:
        res = works.list_works(conn, "東村", {"status": "unread"})
        assert [w["title"] for w in res["works"]] == [KAKU] and res["total"] == 4
    finally:
        conn.close()


# --- 移行 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("base", [6, 7, 8])
def test_migration_9_from_a_works_db_builds_the_index_on_first_use(tmp_path, base):
    """#56 の移行(版 9): 作品まで(版 6)・公開 DB の結線まで(版 7)・小説まで(版 8)の DB に検索の印とトリガーを足し、
    最初の一覧で索引を作る。作品・巻は変わらない。"""
    path = tmp_path / f"v{base}.db"
    conn = db.connect(path)
    with db.transaction(conn) as c:
        for migration in db.MIGRATIONS[:base]:
            for statement in migration:
                c.execute(statement)
        c.execute(f"PRAGMA user_version = {base}")
        c.execute("INSERT INTO library_roots (id, path, online) VALUES (1, 'E:/Books', 1)")
        c.execute("INSERT INTO sources (id, root_id, rel_path, kind) VALUES (1, 1, 'a.cbz', 'archive')")
        c.execute("INSERT INTO volumes (id, source_id, container_path, entry_prefix, title, created_at, updated_at) "
                  "VALUES (7, 1, NULL, '', 'お気楽 第01巻', 't', 't')")
        c.execute("INSERT INTO works (id, created_at, updated_at) VALUES (3, 't', 't')")
        c.execute("INSERT INTO work_fields (work_id, field, value, source, manual, updated_at) "
                  "VALUES (3, 'title', 'お気楽', 'manual', 1, 't')")
        c.execute("INSERT INTO work_volumes (volume_id, work_id, source, manual, updated_at) "
                  "VALUES (7, 3, 'filename', 0, 't')")
    conn.close()

    conn = db.open_db(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) >= 9
        assert conn.execute("SELECT COUNT(*) FROM work_search_dirty").fetchone()[0] == 0
        res = works.list_works(conn, "お気楽")
        assert [w["id"] for w in res["works"]] == [3]
        row = conn.execute("SELECT value, manual FROM work_fields WHERE work_id = 3").fetchone()
        assert tuple(row) == ("お気楽", 1)
        # 移行後の変更はトリガーで印が付く
        with db.transaction(conn) as c:
            c.execute("UPDATE work_fields SET value = '別名の作品' WHERE work_id = 3")
        assert conn.execute("SELECT work_id FROM work_search_dirty").fetchall()[0][0] == 3
        assert [w["id"] for w in works.list_works(conn, "別名の作品")["works"]] == [3]
    finally:
        conn.close()


def test_migration_11_drops_synopsis_from_the_index_but_keeps_the_rows(tmp_path):
    """#82 の移行(版 11): 版 10 の索引に入っていたあらすじを、次の検索の前に索引から外す。あらすじの行は消さない。"""
    path = tmp_path / "v10.db"
    conn = db.connect(path)
    with db.transaction(conn) as c:
        for migration in db.MIGRATIONS[:10]:
            for statement in migration:
                c.execute(statement)
        c.execute("PRAGMA user_version = 10")
        for wid, title in ((3, "お気楽"), (4, "かくかく")):
            c.execute("INSERT INTO works (id, created_at, updated_at) VALUES (?, 't', 't')", (wid,))
            c.execute("INSERT INTO work_fields (work_id, field, value, source, manual, updated_at) "
                      "VALUES (?, 'title', ?, 'filename', 0, 't')", (wid, title))
        c.execute("INSERT INTO work_fields (work_id, field, value, source, manual, updated_at) "
                  "VALUES (3, 'synopsis', '手で書いた領地のあらすじ', 'manual', 1, 't')")
        # 版 10 のコードが作った索引(あらすじを含む)
        search._create_index(c)
        c.execute(f"INSERT INTO {search.INDEX_TABLE} (rowid, body) VALUES (3, ?)",
                  ("\n".join(search.normalize(t) for t in ("お気楽", "手で書いた領地のあらすじ")),))
        c.execute(f"INSERT INTO {search.INDEX_TABLE} (rowid, body) VALUES (4, ?)", (search.normalize("かくかく"),))
        c.execute("DELETE FROM work_search_dirty")
    assert [r[0] for r in conn.execute(f"SELECT rowid FROM {search.INDEX_TABLE} WHERE body LIKE '%領地%'")] == [3]
    conn.close()

    conn = db.open_db(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS) >= 11
        # あらすじの行を持つ作品だけ印が付く
        assert [r[0] for r in conn.execute("SELECT work_id FROM work_search_dirty")] == [3]
        search.prepare(conn)  # 一覧・検索の前に呼ばれる
        assert conn.execute("SELECT COUNT(*) FROM work_search_dirty").fetchone()[0] == 0
        assert search.match_ids(conn, "領地") == set()
        assert search.match_ids(conn, "お気楽") == {3} and search.match_ids(conn, "かくかく") == {4}
        row = conn.execute("SELECT value, source, manual FROM work_fields WHERE work_id = 3 AND field = 'synopsis'")
        assert tuple(row.fetchone()) == ("手で書いた領地のあらすじ", "manual", 1)
    finally:
        conn.close()


def test_locked_database_falls_back_to_the_existing_index(env, monkeypatch):
    settings, client = env
    client.get("/api/works")

    def locked(conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(search, "prepare", locked)
    assert titles(client, q="お気楽") == [JA]  # 索引があれば古いまま引く
    conn = db.connect(settings.db_path)
    try:
        conn.execute("DROP TABLE work_search")
        with pytest.raises(sqlite3.OperationalError):
            works.list_works(conn, "お気楽")  # 索引も無ければ隠さない
    finally:
        conn.close()


# --- 並び順(#273) ----------------------------------------------------------------------------

def ordered_titles(client, **params):
    r = client.get("/api/works", params=params)
    assert r.status_code == 200, r.text
    return [w["title"] for w in r.json()["works"]]


def test_work_sort_orders_and_invalid_sort_is_422(env):
    settings, client = env
    assert client.get("/api/works", params={"sort": "unknown"}).status_code == 422

    # 既定(省略・title)は作品名の自然順
    default_order = ordered_titles(client)
    assert default_order == [ROMAJI, "Other story", JA, KAKU]
    assert ordered_titles(client, sort="title") == default_order

    v_ja1 = vid(client, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第01巻")
    v_ja2 = vid(client, "[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第02巻")
    v_k1 = vid(client, "[東村アキコ] かくかくしかじか 第01巻")
    v_k2 = vid(client, "[東村アキコ] かくかくしかじか 第02巻")
    v_rom = vid(client, "Okiraku ryoshu no tanoshi ryochi v01")
    v_oth = vid(client, "Other story v01")
    wid_rom = work_id(client, ROMAJI)

    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            # 追加日時: Other(2026-01) < JA(第2巻が 2026-02) < ROMAJI(2026-03) < KAKU(第2巻が 2026-04)
            for v, ts in ((v_oth, "2026-01-10T00:00:00+00:00"),
                          (v_ja1, "2026-01-01T00:00:00+00:00"), (v_ja2, "2026-02-01T00:00:00+00:00"),
                          (v_rom, "2026-03-01T00:00:00+00:00"),
                          (v_k1, "2026-01-05T00:00:00+00:00"), (v_k2, "2026-04-01T00:00:00+00:00")):
                c.execute("UPDATE volumes SET created_at = ? WHERE id = ?", (ts, v))
            # 読書日時: KAKU(2026-05-02) > JA(2026-05-01)。ROMAJI・Other は未読(末尾で作品名順)
            c.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 1, 0, ?)",
                      (v_ja1, "2026-05-01T10:00:00+00:00"))
            c.execute("INSERT INTO reading_progress (volume_id, page, finished, updated_at) VALUES (?, 1, 0, ?)",
                      (v_k1, "2026-05-02T10:00:00+00:00"))
            # 発売日: JA は巻の書誌で 2022-08、KAKU は 2015-03-25、ROMAJI は公開 DB 連携(work_links.item)で 2020。Other は発売日なし(末尾)
            c.execute("INSERT INTO volume_fields (volume_id, field, value, source, manual, updated_at) "
                      "VALUES (?, 'released', '2022-08', 'public_db', 0, 't')", (v_ja2,))
            c.execute("INSERT INTO volume_fields (volume_id, field, value, source, manual, updated_at) "
                      "VALUES (?, 'released', '2015-03-25', 'public_db', 0, 't')", (v_k1,))
            c.execute("INSERT INTO work_links (work_id, source, status, manual, source_id, item) "
                      "VALUES (?, 'mediaarts', 'linked', 0, 'a:1', '{\"released\": \"2020\"}')", (wid_rom,))
    finally:
        conn.close()

    assert ordered_titles(client, sort="recent_read") == [KAKU, JA, ROMAJI, "Other story"]
    assert ordered_titles(client, sort="added_desc") == [KAKU, ROMAJI, JA, "Other story"]
    assert ordered_titles(client, sort="added_asc") == ["Other story", JA, ROMAJI, KAKU]
    assert ordered_titles(client, sort="released_desc") == [JA, ROMAJI, KAKU, "Other story"]
    assert ordered_titles(client, sort="released_asc") == [KAKU, ROMAJI, JA, "Other story"]

    rand_order = ordered_titles(client, sort="random")
    assert sorted(rand_order) == sorted(default_order)

