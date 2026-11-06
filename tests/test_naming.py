import pytest

from spinebox.naming import normalize_title, parse_number, parse_work_name, trailing_number


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


# --- 作品名・著者の候補(#43) ----------------------------------------------------------------

OKIRAKU = "Okiraku ryoshu no tanoshi ryochi"
OKIRAKU_JP = "お気楽領主の楽しい領地防衛"


@pytest.mark.parametrize("name, title, authors", [
    # サンプルの全巻名(_参考/サンプル本)と外側のアーカイブ名
    ("20260112RAW.TO_Okiraku ryoshu no tanoshi ryochi v01-06.zip", OKIRAKU, ()),
    ("Okiraku ryoshu no tanoshi ryochi v01s", OKIRAKU, ()),
    ("Okiraku ryoshu no tanoshi ryochi v02s", OKIRAKU, ()),
    ("Okiraku_ryoshu_no_tanoshi_ryochi_v04", OKIRAKU, ()),
    ("Okiraku_ryoshu_no_tanoshi_ryochi_v05A", OKIRAKU, ()),
    ("Okiraku_ryoshu_no_tanoshi_ryochi_v06", OKIRAKU, ()),
    ("[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第01巻", OKIRAKU_JP, ("青色まろ", "赤池宗")),
    ("[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第02巻", OKIRAKU_JP, ("青色まろ", "赤池宗")),
    ("[青色まろ×赤池宗] お気楽領主の楽しい領地防衛 第03巻", OKIRAKU_JP, ("青色まろ", "赤池宗")),
    ("[東村アキコ] かくかくしかじか 第01-04巻.7z", "かくかくしかじか", ("東村アキコ",)),
    ("Kakukaku Shikajika v01-03", "Kakukaku Shikajika", ()),
    ("Kakukaku Shikajika v01-02.rar", "Kakukaku Shikajika", ()),
    ("Kakukaku Shikajika v01", "Kakukaku Shikajika", ()),
    ("Kakukaku Shikajika v04", "Kakukaku Shikajika", ()),
    # Issue の名前の形
    ("[著者] 作品名 第12巻", "作品名", ("著者",)),
    ("[作画×原作] 作品名 第3巻.zip", "作品名", ("作画", "原作")),
    ("［作画／原作］作品名　第３巻", "作品名", ("作画", "原作")),          # 全角の括弧・記号・数字
    ("(一般コミック) [著者] 作品名 第1巻 (2020) [DL版]", "作品名", ("著者",)),
    ("20260112RAW.TO_Title_Name_v01", "Title Name", ()),
    ("DLRAW.TO_Some Title v03", "Some Title", ()),
    ("Some_Title_v01s", "Some Title", ()),
    ("TITLE vol.02", "TITLE", ()),
    ("[Author] Some Title - Vol.01 [Digital]", "Some Title", ("Author",)),
    ("作品名 全3巻", "作品名", ()),
    ("作品名", "作品名", ()),
    ("01巻 作品名", "作品名", ()),
    ("作品 第12話", "作品", ()),
    ("[作品名] 第1巻", "作品名", ()),                                    # 括弧の中しか作品名が無いなら作品名
])
def test_parse_work_name(name, title, authors):
    wn = parse_work_name(name)
    assert wn is not None, name
    assert (wn.title, wn.authors) == (title, authors)
    assert wn.norm == normalize_title(title) and wn.norm
    assert ("title", title) in wn.parts  # 根拠(名前のどの部分を作品名と読んだか)


@pytest.mark.parametrize("name", [
    "DLRAW.TO_001.jpg", "001", "第01巻", "v01", "Vol.1", "cover", "新しいフォルダー", "", "2019", "[DL版]",
])
def test_parse_work_name_without_a_title(name):
    assert parse_work_name(name) is None


def test_work_name_keeps_number_and_parts_as_evidence():
    wn = parse_work_name("20260112RAW.TO_[著者] 作品 v01s.zip")
    assert wn.number.start == 1 and wn.number.evidence == "v01"
    assert wn.parts == (("distributor", "20260112RAW.TO"), ("author", "著者"), ("title", "作品"), ("number", "v01"))
    assert wn.as_dict() == {"title": "作品", "authors": ["著者"], "parts": [list(p) for p in wn.parts]}


def test_romaji_and_japanese_names_are_not_the_same_work_by_name_alone():
    assert normalize_title("Okiraku_ryoshu_no") == normalize_title("OKIRAKU Ryoshu No")
    assert normalize_title("ＯＫＩ　ｒａｋｕ") == normalize_title("oki raku")
    assert normalize_title("カガク") == normalize_title("かがく")
    assert normalize_title(OKIRAKU) != normalize_title(OKIRAKU_JP)  # 結ぶのは重複候補の証拠や別名


# --- 末尾の番号(同じ名前の組が証拠) -----------------------------------------------------------

@pytest.mark.parametrize("name, base, number", [
    ("作品名 01", "作品名", 1),
    ("作品名_2", "作品名", 2),
    ("作品名-12", "作品名", 12),
    ("作品名 (3)", "作品名", 3),
    ("作品名(3)", "作品名", 3),
    ("作品名（０５）", "作品名", 5),          # 全角の括弧・数字
    ("[著者] 作品名 07.cbz", "[著者] 作品名", 7),
])
def test_trailing_number_candidate(name, base, number):
    t = trailing_number(name)
    assert t is not None, name
    assert (t.base, t.number) == (base, number)
    assert t.evidence  # 名前の中で当たった部分


@pytest.mark.parametrize("name", [
    "DLRAW.TO_001.jpg",     # 配布元の直後の数字(ページ番号)
    "DLRAW.AC_89",
    "Manga-Zip.info_1",
    "001",
    "2019",
    "作品名 0",             # 0 巻は無い
    "作品名 第3",           # 印の付いた番号は parse_number の担当
    "作品名 v01",
    "v01-03",
    "作品名",
])
def test_trailing_number_is_not_a_candidate(name):
    assert trailing_number(name) is None


def test_trailing_number_alone_is_not_a_volume_number():
    # `Kitchen 2` が作品名のこともあるので、名前だけでは巻番号と決めない(spinebox/works.py が組で見る)
    assert parse_number("Kitchen 2") is None
    assert trailing_number("Kitchen 2").number == 2
