"""RAR4 の化けた Unicode 名を OEM 名(cp932)で直す(#257)。

実データ: 作成ツールが cp932 の名前を cp437 として読んで Unicode 名を作った rar。7-Zip は Unicode 名を優先するので
`13_#âJâoü[.jpg`(= `13_#カバー.jpg`)と出し、`表` の 2 バイト目 `0x5C` を区切りにして `13_#ò/Äå.jpg` という偽のフォルダを作る。
"""
import pytest

from archives7z import _rar4_block, _rar4_unicode, rar4_bytes
from conftest import page_bytes
from spinebox.archive import list_entries, open_archive
from spinebox.archive import rar4
from spinebox.config import ExtractionLimits

SEP = chr(0x5C)  # RAR の区切り(バックスラッシュ)


def p(*parts: str) -> str:
    return SEP.join(parts)


def broken(name: str) -> tuple[bytes, str]:
    """(正しい OEM 名, cp437 として読んで化けた Unicode 名)。"""
    oem = name.encode("cp932")
    return oem, oem.decode("cp437")


COVER = broken(p("v13", "13_#カバー.jpg"))
TABLE = broken(p("v13", "13_#表紙.jpg"))  # 表 = 0x95 0x5C
FAKE_DIR = (b"v13" + SEP.encode() + b"13_#\x95", p("v13", "13_#ò"))  # 作成ツールが 0x5C で割って作ったフォルダ
CAFE = (p("v13", "caf").encode() + b"\x82.jpg", p("v13", "café.jpg"))


def sample(tmp_path):
    path = tmp_path / "a.rar"
    path.write_bytes(rar4_bytes([
        (b"v13", "v13", None),
        (FAKE_DIR[0], FAKE_DIR[1], None),
        (COVER[0], COVER[1], page_bytes("cover")),
        (TABLE[0], TABLE[1], page_bytes("table")),
        (p("v13", "13_p001.jpg").encode(), p("v13", "13_p001.jpg"), page_bytes("p1")),
        (p("v13", "正しい名前.jpg").encode("cp932"), p("v13", "正しい名前.jpg"), page_bytes("ok")),
        (CAFE[0], CAFE[1], page_bytes("cafe")),
        (p("v13", "plain.jpg").encode(), None, page_bytes("plain")),
    ]))
    return path


def test_decode_unicode_name_kinds():
    assert rar4.decode_unicode_name(b"", _rar4_unicode("表紙ab")) == "表紙ab"
    # 種類 0(下位バイトだけ)・1(high byte と組む)・3(OEM 名の写し。補正つき)
    assert rar4.decode_unicode_name(b"", bytes([0x30, 0b00010000, ord("a"), 0x42])) == "aあ"
    assert rar4.decode_unicode_name(b"abcd", bytes([0, 0b11000000, 2])) == "abcd"
    assert rar4.decode_unicode_name(b"abc", bytes([0x30, 0b11000000, 0x80 | 1, 0x10])) == "ぱひび"


def test_decode_unicode_name_broken_is_none():
    assert rar4.decode_unicode_name(b"", bytes([0, 0b10000000, 0x41])) is None  # 2 バイト目が無い
    assert rar4.decode_unicode_name(b"a", bytes([0, 0b11000000, 5])) is None  # OEM 名より長い写し


def test_recovered_name_only_for_broken_unicode_names():
    assert rar4.recovered_name(COVER[1], COVER[0]) == p("v13", "13_#カバー.jpg")
    assert rar4.recovered_name(TABLE[1], TABLE[0]) == p("v13", "13_#表紙.jpg")
    good = p("v13", "正しい名前.jpg")
    assert rar4.recovered_name(good, good.encode("cp932")) is None  # Unicode 名が正しい
    assert rar4.recovered_name(CAFE[1], CAFE[0]) is None  # 欧文の名前(cp932 として読めない)
    assert rar4.recovered_name(FAKE_DIR[1], FAKE_DIR[0]) is None  # 末尾が 1 バイト目で切れている
    assert rar4.recovered_name("plain.jpg", b"plain.jpg") is None
    assert rar4.recovered_name("Ä", b"\x8e") is None  # 読めても日本語が 2 文字未満


@pytest.mark.parametrize("word", ["élégant.jpg", "téléphone.jpg", "général.jpg", "éléments.jpg", "céréale.jpg",
                                  "derrière derrière.jpg"])
@pytest.mark.parametrize("codepage", ["cp1252", "cp850", "cp437"])
def test_recovered_name_leaves_western_names(word, codepage):
    """欧文の名前の OEM 名(cp1252 の `élégant` は cp932 だと `駘馮ant`、cp850 の `derrière` は `derri較e` と読める)を直さない。"""
    oem = word.encode(codepage)
    assert rar4.recovered_name(word, oem) is None


def test_recovered_name_needs_unicode_name_to_be_the_dos_reading_of_oem():
    oem = "表紙ほか.jpg".encode("cp932")
    assert rar4.recovered_name(oem.decode("cp437"), oem) == "表紙ほか.jpg"
    assert rar4.recovered_name(oem.decode("cp850"), oem) == "表紙ほか.jpg"
    assert rar4.recovered_name(oem.decode("cp1252", errors="replace"), oem) is None  # 化け方が違う
    assert rar4.recovered_name("other.jpgé", oem) is None  # OEM 名と関係の無い Unicode 名


def test_recovered_names_reads_headers(tmp_path):
    names = rar4.recovered_names(sample(tmp_path))
    assert names == {rar4.lookup_key(COVER[1]): p("v13", "13_#カバー.jpg"),
                     rar4.lookup_key(TABLE[1]): p("v13", "13_#表紙.jpg")}
    assert rar4.lookup_key(TABLE[1]) == "v13/13_#ò/Äå.jpg"  # Linux の 7-Zip は `/` で出す


@pytest.mark.parametrize("data", [b"", b"Rar!\x1a\x07\x01\x00" + b"\x00" * 20, b"7z\xbc\xaf\x27\x1c" + b"\x00" * 20])
def test_read_names_not_rar4_is_empty(tmp_path, data):
    path = tmp_path / "x.rar"
    path.write_bytes(data)
    assert rar4.read_names(path) == []


def test_read_names_truncated_or_encrypted_is_empty(tmp_path):
    data = sample(tmp_path).read_bytes()
    cut = tmp_path / "cut.rar"
    cut.write_bytes(data[:60])
    assert rar4.read_names(cut) == []
    main = _rar4_block(0x73, 0, b"\x00" * 6)
    enc = tmp_path / "enc.rar"
    enc.write_bytes(rar4.RAR4_MAGIC + _rar4_block(0x73, 0x0080, b"\x00" * 6) + data[7 + len(main):])  # ヘッダの暗号化
    assert rar4.read_names(enc) == []
    vol = tmp_path / "vol.rar"
    vol.write_bytes(rar4.RAR4_MAGIC + _rar4_block(0x73, 0x0001, b"\x00" * 6) + data[7 + len(main):])  # 分割書庫
    assert rar4.read_names(vol) == []
    assert rar4.read_names(tmp_path / "missing.rar") == []


def test_entries_from_records_matches_either_separator():
    """Windows の 7-Zip はバックスラッシュ、Linux の 7-Zip は `/` で区切る。どちらでも直した名前になる。"""
    from spinebox.archive import sevenzip as sz
    recovered = {rar4.lookup_key(TABLE[1]): p("v13", "13_#表紙.jpg")}
    for path in (TABLE[1], TABLE[1].replace(SEP, "/")):
        (entry,), raw_paths, _ = sz._entries_from_records([{"Path": path, "Size": "1"}], recovered)
        assert entry.name == p("v13", "13_#表紙.jpg") and entry.encoding == "cp932 (rar4)"
        assert raw_paths[0] == path


def test_listing_uses_recovered_names_and_extracts(tmp_path, sevenzip):
    path = sample(tmp_path)
    with open_archive(path, sevenzip_path=sevenzip) as reader:
        listing = list_entries(reader, ExtractionLimits())
        by_name = {e.name: e for e in listing.entries}
        # 偽のフォルダ `13_#ò/` の下ではなく、13 巻のフォルダの直下のページになる
        assert {e.parent for e in listing.entries} == {"v13/"}
        assert set(by_name) == {"v13/13_#カバー.jpg", "v13/13_#表紙.jpg", "v13/13_p001.jpg", "v13/正しい名前.jpg",
                                "v13/café.jpg", "v13/plain.jpg"}
        table = by_name["v13/13_#表紙.jpg"]
        assert table.encoding == "cp932 (rar4)" and table.raw_name == TABLE[0]
        assert by_name["v13/café.jpg"].encoding == "utf-8 (7z)"
        # 取り出しは 7-Zip の名前のまま
        assert reader.read_entry(table, ExtractionLimits()) == page_bytes("table")
        got = reader.read_entries([by_name["v13/13_#カバー.jpg"], table], ExtractionLimits())
        assert got[table.index] == page_bytes("table")


def test_read_names_skips_other_blocks_with_data(tmp_path):
    """ファイル以外のブロック(サブブロック 0x7A。ADD_SIZE 分のデータ付き)は飛ばして続きを読む。"""
    import struct
    data = sample(tmp_path).read_bytes()
    main = _rar4_block(0x73, 0, b"\x00" * 6)
    sub = _rar4_block(0x7A, 0x8000, struct.pack("<I", 5) + b"\x00" * 4) + b"XXXXX"
    path = tmp_path / "sub.rar"
    path.write_bytes(rar4.RAR4_MAGIC + main + sub + data[7 + len(main):])
    assert rar4.recovered_names(path) == rar4.recovered_names(sample(tmp_path))
    assert len(rar4.recovered_names(path)) == 2
