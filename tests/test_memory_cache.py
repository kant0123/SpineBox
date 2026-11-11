"""RAM 層(MemoryCache)と、7z / rar のまとめ取り出し・先読み。

7z が無くても走る部分: MemoryCache 本体、7z リーダーのまとめ取り出し(7z の出力を差し替える)、
Library の先読み(zip リーダーを「まとめて読む形式」に見せかける)。実機の 7z を使うものは `sevenzip` fixture で skip する。
"""
from concurrent.futures import ThreadPoolExecutor
import zipfile

import pytest
from fastapi.testclient import TestClient

from archives7z import make_7z, rar5_bytes
from conftest import page_bytes, snapshot, write_zip, zip_bytes
from spinebox import library as library_mod
from spinebox.app import create_app
from spinebox.archive import ArchiveError, LimitExceeded
from spinebox.archive import sevenzip as sz
from spinebox.archive import zipreader as zipreader_mod
from spinebox.archive.base import ArchiveReader
from spinebox.archive.zipreader import ZipReader
from spinebox.cache import MemoryCache
from spinebox.config import ExtractionLimits

LIMITS = ExtractionLimits()


# --- MemoryCache --------------------------------------------------------------


def test_lru_evicts_oldest_and_never_exceeds_the_limit():
    m = MemoryCache(10)
    for key in "abc":
        assert m.put(key, b"xxxx")
        assert m.total_bytes <= 10
    assert m.get("a") is None and m.get("b") == b"xxxx" and m.get("c") == b"xxxx"
    m.get("b")  # b を最近使ったことにする → 次に消えるのは c
    m.put("d", b"yyyy")
    assert "c" not in m and "b" in m and "d" in m
    assert m.total_bytes == 8


def test_replacing_a_key_does_not_double_count():
    m = MemoryCache(10)
    m.put("a", b"12345678")
    m.put("a", b"12")
    assert (m.total_bytes, len(m)) == (2, 1)


def test_oversized_value_and_zero_limit_store_nothing():
    m = MemoryCache(4)
    m.put("small", b"12")
    assert not m.put("big", b"12345")
    assert "big" not in m and "small" in m  # 置けないものは既存を追い出さない
    off = MemoryCache(0)
    assert not off.enabled and not off.put("a", b"x") and len(off) == 0


def test_explicit_size_for_non_bytes_values():
    m = MemoryCache(100)
    m.put("listing", ("entries", {}), size=60)
    m.put("other", ("x",), size=60)
    assert "listing" not in m and m.total_bytes == 60


def test_concurrent_puts_keep_the_total_bounded():
    m = MemoryCache(1000)

    def work(n):
        for i in range(200):
            m.put(f"{n}-{i}", b"x" * (i % 97 + 1))
            assert m.total_bytes <= 1000

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(work, range(8)))
    assert m.total_bytes == sum(len(m.get(k)) for k in list(m._items))  # noqa: SLF001


# --- 7z リーダーのまとめ取り出し(7z の出力を差し替える) ----------------------


def _crc(data: bytes) -> str:
    import zlib
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


FILES = [("v\\b.jpg", b"BBBB"), ("v\\a [*].jpg", b"AA"), ("v\\日本語.jpg", b"CCCCCC"), ("v\\dup.jpg", b"D1"),
         ("v\\dup.jpg", b"D2")]


def _fake_reader(monkeypatch, files=FILES, *, crc=True, output=None):
    listing = "".join(f"Path = {p}\nSize = {len(d)}\nCRC = {_crc(d) if crc else ''}\n\n" for p, d in files)
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: listing.encode("utf-8"))
    calls = []

    def fake_stream(cmd, dest, max_bytes, timeout):
        names = cmd[cmd.index("--") + 2:]
        calls.append(names)
        if output is not None:
            data = output
        else:
            # 7z は引数の順ではなく書庫内の順に出す。同名は両方出す
            data = b"".join(d for p, d in files if p in names)
        if len(data) > max_bytes:
            raise LimitExceeded("over")
        dest.write(data)
        return len(data)

    monkeypatch.setattr(sz, "_stream", fake_stream)
    reader = sz.SevenZipReader("x.7z", "7z")
    return reader, reader.raw_entries(), calls


def test_read_entries_splits_one_7z_run_by_listing_order(monkeypatch):
    reader, entries, calls = _fake_reader(monkeypatch)
    b, a, ja = entries[0], entries[1], entries[2]
    got = reader.read_entries([ja, a, b], LIMITS)  # 要求の順は書庫内の順と違ってよい
    assert got == {b.index: b"BBBB", a.index: b"AA", ja.index: b"CCCCCC"}
    assert calls == [["v\\b.jpg", "v\\a [*].jpg", "v\\日本語.jpg"]]  # 1 回の起動で、7z が出したままの名前


def test_read_entries_reads_duplicate_names_one_by_one(monkeypatch):
    reader, entries, calls = _fake_reader(monkeypatch)
    dup = entries[3]
    with pytest.raises(ArchiveError, match="目次と合わない"):
        reader.read_entries([entries[0], entries[1], dup], LIMITS)  # 同名は 1 つずつ → サイズ不一致で読まない
    assert calls[0] == ["v\\b.jpg", "v\\a [*].jpg"] and calls[1] == ["v\\dup.jpg"]


def test_read_entries_single_entry_uses_copy_entry(monkeypatch):
    reader, entries, calls = _fake_reader(monkeypatch)
    assert reader.read_entries([entries[2]], LIMITS) == {entries[2].index: b"CCCCCC"}
    assert calls == [["v\\日本語.jpg"]]


@pytest.mark.parametrize("output, message", [
    (b"BBBBA", "合わない"),             # 短い
    (b"BBBBAAX", "超える"),             # 長い(余分は読まずに止める)
    (b"BBBBAB", "CRC"),                 # 長さは合うが中身が違う
])
def test_read_entries_rejects_mismatched_output(monkeypatch, output, message):
    reader, entries, _ = _fake_reader(monkeypatch, output=output)
    with pytest.raises(ArchiveError, match=message):
        reader.read_entries([entries[0], entries[1]], LIMITS)


def test_read_entries_without_crc_still_checks_the_total(monkeypatch):
    reader, entries, _ = _fake_reader(monkeypatch, crc=False, output=b"AAAABB")
    # CRC が無ければ切り分けだけ(合計は合っている)
    assert reader.read_entries([entries[0], entries[1]], LIMITS) == {0: b"AAAA", 1: b"BB"}


def test_read_entries_checks_entry_limit_before_running_7z(monkeypatch):
    reader, entries, calls = _fake_reader(monkeypatch)
    with pytest.raises(LimitExceeded):
        reader.read_entries([entries[0], entries[2]], ExtractionLimits(max_entry_bytes=5))
    assert calls == []


def test_listing_state_round_trip_skips_7z_l(monkeypatch):
    reader, entries, _ = _fake_reader(monkeypatch)
    state = reader.listing_state()
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: pytest.fail("7z l を起動した"))
    restored = sz.SevenZipReader("x.7z", "7z")
    restored.restore_listing(state)
    assert restored.raw_entries() == entries
    other = sz.SevenZipReader.from_cache_state(reader.cache_state())
    assert other.raw_entries() == entries
    assert other.read_entries([entries[0], entries[1]], LIMITS) == {0: b"BBBB", 1: b"AA"}


def test_default_read_entries_reads_one_by_one(tmp_path):
    path = write_zip(tmp_path / "a.zip", [("1.jpg", b"one"), ("2.jpg", b"two")])
    with ZipReader(path) as r:
        entries = r.raw_entries()
        assert not r.batch_reads
        assert r.read_entries(entries, LIMITS) == {0: b"one", 1: b"two"}


def test_cached_zip_reader_reads_head_by_reopening(tmp_path):
    data = page_bytes("head") + b"tail"
    path = write_zip(tmp_path / "a.zip", [("001.jpg", data)])
    with ZipReader(path) as r:
        state, entry = r.cache_state(), r.raw_entries()[0]
    cached = ZipReader.from_cache_state(state)
    assert cached.read_head(entry, 5) == data[:5]  # 先頭だけでも展開の途中まで読むので zipfile に任せる
    assert cached.read_head(entry, len(data) + 100) == data


# --- 先読みの枚数 -------------------------------------------------------------


def test_window_doubles_only_while_reading_on():
    state = library_mod._ArchiveState()  # noqa: SLF001
    assert state.next_window(0) == 1
    state.advance(1, 1)
    assert state.next_window(1) == 2
    state.advance(3, 2)
    assert state.next_window(4) == 4  # 並行の先読みで少し先から来ても続きとみなす
    state.advance(8, 4)
    assert state.next_window(0) == 1  # 開き直して先頭から → 1 枚から
    assert state.next_window(8 + library_mod.READAHEAD_PAGES + 1) == 1  # 大きく飛んだ
    for _ in range(20):
        state.advance(9, state.next_window(9) or 1)
    assert state.next_window(9) == library_mod.READAHEAD_PAGES


def test_archive_states_are_bounded(monkeypatch, make_settings, tmp_path):
    from spinebox.cache import ExtractionCache
    monkeypatch.setattr(library_mod, "MAX_ARCHIVE_STATES", 3)
    settings = make_settings(tmp_path)
    lib = library_mod.Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    busy = lib._archive("busy")  # noqa: SLF001
    with busy.lock:
        for key in "abcde":
            lib._archive(key)  # noqa: SLF001
            assert len(lib._archives) <= 3  # noqa: SLF001
        assert lib._archive("busy") is busy  # noqa: SLF001 (使用中の状態は捨てない)


def test_page_table_locks_are_bounded(monkeypatch, make_settings, tmp_path):
    from spinebox.cache import ExtractionCache
    monkeypatch.setattr(library_mod, "MAX_TABLE_LOCKS", 3)
    settings = make_settings(tmp_path)
    lib = library_mod.Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    busy = lib._table_lock("busy")  # noqa: SLF001
    with busy:
        for key in "abcde":
            lib._table_lock(key)  # noqa: SLF001
            assert len(lib._table_locks) <= 3  # noqa: SLF001
        assert lib._table_lock("busy") is busy  # noqa: SLF001 (使用中のロックは捨てない)


# --- Library(zip を「まとめて読む形式」に見せかけて、7z 無しで先読みを確かめる) --


@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


def _pages(n, size=1000):
    return [(f"{i:03d}.jpg", page_bytes(f"p{i}") + b"x" * size) for i in range(n)]


@pytest.fixture
def batching_zip(monkeypatch):
    """zip リーダーをまとめ読みの形式にして、read_entries に渡されたエントリ番号の列を記録する。"""
    batches = []
    real = ArchiveReader.read_entries

    def counted(self, entries, limits):
        batches.append([e.index for e in entries])
        return real(self, entries, limits)

    monkeypatch.setattr(ZipReader, "batch_reads", True)
    monkeypatch.setattr(ZipReader, "read_entries", counted)
    return batches


def _read_all(c, vid):
    detail = c.get(f"/api/volumes/{vid}").json()
    return [c.get(f"/api/volumes/{vid}/pages/{p['index']}").content for p in detail["pages"]]


def test_readahead_grows_and_pages_come_from_memory(make_client, tmp_path, batching_zip):
    files = _pages(20)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert _read_all(c, vol["id"]) == [d for _, d in files]
    assert [len(b) for b in batching_zip] == [1, 2, 4, 8, 5]  # 最初は 1 枚、続きを読むたびに倍(残りは 5 枚)
    memory = c.app.state.memory
    assert len(memory) == 21  # 20 ページ + ページ表 1 件
    batching_zip.clear()
    assert _read_all(c, vol["id"]) == [d for _, d in files]
    assert batching_zip == []  # 2 回目は全部 RAM から


def test_memory_never_exceeds_the_limit_while_reading(make_client, tmp_path, batching_zip):
    files = _pages(30, size=2000)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    limit = 3 * 2100  # 3 ページ分ほど
    c, _ = make_client(tmp_path / "lib", memory_cache_limit_bytes=limit)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    memory = c.app.state.memory
    vid = vol["id"]
    detail = c.get(f"/api/volumes/{vid}").json()
    for p in detail["pages"]:
        assert c.get(f"/api/volumes/{vid}/pages/{p['index']}").content == files[p["index"]][1]
        assert memory.total_bytes <= limit
    # 先読みは上限の 1/4 まで(ここでは 1 枚ずつ)。古いページは消えている
    assert max(len(b) for b in batching_zip) == 1 and len(memory) <= 3
    assert c.get(f"/api/volumes/{vid}/pages/0").content == files[0][1]  # 消えたページは読み直す


def test_failed_batch_falls_back_to_the_requested_page(make_client, tmp_path, monkeypatch):
    files = _pages(6)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    monkeypatch.setattr(ZipReader, "batch_reads", True)

    def broken(self, entries, limits):
        if len(entries) > 1:
            raise ArchiveError("先読みの側が壊れている")
        return ArchiveReader.read_entries(self, entries, limits)

    monkeypatch.setattr(ZipReader, "read_entries", broken)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert _read_all(c, vol["id"]) == [d for _, d in files]


def test_zip_pages_are_not_kept_in_memory(make_client, tmp_path):
    files = _pages(5)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    write_zip(tmp_path / "lib" / "outer.zip", [("v02.zip", write_zip(tmp_path / "x.zip", files).read_bytes())])
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    for vol in c.get("/api/volumes").json():
        assert _read_all(c, vol["id"]) == [d for _, d in files]
    # ページ表(目次)は RAM に置く。ページ本体は置かない(計測で差が出なかった)
    assert 0 < c.app.state.memory.total_bytes < 64_000


def test_zip_page_table_avoids_reopening_the_archive(make_client, tmp_path, monkeypatch):
    files = _pages(6)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    assert c.get(f"/api/volumes/{vid}/pages/0").content == files[0][1]  # ここでページ表を作る
    calls = []
    real_init = ZipReader.__init__

    def counted(self, path):
        calls.append(path)
        real_init(self, path)

    monkeypatch.setattr(ZipReader, "__init__", counted)
    assert c.get(f"/api/volumes/{vid}").status_code == 200
    for i in range(1, len(files)):
        assert c.get(f"/api/volumes/{vid}/pages/{i}").content == files[i][1]
    etag = c.get(f"/api/volumes/{vid}/pages/0").headers["etag"]
    assert c.get(f"/api/volumes/{vid}/pages/0", headers={"If-None-Match": etag}).status_code == 304
    assert calls == []  # 2 回目以降は zip の中央ディレクトリを読み直さない


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_page_table_reads_bzip2_and_lzma_pages(make_client, tmp_path, compression):
    files = _pages(4)
    path = tmp_path / "lib" / "v01.cbz"
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w", compression) as z:
        for name, data in files:
            z.writestr(name, data)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    for i, (_, data) in enumerate(files):
        assert c.get(f"/api/volumes/{vid}/pages/{i}").content == data


def test_page_table_falls_back_to_zipfile_for_unsupported_compression(make_client, tmp_path, monkeypatch):
    files = _pages(3)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    assert c.get(f"/api/volumes/{vid}/pages/0").content == files[0][1]

    def unsupported(raw, method, limit):
        raise NotImplementedError("未対応の圧縮方式")

    monkeypatch.setattr(zipreader_mod, "_decompress", unsupported)
    opened = []
    real_init = ZipReader.__init__

    def counted(self, path):
        opened.append(path)
        real_init(self, path)

    monkeypatch.setattr(ZipReader, "__init__", counted)
    assert c.get(f"/api/volumes/{vid}/pages/1").content == files[1][1]
    assert len(opened) == 1  # 未対応の圧縮方式だけ zipfile に任せる


def test_folder_page_table_avoids_listing_the_folder_again(make_client, tmp_path, monkeypatch):
    folder = tmp_path / "lib" / "v01"
    folder.mkdir(parents=True)
    files = _pages(5)
    for name, data in files:
        (folder / name).write_bytes(data)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    assert c.get(f"/api/volumes/{vid}/pages/0").content == files[0][1]
    calls = []
    real_sort = library_mod.sort_pages

    def counted(names):
        calls.append(1)
        return real_sort(names)

    monkeypatch.setattr(library_mod, "sort_pages", counted)
    assert c.get(f"/api/volumes/{vid}").status_code == 200
    for i in range(1, len(files)):
        assert c.get(f"/api/volumes/{vid}/pages/{i}").content == files[i][1]
    assert calls == []  # 2 回目以降はフォルダを列挙しない


def test_page_table_is_dropped_when_the_file_changes(make_client, tmp_path):
    old = _pages(3)
    path = write_zip(tmp_path / "lib" / "v01.cbz", old)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    assert c.get(f"/api/volumes/{vid}/pages/0").content == old[0][1]
    new = [(name, data + b"changed") for name, data in old]
    write_zip(path, new)  # サイズが変わるので版も変わる
    assert c.get(f"/api/volumes/{vid}/pages/0").content == new[0][1]


def test_page_table_is_rebuilt_when_the_extracted_inner_archive_is_gone(make_client, tmp_path):
    files = _pages(5)
    inner_set = zip_bytes([
        ("v01.zip", write_zip(tmp_path / "x1.zip", files).read_bytes()),
        ("v02.zip", write_zip(tmp_path / "x2.zip", files).read_bytes()),
    ])
    write_zip(tmp_path / "lib" / "outer.zip", [("set v01-02.zip", inner_set)])
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (outer,) = c.get("/api/volumes").json()  # 外側アーカイブの中の内側アーカイブ(分解前は 1 巻)
    c.get(f"/api/volumes/{outer['id']}")  # 内側の巻を子として登録する
    child = next(v for v in c.get("/api/volumes").json() if v["parent_id"] == outer["id"])
    assert c.get(f"/api/volumes/{child['id']}/pages/0").content == files[0][1]
    for cached in list(c.app.state.cache.directory.iterdir()):
        cached.unlink()
    assert c.get(f"/api/volumes/{child['id']}/pages/1").content == files[1][1]  # 取り出し直して読む


def test_concurrent_requests_share_one_extraction(make_client, tmp_path, batching_zip):
    files = _pages(12)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    c, _ = make_client(tmp_path / "lib")
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    c.get(f"/api/volumes/{vid}/pages/0")
    c.get(f"/api/volumes/{vid}/pages/1")  # 次は 4 枚まとめて(2〜5)
    with ThreadPoolExecutor(4) as ex:
        got = list(ex.map(lambda i: c.get(f"/api/volumes/{vid}/pages/{i}").content, [3, 2, 5, 4]))
    assert got == [files[i][1] for i in (3, 2, 5, 4)]
    extracted = [i for b in batching_zip for i in b]
    assert len(extracted) == len(set(extracted))  # 並行した要求でも同じページを二重に取り出さない


# --- 実機の 7z ------------------------------------------------------------------


def _solid(sevenzip, path, n=24):
    files = {f"v/{i:03d}.jpg": page_bytes(f"s{i}") * 50 for i in range(n)}
    make_7z(sevenzip, path, files)
    return [files[k] for k in sorted(files)]


def _count_7z(monkeypatch):
    runs = {"l": 0, "e": [], }
    real_capture, real_stream = sz._run_capture, sz._stream

    def capture(cmd, timeout):
        runs["l"] += 1
        return real_capture(cmd, timeout)

    def stream(cmd, dest, max_bytes, timeout):
        runs["e"].append(len(cmd) - cmd.index("--") - 2)
        return real_stream(cmd, dest, max_bytes, timeout)

    monkeypatch.setattr(sz, "_run_capture", capture)
    monkeypatch.setattr(sz, "_stream", stream)
    return runs


def test_7z_volume_reads_with_few_7z_runs(sevenzip, make_client, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    expected = _solid(sevenzip, lib / "solid v01.7z")
    before = snapshot(lib)
    c, _ = make_client(lib, sevenzip_path=sevenzip)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    runs = _count_7z(monkeypatch)
    vid = vol["id"]
    c.get(f"/api/volumes/{vid}")
    assert c.get(f"/api/volumes/{vid}/pages/0").content == expected[0]
    assert runs["e"] == [1]  # 開いて最初のページは 1 枚だけ取り出す(速く出す)
    assert _read_all(c, vid) == expected
    assert len(runs["e"]) <= 6 and sum(runs["e"]) == len(expected)  # 24 ページで 7z e は数回
    assert runs["l"] == 1  # 目次(7z l)は RAM に持ち越す
    assert snapshot(lib) == before


def test_7z_without_memory_reads_page_by_page(sevenzip, make_client, tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    expected = _solid(sevenzip, lib / "solid v01.7z", n=5)
    c, _ = make_client(lib, sevenzip_path=sevenzip, memory_cache_limit_bytes=0)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    runs = _count_7z(monkeypatch)
    assert _read_all(c, vol["id"]) == expected
    assert runs["e"] == [1] * 5 and runs["l"] == 6  # 従来どおり: 要求ごとに 7z l、ページごとに 7z e
    assert c.app.state.memory.total_bytes == 0


def test_rar_in_7z_pages_and_memory_limit(sevenzip, make_client, tmp_path, monkeypatch):
    names = [f"r/{i:02d}.jpg" for i in range(16)]
    rar = rar5_bytes([("r/", None)] + [(n, page_bytes(n) * 40) for n in names])
    lib = tmp_path / "lib"
    make_7z(sevenzip, lib / "outer.7z", {"vol 01.rar": rar})
    limit = 4 * len(page_bytes(names[0]) * 40) + 8000  # 数ページと目次ぶん
    c, _ = make_client(lib, sevenzip_path=sevenzip, memory_cache_limit_bytes=limit)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    detail = c.get(f"/api/volumes/{vid}").json()
    for p in detail["pages"]:
        assert c.get(f"/api/volumes/{vid}/pages/{p['index']}").content == page_bytes(names[p["index"]]) * 40
        assert c.app.state.memory.total_bytes <= limit


def test_7z_concurrent_page_requests_are_correct(sevenzip, make_client, tmp_path):
    lib = tmp_path / "lib"
    expected = _solid(sevenzip, lib / "solid v01.7z", n=16)
    c, _ = make_client(lib, sevenzip_path=sevenzip)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    vid = vol["id"]
    c.get(f"/api/volumes/{vid}")
    order = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 15, 14, 13]
    with ThreadPoolExecutor(5) as ex:
        got = list(ex.map(lambda i: c.get(f"/api/volumes/{vid}/pages/{i}").content, order))
    assert got == [expected[i] for i in order]



def test_zero_limit_reads_page_by_page_without_batching(make_client, tmp_path, batching_zip):
    files = _pages(6)
    write_zip(tmp_path / "lib" / "v01.cbz", files)
    c, _ = make_client(tmp_path / "lib", memory_cache_limit_bytes=0)
    c.post("/api/scan")
    (vol,) = c.get("/api/volumes").json()
    assert _read_all(c, vol["id"]) == [d for _, d in files]
    assert batching_zip == [] and c.app.state.memory.total_bytes == 0  # 0 = 従来どおり 1 ページずつ
