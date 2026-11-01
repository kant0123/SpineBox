"""候補の確からしさ(数値と根拠)と、ローマ字・かなの正規化。"""
import pytest

from metadata_fakes import fixture
from spinebox.metadata import SearchQuery, ndl
from spinebox.metadata.kana import to_romaji
from spinebox.metadata.match import author_score, normalize, normalize_romaji, score_item, title_score
from spinebox.metadata.model import KIND_MANGA, KIND_NOVEL, SCOPE_VOLUME, SCOPE_WORK, MetadataItem


def item(**kw) -> MetadataItem:
    base = dict(source="t", source_id="1", scope=SCOPE_WORK, title="お気楽領主の楽しい領地防衛")
    base.update(kw)
    return MetadataItem(**base)


@pytest.mark.parametrize("kana, romaji", [
    ("オキラク リョウシュ ノ タノシイ リョウチ ボウエイ", "okiraku ryoushu no tanoshii ryouchi bouei"),
    ("カクカクシカジカ", "kakukakushikajika"),
    ("ジャンプ", "janpu"),
    ("マッチャ", "matcha"),
    ("コーヒー", "koohii"),
    ("きゃく", "kyaku"),
])
def test_to_romaji(kana, romaji):
    assert to_romaji(kana) == romaji


def test_normalize_romaji_folds_long_vowel_spellings():
    # 綴りの揺れ(ou / o、ii / i、マクロン)を同じにする
    assert normalize_romaji("Okiraku Ryoushu no Tanoshii Ryouchi") == normalize_romaji("Okiraku ryoshu no tanoshi ryochi")
    assert normalize_romaji("Tōkyō") == normalize_romaji("Tokyo")
    # 促音・重ね字は畳まない
    assert normalize_romaji("Kakkaku") != normalize_romaji("Kakaku")


def test_normalize_unifies_width_case_and_kana():
    assert normalize("ＡＢＣ　かくかく ・ シカジカ") == normalize("abc カクカク シカジカ")


def test_title_score_exact_contains_and_unrelated():
    names = ("お気楽領主の楽しい領地防衛", "Okiraku Ryoushu no Tanoshii Ryouchi Bouei")
    assert title_score("お気楽領主の楽しい領地防衛", names) == (1.0, "title:exact")
    assert title_score("カクカクシカジカ", ("かくかくしかじか",))[1] == "title:exact"  # かなの字種の違い
    score, why = title_score("お気楽領主", names)
    assert why == "title:contains" and 0.6 < score < 0.95
    score, why = title_score("全く別の作品", names)
    assert score < 0.3 and why.startswith("title:similar")


def test_title_score_romaji_query_matches_romaji_name():
    names = ("かくかくしかじか", "Kakukaku Shikajika")
    assert title_score("kakukaku shikajika", names) == (1.0, "title:exact")
    assert title_score("Okiraku ryoshu no tanoshi ryochi", ("お気楽領主の楽しい領地防衛",
                       "Okiraku Ryoushu no Tanoshii Ryouchi Bouei"))[1] == "title:contains"


def test_romaji_query_matches_ndl_item_through_reading():
    # NDL の項目にローマ字名は無いが、読み(カタカナ)をローマ字にして比べられる
    items = ndl.parse_opensearch(fixture("ndl/okiraku-search.xml"))
    match = score_item(SearchQuery("Okiraku ryoshu no tanoshi ryochi"), items[0])
    assert match.components["title"] > 0.8
    assert match.reasons[0] == "title:contains"


@pytest.mark.parametrize("query, authors, expected", [
    ("東村アキコ", ("東村アキコ",), (1.0, "author:match")),
    ("東村 アキコ", ("東村アキコ",), (1.0, "author:match")),
    ("東村アキコ", ("せひらあやみ", "東村アキコ"), (1.0, "author:match")),
    ("東村", ("東村アキコ",), (1.0, "author:match")),             # 姓だけ
    ("赤池宗", ("東村アキコ",), (0.0, "author:mismatch")),
    ("東村アキコ", ("HIGASHIMURA Akiko",), (None, "author:not_comparable")),  # 日本語 ↔ ローマ字
    ("Higashimura Akiko", ("HIGASHIMURA Akiko",), (1.0, "author:match")),
    ("東村アキコ", (), (None, "author:unknown")),
])
def test_author_score(query, authors, expected):
    assert author_score(query, authors) == expected


def test_score_item_volume_author_kind_components():
    items = ndl.parse_opensearch(fixture("ndl/kakuauthor-search.xml"))
    vol2 = next(i for i in items if i.volume == 2)
    full = score_item(SearchQuery("かくかくしかじか", author="東村アキコ", volume=2, kind=KIND_MANGA), vol2)
    assert full.score == 1.0
    assert full.components == {"title": 1.0, "author": 1.0, "volume": 1.0, "kind": 1.0}
    assert full.reasons == ("title:exact", "author:match", "volume:match", "kind:match")

    wrong_volume = score_item(SearchQuery("かくかくしかじか", author="東村アキコ", volume=3), vol2)
    assert "volume:mismatch" in wrong_volume.reasons
    assert wrong_volume.components["volume"] == 0.0
    assert wrong_volume.score < full.score

    wrong_kind = score_item(SearchQuery("かくかくしかじか", kind=KIND_NOVEL), vol2)
    assert wrong_kind.components["kind"] == 0.0 and "kind:mismatch" in wrong_kind.reasons


def test_score_skips_aspects_that_cannot_be_compared():
    # 作品単位の項目は巻を比べない / 巻の情報が無い項目は比べない / 種別不明は比べない
    work = score_item(SearchQuery("お気楽領主の楽しい領地防衛", volume=1, kind=KIND_MANGA), item(kind=None))
    assert set(work.components) == {"title"}
    assert "volume:not_applicable" in work.reasons and "kind:unknown" in work.reasons
    no_volume = score_item(SearchQuery("お気楽領主の楽しい領地防衛", volume=1), item(scope=SCOPE_VOLUME))
    assert "volume:unknown" in no_volume.reasons and no_volume.components["volume"] == 0.5  # 一致と食い違いの中間
    assert work.score == 1.0  # 比べられた観点(題名)だけで平均する
    assert no_volume.score < work.score


def test_isbn_match_is_recorded():
    it = item(scope=SCOPE_VOLUME, title="別の題名", isbn="9784087824575")
    match = score_item(SearchQuery("かくかくしかじか", isbn="978-4-08-782457-5"), it)
    assert "isbn:match" in match.reasons and match.components["title"] == 1.0


def test_score_is_between_zero_and_one_for_garbage():
    match = score_item(SearchQuery("x"), item(title="日本語", aliases=("Another",)))
    assert 0.0 <= match.score <= 1.0
