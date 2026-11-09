import pytest

from spinebox.naming import normalize_title, parse_number, parse_number_in, parse_work_name, trailing_number


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
    ("第01卷", "volume", 1, 1),        # 簡体字の巻表示も巻として読む
    ("01卷", "volume", 1, 1),          # 簡体字の巻表示も巻として読む
    ("第3话", "episode", 3, 3),        # 簡体字の話表示も話として読む
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
    "ようこそ実力至上主義の教室へ 第04.5巻",  # 小数点の巻は整数で持てない
    "第04.5巻",            # 小数点の巻は整数で持てない
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
    ("ようこそ実力至上主義の教室へ 第04.5巻", "ようこそ実力至上主義の教室へ", ()),  # 小数点の巻は番号なしで作品名にも残さない
    ("作品名", "作品名", ()),
    ("01巻 作品名", "作品名", ()),
    ("作品 第12話", "作品", ()),
    ("[作品名] 第1巻", "作品名", ()),                                    # 括弧の中しか作品名が無いなら作品名
    ("スキルがなければレベルを上げる Chapter 002.2", "スキルがなければレベルを上げる", ()),  # 小数点チャプター落とし(#220)
    ("スキルがなければレベルを上げる ch.02.2", "スキルがなければレベルを上げる", ()),
    ("スキルがなければレベルを上げる 第02.5話", "スキルがなければレベルを上げる", ()),
    ("Chapter 002.2 スキルがなければレベルを上げる", "スキルがなければレベルを上げる", ()),
    ("[東村アキコ] かくかくしかじか Chapter 002.2", "かくかくしかじか", ("東村アキコ",)),
])
def test_parse_work_name(name, title, authors):
    wn = parse_work_name(name)
    assert wn is not None, name
    assert (wn.title, wn.authors) == (title, authors)
    assert wn.norm == normalize_title(title) and wn.norm
    assert ("title", title) in wn.parts  # 根拠(名前のどの部分を作品名と読んだか)


@pytest.mark.parametrize("name", [
    "DLRAW.TO_001.jpg", "001", "第01巻", "v01", "Vol.1", "cover", "新しいフォルダー", "", "2019", "[DL版]",
    # 小数点のチャプター・話数のみの名前は作品名と読まない(#220)
    "Chapter 002.2", "Chapter 002.3", "Chapter 002.4", "Chapter 003.1", "ch. 02.5", "ch02.5", "第02.5話", "02.5話", "02.5巻", "Vol.02.5", "v02.5",
    # 単語の後ろに数字が付いたJUNK名・チャプター単体(#220)
    "cover2", "cover02", "scans 01", "raw1", "images 2", "page01", "Chapter", "Episode",
])
def test_parse_work_name_without_a_title(name):
    assert parse_work_name(name) is None


@pytest.mark.parametrize("name", [
    # 配布元の接頭辞を落とすと残る、ファイル数・ページ数の見出し(実データ。 #158)
    "DLRAW.AC_57_files", "57 files", "57_files.cbz", "88 Files", "4 cover", "1 pg", "120 pages",
])
def test_counted_names_are_not_work_titles(name):
    assert parse_work_name(name) is None


@pytest.mark.parametrize("name, title", [
    ("FILES", "FILES"),                                  # 汎用語だけでも、数が付いていなければ作品名
    ("5 Centimeters per Second", "5 Centimeters per Second"),  # 数 + 固有の語
])
def test_a_number_alone_does_not_make_a_name_a_count(name, title):
    wn = parse_work_name(name)
    assert wn is not None and wn.title == title


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
    # 末尾の番号の後ろの装飾(括弧・著者列)は落として番号を読む
    ("異種族レビュアーズ 2 [ドラゴンコミックスエイジ][AVIF]", "異種族レビュアーズ", 2),
    ("お気楽領主の楽しい領地防衛 1 - 青色まろ & 赤池宗 & 転", "お気楽領主の楽しい領地防衛", 1),
    ("ハズレ判定から始まったチート魔術士生活(コミック) : 2 (モンスターコミックス)",
     "ハズレ判定から始まったチート魔術士生活(コミック)", 2),
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


def test_trailing_number_evidence_keeps_dropped_decor():
    t = trailing_number("異種族レビュアーズ 2 [ドラゴンコミックスエイジ][AVIF]")
    assert "ドラゴンコミックスエイジ" in t.evidence and "AVIF" in t.evidence  # 落とした装飾は根拠に残す
    t = trailing_number("お気楽領主の楽しい領地防衛 1 - 青色まろ & 赤池宗 & 転")
    assert "青色まろ" in t.evidence  # 著者列も根拠に残す
    t = trailing_number("ハズレ判定から始まったチート魔術士生活(コミック) : 2 (モンスターコミックス)")
    assert "モンスターコミックス" in t.evidence
    t = trailing_number("作品名 1 - 著者 [レーベル]")
    assert "[レーベル]" in t.evidence  # 著者列と括弧装飾が併用されても落とした装飾は根拠に残す


# --- 章の見出し・断片・保存ページ(全体同期の名寄せ) ---------------------------------------------

@pytest.mark.parametrize("name", ["DLRAW.TO_第4話 CAオブザデッド①(前編)", "第100部分:コツ", "DLRAW.TO_chap 038",
                                  "特別編:ショークラブオブザデッド", "番外編 その1", "Chapter 12"])
def test_chapter_labels_are_recognised(name):
    from spinebox.naming import is_chapter_label
    assert is_chapter_label(name)


@pytest.mark.parametrize("name", ["[作者] 29歳独身中堅冒険者の日常 01", "Koyuu Skill v01", "第二次世界大戦"])
def test_titles_are_not_chapter_labels(name):
    from spinebox.naming import is_chapter_label
    assert not is_chapter_label(name)


@pytest.mark.parametrize("name, expected", [("DLRAW.TO_CONTENTAS", True), ("DLRAW.TO_c", True), ("DLRAW.TO_❺", True),
                                            ("Manga-Zip.info_4_cover", True), ("目次", True),
                                            ("Koyuu Skill v01", False), ("57 files", False)])
def test_fragment_names(name, expected):
    from spinebox.naming import is_fragment_name
    assert is_fragment_name(name) is expected


def test_web_novel_parts_and_c_ranges_are_episodes():
    n = parse_number("第100部分:コツ")
    assert (n.kind, n.start) == ("episode", 100)
    w = parse_work_name("[作者] 植物魔法チート 逆転人生始まった件c06-08")
    assert w.title == "植物魔法チート 逆転人生始まった件" and (w.number.kind, w.number.start, w.number.end) == ("episode", 6, 8)


@pytest.mark.parametrize("name", ["16_files", "bbjjf1_y_files", "Manga-Zip.info_2_files", "03s", "01b"])
def test_saved_page_folders_and_marked_numbers_are_not_work_names(name):
    assert parse_work_name(name) is None


@pytest.mark.parametrize("name", ["extra_01", "Extra-02", "bonus", "omake 3"])
def test_extra_chapters_with_separators_are_labels(name):
    from spinebox.naming import is_chapter_label
    assert is_chapter_label(name)


def test_extraordinary_is_not_a_label():
    from spinebox.naming import is_chapter_label
    assert not is_chapter_label("Extraordinary Life v01")


@pytest.mark.parametrize("name", ["[サークル] 作品名 C100", "作品名 (C99)", "作品名 C98"])
def test_comiket_numbers_are_not_episodes(name):
    n = parse_number(name)
    assert n is None or n.kind != "episode"


def test_a_one_kanji_name_is_not_a_fragment():
    from spinebox.naming import is_fragment_name
    assert not is_fragment_name("岳") and is_fragment_name("c") and is_fragment_name("❺")


@pytest.mark.parametrize("name, ancestors, expected", [
    # 巻のフォルダ(v06s)の中の、印の無い範囲・章は巻番号ではない(#248)
    ("DLRAW.AC_110-113", ("DLRAW.AC_Atumare Fushigi Kenkyubu v06s",), None),
    ("DLRAW.AC_Atsumare! Fushigi Kenkyu-bu - Raw Chapter 171", ("DLRAW.AC_Atsumare Fushigi Kenkyubu v10s",), None),
    ("第12話", ("作品 第03巻",), None),
    # 外側が範囲の巻なら、中のフォルダは巻のことがあるので従来どおり読む
    ("01-04", ("作品 第01-20巻",), ("volume", 1, 4)),
    # 外側に巻の無い名前・外側の無いときは従来どおり
    ("110-113", ("作品",), ("volume", 110, 113)),
    ("110-113", (), ("volume", 110, 113)),
    ("ch.5", ("110-113",), ("episode", 5, 5)),  # 印の無い範囲は外側の巻とみなさない
    # 巻の印の付いた名前は、外側に巻があっても巻として読む
    ("v07", ("作品 v06s",), ("volume", 7, 7)),
])
def test_parse_number_in_ignores_chapters_inside_a_volume_folder(name, ancestors, expected):
    cand = parse_number_in(name, ancestors)
    assert (cand and (cand.kind, cand.start, cand.end)) == expected
