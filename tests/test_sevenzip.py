"""7-Zip CLI リーダー。

純粋な部分(目次の解釈・コマンドの組み立て・異常系)は 7z が無くても走る。実機の 7z を使うテストは
`sevenzip` fixture が見つからなければ skip する(CI の ubuntu など)。
"""
import io
import sys

import pytest

from archives7z import make_7z, rar5_bytes
from conftest import NO_SEVENZIP, page_bytes
from spinebox.archive import ArchiveError, LimitExceeded, UnsupportedArchive, list_entries, open_archive
from spinebox.archive import sevenzip as sz
from spinebox.archive.base import reader_available
from spinebox.config import ExtractionLimits

LIMITS = ExtractionLimits()

LISTING = (
    "Path = vol1\r\nFolder = +\r\nSize = 0\r\nAttributes = D\r\nCRC = \r\n\r\n"
    "Path = vol1\\001.jpg\r\nFolder = -\r\nSize = 12\r\nAttributes = A\r\nCRC = 0C47DEFE\r\n\r\n"
    "Path = vol1\\日本語 [1].jpg\r\nSize = 5\r\nAttributes = A\r\n\r\n"
).encode("utf-8")


# --- 目次の解釈(7z 不要) -----------------------------------------------------


def test_parse_listing_handles_crlf_bom_and_names():
    records = sz.parse_listing(b"\xef\xbb\xbf" + LISTING)
    assert [r["Path"] for r in records] == ["vol1", "vol1\\001.jpg", "vol1\\日本語 [1].jpg"]
    assert records[1]["CRC"] == "0C47DEFE" and records[0]["CRC"] == ""


def test_parse_listing_skips_preamble_before_separator():
    header = b"\n7-Zip 26.02\n\nListing archive: x.7z\n\n--\nPath = x.7z\nType = 7z\n\n----------\n"
    records = sz.parse_listing(header + b"Path = a.jpg\nSize = 1\n\n")
    assert [r["Path"] for r in records] == ["a.jpg"]


def test_parse_listing_keeps_unicode_line_separators_in_names():
    records = sz.parse_listing("Path = a\u2028b.jpg\nSize = 1\n\n".encode("utf-8"))
    assert records[0]["Path"] == "a\u2028b.jpg"


def test_parse_listing_continuation_line_and_stripped_empty_value():
    # 名前に改行があると次の行は `キー = 値` の形にならない。値が空の行は末尾の空白が落ちることもある
    records = sz.parse_listing(b"Path = a\nb.jpg\nSize = 1\nCreated =\nCRC = 1\n\n")
    assert records[0]["Path"] == "a\nb.jpg" and records[0]["Created"] == "" and records[0]["CRC"] == "1"


def test_parse_listing_empty_and_garbage():
    assert sz.parse_listing(b"") == []
    assert sz.parse_listing(b"ERROR: nothing useful\n\n") == []


def test_entries_from_records():
    entries, raw = sz.entries_from_records(sz.parse_listing(LISTING))
    assert [(e.name, e.size, e.is_dir) for e in entries] == [
        ("vol1/", 0, True), ("vol1\\001.jpg", 12, False), ("vol1\\日本語 [1].jpg", 5, False)]
    assert raw[1] == "vol1\\001.jpg" and entries[1].raw_name == b"vol1\\001.jpg"


@pytest.mark.parametrize("record, is_dir", [
    ({"Path": "d", "Attributes": "D", "Size": "0"}, True),
    ({"Path": "d", "Attributes": "D_ drwxr-xr-x", "Size": "0"}, True),
    ({"Path": "d", "Folder": "+", "Size": "0"}, True),
    ({"Path": "f", "Attributes": " -rw-r--r--", "Size": "3"}, False),
    ({"Path": "f", "Attributes": "A", "Size": "3"}, False),
])
def test_directory_detection(record, is_dir):
    (e,), _ = sz.entries_from_records([record])
    assert e.is_dir is is_dir


def test_links_are_not_pages_and_bad_sizes_are_errors():
    entries, _ = sz.entries_from_records([
        {"Path": "link.jpg", "Size": "9", "Symbolic Link": "/etc/passwd"}, {"Path": "ok.jpg", "Size": "1"}])
    assert [e.name for e in entries] == ["ok.jpg"]
    with pytest.raises(ArchiveError):
        sz.entries_from_records([{"Path": "f.jpg", "Size": ""}])
    with pytest.raises(ArchiveError):
        sz.entries_from_records([{"Path": "f.jpg", "Size": "abc"}])


def test_dangerous_7z_names_are_skipped_by_list_entries(monkeypatch):
    out = b"Path = ..\\evil.jpg\nSize = 1\n\nPath = C:\\x.jpg\nSize = 1\n\nPath = ok\\1.jpg\nSize = 1\n\n"
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: out)
    listing = list_entries(sz.SevenZipReader("dummy.7z", "7z"), LIMITS)
    assert [e.name for e in listing.entries] == ["ok/1.jpg"]
    assert len(listing.skipped) == 2


# --- コマンドの組み立てと異常系(7z 不要) --------------------------------------


def test_commands_pin_encoding_and_stop_wildcards(tmp_path):
    archive = tmp_path / "-weird [1].7z"
    cmd = sz.list_command("7z", archive)
    assert cmd[:2] == ["7z", "l"] and "-slt" in cmd and "-ba" in cmd and "-sccUTF-8" in cmd and "-spd" in cmd
    assert cmd[-2:] == ["--", str(archive)]  # -- の後ろはスイッチとして読まれない
    cmd = sz.extract_command("7z", archive, "a\\*.jpg")
    assert cmd[:3] == ["7z", "e", "-so"] and "-spd" in cmd and "-scsUTF-8" in cmd
    assert cmd[-3:] == ["--", str(archive), "a\\*.jpg"]


def test_empty_output_is_an_error_not_an_empty_archive(monkeypatch):
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: b" \r\n")
    with pytest.raises(ArchiveError, match="空"):
        sz.SevenZipReader("x.7z", "7z").raw_entries()
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: b"WARNING: junk only\r\n\r\n")
    with pytest.raises(ArchiveError, match="解釈"):
        sz.SevenZipReader("x.7z", "7z").raw_entries()


def test_run_capture_nonzero_exit_is_an_error():
    code = "import sys; sys.stderr.write('Headers Error'); sys.exit(2)"
    with pytest.raises(ArchiveError, match="終了コード 2.*Headers Error"):
        sz._run_capture([sys.executable, "-c", code], 30)


def test_failure_reason_is_one_error_line_without_the_archive_path(tmp_path, caplog):
    # 7-Zip の実際の出力に近い標準エラー(BOM なし・CRLF・警告行・フルパスつきのエラー行)
    archive = tmp_path / "壊れた巻.zip"
    stderr = (f"\r\nWARNING: junk\r\nERROR: {archive} : {archive} Open ERROR: Cannot open the file as [zip] archive\r\n"
              "\r\nERRORS:\r\nIs not archive\r\n")
    script = f"import sys; sys.stderr.buffer.write({stderr.encode('utf-8')!r}); sys.exit(2)"
    cmd = [sys.executable, "-c", script, "--", str(archive)]
    with caplog.at_level("WARNING", logger="spinebox.archive.sevenzip"):
        with pytest.raises(ArchiveError) as info:
            sz._run_capture(cmd, 30)
    msg = str(info.value)
    assert msg == ("7-Zip が失敗した(終了コード 2): ERROR: <ファイル> : <ファイル> "
                   "Open ERROR: Cannot open the file as [zip] archive")
    assert "\n" not in msg and str(archive) not in msg and "WARNING" not in msg and "ERRORS" not in msg
    assert "Is not archive" in caplog.text  # 全文はログ側に残る


def test_run_capture_timeout_and_missing_executable(tmp_path):
    with pytest.raises(ArchiveError, match="終わらない"):
        sz._run_capture([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    with pytest.raises(ArchiveError, match="起動できない"):
        sz._run_capture([str(tmp_path / "nope.exe")], 5)


def test_stream_writes_stdout_and_checks_exit_code():
    dest = io.BytesIO()
    ok = "import sys; sys.stdout.buffer.write(b'x' * 3000000)"
    assert sz._stream([sys.executable, "-c", ok], dest, 10_000_000, 30) == 3_000_000
    assert len(dest.getvalue()) == 3_000_000
    bad = "import sys; sys.stdout.buffer.write(b'partial'); sys.stderr.write('CRC Failed'); sys.exit(2)"
    with pytest.raises(ArchiveError, match="CRC Failed"):
        sz._stream([sys.executable, "-c", bad], io.BytesIO(), 10_000_000, 30)


def test_stream_stops_at_the_byte_limit_and_times_out():
    flood = "import sys\nwhile True: sys.stdout.buffer.write(b'x' * 65536)"
    dest = io.BytesIO()
    with pytest.raises(LimitExceeded):
        sz._stream([sys.executable, "-c", flood], dest, 2_000_000, 30)
    assert len(dest.getvalue()) <= 2_000_000
    with pytest.raises(ArchiveError, match="終わらない"):
        sz._stream([sys.executable, "-c", "import time; time.sleep(30)"], io.BytesIO(), 10, 0.5)


def test_copy_entry_rejects_size_mismatch_and_over_limit(monkeypatch):
    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: b"Path = a.jpg\nSize = 10\n\n")
    monkeypatch.setattr(sz, "_stream", lambda cmd, dest, max_bytes, timeout: dest.write(b"12345"))
    reader = sz.SevenZipReader("x.7z", "7z")
    (entry,) = reader.raw_entries()
    with pytest.raises(ArchiveError, match="目次と合わない"):
        reader.copy_entry(entry, io.BytesIO(), 100)
    with pytest.raises(LimitExceeded):
        reader.copy_entry(entry, io.BytesIO(), 9)


def test_copy_entry_passes_the_listed_name_to_7z(monkeypatch):
    calls = []

    def fake_stream(cmd, dest, max_bytes, timeout):
        calls.append(cmd)
        return dest.write(b"abc")

    monkeypatch.setattr(sz, "_run_capture", lambda cmd, timeout: b"Path = d\\a [*].jpg\nSize = 3\n\n")
    monkeypatch.setattr(sz, "_stream", fake_stream)
    reader = sz.SevenZipReader("x.7z", "7z")
    (entry,) = reader.raw_entries()
    reader.copy_entry(entry, io.BytesIO(), 100)
    assert calls[0][-1] == "d\\a [*].jpg"  # 7z が出したままの名前。ワイルドカードは -spd で止める


def test_without_7zip_7z_and_rar_are_unsupported(tmp_path):
    assert not reader_available("x.7z", NO_SEVENZIP) and not reader_available("x.rar", NO_SEVENZIP)
    assert reader_available("x.zip", NO_SEVENZIP)
    for name in ("a.7z", "a.cb7", "a.rar", "a.cbr"):
        (tmp_path / name).write_bytes(b"junk")
        with pytest.raises(UnsupportedArchive, match="未対応"):
            open_archive(tmp_path / name, sevenzip_path=NO_SEVENZIP)


def test_find_sevenzip(tmp_path):
    fake = tmp_path / "7z.exe"
    fake.write_bytes(b"")
    assert sz.find_sevenzip(str(fake)) == str(fake)
    assert sz.find_sevenzip(str(tmp_path / "missing.exe")) is None
    assert sz.find_sevenzip("") is None and sz.find_sevenzip(NO_SEVENZIP) is None


# --- 実機の 7z を使う ----------------------------------------------------------


def read_all(path, sevenzip, name=None):
    with open_archive(path, name, sevenzip_path=sevenzip) as r:
        listing = list_entries(r, LIMITS)
        return {e.name: r.read_entry(e, LIMITS) for e in listing.entries}


def test_7z_roundtrip_with_awkward_names(sevenzip, tmp_path):
    files = {"v 1/001.jpg": page_bytes("a"), "v 1/日本語 [1].jpg": page_bytes("b"),
             "-lead.jpg": page_bytes("dash"), "empty.jpg": b""}
    path = make_7z(sevenzip, tmp_path / "[作者] 本 -1.7z", files)
    assert read_all(path, sevenzip) == files


def test_7z_solid_many_entries_and_binary_exactness(sevenzip, tmp_path):
    files = {f"p/{i:03}.jpg": bytes([i]) * (1000 + i) + b"\r\n" for i in range(30)}
    path = make_7z(sevenzip, tmp_path / "solid.cb7", files)
    assert read_all(path, sevenzip) == files  # CRLF を含む中身が変換されない


def test_rar5_reader(sevenzip, tmp_path):
    path = tmp_path / "hand.rar"
    path.write_bytes(rar5_bytes([("Vol/", None), ("Vol/1.jpg", page_bytes("r1")), ("Vol/日本.png", page_bytes("r2"))]))
    assert read_all(path, sevenzip) == {"Vol/1.jpg": page_bytes("r1"), "Vol/日本.png": page_bytes("r2")}
    with open_archive(path, sevenzip_path=sevenzip) as r:
        assert [e.name for e in list_entries(r, LIMITS).dirs] == ["Vol/"]


def test_broken_and_missing_archives_are_archive_errors(sevenzip, tmp_path):
    junk = tmp_path / "junk.7z"
    junk.write_bytes(b"7z\xbc\xaf\x27\x1c junk")
    with open_archive(junk, sevenzip_path=sevenzip) as r:
        with pytest.raises(ArchiveError):
            r.raw_entries()
    with open_archive(tmp_path / "absent.rar", sevenzip_path=sevenzip) as r:
        with pytest.raises(ArchiveError):
            r.raw_entries()


def test_truncated_7z_is_an_error_not_short_data(sevenzip, tmp_path):
    files = {f"{i}.jpg": bytes([i]) * 200_000 for i in range(5)}
    path = make_7z(sevenzip, tmp_path / "t.7z", files)
    path.write_bytes(path.read_bytes()[:-3000])
    with pytest.raises(ArchiveError):
        read_all(path, sevenzip)


def test_entry_over_limit_is_refused_before_running_7z(sevenzip, tmp_path, monkeypatch):
    path = make_7z(sevenzip, tmp_path / "big.7z", {"a.jpg": b"x" * 5000})
    with open_archive(path, sevenzip_path=sevenzip) as r:
        (e,) = list_entries(r, LIMITS).entries
        monkeypatch.setattr(sz, "_stream", lambda *a: pytest.fail("7z を起動した"))
        with pytest.raises(LimitExceeded):
            r.read_entry(e, ExtractionLimits(max_entry_bytes=100))


def test_reading_does_not_write_next_to_the_archive(sevenzip, tmp_path):
    path = make_7z(sevenzip, tmp_path / "w" / "x.7z", {"a.jpg": b"1", "b/c.jpg": b"2"})
    before = sorted(p.name for p in path.parent.iterdir())
    read_all(path, sevenzip)
    assert sorted(p.name for p in path.parent.iterdir()) == before  # -so はディスクに展開しない
