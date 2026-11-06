"""作品の名寄せ(#43)。蔵書は一時ディレクトリに動的生成し、重複候補の証拠は DB に直接足す。"""
import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip
from spinebox import db, works
from spinebox.app import create_app
from spinebox.naming import normalize_title


def cbz(lib, name, pages=3):
    write_zip(lib / name, [(f"{i:03d}.jpg", page_bytes(f"{name}/{i}")) for i in range(pages)])


@pytest.fixture
def make_env(make_settings, tmp_path):
    def make(files=(), *, scan=True):
        lib = tmp_path / "lib"
        lib.mkdir(exist_ok=True)
        for f in files:
            cbz(lib, f)
        settings = make_settings(lib)
        client = TestClient(create_app(settings, frontend_dist=None))
        if scan:
            client.post("/api/scan")
        return lib, settings, client
    return make


def by_title(client):
    return {w["title"]: w for w in client.get("/api/works").json()["works"]}


def vid(client, title):
    return next(v["id"] for v in client.get("/api/volumes").json() if v["title"] == title)


def add_candidate(settings, a, b, matched, compared, rule="same_number", dismissed=False):
    a, b = sorted((a, b))
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO duplicate_candidates (volume_a, volume_b, rule, matched_pages, compared_pages,
                         evidence, computed_at, dismissed_at) VALUES (?, ?, ?, ?, ?, '{}', ?, ?)""",
                      (a, b, rule, matched, compared, db.now(), db.now() if dismissed else None))
    finally:
        conn.close()


def rows(settings, sql, *args):
    conn = db.connect(settings.db_path)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def add_alias(settings, work_id, alias, *, source="public_db", confidence=1.0, manual=False):
    """公開 DB の連携などで付く別名を DB に足す(結線そのものは tests/test_linking.py)。"""
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO work_aliases (work_id, alias, norm, source, manual, confidence, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (work_id, alias, normalize_title(alias), source, int(manual), confidence, db.now()))
    finally:
        conn.close()


def set_alias_confidence(settings, alias, confidence):
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("UPDATE work_aliases SET confidence = ? WHERE alias = ?", (confidence, alias))
    finally:
        conn.close()


def add_manual_link(settings, work_id, source="mangaupdates", source_id="test"):
    """利用者が選んだ公開 DB の連携(手動の決定)。"""
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO work_links (work_id, source, status, source_id, manual, confidence)
                         VALUES (?, ?, 'linked', ?, 1, 1.0)""", (work_id, source, source_id))
    finally:
        conn.close()


def add_linked_item(settings, work_id, source="mangaupdates", source_id="same-id", confidence=0.8):
    """公開 DB の自動連携(手動でない)。source と source_id で同じ提示先を表す。"""
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            c.execute("""INSERT INTO work_links (work_id, source, status, source_id, manual, confidence)
                         VALUES (?, ?, 'linked', ?, 0, ?)""", (work_id, source, source_id, confidence))
    finally:
        conn.close()


# --- 名前から作品へ ------------------------------------------------------------------------

def test_same_normalized_title_is_one_work(make_env):
    _, _, client = make_env(["Okiraku ryoshu v01.cbz", "Okiraku_ryoshu_v02.cbz", "okiraku ryoshu v03.cbz", "Other v01.cbz"])
    ws = by_title(client)
    assert set(ws) == {"Okiraku ryoshu", "Other"}
    assert ws["Okiraku ryoshu"]["volume_count"] == 3 and ws["Other"]["volume_count"] == 1
    assert client.get("/api/works").json()["unassigned"] == 0
    assert all(v["work_id"] for v in client.get("/api/volumes").json())


def test_trailing_numbers_of_the_same_name_are_volume_numbers(make_env):
    _, _, client = make_env(["[著者] 作品名 1.cbz", "[著者] 作品名 2.cbz", "[著者] 作品名 4.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["author"], w["volume_count"], w["missing_volumes"]) == ("作品名", "著者", 3, [3])
    assert client.get("/api/works/proposals").json() == []  # 別作品にならないので統合の提案も出ない
    d = client.get(f"/api/works/{w['id']}").json()
    assert [s["label"] for s in d["slots"]] == ["1巻", "2巻", "4巻"]
    v = d["slots"][0]["volumes"][0]
    assert v["volume_number"] == {"kind": "volume", "start": 1, "end": 1, "evidence": "1"}
    assert ["author", "著者"] in v["assignment"]["evidence"]["parts"]
    assert ["number", "1"] in v["assignment"]["evidence"]["parts"]
    assert v["assignment"]["evidence"]["name"] == "[著者] 作品名 1"
    assert v["assignment"]["confidence"] == 0.9 and v["assignment"]["source"] == "filename"


def test_trailing_number_alone_stays_in_the_title(make_env):
    # `Kitchen 2` が作品名のこともあるので、名前だけでは巻番号と決めない(同じ名前の組が要る)
    _, _, client = make_env(["Kitchen 2.cbz", "Kitchen.cbz"])
    assert set(by_title(client)) == {"Kitchen 2", "Kitchen"}
    assert all(v["volume_number"] is None for v in client.get("/api/volumes").json())


def test_trailing_number_in_parentheses(make_env):
    _, _, client = make_env(["作品名(1).cbz", "作品名(2).cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("作品名", 2)


def test_trailing_numbers_are_stable_after_resync(make_env):
    _, _, client = make_env(["作品名 1.cbz", "作品名 2.cbz"])
    client.post("/api/works/refresh")
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("作品名", 2)
    assert [s["label"] for s in client.get(f"/api/works/{w['id']}").json()["slots"]] == ["1巻", "2巻"]


def test_numbers_are_dropped_when_the_group_is_gone(make_env):
    lib, _, client = make_env(["作品名 1.cbz", "作品名 2.cbz"])
    assert set(by_title(client)) == {"作品名"}
    (lib / "作品名 2.cbz").unlink()
    client.post("/api/scan")
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("作品名", 1)  # 外側の名前の別名で同じ作品に残る
    assert all(v["volume_number"] is None for v in client.get("/api/volumes").json())  # 番号は名前から導出なので消える


def test_a_linked_volume_work_returns_to_the_series(make_env):
    """公開 DB の別名で系列名を持つ 1 巻 = 1 作品は、系列名の作品へ戻す(#162)。

    1 巻ずつの作品が公開 DB に linked されて別名 `作品名` を持つと、名前から読み直した `作品名` が
    「今の作品の別名」と一致して付け替えられず、1 巻 = 1 作品のまま提案が総当たりで残っていた。
    """
    lib, settings, client = make_env(["作品名.cbz", "[著者] 作品名 1.cbz"])
    ws = by_title(client)
    assert set(ws) == {"作品名", "作品名 1"}
    add_alias(settings, ws["作品名 1"]["id"], "作品名", confidence=0.8)  # 公開 DB の連携で付く系列名の別名
    cbz(lib, "[著者] 作品名 2.cbz")
    client.post("/api/scan")
    ws = by_title(client)
    assert set(ws) == {"作品名"}  # 番号付きの作品は空になり、一覧から消える
    assert ws["作品名"]["volume_count"] == 3
    assert client.get("/api/works/proposals").json() == []


def test_a_work_with_a_manual_link_is_not_folded_into_the_series(make_env):
    """手で選んだ公開 DB の連携がある作品は、系列名へ寄せず、自動統合もしない(#162)。"""
    lib, settings, client = make_env(["[著者] 作品名 1.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    add_alias(settings, w["id"], "作品名")
    add_manual_link(settings, w["id"])
    cbz(lib, "[著者] 作品名 2.cbz")
    client.post("/api/scan")
    assert set(by_title(client)) == {"作品名 1", "作品名"}
    (p,) = client.get("/api/works/proposals").json()  # 0.9 でも手動が絡むので確認待ち
    assert (p["status"], p["confidence"]) == ("open", 0.9)


def test_an_open_proposal_is_merged_when_the_evidence_becomes_certain(make_env):
    """後から 0.9 以上になった確認待ちは、再計算で自動統合する(#162)。"""
    _, settings, client = make_env(["Alpha 1.cbz", "Beta 1.cbz"])
    for w in by_title(client).values():
        add_alias(settings, w["id"], "作品名", confidence=0.8)
    client.post("/api/works/refresh")
    (p,) = client.get("/api/works/proposals").json()
    assert (p["status"], p["confidence"]) == ("open", 0.8)
    set_alias_confidence(settings, "作品名", 1.0)  # 連携が確かになり、別名の一致が 1.0 になる
    client.post("/api/works/refresh")
    assert client.get("/api/works/proposals").json() == []
    (w,) = client.get("/api/works").json()["works"]
    assert w["volume_count"] == 2
    merges = client.get(f"/api/works/{w['id']}").json()["merges"]
    assert [(m["rule"], m["status"]) for m in merges] == [("alias", "auto")]


def test_series_with_decor_after_the_trailing_number(make_env):
    """末尾の番号の後ろの括弧の装飾を落として系列と読む(#167)。"""
    _, _, client = make_env([
        "異種族レビュアーズ 1 [ドラゴンコミックスエイジ][AVIF].cbz",
        "異種族レビュアーズ 2 [ドラゴンコミックスエイジ][AVIF].cbz",
        "異種族レビュアーズ 3 [ドラゴンコミックスエイジ][AVIF].cbz",
    ])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("異種族レビュアーズ", 3)
    assert client.get("/api/works/proposals").json() == []


def test_author_list_after_the_trailing_number(make_env):
    """末尾の番号の後ろの著者列を落として系列と読む(#167)。"""
    _, _, client = make_env([
        "お気楽領主の楽しい領地防衛 1 - 青色まろ & 赤池宗 & 転.cbz",
        "お気楽領主の楽しい領地防衛 2 - 青色まろ & 赤池宗 & 転.cbz",
        "お気楽領主の楽しい領地防衛 3 - 青色まろ & 赤池宗 & 転.cbz",
    ])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("お気楽領主の楽しい領地防衛", 3)


def test_jian_volume_mark_is_read(make_env):
    """簡体字の巻表示(`第01卷`)も巻番号として読む(#167)。"""
    _, _, client = make_env(["Aチャンネル 第01卷.cbz", "Aチャンネル 第02卷.cbz", "Aチャンネル 第03卷.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("Aチャンネル", 3)
    d = client.get(f"/api/works/{w['id']}").json()
    assert [s["label"] for s in d["slots"]] == ["1巻", "2巻", "3巻"]


def test_decimal_volume_converts_to_numberless_series_member(make_env):
    """小数点の巻(`第04.5巻`)は番号なしの巻として同じ作品に入る(#167)。"""
    _, _, client = make_env([
        "ようこそ実力至上主義の教室へ 第04巻.cbz",
        "ようこそ実力至上主義の教室へ 第04.5巻.cbz",
        "ようこそ実力至上主義の教室へ 第05巻.cbz",
    ])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["volume_count"]) == ("ようこそ実力至上主義の教室へ", 3)
    vs = {v["title"]: v for v in client.get("/api/volumes").json()}
    assert vs["ようこそ実力至上主義の教室へ 第04.5巻"]["volume_number"] is None  # 整数で持てないので番号なし
    assert vs["ようこそ実力至上主義の教室へ 第04巻"]["volume_number"]["start"] == 4


def test_same_public_db_item_merges_below_threshold(make_env):
    """同じ公開 DB 項目に linked した組は、別名の確信度で 0.9 に届かなくても自動統合する(#167)。"""
    _, settings, client = make_env(["Alpha 1.cbz", "Beta 1.cbz"])
    for w in by_title(client).values():
        add_alias(settings, w["id"], "同じ別名", confidence=0.8)
        add_linked_item(settings, w["id"])  # 同じ source / source_id に linked
    client.post("/api/works/refresh")
    assert client.get("/api/works/proposals").json() == []
    (w,) = client.get("/api/works").json()["works"]
    assert w["volume_count"] == 2
    merges = client.get(f"/api/works/{w['id']}").json()["merges"]
    assert [(m["rule"], m["status"], m["confidence"]) for m in merges] == [("alias", "auto", 0.9)]


def test_same_public_db_item_with_manual_decision_is_only_a_proposal(make_env):
    """同じ項目に linked していても、手動の決定が絡む組は確認待ちのまま(#167)。"""
    _, settings, client = make_env(["Alpha 1.cbz", "Beta 1.cbz"])
    ws = by_title(client)
    add_alias(settings, ws["Alpha 1"]["id"], "同じ別名", confidence=0.8)
    add_alias(settings, ws["Beta 1"]["id"], "同じ別名", confidence=0.8)
    add_linked_item(settings, ws["Alpha 1"]["id"])
    add_manual_link(settings, ws["Beta 1"]["id"], source_id="same-id")  # 手で選んだ公開 DB の連携(同じ項目)
    client.post("/api/works/refresh")
    (p,) = client.get("/api/works/proposals").json()
    assert (p["status"], p["confidence"]) == ("open", 0.9)
    assert set(by_title(client)) == {"Alpha 1", "Beta 1"}


def test_a_work_without_volumes_gets_no_new_proposal(make_env):
    """巻の組み替えで空になった作品は確認待ちの提案を作らない(画面に出ない行が増えるだけなので)。"""
    lib, settings, client = make_env(["A 1.cbz", "B.cbz"])
    ws = {w["title"]: w["id"] for w in client.get("/api/works").json()["works"]}
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn) as c:
            for wid in (ws["A 1"], ws["B"]):  # 0.8 で一致する別名を同じだけ持たせる
                c.execute("""INSERT INTO work_aliases (work_id, alias, norm, source, manual, confidence, evidence,
                             created_at) VALUES (?, 'Same', 'same', 'filename', 0, 0.8, '{}', ?)""",
                          (wid, db.now()))
    finally:
        conn.close()
    client.post("/api/works/refresh")
    assert len(client.get("/api/works/proposals").json()) == 1
    cbz(lib, "A 2.cbz")  # A 1 と A 2 が 1 作品になり、元の A 1 は巻を持たなくなる
    client.post("/api/scan")
    assert set(by_title(client)) == {"A", "B"}
    assert client.get("/api/works/proposals").json() == []
    assert rows(settings, "SELECT COUNT(*) AS n FROM work_merge_proposals WHERE status = 'open'")[0]["n"] == 0


def test_author_and_title_come_from_the_name_with_evidence(make_env):
    _, _, client = make_env(["[青色まろ×赤池宗] お気楽 第01巻.cbz", "[青色まろ×赤池宗] お気楽 第02巻.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["author"]) == ("お気楽", "青色まろ、赤池宗")
    assert (w["title_source"], w["title_manual"], w["author_source"], w["author_manual"]) == ("filename", False, "filename", False)
    d = client.get(f"/api/works/{w['id']}").json()
    assert d["title_confidence"] == 0.9
    v = d["slots"][0]["volumes"][0]
    assert v["assignment"]["source"] == "filename" and v["assignment"]["confidence"] == 0.9
    assert ["author", "青色まろ×赤池宗"] in v["assignment"]["evidence"]["parts"]


def test_missing_volumes_are_listed(make_env):
    _, _, client = make_env(["T v01.cbz", "T v02.cbz", "T v04.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert w["max_volume"] == 4 and w["missing_volumes"] == [3]
    d = client.get(f"/api/works/{w['id']}").json()
    assert d["numbers"]["volume"] == {"max": 4, "missing": [3], "limited": False}
    assert [s["label"] for s in d["slots"]] == ["1巻", "2巻", "4巻"]


def test_ranges_cover_numbers_and_episodes_are_counted_separately(make_env):
    _, _, client = make_env(["A 第01-03巻.cbz", "A 第05巻.cbz", "B 第1話.cbz", "B 第2話.cbz", "B 第4話.cbz"])
    ws = by_title(client)
    assert ws["A"]["missing_volumes"] == [4] and ws["A"]["missing_episodes"] == []
    assert ws["B"]["missing_episodes"] == [3] and ws["B"]["missing_volumes"] == []


def test_absurd_max_volume_is_not_expanded(make_env):
    _, _, client = make_env(["A 第01巻.cbz", "A 第9000巻.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert w["missing_volumes"] == []
    assert client.get(f"/api/works/{w['id']}").json()["numbers"]["volume"]["limited"] is True


def test_same_number_volumes_are_grouped_as_duplicates(make_env):
    _, _, client = make_env(["T v01.cbz", "T 第01巻.cbz", "T v02.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    d = client.get(f"/api/works/{w['id']}").json()
    assert [(s["label"], s["duplicate"], len(s["volumes"])) for s in d["slots"]] == [("1巻", True, 2), ("2巻", False, 1)]
    assert w["duplicate_slots"] == 1


def test_name_without_title_uses_enclosing_folder_with_lower_confidence(make_env):
    lib, _, client = make_env(scan=False)
    cbz(lib / "シリーズX", "01.cbz")
    cbz(lib / "シリーズX", "02.cbz")
    client.post("/api/scan")
    (w,) = client.get("/api/works").json()["works"]
    assert w["title"] == "シリーズX" and w["volume_count"] == 2
    d = client.get(f"/api/works/{w['id']}").json()
    assert {x["assignment"]["confidence"] for x in d["unnumbered"]} == {0.6}  # 名前に番号が無い巻は番号なしの欄に出る
    assert d["unnumbered"][0]["assignment"]["evidence"]["level"] == "enclosing"


def test_container_name_becomes_alias_and_author_only_when_it_holds_one_work(make_env):
    lib, _, client = make_env(scan=False)
    write_zip(lib / "[東村アキコ] かくかくしかじか 第01-02巻.zip", [
        (f"Kakukaku Shikajika v0{n}/{i}.jpg", page_bytes(f"k{n}{i}")) for n in (1, 2) for i in range(2)])
    write_zip(lib / "詰め合わせ.zip", [("AAA v01/1.jpg", page_bytes("a")), ("BBB v01/1.jpg", page_bytes("b"))])
    client.post("/api/scan")
    ws = by_title(client)
    k = ws["Kakukaku Shikajika"]
    assert k["author"] == "東村アキコ" and k["author_source"] == "container"
    assert [(a["alias"], a["source"], a["confidence"]) for a in k["aliases"]] == [("かくかくしかじか", "container", 0.5)]
    assert ws["AAA"]["aliases"] == [] and ws["BBB"]["aliases"] == []  # 詰め合わせの名前はどの作品の別名にもならない


# --- 重複候補の証拠による統合 -------------------------------------------------------------------

@pytest.fixture
def two_names(make_env):
    lib, settings, client = make_env(["Okiraku v01.cbz", "Okiraku v02.cbz", "お気楽 第01巻.cbz", "お気楽 第02巻.cbz"])
    return lib, settings, client


def test_strong_duplicate_evidence_merges_automatically_with_confidence_and_evidence(two_names):
    _, settings, client = two_names
    assert set(by_title(client)) == {"Okiraku", "お気楽"}
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 186, 188)
    add_candidate(settings, vid(client, "Okiraku v02"), vid(client, "お気楽 第02巻"), 142, 146)
    assert client.post("/api/works/refresh").json()["merged"] == 1
    ws = by_title(client)
    assert list(ws) == ["お気楽"]  # 日本語の作品名を残す
    w = ws["お気楽"]
    assert w["volume_count"] == 4 and w["merged"] is True and w["pending_proposals"] == 0
    assert [a["alias"] for a in w["aliases"]] == ["Okiraku"] and w["aliases"][0]["source"] == "merge"
    d = client.get(f"/api/works/{w['id']}").json()
    (m,) = d["merges"]
    assert m["status"] == "auto" and m["rule"] == "duplicate" and m["confidence"] == pytest.approx(186 / 188, abs=1e-3)
    assert len(m["evidence"]["duplicate"]) == 2 and m["evidence"]["duplicate"][0]["matched_pages"] == 186
    sources = {x["assignment"]["source"] for s in d["slots"] for x in s["volumes"]}
    assert sources == {"filename", "duplicate"}  # 元から日本語名の巻はそのまま、ローマ字名の巻は重複の証拠で移った
    assert [(s["label"], s["duplicate"]) for s in d["slots"]] == [("1巻", True), ("2巻", True)]
    # 再計算しても二重に統合・提案されない
    assert client.post("/api/works/refresh").json()["merged"] == 0
    assert client.get("/api/works/proposals").json() == []


def test_low_confidence_becomes_a_proposal_and_is_not_merged(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 60, 100)
    client.post("/api/works/refresh")
    assert set(by_title(client)) == {"Okiraku", "お気楽"}
    (p,) = client.get("/api/works/proposals").json()
    assert p["status"] == "open" and p["rule"] == "duplicate" and p["confidence"] == 0.6
    assert {p["a"]["title"], p["b"]["title"]} == {"Okiraku", "お気楽"}
    assert p["evidence"]["duplicate"][0]["matched_pages"] == 60
    ws = by_title(client)
    assert ws["Okiraku"]["pending_proposals"] == 1 and ws["お気楽"]["pending_proposals"] == 1
    client.post("/api/works/refresh")
    assert len(client.get("/api/works/proposals").json()) == 1  # 再計算で増えない


def test_visual_only_evidence_never_merges_automatically(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 100, 100, rule="visual_only")
    client.post("/api/works/refresh")
    (p,) = client.get("/api/works/proposals").json()
    assert p["confidence"] == 0.85 and len(by_title(client)) == 2


def test_below_threshold_is_not_even_proposed(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 30, 100)
    client.post("/api/works/refresh")
    assert client.get("/api/works/proposals").json() == []


def test_approve_merges_and_dismiss_is_not_revived(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 60, 100)
    client.post("/api/works/refresh")
    (p,) = client.get("/api/works/proposals").json()
    assert client.post(f"/api/works/proposals/{p['id']}/dismiss").json() == {"id": p["id"], "status": "dismissed"}
    client.post("/api/works/refresh")
    assert client.get("/api/works/proposals").json() == [] and len(by_title(client)) == 2  # 却下した提案は戻らない
    assert [x["id"] for x in client.get("/api/works/proposals?status=dismissed").json()] == [p["id"]]
    # 高い確信度の証拠が後から出ても、却下した組は自動で統合しない
    add_candidate(settings, vid(client, "Okiraku v02"), vid(client, "お気楽 第02巻"), 140, 146)
    client.post("/api/works/refresh")
    assert len(by_title(client)) == 2
    assert client.post(f"/api/works/proposals/{p['id']}/approve").status_code == 409  # 確認待ちではない
    assert client.post(f"/api/works/proposals/{p['id']}/restore").json()["status"] == "open"
    assert client.post(f"/api/works/proposals/{p['id']}/approve").json()["status"] == "approved"
    assert list(by_title(client)) == ["お気楽"] and by_title(client)["お気楽"]["volume_count"] == 4
    assert client.post(f"/api/works/proposals/{p['id']}/approve").status_code == 409
    assert client.post("/api/works/proposals/999/approve").status_code == 404
    assert client.post(f"/api/works/proposals/{p['id']}/bogus").status_code == 422
    assert client.get("/api/works/proposals?status=bogus").status_code == 422


def test_dismissed_duplicate_candidate_is_not_evidence(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 186, 188, dismissed=True)
    client.post("/api/works/refresh")
    assert len(by_title(client)) == 2 and client.get("/api/works/proposals").json() == []


def test_proposal_disappears_when_its_evidence_disappears(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 60, 100)
    client.post("/api/works/refresh")
    assert len(client.get("/api/works/proposals").json()) == 1
    conn = db.connect(settings.db_path)
    with db.transaction(conn) as c:
        c.execute("DELETE FROM duplicate_candidates")
    conn.close()
    client.post("/api/works/refresh")
    assert client.get("/api/works/proposals").json() == []


def test_manual_decisions_block_automatic_merge(two_names):
    _, settings, client = two_names
    wid = by_title(client)["お気楽"]["id"]
    assert client.patch(f"/api/works/{wid}", json={"title": "お気楽領主"}).status_code == 200
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 186, 188)
    client.post("/api/works/refresh")
    assert len(by_title(client)) == 2  # 手動で直した作品名が絡む統合は、確信度が高くても確認待ち
    (p,) = client.get("/api/works/proposals").json()
    assert p["confidence"] == pytest.approx(186 / 188, abs=1e-3)


def test_manual_alias_equal_to_another_title_merges_with_alias_evidence(make_env):
    _, _, client = make_env(["Okiraku v01.cbz", "お気楽 第01巻.cbz"])
    ws = by_title(client)
    assert client.post(f"/api/works/{ws['お気楽']['id']}/aliases", json={"alias": "OKIRAKU"}).status_code == 200
    (w,) = client.get("/api/works").json()["works"]
    (m,) = client.get(f"/api/works/{w['id']}").json()["merges"]
    assert w["title"] == "お気楽" and w["volume_count"] == 2
    assert m["rule"] == "alias" and m["status"] == "auto" and m["confidence"] == 0.9
    assert m["evidence"]["alias"]["norm"] == "okiraku"


def test_alias_with_manual_title_only_proposes(make_env):
    _, _, client = make_env(["Okiraku v01.cbz", "お気楽 第01巻.cbz"])
    ws = by_title(client)
    client.patch(f"/api/works/{ws['お気楽']['id']}", json={"title": "お気楽領主"})  # 手動の作品名が絡む
    client.post(f"/api/works/{ws['お気楽']['id']}/aliases", json={"alias": "OKIRAKU"})
    (p,) = client.get("/api/works/proposals").json()
    assert p["rule"] == "alias" and p["status"] == "open" and len(by_title(client)) == 2


def test_container_alias_matching_a_title_is_only_a_proposal(make_env):
    lib, _, client = make_env(["かくかくしかじか 第03巻.cbz"], scan=False)
    write_zip(lib / "[東村アキコ] かくかくしかじか 第01-02巻.zip", [
        (f"Kakukaku Shikajika v0{n}/{i}.jpg", page_bytes(f"k{n}{i}")) for n in (1, 2) for i in range(2)])
    client.post("/api/scan")
    assert len(by_title(client)) == 2
    (p,) = client.get("/api/works/proposals").json()
    assert p["rule"] == "alias" and p["confidence"] == 0.5


# --- 手動の編集 ----------------------------------------------------------------------------------

def test_manual_title_author_survive_rescan_and_old_title_stays_matchable(make_env):
    lib, settings, client = make_env(["Okiraku v01.cbz", "Okiraku v02.cbz"])
    wid = by_title(client)["Okiraku"]["id"]
    r = client.patch(f"/api/works/{wid}", json={"title": "お気楽領主", "author": "  赤池宗 "})
    assert r.status_code == 200
    w = r.json()
    assert (w["title"], w["title_source"], w["title_manual"], w["author"], w["author_manual"]) == ("お気楽領主", "manual", True, "赤池宗", True)
    assert [(a["alias"], a["source"], a["manual"]) for a in w["aliases"]] == [("Okiraku", "previous_title", True)]
    cbz(lib, "Okiraku v03.cbz")
    cbz(lib, "[別の人] Okiraku v04.cbz")
    client.post("/api/scan")
    client.post("/api/works/refresh")
    (w,) = client.get("/api/works").json()["works"]
    assert (w["title"], w["author"], w["title_manual"], w["author_manual"]) == ("お気楽領主", "赤池宗", True, True)
    assert w["volume_count"] == 4  # 直す前の名前の巻も同じ作品に入る(新しい作品にならない)
    # 手で空にした著者は、名前から読んだ著者で埋め直さない
    client.patch(f"/api/works/{wid}", json={"author": ""})
    client.post("/api/works/refresh")
    assert client.get(f"/api/works/{wid}").json()["author"] is None


def test_edit_validation(make_env):
    _, _, client = make_env(["Okiraku v01.cbz"])
    (w,) = client.get("/api/works").json()["works"]
    assert client.patch(f"/api/works/{w['id']}", json={}).status_code == 422
    assert client.patch(f"/api/works/{w['id']}", json={"title": "  "}).status_code == 422
    assert client.patch(f"/api/works/{w['id']}", json={"title": None}).status_code == 422
    assert client.patch(f"/api/works/{w['id']}", json={"title": 5}).status_code == 422
    assert client.patch("/api/works/9999", json={"title": "x"}).status_code == 404
    assert client.get("/api/works/9999").status_code == 404
    assert client.post(f"/api/works/{w['id']}/aliases", json={"alias": "okiraku"}).status_code == 409
    assert client.post(f"/api/works/{w['id']}/aliases", json={"alias": "  "}).status_code == 422


def test_removed_alias_is_not_added_back(make_env):
    lib, _, client = make_env(scan=False)
    write_zip(lib / "[東村アキコ] かくかくしかじか 第01-02巻.zip", [
        (f"Kakukaku Shikajika v0{n}/{i}.jpg", page_bytes(f"k{n}{i}")) for n in (1, 2) for i in range(2)])
    client.post("/api/scan")
    (w,) = client.get("/api/works").json()["works"]
    (alias,) = w["aliases"]
    d = client.delete(f"/api/works/{w['id']}/aliases/{alias['id']}").json()
    assert d["aliases"] == []
    client.post("/api/works/refresh")
    client.post("/api/scan")
    assert client.get(f"/api/works/{w['id']}").json()["aliases"] == []
    assert client.delete(f"/api/works/{w['id']}/aliases/999").status_code == 404
    client.post(f"/api/works/{w['id']}/aliases", json={"alias": "かくかくしかじか"})  # 手で足し直せる
    assert client.get(f"/api/works/{w['id']}").json()["aliases"][0]["manual"] is True


def test_manual_assignment_survives_recompute_and_rescan(make_env):
    _, _, client = make_env(["A v01.cbz", "A v02.cbz", "B v01.cbz"])
    ws = by_title(client)
    v2 = vid(client, "A v02")
    r = client.post("/api/works/assign", json={"volume_ids": [v2], "work_id": ws["B"]["id"]})
    assert r.json() == {"work_id": ws["B"]["id"]}
    for _ in range(2):
        client.post("/api/scan")
        client.post("/api/works/refresh")
    ws = by_title(client)
    assert ws["A"]["volume_count"] == 1 and ws["B"]["volume_count"] == 2
    moved = next(x for s in client.get(f"/api/works/{ws['B']['id']}").json()["slots"] for x in s["volumes"] if x["id"] == v2)
    assert moved["assignment"]["manual"] is True and moved["assignment"]["source"] == "manual"
    assert client.get("/api/volumes").json()[0]["work_id"]  # 一覧の巻に作品が付く


def test_split_makes_a_new_work_and_is_not_merged_back(two_names):
    _, settings, client = two_names
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 186, 188)
    client.post("/api/works/refresh")
    (w,) = client.get("/api/works").json()["works"]
    moved = [vid(client, "Okiraku v01"), vid(client, "Okiraku v02")]
    new_id = client.post("/api/works/assign", json={"volume_ids": moved, "work_id": None, "title": "別の作品"}).json()["work_id"]
    client.post("/api/works/refresh")
    ws = by_title(client)
    assert set(ws) == {"お気楽", "別の作品"} and ws["別の作品"]["id"] == new_id
    assert ws["別の作品"]["volume_count"] == 2 and ws["別の作品"]["title_manual"] is True
    assert client.get("/api/works/proposals").json() == []  # 重複の証拠があっても、分けた組を戻さない
    assert len(client.get("/api/works/proposals?status=dismissed").json()) == 1
    # 作品名を省くと巻の名前から付く
    nid = client.post("/api/works/assign", json={"volume_ids": [vid(client, "お気楽 第02巻")], "work_id": None}).json()["work_id"]
    assert client.get(f"/api/works/{nid}").json()["title"] == "お気楽" and client.get(f"/api/works/{nid}").json()["title_manual"] is False


def test_assign_validation(make_env):
    _, _, client = make_env(["A v01.cbz"])
    assert client.post("/api/works/assign", json={"volume_ids": []}).status_code == 422
    assert client.post("/api/works/assign", json={"volume_ids": [9999]}).status_code == 404
    assert client.post("/api/works/assign", json={"volume_ids": [vid(client, "A v01")], "work_id": 9999}).status_code == 404
    assert client.post("/api/works/assign", json={"volume_ids": [vid(client, "A v01")], "title": "!!!"}).status_code == 422


def test_manual_merge(make_env):
    _, _, client = make_env(["Okiraku v01.cbz", "お気楽 第01巻.cbz", "Z v01.cbz"])
    ws = by_title(client)
    r = client.post(f"/api/works/{ws['お気楽']['id']}/merge", json={"other_id": ws["Okiraku"]["id"]})
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "お気楽" and d["volume_count"] == 2 and d["merges"][0]["rule"] == "manual"
    assert {x["title"]: x["assignment"]["manual"] for s in d["slots"] for x in s["volumes"]} == {
        "お気楽 第01巻": False, "Okiraku v01": True}  # 動かした巻だけ手動の割り当てになる
    assert client.get(f"/api/works/{ws['Okiraku']['id']}").status_code == 404  # 吸収された作品は一覧にも詳細にも出ない
    assert client.post(f"/api/works/{ws['お気楽']['id']}/merge", json={"other_id": ws["Okiraku"]["id"]}).status_code == 404
    assert client.post(f"/api/works/{ws['Z']['id']}/merge", json={"other_id": ws["Z"]["id"]}).status_code == 409
    client.post("/api/works/refresh")
    assert set(by_title(client)) == {"お気楽", "Z"}


# --- 蔵書・DB への影響 -----------------------------------------------------------------------------

def test_offline_root_volumes_stay_in_their_work_and_nothing_is_deleted(make_env):
    lib, settings, client = make_env(["T v01.cbz", "T v02.cbz"])
    wid = by_title(client)["T"]["id"]
    before = rows(settings, "SELECT volume_id, work_id FROM work_volumes ORDER BY volume_id")
    gone = lib.with_name("lib-offline")
    lib.rename(gone)
    try:
        client.post("/api/scan")  # ルートが見えない → オフライン
        client.post("/api/works/refresh")
        w = by_title(client)["T"]
        assert w["id"] == wid and w["volume_count"] == 2 and w["offline_count"] == 2
        assert rows(settings, "SELECT volume_id, work_id FROM work_volumes ORDER BY volume_id") == before
    finally:
        gone.rename(lib)
    client.post("/api/scan")
    assert by_title(client)["T"]["offline_count"] == 0


def test_missing_volume_keeps_its_assignment_and_returns_to_the_same_work(make_env):
    lib, settings, client = make_env(["T v01.cbz", "T v02.cbz"])
    (lib / "T v02.cbz").rename(lib.parent / "T v02.cbz.bak")
    client.post("/api/scan")
    w = by_title(client)["T"]
    assert w["volume_count"] == 1 and w["missing_volumes"] == []
    assert len(rows(settings, "SELECT * FROM work_volumes")) == 2  # 見えなくなった巻の割り当ては残す
    (lib.parent / "T v02.cbz.bak").rename(lib / "T v02.cbz")
    client.post("/api/scan")
    assert by_title(client)["T"]["volume_count"] == 2


def test_scan_with_works_never_writes_to_the_library_or_extracts(make_env, make_settings, tmp_path):
    lib, settings, client = make_env(["T v01.cbz", "T v02.cbz"])
    before = snapshot(lib)
    client.post("/api/scan")
    client.post("/api/works/refresh")
    client.get("/api/works")
    assert snapshot(lib) == before
    assert list(settings.cache_dir.iterdir()) == []


def test_startup_assigns_volumes_scanned_before_works_existed(make_env, make_settings, tmp_path):
    lib, settings, client = make_env(["T v01.cbz", "T v02.cbz"])
    conn = db.connect(settings.db_path)
    with db.transaction(conn) as c:
        for t in ("work_merge_proposals", "work_aliases", "work_fields", "work_volumes"):
            c.execute(f"DELETE FROM {t}")
        c.execute("DELETE FROM works")
    conn.close()
    client2 = TestClient(create_app(settings, frontend_dist=None))  # 再起動
    assert by_title(client2)["T"]["volume_count"] == 2


def test_sync_is_idempotent_and_writes_nothing_when_nothing_changed(make_env):
    _, settings, client = make_env(["T v01.cbz", "A 第01巻.cbz", "[x] A 第02巻.cbz"])
    before = [tuple(r) for r in rows(settings, "SELECT * FROM work_volumes ORDER BY volume_id")]
    fields = [tuple(r) for r in rows(settings, "SELECT * FROM work_fields ORDER BY work_id, field")]
    s = client.post("/api/works/refresh").json()
    assert s["works_created"] == 0 and s["volumes_assigned"] == 0 and s["merged"] == 0
    assert [tuple(r) for r in rows(settings, "SELECT * FROM work_volumes ORDER BY volume_id")] == before
    assert [tuple(r) for r in rows(settings, "SELECT * FROM work_fields ORDER BY work_id, field")][:1] == fields[:1]


def test_organize_carries_the_work_assignment_to_the_new_volume_and_back(make_env):
    lib, settings, client = make_env(["T v01.cbz", "T v02.cbz", "U v01.cbz"])
    ws = by_title(client)
    v1 = vid(client, "T v01")
    client.post("/api/works/assign", json={"volume_ids": [v1], "work_id": ws["U"]["id"]})  # 手動の決定
    plan = client.post("/api/organize/plan", json={"volume_id": v1}).json()
    assert plan["state"] == "planned"
    done = client.post(f"/api/organize/{plan['op_id']}/execute").json()
    new_id = done["volumes"][0]["new_volume_id"]
    assert new_id and new_id != v1
    (new_assign,) = rows(settings, "SELECT * FROM work_volumes WHERE volume_id = ?", new_id)
    assert (new_assign["work_id"], new_assign["manual"], new_assign["source"]) == (ws["U"]["id"], 1, "manual")
    client.post("/api/works/refresh")
    assert by_title(client)["U"]["volume_count"] == 2  # 置き換わった新しい巻が作品に残る(元の巻は一覧から外れる)
    assert client.post(f"/api/organize/{plan['op_id']}/undo").json()["state"] == "undone"
    (old_assign,) = rows(settings, "SELECT * FROM work_volumes WHERE volume_id = ?", v1)
    assert old_assign["work_id"] == ws["U"]["id"] and old_assign["manual"] == 1


def test_unnamed_volume_is_counted_as_unassigned(make_env):
    lib, _, client = make_env(scan=False)
    write_zip(lib / "001.zip", [("1.jpg", page_bytes("x"))])
    client.post("/api/scan")
    r = client.get("/api/works").json()
    assert r["works"] == [] and r["unassigned"] == 1


def test_counted_names_are_not_work_titles(make_env):
    # 実データ(#158): 配布元の接頭辞を落とすと「57 files」になり、1〜109 files などの 74 作品ができていた。
    # 1〜3 は、末尾の番号の系列(#157)として「files」1〜3 巻にも読ませない
    _, _, client = make_env([f"DLRAW.AC_{n}_files.cbz" for n in (1, 2, 3, 57)])
    r = client.get("/api/works").json()
    assert r["works"] == [] and r["unassigned"] == 4


def test_counted_names_fall_back_to_the_enclosing_name(make_env):
    # 数えた名前しか無い巻でも、外側の名前が作品名なら、その作品に入る(確信度は低い)
    lib, _, client = make_env(scan=False)
    for n in (57, 58):
        d = lib / "ふしぎ研究部 第01-20巻" / f"DLRAW.AC_{n}_files"
        d.mkdir(parents=True)
        for i in range(3):
            (d / f"DLRAW.AC_{i + 1:04d}.jpg").write_bytes(page_bytes(f"{n}/{i}"))
    client.post("/api/scan")
    ws = by_title(client)
    assert set(ws) == {"ふしぎ研究部"}
    w = client.get(f"/api/works/{ws['ふしぎ研究部']['id']}").json()
    assert w["volume_count"] == 2 and w["title_confidence"] == 0.6


def test_children_of_a_split_bundle_inherit_a_manual_decision(make_env, make_settings):
    from conftest import zip_bytes
    lib, settings, client = make_env(["Other v01.cbz"], scan=False)
    inner = zip_bytes([(f"Book v0{n}/{i}.jpg", page_bytes(f"{n}{i}")) for n in (1, 2) for i in range(2)])
    write_zip(lib / "outer.zip", [("Book v01-02.zip", inner)])
    client.post("/api/scan")
    ws = by_title(client)
    bundle = vid(client, "Book v01-02")
    client.post("/api/works/assign", json={"volume_ids": [bundle], "work_id": ws["Other"]["id"]})
    assert client.post(f"/api/volumes/{bundle}/analyze").json()["analyzed"] is True  # 中の巻に分解される
    d = client.get(f"/api/works/{ws['Other']['id']}").json()
    labels = [s["label"] for s in d["slots"]]
    assert labels == ["1巻", "2巻"] and d["volume_count"] == 3  # Other v01 と 子の 1・2 巻
    children = [x for s in d["slots"] for x in s["volumes"] if x["title"].startswith("Book")]
    assert len(children) == 2 and {x["assignment"]["manual"] for x in children} == {True}


def test_sync_function_directly_reports_a_summary(make_env):
    _, settings, _ = make_env(["T v01.cbz"])
    conn = db.connect(settings.db_path)
    try:
        s = works.sync(conn)
        assert (s.works_created, s.volumes_assigned, s.merged, s.proposals_open) == (0, 0, 0, 0)
    finally:
        conn.close()


# --- agy レビュー(#47)で成立を確かめた指摘 ------------------------------------------------------

def test_alias_added_to_the_work_that_gets_absorbed_returns_the_surviving_work(make_env):
    _, _, client = make_env(["Okiraku v01.cbz", "お気楽 第01巻.cbz"])
    ws = by_title(client)
    # ローマ字の作品へ別名「お気楽」を足すと、日本語の作品へ自動で統合される(吸収されるのは操作した作品の側)
    r = client.post(f"/api/works/{ws['Okiraku']['id']}/aliases", json={"alias": "お気楽"})
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == ws["お気楽"]["id"] and d["title"] == "お気楽" and d["volume_count"] == 2


def test_manual_author_blocks_automatic_merge(two_names):
    _, settings, client = two_names
    wid = by_title(client)["Okiraku"]["id"]
    assert client.patch(f"/api/works/{wid}", json={"author": "手で入れた著者"}).status_code == 200
    add_candidate(settings, vid(client, "Okiraku v01"), vid(client, "お気楽 第01巻"), 186, 188)
    client.post("/api/works/refresh")
    assert len(by_title(client)) == 2  # 手動の著者が絡む統合は、確信度が高くても確認待ち
    (p,) = client.get("/api/works/proposals").json()
    assert p["status"] == "open"


def test_manual_author_of_the_absorbed_work_is_not_lost_on_merge(make_env):
    _, _, client = make_env(["Okiraku v01.cbz", "[ファイル名の著者] お気楽 第01巻.cbz"])
    ws = by_title(client)
    assert ws["お気楽"]["author"] == "ファイル名の著者" and ws["Okiraku"]["author"] is None
    assert client.patch(f"/api/works/{ws['Okiraku']['id']}", json={"author": "手で入れた著者"}).status_code == 200
    r = client.post(f"/api/works/{ws['お気楽']['id']}/merge", json={"other_id": ws["Okiraku"]["id"]})
    assert r.status_code == 200
    d = r.json()
    assert (d["author"], d["author_manual"]) == ("手で入れた著者", True)  # 名前から読んだ著者で手動の著者を隠さない
