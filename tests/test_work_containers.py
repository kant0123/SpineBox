"""1 作品だけを収めた外側のフォルダ・アーカイブの名前による割り当てと、本の断片の除外(全体同期の名寄せ)。"""
import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, write_zip
from spinebox import db
from spinebox.app import create_app


def cbz(lib, rel, pages=3):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_zip(path, [(f"{i:03d}.jpg", page_bytes(f"{rel}/{i}")) for i in range(pages)])


def archive(lib, rel, folders):
    """folders: {アーカイブ内のフォルダ名: ページ数}"""
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    write_zip(path, [(f"{d}/{i:03d}.jpg", page_bytes(f"{rel}/{d}/{i}")) for d, n in folders.items() for i in range(n)])


@pytest.fixture
def env(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()

    def scan():
        settings = make_settings(lib)
        client = TestClient(create_app(settings, frontend_dist=None))
        client.post("/api/scan")
        return settings, client
    return lib, scan


def titles(client):
    return sorted((w["title"], w["volume_count"]) for w in client.get("/api/works").json()["works"])


def test_romaji_and_japanese_volumes_in_a_japanese_folder_become_one_japanese_work(env):
    lib, scan = env
    cbz(lib, "固有スキル「奴隷図鑑」/Koyuu Skill v01.cbz")
    cbz(lib, "固有スキル「奴隷図鑑」/Koyuu Skill v02.cbz")
    cbz(lib, "固有スキル「奴隷図鑑」/[作者] 固有スキル「奴隷図鑑」 第03巻.cbz")
    settings, client = scan()
    assert titles(client) == [("固有スキル「奴隷図鑑」", 3)]
    work = client.get("/api/works").json()["works"][0]
    detail = client.get(f"/api/works/{work['id']}").json()
    assert "Koyuu Skill" in [a["alias"] for a in detail["aliases"]]  # ローマ字は別名に残る


def test_chapter_folders_and_fragments_in_a_romaji_archive_join_the_japanese_work(env):
    lib, scan = env
    archive(lib, "DLRAW.TO_Zombie_100 vol 01-02.zip", {
        "DLRAW.TO_[作者] ゾン100 第01巻": 20,
        "DLRAW.TO_[作者] ゾン100 第02巻/DLRAW.TO_第4話 CAオブザデッド(前編)": 10,
        "DLRAW.TO_[作者] ゾン100 第02巻/DLRAW.TO_特別編:ショークラブオブザデッド": 4,
        "DLRAW.TO_[作者] ゾン100 第02巻/DLRAW.TO_CONTENTAS": 2,
        "DLRAW.TO_[作者] ゾン100 第02巻/DLRAW.TO_c": 3,
    })
    settings, client = scan()
    assert titles(client) == [("ゾン100", 3)]  # 目次(CONTENTAS)と 1 文字の束(c)は巻にしない
    conn = db.connect(settings.db_path)
    try:
        reasons = [r[0] for r in conn.execute("SELECT reason FROM scan_issues")]
    finally:
        conn.close()
    assert sum(r.startswith("登録不要") for r in reasons) == 2


def test_a_fragment_alone_is_still_a_volume(env):
    lib, scan = env
    archive(lib, "Contents.zip", {"CONTENTS": 3})
    _, client = scan()
    assert len(client.get("/api/volumes").json()) == 1


def test_a_category_folder_does_not_merge_different_works(env):
    lib, scan = env
    cbz(lib, "小説/ようこそ実力至上主義の教室へ 第01巻.cbz")
    cbz(lib, "小説/転生したらスライムだった件 第01巻.cbz")
    cbz(lib, "小説/Koyuu Skill v01.cbz")
    _, client = scan()
    assert [t for t, _ in titles(client)] == ["Koyuu Skill", "ようこそ実力至上主義の教室へ", "転生したらスライムだった件"]


def test_two_romaji_series_under_one_japanese_folder_stay_apart(env):
    lib, scan = env
    cbz(lib, "技術本/Ki Hon Jou Hou v01.cbz")
    cbz(lib, "技術本/Ou You Jou Hou v01.cbz")
    _, client = scan()
    assert [t for t, _ in titles(client)] == ["Ki Hon Jou Hou", "Ou You Jou Hou"]


def test_a_short_folder_name_does_not_swallow_titles_that_only_start_with_it(env):
    lib, scan = env
    cbz(lib, "ゾンビ/ゾンビだらけの世界 第01巻.cbz")
    cbz(lib, "ゾンビ/ゾンビのあふれた世界 第01巻.cbz")
    _, client = scan()
    assert [t for t, _ in titles(client)] == ["ゾンビだらけの世界", "ゾンビのあふれた世界"]


def test_numbered_and_subtitled_volumes_in_a_work_folder_join_it(env):
    lib, scan = env
    cbz(lib, "オーバーロード/オーバーロード 1 不死者の王.cbz")
    cbz(lib, "オーバーロード/オーバーロード 2 漆黒の戦士.cbz")
    cbz(lib, "オーバーロード/アニメ「オーバーロード」完全設定資料集.cbz")
    cbz(lib, "植物魔法チート/[作者] 植物魔法チート 前世の知識を駆使して 第01巻.cbz")
    cbz(lib, "植物魔法チート/Shokubutsu Maho v02.cbz")
    _, client = scan()
    assert titles(client) == [("アニメ「オーバーロード」完全設定資料集", 1), ("オーバーロード", 2),
                              ("植物魔法チート", 2)]


def test_the_container_claim_does_not_move_manual_decisions_and_is_idempotent(env):
    lib, scan = env
    cbz(lib, "野人転生/Yajin Tensei v01.cbz")
    cbz(lib, "野人転生/Yajin Tensei v02.cbz")
    settings, client = scan()
    wid = client.get("/api/works").json()["works"][0]["id"]
    assert client.patch(f"/api/works/{wid}", json={"title": "手で付けた名前"}).status_code == 200
    client.post("/api/scan")
    client.post("/api/scan")
    assert titles(client) == [("手で付けた名前", 2)]


def test_an_emptied_work_never_absorbs_a_work_with_a_manual_decision(env):
    from spinebox import works
    lib, scan = env
    cbz(lib, "野人転生/Yajin Tensei v01.cbz")
    cbz(lib, "野人転生/Yajin Tensei v02.cbz")
    settings, client = scan()
    wid = client.get("/api/works").json()["works"][0]["id"]
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:  # 利用者が選んだ公開 DB の連携(手動の決定。作品名は名前由来のまま)
            c.execute("INSERT INTO work_links (work_id, source, status, source_id, manual) "
                      "VALUES (?, 'mangaupdates', 'linked', 'x', 1)", (wid,))
    finally:
        conn.close()
    # 前の同期で巻を持っていた、公開 DB の日本語の作品名の作品(今は空)
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            old = works._new_work(c, "野人転生", source=works.SOURCE_PUBLIC_DB, manual=False, confidence=1.0,
                                  evidence=None)
            st = works._State(c)
            works._settle_claims(c, st, {}, {old: {wid}}, works.SyncSummary())
        assert conn.execute("SELECT merged_into FROM works WHERE id = ?", (wid,)).fetchone()[0] is None
        assert conn.execute("SELECT merged_into FROM works WHERE id = ?", (old,)).fetchone()[0] == wid
    finally:
        conn.close()


def test_a_fragment_next_to_inner_archives_is_skipped(env):
    """外側のアーカイブに目次のフォルダと内側アーカイブだけがあるときも、目次は巻にしない(内側アーカイブもほかの巻)。"""
    import io
    import zipfile
    lib, scan = env
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        for i in range(3):
            z.writestr(f"{i:03d}.jpg", page_bytes(f"inner/{i}"))
    write_zip(lib / "Series.zip", [*[(f"CONTENTS/{i:03d}.jpg", page_bytes(f"c/{i}")) for i in range(3)],
                                   ("Series v01.zip", inner.getvalue())])
    _, client = scan()
    assert [v["title"] for v in client.get("/api/volumes").json()] == ["Series v01"]


def test_a_longer_folder_name_does_not_swallow_a_shorter_different_title(env):
    lib, scan = env
    cbz(lib, "魔法使いの嫁/[作者] 魔法使いの嫁 第01巻.cbz")
    cbz(lib, "魔法使いの嫁/魔法使い 第01巻.cbz")
    cbz(lib, "フシノカミ ~辺境から始める文明再生記~/フシノカミ 第01巻.cbz")
    _, client = scan()
    assert [t for t, _ in titles(client)] == ["フシノカミ ~辺境から始める文明再生記", "魔法使い", "魔法使いの嫁"]


def test_settling_claims_does_not_query_per_volume(env):
    from spinebox import db as sdb, works
    lib, scan = env
    for n in range(1, 31):
        cbz(lib, f"固有スキル/Koyuu Skill v{n:02d}.cbz", pages=1)
    settings, _ = scan()
    conn = sdb.connect(settings.db_path)
    statements: list[str] = []
    try:
        conn.set_trace_callback(statements.append)
        works.sync(conn)
    finally:
        conn.close()
    per_volume = [s for s in statements if "FROM work_volumes WHERE volume_id" in s]
    assert len(per_volume) < 5  # 巻の数(30)に比例しない
