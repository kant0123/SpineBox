"""記録した応答(tests/fixtures/metadata/)から、各取得元の項目が正規化されて取れる。"""
import pytest

from metadata_fakes import fixture
from spinebox.metadata import bangumi, mediaarts, ndl
from spinebox.metadata.base import ParseError
from spinebox.metadata.model import KIND_MANGA, KIND_NOVEL, SCOPE_VOLUME, SCOPE_WORK


# --- 国立国会図書館サーチ ---

def test_ndl_okiraku_manga_and_novel_volumes():
    items = ndl.parse_opensearch(fixture("ndl/okiraku-search.xml"))
    assert len(items) == 10
    assert all(i.source == "ndl" and i.scope == SCOPE_VOLUME for i in items)

    manga = next(i for i in items if i.label == "ガルドコミックス" and i.volume == 1)
    assert manga.title == "お気楽領主の楽しい領地防衛"
    assert manga.subtitle is None
    assert manga.authors == ("青色まろ", "赤池宗")  # `青色, まろ` の読点区切りを姓名の連結に直す
    assert manga.publisher == "オーバーラップ"
    assert manga.released == "2022-02"
    assert manga.isbn == "9784824001177"
    assert manga.kind == KIND_MANGA
    assert manga.reading.startswith("オキラク リョウシュ")
    assert manga.source_url == "https://ndlsearch.ndl.go.jp/books/R100000002-I031966805"
    assert manga.source_id == "031966805"

    novel = next(i for i in items if i.label == "OVERLAP NOVELS" and i.volume == 1)
    assert novel.title == "お気楽領主の楽しい領地防衛"          # 副題は分ける
    assert novel.subtitle == "生産系魔術で名もなき村を最強の城塞都市に"
    assert novel.kind == KIND_NOVEL
    assert novel.authors == ("赤池宗",)


def test_ndl_author_filtered_search_returns_all_five_manga_volumes():
    items = ndl.parse_opensearch(fixture("ndl/kakuauthor-search.xml"))
    manga = [i for i in items if i.kind == KIND_MANGA]
    assert [i.volume for i in manga] == [1, 2, 3, 4, 5]
    assert all(i.authors == ("東村アキコ",) and i.publisher == "集英社" for i in manga)  # 生没年は著者名に残さない
    assert manga[0].isbn == "9784087824575"


def test_ndl_kind_is_none_when_undecidable():
    items = ndl.parse_opensearch(fixture("ndl/kaku-search.xml"))
    poem = next(i for i in items if "詩集" in (i.subtitle or ""))
    assert poem.kind is None and poem.volume is None
    assert poem.isbn == "4894026759"  # 10 桁のまま


@pytest.mark.parametrize("raw, expected", [
    ("赤池, 宗", "赤池宗"),
    ("東村, アキコ, 1975-", "東村アキコ"),
    ("辻ゆう子, 1942-", "辻ゆう子"),
    ("Smith, John", "John Smith"),
    ("伊達さん", "伊達さん"),
])
def test_ndl_normalize_creator(raw, expected):
    assert ndl.normalize_creator(raw) == expected


@pytest.mark.parametrize("label, expected", [("1", 1), ("０３", 3), ("12", 12), ("上", None), ("1-2", None), (None, None)])
def test_ndl_parse_volume(label, expected):
    assert ndl.parse_volume(label) == expected


@pytest.mark.parametrize("issued, date, expected", [
    ("2022.2", "2022", "2022-02"), ("2021.9.10", None, "2021-09-10"), ("2005", None, "2005"),
    (None, "1998", "1998"), ("令和3年", None, None), (None, None, None),
])
def test_ndl_parse_issued(issued, date, expected):
    assert ndl.parse_issued(issued, date) == expected


def test_ndl_split_title():
    assert ndl.split_title("作品 : 副題 : さらに") == ("作品", "副題 : さらに")
    assert ndl.split_title("副題のない作品") == ("副題のない作品", None)


@pytest.mark.parametrize("body", [b"", b"not xml", b"<rss", b"<html><body>error</body></html>", b'{"json": true}'])
def test_ndl_invalid_response_is_parse_error(body):
    with pytest.raises(ParseError):
        ndl.parse_opensearch(body)


def test_ndl_empty_result_is_not_an_error():
    xml = b'<rss version="2.0"><channel><title>x</title></channel></rss>'
    assert ndl.parse_opensearch(xml) == []


# --- メディア芸術データベース ---

def test_mediaarts_kaku_uses_series_entity():
    item = mediaarts.parse_search(fixture("mediaarts/kaku-search.json"))[0]
    assert item.source == "mediaarts" and item.scope == SCOPE_WORK
    assert item.source_id == "C333239"
    assert item.title == "かくかくしかじか"
    assert item.authors == ("東村アキコ",)
    assert item.publisher == "集英社"
    assert item.total_volumes == 5  # シリーズの実体(schema:numberOfItems)から
    assert item.kind == KIND_MANGA


def test_mediaarts_okiraku_falls_back_to_volume_max_when_series_entity_is_missing():
    # 「お気楽領主の楽しい領地防衛」はシリーズの実体(class:MangaBookSeries)が無く、巻(MangaBook)だけ
    # 登録されている(実通信で確認)。巻の schema:volumeNumber の最大値を全巻数として使う
    items = mediaarts.parse_search(fixture("mediaarts/okiraku-search.json"))
    main = next(i for i in items if i.title == "お気楽領主の楽しい領地防衛")
    assert main.total_volumes == 8
    assert main.authors == ("赤池宗",)
    assert main.publisher == "オーバーラップ"


def test_mediaarts_no_hit_is_empty_success():
    assert mediaarts.parse_search(fixture("mediaarts/romaji-search.json")) == []


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"results": null}', b'{"results": {"bindings": "x"}}'])
def test_mediaarts_invalid_response_is_parse_error(body):
    with pytest.raises(ParseError):
        mediaarts.parse_search(body)


@pytest.mark.parametrize("value, expected", [
    ("[著]東村アキコ", ("東村アキコ",)),
    ("[著]青色まろ∥[著]赤池宗", ("青色まろ", "赤池宗")),
    (None, ()),
])
def test_mediaarts_split_agents(value, expected):
    assert mediaarts._split_agents(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("集英社　∥　シュウエイシャ", "集英社"),
    ("オーバーラップ", "オーバーラップ"),
    (None, None),
])
def test_mediaarts_primary_reading(value, expected):
    assert mediaarts._primary_reading(value) == expected


# --- Bangumi ---

def test_bangumi_search_aggregates_volumes_by_base_name():
    hits = bangumi.parse_search(fixture("bangumi/kaku-search.json"))
    items = bangumi._aggregate(hits)
    kaku = next(i for i in items if i.title == "かくかくしかじか")
    assert kaku.source == "bangumi" and kaku.scope == SCOPE_WORK
    assert kaku.total_volumes == 5           # (1)〜(5) の最大
    assert "写写画画" in kaku.aliases
    # 別の作品(映画ノベライズ)は前置きが付くので別の候補のまま混ざらない
    assert any(i.title == "映画ノベライズ かくかくしかじか" and i.total_volumes is None for i in items)


def test_bangumi_search_strips_recurring_subtitle():
    hits = bangumi.parse_search(fixture("bangumi/okiraku-search.json"))
    items = bangumi._aggregate(hits)
    main = next(i for i in items if i.title == "お気楽領主の楽しい領地防衛")
    assert main.total_volumes == 11
    assert "安逸领主的愉快领地防卫" in main.aliases


def test_bangumi_detail_adds_author_publisher_and_kind():
    from spinebox.metadata.model import MetadataItem
    base = MetadataItem(source="bangumi", source_id="74283", scope=SCOPE_WORK,
                         title="かくかくしかじか", total_volumes=5)
    item = bangumi.parse_detail(fixture("bangumi/kaku-detail-74283.json"), base)
    assert item.authors == ("東村アキコ",)
    assert item.publisher == "集英社"
    assert item.kind == KIND_MANGA
    assert item.tags == ()  # タグ(簡体字中国語)は読み取らない(#278)
    assert item.total_volumes == 5  # base の値を保つ(詳細には全巻数が無い)


def test_bangumi_detail_combines_art_and_story_roles():
    # 作画・原作が分かれる作品(「お気楽領主の楽しい領地防衛」)は infobox の「作者」ではなく
    # 「作画」「原作」に分かれて入る(実データで確認)
    from spinebox.metadata.model import MetadataItem
    base = MetadataItem(source="bangumi", source_id="395984", scope=SCOPE_WORK,
                         title="お気楽領主の楽しい領地防衛", total_volumes=11)
    item = bangumi.parse_detail(fixture("bangumi/okiraku-detail-395984.json"), base)
    assert item.authors == ("青色まろ", "赤池宗")
    assert item.publisher == "オーバーラップ"
    assert item.kind == KIND_MANGA


def test_bangumi_no_hit_is_empty_success():
    assert bangumi.parse_search(fixture("bangumi/romaji-search.json")) == []


def test_bangumi_legacy_404_is_empty_success_not_a_parse_error():
    # 旧 API は 0 件のとき HTTP 200 のまま {"code": 404, "error": "Not Found"} を返す(実通信で確認)
    assert bangumi.parse_search(b'{"request": "x", "code": 404, "error": "Not Found"}') == []


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b'{"list": "x"}'])
def test_bangumi_invalid_response_is_parse_error(body):
    with pytest.raises(ParseError):
        bangumi.parse_search(body)


@pytest.mark.parametrize("body", [b"", b"not json", b"[]", b"{}", b'{"id": "not-an-int"}'])
def test_bangumi_invalid_detail_is_parse_error(body):
    from spinebox.metadata.model import MetadataItem
    base = MetadataItem(source="bangumi", source_id="1", scope=SCOPE_WORK, title="t")
    with pytest.raises(ParseError):
        bangumi.parse_detail(body, base)


@pytest.mark.parametrize("name, expected", [
    ("かくかくしかじか (1)", "かくかくしかじか"),
    ("お気楽領主の楽しい領地防衛 (7) ~生産系魔術で名もなき村を最強の城塞都市に~", "お気楽領主の楽しい領地防衛"),
    ("お気楽領主の楽しい領地防衛 ~生産系魔術で名もなき村を最強の城塞都市に~", "お気楽領主の楽しい領地防衛"),
    ("映画ノベライズ かくかくしかじか", "映画ノベライズ かくかくしかじか"),
])
def test_bangumi_base_name(name, expected):
    assert bangumi._base_name(name) == expected
