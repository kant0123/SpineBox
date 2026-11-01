"""別スキャンの重複候補(#38)。画像は Pillow で合成し、蔵書は一時ディレクトリに動的生成する。"""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import snapshot, write_zip, zip_bytes
from images import blank_page, jpeg, make_page, make_series
from spinebox import db, duplicates
from spinebox.app import create_app
from spinebox.cache import ExtractionCache
from spinebox.library import Library
from spinebox.scanner import Scanner


def scan_pages(series: int, n: int, *, scale=1.0, quality=90, extra_front=0, extra_back=0, skip=()):
    """series の n ページ。別スキャンの再現: 縮小・再圧縮・先頭や末尾にページ追加・途中のページ抜け。"""
    pages = make_series(series, n)
    out = [(f"{i:04d}.jpg", jpeg(p, scale=scale, quality=quality)) for i, p in enumerate(pages) if i not in skip]
    front = [(f"a{i:02d}.jpg", jpeg(make_page(900_000 + series * 100 + i))) for i in range(extra_front)]
    back = [(f"z{i:02d}.jpg", jpeg(make_page(800_000 + series * 100 + i))) for i in range(extra_back)]
    return front + out + back


def other_pages(n: int, base: int = 70_000):
    return [(f"x{i}.jpg", jpeg(make_page(base + i))) for i in range(n)]


@pytest.fixture
def env(make_settings):
    def make(*roots):
        settings = make_settings(*roots)
        conn = db.open_db(settings.db_path)
        Scanner(conn, settings).scan()
        library = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
        return settings, conn, library
    return make


def candidates(conn, *, dismissed=False):
    rows = conn.execute("""SELECT a.title AS ta, b.title AS tb FROM duplicate_candidates c
                           JOIN volumes a ON a.id = c.volume_a JOIN volumes b ON b.id = c.volume_b
                           WHERE (c.dismissed_at IS NOT NULL) = ? ORDER BY c.id""", (int(dismissed),)).fetchall()
    return [tuple(sorted((r["ta"], r["tb"]))) for r in rows]


def run(conn, library, **kw):
    return duplicates.DuplicateScanner(conn, library).run(**kw)


def test_rescaled_recompressed_and_padded_copy_is_a_candidate_but_other_books_are_not(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "Book v01.cbz", scan_pages(1, 30))
    write_zip(lib / "別スキャン 第01巻.cbz", scan_pages(1, 30, scale=0.6, quality=45, extra_front=2, extra_back=3, skip={7}))
    write_zip(lib / "Other v01.cbz", scan_pages(2, 30))
    write_zip(lib / "Book v02.cbz", scan_pages(3, 30))
    _, conn, library = env(lib)
    summary = run(conn, library)
    assert summary.fingerprinted == 4 and summary.errors == 0 and summary.candidates == 1
    assert candidates(conn) == [("Book v01", "別スキャン 第01巻")]
    row = conn.execute("SELECT * FROM duplicate_candidates").fetchone()
    assert row["rule"] == "same_number" and row["matched_pages"] >= 28 and row["compared_pages"] == 30


def test_different_volume_number_is_not_a_candidate_even_if_pages_look_alike(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "Book v01.cbz", scan_pages(1, 30))
    write_zip(lib / "Book v02.cbz", scan_pages(1, 30, quality=60))
    _, conn, library = env(lib)
    assert run(conn, library).candidates == 0


def test_unknown_volume_number_needs_a_strong_visual_match(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "名無し.cbz", scan_pages(1, 40))
    write_zip(lib / "Book v01.cbz", scan_pages(1, 40, scale=0.5, quality=50))
    # 半分だけ同じ画像(後半は別): 番号が不明な相手には強い一致が要るので候補にならない
    write_zip(lib / "半分.cbz", scan_pages(1, 20) + other_pages(20))
    _, conn, library = env(lib)
    run(conn, library)
    assert candidates(conn) == [("Book v01", "名無し")]
    assert conn.execute("SELECT rule FROM duplicate_candidates").fetchone()["rule"] == "visual_only"


def test_known_number_accepts_a_weaker_match_than_unknown(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 40))
    write_zip(lib / "B v01.cbz", scan_pages(1, 20) + other_pages(20))
    _, conn, library = env(lib)
    run(conn, library)
    assert candidates(conn) == [("A v01", "B v01")]


def test_too_small_overlap_is_not_a_candidate(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 40))
    write_zip(lib / "B v01.cbz", scan_pages(1, 3) + other_pages(37))
    _, conn, library = env(lib)
    assert run(conn, library).candidates == 0


def test_shared_blank_pages_do_not_make_a_candidate(env, tmp_path):
    lib = tmp_path / "lib"
    blanks = [(f"b{i}.jpg", blank_page(255 - i)) for i in range(30)]
    write_zip(lib / "A v01.cbz", scan_pages(1, 20) + blanks)
    write_zip(lib / "B v01.cbz", scan_pages(2, 20) + blanks)
    _, conn, library = env(lib)
    assert run(conn, library).candidates == 0


def test_dismissed_candidate_is_not_resurrected_by_recompute(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 20))
    write_zip(lib / "B v01.cbz", scan_pages(1, 20, scale=0.7))
    _, conn, library = env(lib)
    run(conn, library)
    cid = conn.execute("SELECT id FROM duplicate_candidates").fetchone()["id"]
    assert duplicates.candidate_counts(conn) != {}
    duplicates.dismiss(conn, cid)
    assert duplicates.candidate_counts(conn) == {}
    # 指紋を作り直させても、再計算しても、却下は残る
    conn.execute("DELETE FROM volume_fingerprints")
    run(conn, library)
    duplicates.refresh_candidates(conn)
    assert candidates(conn) == [] and candidates(conn, dismissed=True) == [("A v01", "B v01")]
    assert duplicates.candidate_counts(conn) == {}
    duplicates.dismiss(conn, cid, restore=True)
    assert candidates(conn) == [("A v01", "B v01")]
    with pytest.raises(duplicates.CandidateNotFound):
        duplicates.dismiss(conn, 9999)


def test_stale_open_candidate_goes_away_when_it_no_longer_holds(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 20))
    b = write_zip(lib / "B v01.cbz", scan_pages(1, 20))
    settings, conn, library = env(lib)
    run(conn, library)
    assert len(candidates(conn)) == 1
    write_zip(b, scan_pages(5, 21))  # 中身が別の本に差し替わった
    Scanner(conn, settings).scan()
    summary = run(conn, library)
    assert summary.fingerprinted == 1 and summary.candidates == 0
    assert candidates(conn) == []


def test_fingerprints_are_not_recomputed_unless_the_file_changes(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    b = write_zip(lib / "B v01.cbz", scan_pages(2, 12))
    settings, conn, library = env(lib)
    assert run(conn, library).fingerprinted == 2
    assert run(conn, library).fingerprinted == 0
    write_zip(b, scan_pages(2, 13))
    Scanner(conn, settings).scan()
    assert run(conn, library).fingerprinted == 1
    row = conn.execute("SELECT f.page_count, f.hashed_pages FROM volume_fingerprints f "
                       "JOIN volumes v ON v.id = f.volume_id WHERE v.title = 'B v01'").fetchone()
    assert tuple(row) == (13, 13)


def test_scan_does_not_compute_fingerprints_or_extract(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "set.zip", [("inner v01.zip", zip_bytes(scan_pages(1, 12))),
                                ("inner v02.zip", zip_bytes(scan_pages(2, 12)))])
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    settings, conn, _ = env(lib)  # env はスキャン済み
    assert conn.execute("SELECT COUNT(*) FROM volume_fingerprints").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM duplicate_candidates").fetchone()[0] == 0
    assert not settings.cache_dir.exists() or list(settings.cache_dir.iterdir()) == []


def test_inner_archive_volume_is_opened_for_the_fingerprint_and_matches_a_top_level_copy(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "set.zip", [("inner v01.zip", zip_bytes(scan_pages(1, 15))),
                                ("inner v02.zip", zip_bytes(scan_pages(2, 15)))])
    write_zip(lib / "copy 第01巻.cbz", scan_pages(1, 15, scale=0.5, quality=40, extra_back=2))
    settings, conn, library = env(lib)
    summary = run(conn, library)
    assert summary.fingerprinted == 3 and summary.errors == 0
    assert candidates(conn) == [("copy 第01巻", "inner v01")]
    assert any(settings.cache_dir.iterdir())  # 内側アーカイブは展開上限・キャッシュの仕組みで取り出された


def test_inner_archive_with_several_volumes_fingerprints_the_children(env, tmp_path):
    lib = tmp_path / "lib"
    inner = zip_bytes([(f"v01/{n}", d) for n, d in scan_pages(1, 12)] + [(f"v02/{n}", d) for n, d in scan_pages(2, 12)])
    write_zip(lib / "set.zip", [("pack.zip", inner)])
    write_zip(lib / "copy v02.cbz", scan_pages(2, 12, scale=0.6))
    _, conn, library = env(lib)
    summary = run(conn, library)
    assert summary.errors == 0
    # 親(まとめ)は指紋を作らず、分解された子 2 巻と単独の 1 巻に作る
    assert summary.fingerprinted == 3
    assert candidates(conn) == [("copy v02", "v02")]


def test_unreadable_volume_is_recorded_and_retried_and_does_not_stop_the_run(env, library):
    write_zip(library / "good v01.cbz", scan_pages(1, 12))
    _, conn, lib = env(library)
    first = run(conn, lib)
    assert first.errors >= 1  # inner3.rar は 7-Zip が無い設定では開けない
    err = conn.execute("SELECT v.title FROM volume_fingerprints f JOIN volumes v ON v.id = f.volume_id "
                       "WHERE f.error IS NOT NULL").fetchall()
    assert [r["title"] for r in err] == ["inner3"]
    good = conn.execute("SELECT f.hashed_pages FROM volume_fingerprints f JOIN volumes v ON v.id = f.volume_id "
                        "WHERE v.title = 'good v01'").fetchone()
    assert good["hashed_pages"] == 12
    assert run(conn, lib).errors == 1  # 失敗した巻は明示操作のたびにやり直す


def test_offline_root_keeps_fingerprints_and_candidates(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    write_zip(lib / "B v01.cbz", scan_pages(1, 12, scale=0.6))
    settings, conn, library = env(lib)
    run(conn, library)
    assert len(candidates(conn)) == 1
    lib.rename(tmp_path / "away")
    Scanner(conn, settings).scan()
    summary = run(conn, library)
    assert summary.fingerprinted == 0 and summary.candidates == 1
    assert conn.execute("SELECT COUNT(*) FROM volume_fingerprints WHERE error IS NULL").fetchone()[0] == 2
    assert len(candidates(conn)) == 1


def test_originals_are_never_modified(env, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    write_zip(lib / "B v01.cbz", scan_pages(1, 12, scale=0.6))
    write_zip(lib / "set.zip", [("inner v02.zip", zip_bytes(scan_pages(2, 12)))])
    before = snapshot(lib)
    _, conn, library = env(lib)
    run(conn, library)
    duplicates.dismiss(conn, conn.execute("SELECT id FROM duplicate_candidates").fetchone()["id"])
    run(conn, library)
    assert snapshot(lib) == before


def test_limit_stops_after_that_many_volumes(env, tmp_path):
    lib = tmp_path / "lib"
    for i in range(1, 5):
        write_zip(lib / f"S{i} v01.cbz", scan_pages(i, 10))
    _, conn, library = env(lib)
    assert run(conn, library, limit=2).fingerprinted == 2
    assert run(conn, library).fingerprinted == 2


def test_hash_index_finds_exactly_the_near_hashes():
    import random
    rnd = random.Random(0)
    items = [(i, rnd.getrandbits(64)) for i in range(300)]
    items += [(300 + i, h ^ sum(1 << rnd.randrange(64) for _ in range(i % 6))) for i, (_, h) in enumerate(items[:60])]
    index = duplicates.HashIndex(items)
    for _, h in items[:80]:
        expected = {pos: (h ^ x).bit_count() for pos, (_, x) in enumerate(items) if (h ^ x).bit_count() <= 8}
        assert dict(index.near(h)) == expected


# --- API ---------------------------------------------------------------------------


@pytest.fixture
def api(make_settings, tmp_path):
    lib = tmp_path / "lib"
    write_zip(lib / "Book v01.cbz", scan_pages(1, 14))
    write_zip(lib / "別 第01巻.cbz", scan_pages(1, 14, scale=0.5, quality=40, extra_front=1))
    write_zip(lib / "Other v01.cbz", scan_pages(2, 14))
    client = TestClient(create_app(make_settings(lib), frontend_dist=None))
    client.post("/api/scan")
    return client


def test_api_scan_list_dismiss_flow(api):
    assert all(v["duplicate_count"] == 0 for v in api.get("/api/volumes").json())
    assert api.get("/api/duplicates").json()["candidates"] == []
    done = api.post("/api/duplicates/scan?wait=true").json()
    assert done["running"] is False and done["error"] is None
    assert done["summary"]["fingerprinted"] == 3 and done["summary"]["candidates"] == 1

    body = api.get("/api/duplicates").json()
    (cand,) = body["candidates"]
    assert body["scan"]["summary"]["candidates"] == 1
    assert {cand["a"]["title"], cand["b"]["title"]} == {"Book v01", "別 第01巻"}
    assert cand["rule"] == "same_number" and cand["matched_pages"] >= 13 and cand["compared_pages"] == 14
    ev = cand["evidence"]
    assert {ev["number_a"], ev["number_b"]} == {"v01", "第01巻"} and ev["near_bits"] == 8 and ev["samples"]
    assert cand["a"]["volume_number"]["start"] == 1 and cand["dismissed"] is False

    listed = {v["title"]: v for v in api.get("/api/volumes").json()}
    assert listed["Book v01"]["duplicate_count"] == 1 and listed["別 第01巻"]["duplicate_count"] == 1
    assert listed["Other v01"]["duplicate_count"] == 0

    assert api.post(f"/api/duplicates/{cand['id']}/dismiss").json() == {"id": cand["id"], "dismissed": True}
    assert api.get("/api/duplicates").json()["candidates"] == []
    assert [c["id"] for c in api.get("/api/duplicates?status=dismissed").json()["candidates"]] == [cand["id"]]
    assert all(v["duplicate_count"] == 0 for v in api.get("/api/volumes").json())
    api.post("/api/duplicates/scan?wait=true")  # 再計算しても復活しない
    assert api.get("/api/duplicates").json()["candidates"] == []
    assert api.post(f"/api/duplicates/{cand['id']}/restore").json() == {"id": cand["id"], "dismissed": False}
    assert len(api.get("/api/duplicates").json()["candidates"]) == 1


def test_api_errors(api):
    assert api.post("/api/duplicates/12345/dismiss").status_code == 404
    assert api.post("/api/duplicates/12345/restore").status_code == 404
    assert api.get("/api/duplicates?status=all").status_code == 422


def test_api_scan_in_background_returns_202_and_finishes(api):
    r = api.post("/api/duplicates/scan")
    assert r.status_code == 202 and r.json()["running"] is True
    for _ in range(200):
        state = api.get("/api/duplicates").json()["scan"]
        if not state["running"]:
            break
        time.sleep(0.1)
    assert state["running"] is False and state["summary"]["candidates"] == 1


def test_api_scan_is_refused_while_another_is_running_or_the_library_is_scanning(api):
    lock = api.app.state.duplicate_lock
    assert lock.acquire(blocking=False)
    try:
        assert api.post("/api/duplicates/scan").status_code == 409
    finally:
        lock.release()
    scan_lock = api.app.state.scan_lock
    assert scan_lock.acquire(blocking=False)
    try:
        assert api.post("/api/duplicates/scan").status_code == 409
    finally:
        scan_lock.release()
    assert api.post("/api/duplicates/scan?wait=true").status_code == 200


def test_api_scan_failure_is_reported_and_releases_the_lock(api, monkeypatch):
    def boom(self, limit=None):
        raise RuntimeError("壊れた")
    monkeypatch.setattr(duplicates.DuplicateScanner, "run", boom)
    state = api.post("/api/duplicates/scan?wait=true").json()
    assert state["running"] is False and state["error"] == "壊れた"
    monkeypatch.undo()
    assert api.post("/api/duplicates/scan?wait=true").json()["error"] is None


def test_prefilter_keeps_overlapping_sets_and_drops_disjoint_ones():
    import random
    rnd = random.Random(7)
    a = [(i, rnd.getrandbits(64)) for i in range(100)]
    b = [(i, rnd.getrandbits(64)) for i in range(100)]
    near_a = [(i, h ^ 0b101) for i, h in a]  # 2 ビットだけ違う別スキャン
    assert duplicates.prefilter(a, duplicates.HashIndex(near_a))
    assert duplicates.prefilter(a[:10], duplicates.HashIndex(near_a))
    assert not duplicates.prefilter(a, duplicates.HashIndex(b))
    assert not duplicates.prefilter([], duplicates.HashIndex(b))


def test_organized_volumes_are_neither_fingerprinted_nor_candidates(env, tmp_path):
    """#31 で整理済み(CBZ 化して元を退避)の巻は一覧から外れる。指紋・候補の対象もその条件に揃える。"""
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    write_zip(lib / "B v01.cbz", scan_pages(1, 12, scale=0.6))
    _, conn, library = env(lib)
    run(conn, library)
    assert len(candidates(conn)) == 1
    a_id = conn.execute("SELECT id, source_id FROM volumes WHERE title = 'A v01'").fetchone()
    conn.execute("INSERT INTO organize_ops (id, source_id, state, plan_json, created_at, updated_at) "
                 "VALUES ('op1', ?, 'done', '{}', 't', 't')", (a_id["source_id"],))
    conn.execute("UPDATE volumes SET organized_op_id = 'op1' WHERE id = ?", (a_id["id"],))
    conn.execute("DELETE FROM volume_fingerprints WHERE volume_id = ?", (a_id["id"],))
    summary = run(conn, library)
    assert summary.fingerprinted == 0 and summary.candidates == 0
    assert candidates(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM volume_fingerprints WHERE volume_id = ?", (a_id["id"],)).fetchone()[0] == 0


def test_pending_is_listed_once_per_round_not_once_per_volume(env, tmp_path, monkeypatch):
    """指紋を作る巻を 1 つ作るたびに全巻を引き直す(巻数 × 巻数の SQL と stat)と、蔵書が増えると遅くなる(agy #40)。"""
    lib = tmp_path / "lib"
    for i in range(1, 7):
        write_zip(lib / f"S{i} v01.cbz", scan_pages(i, 6))
    _, conn, library = env(lib)
    calls = []
    original = duplicates.DuplicateScanner.pending

    def counting(self, done):
        result = original(self, done)
        calls.append(len(result))
        return result

    monkeypatch.setattr(duplicates.DuplicateScanner, "pending", counting)
    assert run(conn, library).fingerprinted == 6
    assert calls == [6, 0]  # 最初に 6 巻を拾い、全部作ってから 1 回だけ引き直して終わる


def test_pending_rescans_after_a_volume_is_split_into_children(env, tmp_path):
    lib = tmp_path / "lib"
    inner = zip_bytes([(f"v01/{n}", d) for n, d in scan_pages(1, 8)] + [(f"v02/{n}", d) for n, d in scan_pages(2, 8)])
    write_zip(lib / "set.zip", [("pack.zip", inner)])
    write_zip(lib / "plain v03.cbz", scan_pages(3, 8))
    _, conn, library = env(lib)
    assert run(conn, library).fingerprinted == 3  # 分解で現れた子 2 巻 + 単独 1 巻


def test_badge_counts_ignore_pairs_whose_volume_left_the_list(env, tmp_path):
    """片方が一覧から消えた(missing・整理済み)候補は、画面にも出ないので印にも数えない(agy #40)。"""
    lib = tmp_path / "lib"
    write_zip(lib / "A v01.cbz", scan_pages(1, 12))
    write_zip(lib / "B v01.cbz", scan_pages(1, 12, scale=0.6))
    _, conn, library = env(lib)
    run(conn, library)
    assert sorted(duplicates.candidate_counts(conn).values()) == [1, 1]
    conn.execute("UPDATE volumes SET missing = 1 WHERE title = 'B v01'")
    assert duplicates.candidate_counts(conn) == {}
