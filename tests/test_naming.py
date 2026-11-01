import pytest

from spinebox.naming import parse_number


@pytest.mark.parametrize("name, kind, start, end", [
    ("v01", "volume", 1, 1),
    ("v01s", "volume", 1, 1),
    ("v05A", "volume", 5, 5),
    ("Vol.1", "volume", 1, 1),
    ("vol 12", "volume", 12, 12),
    ("Volume 3", "volume", 3, 3),
    ("Vol1", "volume", 1, 1),
    ("第01巻", "volume", 1, 1),
    ("第01-04巻", "volume", 1, 4),
    ("かくかくしかじか 第01-04巻.7z", "volume", 1, 4),
    ("第1〜3巻", "volume", 1, 3),
    ("第1~3巻", "volume", 1, 3),
    ("12巻", "volume", 12, 12),
    ("v01-06", "volume", 1, 6),
    ("v01-v06.zip", "volume", 1, 6),
    ("v01-03.rar", "volume", 1, 3),
    ("01-03", "volume", 1, 3),
    ("作品名 01-03.zip", "volume", 1, 3),
    ("Okiraku ryoshu no tanoshi ryochi v01", "volume", 1, 1),
    ("[作者] 作品名 v02.zip", "volume", 2, 2),
    ("第０３巻", "volume", 3, 3),        # 全角数字
    ("ｖ０７", "volume", 7, 7),           # 全角英数字
    ("第０１－０４巻", "volume", 1, 4),   # 全角の区切り
    ("第12話", "episode", 12, 12),
    ("第1-5話", "episode", 1, 5),
    ("ch.12", "episode", 12, 12),
    ("Ch 3", "episode", 3, 3),
    ("chapter12", "episode", 12, 12),
    ("ep.4", "episode", 4, 4),
    ("作品名 第03巻 第12話", "volume", 3, 3),  # 巻の印を話より先に採る
])
def test_parses_candidates(name, kind, start, end):
    cand = parse_number(name)
    assert cand is not None, name
    assert (cand.kind, cand.start, cand.end) == (kind, start, end)
    assert cand.evidence  # 根拠(名前の中で当たった部分)を持つ


@pytest.mark.parametrize("name", [
    "DLRAW.TO_001.jpg",    # ページ番号
    "001.jpg",
    "DLRAW.TO_cover.jpg",
    "Dev01",               # 語の途中の v
    "TV2",
    "全3巻",               # 総巻数であって範囲ではない
    "2019",
    "2019-01-03",          # 日付
    "1999-2005",           # 年の範囲
    "Step3",
    "Kitchen 2",
    "03-01",               # 逆順
    "",
    "おまけ",
])
def test_does_not_misdetect(name):
    assert parse_number(name) is None


def test_extension_is_stripped_only_when_known():
    assert parse_number("Vol.1").start == 1           # `.1` は拡張子ではない
    assert parse_number("v02.cbz").start == 2
    assert parse_number("dir/sub/v03/").start == 3     # パスなら最後の要素


def test_as_dict_shape():
    assert parse_number("v01-03").as_dict() == {"kind": "volume", "start": 1, "end": 3, "evidence": "v01-03"}
