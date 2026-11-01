"""記録した応答(tests/fixtures/metadata/)から、各取得元の項目が正規化されて取れる。"""
import pytest

from metadata_fakes import fixture
from spinebox.metadata import anilist, mangaupdates, ndl
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


# --- AniList ---

def test_anilist_kaku():
    item = anilist.parse_search(fixture("anilist/kaku-search.json"))[0]
    assert item.source == "anilist" and item.scope == SCOPE_WORK
    assert item.title == "かくかくしかじか"
    assert item.romaji == "Kakukaku Shikajika"
    assert any(a.startswith("Blank Canvas") for a in item.aliases)
    assert item.authors == ("東村アキコ",)
    assert item.total_volumes == 5
    assert item.released == "2011-09-28"
    assert item.kind == KIND_MANGA
    assert "Drama" in item.genres
    assert "Autobiographical" in item.tags and "Drawing" in item.tags
    assert item.synopsis and "<" not in item.synopsis
    assert item.source_url == "https://anilist.co/manga/74307"


def test_anilist_okiraku_manga_and_novel_are_separate_candidates():
    items = anilist.parse_search(fixture("anilist/okiraku-search.json"))
    assert {i.kind for i in items} == {KIND_MANGA, KIND_NOVEL}
    assert all(i.romaji.startswith("Okiraku Ryoushu no Tanoshii") for i in items)
    assert all(i.title.startswith("お気楽領主の楽しい領地防衛") for i in items)


def test_anilist_no_hit_is_empty_success():
    assert anilist.parse_search(fixture("anilist/romaji-search.json")) == []


@pytest.mark.parametrize("body", [
    b"", b"not json", b"[]", b'{"data": null}', b'{"data": {"Page": {"media": "x"}}}',
    b'{"errors": [{"message": "Too Many Requests.", "status": 429}], "data": null}',
])
def test_anilist_invalid_response_is_parse_error(body):
    with pytest.raises(ParseError):
        anilist.parse_search(body)


def test_anilist_tags_drop_spoilers_and_low_rank():
    body = (b'{"data":{"Page":{"media":[{"id":1,"title":{"native":"x"},"tags":['
            b'{"name":"A","rank":90},{"name":"Spoiler","rank":95,"isMediaSpoiler":true},'
            b'{"name":"Low","rank":5},{"name":"B","rank":60}]}]}}}')
    (item,) = anilist.parse_search(body)
    assert item.tags == ("A", "B")


def test_anilist_clean_text():
    assert anilist.clean_text("a<br>b<br/>c &amp; <i>d</i>") == "a\nb\nc & d"
    assert anilist.clean_text(None) is None and anilist.clean_text("  ") is None


# --- MangaUpdates ---

def test_mangaupdates_search_hits():
    hits = mangaupdates.parse_search(fixture("mangaupdates/okiraku-search.json"))
    assert len(hits) == 10
    assert hits[0].record["series_id"] == 41097037681
    assert hits[0].hit_title == "お気楽領主の楽しい領地防衛"  # 検索が当たった別名


def test_mangaupdates_detail_prefers_japanese_title_and_keeps_romaji():
    item = mangaupdates.parse_detail(fixture("mangaupdates/okiraku-detail-41097037681.json"))
    assert item.source == "mangaupdates" and item.scope == SCOPE_WORK
    assert item.source_id == "41097037681"
    assert item.title == "お気楽領主の楽しい領地防衛"
    assert item.romaji.startswith("Okiraku Ryoushu no Tanoshii Ryouchi Bouei")
    assert "Fun Territory Defense by the Optimistic Lord" in item.aliases
    assert item.title not in item.aliases and item.romaji not in item.aliases
    assert item.authors == ("AKAIKE Sou", "AOIRO Maro")
    assert item.publisher == "Overlap"          # 原版元(type = Original)
    assert item.total_volumes == 8
    assert item.released == "2021"
    assert item.kind == KIND_MANGA
    assert "Shounen" in item.genres
    assert "Magic" in item.tags


def test_mangaupdates_detail_novel_and_kaku():
    novel = mangaupdates.parse_detail(fixture("mangaupdates/okiraku-detail-64061546373.json"))
    assert novel.kind == KIND_NOVEL and novel.total_volumes == 10
    kaku = mangaupdates.parse_detail(fixture("mangaupdates/kaku-detail-59267573840.json"))
    assert kaku.title == "かくかくしかじか" and kaku.romaji == "Kakukaku Shikajika"
    assert kaku.publisher == "Shueisha" and kaku.total_volumes == 5
    assert len(kaku.tags) == mangaupdates.CATEGORY_LIMIT  # 得票順に上限まで


def test_mangaupdates_detail_without_japanese_name_keeps_primary_title():
    item = mangaupdates.parse_detail(b'{"series_id": 1, "title": "Some Title", "associated": [{"title": "Other"}]}')
    assert item.title == "Some Title" and item.romaji is None and item.aliases == ("Other",)


def test_mangaupdates_zero_volumes_is_unknown():
    item = mangaupdates.parse_detail(b'{"series_id": 1, "title": "T", "status": "0 Volumes (Ongoing)"}')
    assert item.total_volumes is None


@pytest.mark.parametrize("body", [b"", b"nope", b"[]", b'{"status": "exception", "reason": "Request must be an object"}'])
def test_mangaupdates_invalid_search_is_parse_error(body):
    with pytest.raises(ParseError):
        mangaupdates.parse_search(body)


@pytest.mark.parametrize("body", [b"", b"nope", b"[]", b"{}", b'{"series_id": 1}'])
def test_mangaupdates_invalid_detail_is_parse_error(body):
    with pytest.raises(ParseError):
        mangaupdates.parse_detail(body)
