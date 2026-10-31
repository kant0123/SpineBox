import pytest

from conftest import RawName, unicode_path_extra, write_zip
from spinebox.archive import LimitExceeded, UnsupportedArchive, list_entries, open_archive
from spinebox.config import ExtractionLimits

NAME = "第01巻/表紙.jpg"


def _entry(path):
    with open_archive(path) as r:
        (e,) = list_entries(r, ExtractionLimits()).entries
        return e


def test_cp932_without_flag(tmp_path):
    raw = NAME.encode("cp932")
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw), b"x")]))
    assert (e.name, e.raw_name, e.encoding) == (NAME, raw, "cp932")


def test_cp932_trail_byte_5c_is_not_a_separator(tmp_path):
    # 「ソ」は cp932 で 0x83 0x5C。2 バイト目を区切り(\)と取り違えない
    name = "ソード/01.jpg"
    raw = name.encode("cp932")
    assert b"\\" in raw
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw), b"x")]))
    assert e.name == name


def test_utf8_flag(tmp_path):
    raw = NAME.encode("utf-8")
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw, utf8_flag=True), b"x")]))
    assert (e.name, e.raw_name, e.encoding) == (NAME, raw, "utf-8")


def test_unicode_path_extra(tmp_path):
    # ヘッダの名前は化けた(cp437 想定の)代替名、正しい名前は 0x7075 にある
    raw = b"_01_/_.jpg"
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw, extra=unicode_path_extra(raw, NAME)), b"x")]))
    assert (e.name, e.raw_name, e.encoding) == (NAME, raw, "utf-8 (unicode path extra)")


def test_unicode_path_extra_with_wrong_crc_is_ignored(tmp_path):
    raw = NAME.encode("cp932")
    extra = unicode_path_extra(raw, "別の名前.jpg", crc=12345)
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw, extra=extra), b"x")]))
    assert (e.name, e.encoding) == (NAME, "cp932")


def test_undecodable_name_uses_replacement(tmp_path):
    raw = b"\x81\x7f\xff.jpg"
    e = _entry(write_zip(tmp_path / "a.zip", [(RawName(raw), b"x")]))
    assert e.encoding == "cp932 (replaced)"
    assert "\ufffd" in e.name and e.raw_name == raw


def test_read_entry_in_memory_and_limit(tmp_path):
    path = write_zip(tmp_path / "a.zip", [("p.jpg", b"0123456789")])
    with open_archive(path) as r:
        (e,) = list_entries(r, ExtractionLimits()).entries
        assert r.read_entry(e, ExtractionLimits()) == b"0123456789"
        with pytest.raises(LimitExceeded):
            r.read_entry(e, ExtractionLimits(max_entry_bytes=5))


def test_archive_limits(tmp_path):
    path = write_zip(tmp_path / "a.zip", [(f"{i}.jpg", b"x" * 10) for i in range(3)])
    with open_archive(path) as r:
        with pytest.raises(LimitExceeded, match="エントリ数"):
            list_entries(r, ExtractionLimits(max_entries=2))
        with pytest.raises(LimitExceeded, match="合計"):
            list_entries(r, ExtractionLimits(max_total_bytes=29))


def test_unsafe_entries_skipped_with_reason(tmp_path):
    path = write_zip(tmp_path / "a.zip", [
        (RawName(b"../x.jpg"), b"x"), (RawName(b"/abs.jpg"), b"x"), (RawName(b"C:\\x.jpg"), b"x"), ("ok.jpg", b"x"),
    ])
    with open_archive(path) as r:
        listing = list_entries(r, ExtractionLimits())
    assert [e.name for e in listing.entries] == ["ok.jpg"]
    assert sorted(s.entry_name for s in listing.skipped) == ["../x.jpg", "/abs.jpg", "C:\\x.jpg"]
    assert all(s.reason for s in listing.skipped)


def test_unsupported_formats(tmp_path):
    for name in ["a.7z", "a.rar", "a.cbr"]:
        (tmp_path / name).write_bytes(b"junk")
        with pytest.raises(UnsupportedArchive):
            open_archive(tmp_path / name)
