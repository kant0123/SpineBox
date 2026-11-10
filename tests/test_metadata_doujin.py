"""同人誌の取得元(nHentai・E-Hentai)#275。ネットワークには出ない(記録した実応答 + 偽の取得元)。

記録は `scripts/metadata_probe.py --source nhentai --source ehentai --author "バルバロッサ (東雲太郎)" --record … Flowers` で採った。
題名の例は、実際の蔵書の同人誌のファイル名の書式から取った。
"""
import httpx
import pytest

from metadata_fakes import FakeClock, FakeNetwork, fixture, json_body
from spinebox.config import MetadataSettings
from spinebox.db import open_db
from spinebox.metadata import MetadataService, SearchQuery
from spinebox.metadata import ehentai, nhentai
from spinebox.metadata.base import ParseError
from spinebox.metadata.doujin import (
    is_translation, parse_doujin_title, search_keywords, search_queries,
)
from spinebox.metadata.http import HttpFetcher
from spinebox.metadata.model import FAIL_INVALID, FAIL_RATE_LIMITED, KIND_MANGA
from spinebox.metadata.store import ResponseStore

NH_SEARCH = "https://nhentai.net/api/v2/search"
NH_GALLERY = "https://nhentai.net/api/v2/galleries/"
EH_SEARCH = "https://e-hentai.org/"
EH_API = "https://api.e-hentai.org/api.php"
AUTHOR = "バルバロッサ (東雲太郎)"


# --- 題名の書式 ---

@pytest.mark.parametrize("text, event, circle, author, title, parody, marks", [
    ("(C70) [バルバロッサ (東雲太郎)] Flowers (ストリートファイター)",
     "C70", "バルバロッサ", "東雲太郎", "Flowers", ("ストリートファイター",), ()),
    # ] の直後に空白が無い・題名と原作の間に空白が無い
    ("(C103) [ジャッカス (ペペ田デミオ)]サオリの恩返し(ブルーアーカイブ)",
     "C103", "ジャッカス", "ペペ田デミオ", "サオリの恩返し", ("ブルーアーカイブ",), ()),
    # サークルの括弧の前に空白が無い・原作が複数・末尾の印
    ("(C78) [偽MIDI泥の会 (石恵)] なおりました。 + のおまけ (エンジェルビーツ!、ボーカロイド) [無修正]",
     "C78", "偽MIDI泥の会", "石恵", "なおりました。 + のおまけ", ("エンジェルビーツ!", "ボーカロイド"), ("無修正",)),
    ("(C88) [てんらけ茶屋 (あーる。)] ちとちよビーチ (艦隊これくしょん -艦これ-) [中国翻訳]",
     "C88", "てんらけ茶屋", "あーる。", "ちとちよビーチ", ("艦隊これくしょん -艦これ-",), ("中国翻訳",)),
    # サークルと作者が同じ名前の `[作者]` だけ。原作の無い題名の末尾の括弧は割らない
    ("(C101) [海凪コウ]放課後裏垢女子", "C101", None, "海凪コウ", "放課後裏垢女子", (), ()),
    ("(C93) [スタイリッシュ丸投げ (パトリシア)] 涼月ちゃんとホワイト(意味浅)鎮守府 (艦隊これくしょん -艦これ-)",
     "C93", "スタイリッシュ丸投げ", "パトリシア", "涼月ちゃんとホワイト(意味浅)鎮守府", ("艦隊これくしょん -艦これ-",), ()),
    # nHentai の英語題名の末尾の訳者表記
    ("(C71) [Arisan-Antenna (Koari)] Eat The Rich! (Sukatto Golf Pangya) [English] =TV=",
     "C71", "Arisan-Antenna", "Koari", "Eat The Rich!", ("Sukatto Golf Pangya",), ("English", "=TV=")),
])
def test_parse_doujin_title(text, event, circle, author, title, parody, marks):
    d = parse_doujin_title(text)
    assert (d.event, d.circle, d.author, d.title, d.parody, d.marks) == (event, circle, author, title, parody, marks)


def test_unstructured_title_is_kept_whole_and_parody_is_not_guessed():
    # 書式を読めなかった題名は割らない(末尾の括弧は題名の一部かもしれない)
    d = parse_doujin_title("魔法少女(仮)")
    assert d.title == "魔法少女(仮)" and not d.structured and d.parody == ()
    assert parse_doujin_title("  ").title == ""


def test_credit_keeps_the_filename_form():
    assert parse_doujin_title("(C70) [バルバロッサ (東雲太郎)] x").credit == AUTHOR
    assert parse_doujin_title("[赤池宗] x").credit == "赤池宗"


def test_search_keywords_drop_search_syntax():
    # `:` は名前空間(artist:…)、`-` は否定、引用符は完全一致として読まれる
    assert search_keywords('Re:ゼロ "x" -y $') == "Re ゼロ x y"
    assert search_queries("Flowers", AUTHOR) == ["Flowers バルバロッサ", "Flowers"]
    assert search_queries("Flowers", "赤池宗") == ["Flowers 赤池宗", "Flowers"]
    assert search_queries("Flowers", None) == ["Flowers"]
    assert search_queries(" : ", AUTHOR) == []


def test_translation_is_detected_from_marks_and_language_tags():
    assert is_translation(("中国翻訳",)) and is_translation(("English", "=TV=")) and is_translation(("英訳",))
    assert is_translation((), ("translated",)) and is_translation((), ("english",))
    assert not is_translation(("DL版", "無修正")) and not is_translation((), ("japanese",))


# --- パーサ(記録した実応答) ---

def test_nhentai_parse_search_and_detail():
    records = nhentai.parse_search(fixture("nhentai/flowers-search.json"))
    assert [r["id"] for r in records] == [96151, 13528]

    item = nhentai.parse_detail(fixture("nhentai/flowers-detail-13528.json"))
    assert (item.source, item.source_id, item.scope) == ("nhentai", "13528", "work")
    assert item.title == "Flowers" and item.authors == (AUTHOR,) and item.publisher == "バルバロッサ"
    assert item.label == "C70" and item.kind == KIND_MANGA and item.genres == ("同人誌",)
    assert item.source_url == "https://nhentai.net/g/13528/"
    assert "ストリートファイター" in item.tags and "futanari" in item.tags
    assert "Flowers (ストリートファイター)" in item.aliases and "Flowers (Street Fighter)" in item.aliases
    assert "japanese" not in item.tags and "doujinshi" not in item.tags  # 言語・分類はタグにしない


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"nope": 1}', b'{"result": "x"}'])
def test_nhentai_parse_search_rejects_other_shapes(body):
    with pytest.raises(ParseError):
        nhentai.parse_search(body)


def test_nhentai_parse_detail_rejects_other_shapes():
    for body in (b"x", b'{"id": 1}', b'{"id": 1, "title": {}}'):
        with pytest.raises(ParseError):
            nhentai.parse_detail(body)


def test_ehentai_parse_search_page_links_in_order_without_duplicates():
    assert ehentai.parse_search_page(fixture("ehentai/flowers-search.html")) == [
        (618473, "c1e261db4e"), (41662, "69ff454309")]


def test_ehentai_search_page_no_hits_ban_and_other_pages():
    assert ehentai.parse_search_page(b"<html><p>No hits found</p></html>") == []
    with pytest.raises(ehentai.BannedError):
        ehentai.parse_search_page(b"<html>Your IP address has been temporarily banned</html>")
    with pytest.raises(ParseError):
        ehentai.parse_search_page(b"<html>Checking your browser</html>")  # リンクも 0 件の案内も無い


def test_ehentai_parse_gdata():
    galleries = {g.gid: g for g in ehentai.parse_gdata(fixture("ehentai/flowers-detail.json"))}
    g = galleries[41662]
    assert g.token == "69ff454309" and g.has_japanese and not g.translated
    item = g.item
    assert (item.source, item.source_id) == ("ehentai", "41662/69ff454309")
    assert item.title == "Flowers" and item.authors == (AUTHOR,) and item.publisher == "バルバロッサ"
    assert item.label == "C70" and item.kind == KIND_MANGA and item.genres == ("同人誌",)
    assert item.source_url == "https://e-hentai.org/g/41662/69ff454309/"
    assert "futanari" in item.tags and "schoolgirl uniform" in item.tags
    assert not any(t.startswith(("language", "artist", "group")) for t in item.tags)


def test_ehentai_parse_gdata_skips_errors_and_expunged():
    body = (b'{"gmetadata":[{"gid":1,"error":"Key missing"},{"gid":2,"token":"aaaaaaaaaa","title":"x","expunged":true,'
            b'"tags":[]},{"gid":3,"token":"bbbbbbbbbb","title":"(C1) [A (B)] T","category":"Manga","tags":[]}]}')
    assert [g.gid for g in ehentai.parse_gdata(body)] == [3]
    for bad in (b"x", b"[]", b'{"error": "Slow down"}', b'{"x": 1}'):
        with pytest.raises(ParseError):
            ehentai.parse_gdata(bad)


# --- 取得元を通した検索 ---

class Env:
    def __init__(self, tmp_path, net: FakeNetwork, **settings):
        self.net = net
        self.clock = FakeClock()
        self.settings = MetadataSettings(sources=("nhentai", "ehentai"), max_retries=0, **settings)
        self.conn = open_db(tmp_path / "doujin.db")
        fetcher = HttpFetcher(self.settings, transport=net.transport, clock=self.clock, sleep=self.clock.sleep)
        self.fetcher = fetcher
        self.service = MetadataService(self.settings, self.conn, fetcher=fetcher, store=ResponseStore(self.conn))

    def search(self, title="Flowers", sources=None, **query):
        return {o.source: o for o in self.service.search(SearchQuery(title, **query), sources)}


def doujin_network() -> FakeNetwork:
    net = FakeNetwork()
    net.route("GET", NH_SEARCH, fixture("nhentai/flowers-search.json"))
    net.route("GET", NH_GALLERY + "13528", fixture("nhentai/flowers-detail-13528.json"))
    net.route("GET", EH_SEARCH, fixture("ehentai/flowers-search.html"))
    net.route("POST", EH_API, fixture("ehentai/flowers-detail.json"))
    return net


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path, doujin_network())


def test_both_sources_identify_the_book_from_title_and_credit(env):
    out = env.search(author=AUTHOR, kind=KIND_MANGA)
    for name in ("nhentai", "ehentai"):
        o = out[name]
        assert o.ok and len(o.candidates) == 1, name  # 同じ本の再アップロードは 1 件にまとめる
        best = o.candidates[0]
        assert best.item.title == "Flowers" and best.item.publisher == "バルバロッサ"
        assert best.match.score == 1.0
        assert best.match.reasons == ("title:exact", "author:match", "kind:match")
        assert any("1 件にまとめた" in n for n in o.notes)
    # 古い方の ID(原本寄り)が残る
    assert out["nhentai"].candidates[0].item.source_id == "13528"
    assert out["ehentai"].candidates[0].item.source_id.startswith("41662/")
    # 検索の一覧しか見ていない 2 件目は詳細を引かない(確からしさの上位 detail_limit 件だけ)ので、詳細の要求は 1 件
    assert env.net.count(NH_GALLERY) == 1


def test_circle_name_is_added_to_the_query_and_dropped_when_nothing_hits(tmp_path):
    net = doujin_network()
    net.route("GET", NH_SEARCH, lambda r: fixture("nhentai/flowers-search.json")
              if "バルバロッサ" not in r.url.params["query"] else b'{"result":[],"num_pages":0,"per_page":25,"total":0}')
    net.route("GET", EH_SEARCH, lambda r: fixture("ehentai/flowers-search.html")
              if "バルバロッサ" in r.url.params["f_search"] else b"No hits found")
    env = Env(tmp_path, net)
    out = env.search(author=AUTHOR)
    assert out["nhentai"].ok and out["nhentai"].candidates  # サークル付きが 0 件 → 作品名だけで引き直した
    assert [r.url.params["query"] for r in net.requests if str(r.url).startswith(NH_SEARCH)] == [
        "Flowers バルバロッサ", "Flowers"]
    assert out["ehentai"].ok
    assert net.requests[-1].url.host == "api.e-hentai.org"
    body = json_body(net.requests[-1])
    assert body["method"] == "gdata" and body["namespace"] == 1
    assert [tuple(x) for x in body["gidlist"]] == [(618473, "c1e261db4e"), (41662, "69ff454309")]


def test_no_hits_is_a_successful_empty_result(tmp_path):
    net = FakeNetwork()
    net.route("GET", NH_SEARCH, b'{"result":[],"num_pages":0,"per_page":25,"total":0}')
    net.route("GET", EH_SEARCH, b"<html>No hits found</html>")
    out = Env(tmp_path, net).search()
    assert out["nhentai"].ok and out["nhentai"].candidates == ()
    assert out["ehentai"].ok and out["ehentai"].candidates == ()
    assert net.count(EH_API) == 0  # 0 件なら gdata は呼ばない


def test_translations_are_merged_and_the_japanese_original_is_kept(tmp_path):
    search = (b'{"result":['
              b'{"id":50,"media_id":"1","english_title":"(C70) [A (B)] Title (X) [English]",'
              b'"japanese_title":"(C70) [\\u3042 (\\u3044)] Title (\\u30a8\\u30c3\\u30af\\u30b9) [\\u82f1\\u8a33]"},'
              b'{"id":60,"media_id":"2","english_title":"(C70) [A (B)] Title (X)",'
              b'"japanese_title":"(C70) [\\u3042 (\\u3044)] Title (\\u30a8\\u30c3\\u30af\\u30b9)"}],'
              b'"num_pages":1,"per_page":25,"total":2}')
    net = FakeNetwork()
    net.route("GET", NH_SEARCH, search)
    out = Env(tmp_path, net, detail_limit=0).search(author="あ (い)")
    cands = out["nhentai"].candidates
    assert [c.item.source_id for c in cands] == ["60"]  # 翻訳(英訳)の古い ID より、翻訳でない方を残す
    assert cands[0].item.title == "Title"


def test_ehentai_ban_page_fails_as_rate_limited_and_stops_requests(tmp_path):
    net = FakeNetwork()
    net.route("GET", EH_SEARCH, b"<html>Your IP address has been temporarily banned for excessive pageloads</html>")
    env = Env(tmp_path, net)
    out = env.search()
    assert out["ehentai"].failure.kind == FAIL_RATE_LIMITED and out["ehentai"].failure.retry_after
    first = net.count(EH_SEARCH)
    again = env.search()["ehentai"]  # 遮断中は待たずに失敗させ、要求を出さない
    assert again.failure.kind == FAIL_RATE_LIMITED and net.count(EH_SEARCH) == first
    assert net.count(EH_API) == 0


def test_invalid_responses_become_failures_not_exceptions(tmp_path):
    net = FakeNetwork()
    net.route("GET", NH_SEARCH, b"<html>Just a moment</html>")
    net.route("GET", EH_SEARCH, b"<html>Checking your browser</html>")
    out = Env(tmp_path, net).search()
    assert out["nhentai"].failure.kind == FAIL_INVALID and out["ehentai"].failure.kind == FAIL_INVALID


def test_nhentai_detail_failure_falls_back_to_the_list_item(tmp_path):
    net = doujin_network()
    net.route("GET", NH_GALLERY, httpx.Response(500))
    out = Env(tmp_path, net).search(author=AUTHOR)["nhentai"]
    assert out.ok and out.candidates[0].item.title == "Flowers"
    assert out.candidates[0].item.genres == ()  # 一覧には分類が無い(詳細にだけある)
    assert any("詳細を取得できなかった" in n for n in out.notes)


def test_api_key_is_sent_but_never_stored(tmp_path):
    net = doujin_network()
    key = "SECRET-KEY-VALUE"
    env = Env(tmp_path, net, keys={"nhentai_api_key": key})
    env.search(author=AUTHOR, sources=["nhentai"])
    sent = [r for r in net.requests if r.url.host == "nhentai.net"]
    assert sent and all(r.headers["Authorization"] == f"Key {key}" for r in sent)
    stored = " ".join(str(tuple(r)) for r in env.conn.execute("SELECT * FROM metadata_responses"))
    assert key not in stored
    # キーが無ければ Authorization は付けない
    anon = doujin_network()
    (tmp_path / "anon").mkdir()
    Env(tmp_path / "anon", anon).search(author=AUTHOR, sources=["nhentai"])
    assert anon.requests and all("Authorization" not in r.headers for r in anon.requests)


def test_default_intervals_follow_the_published_limits():
    s = MetadataSettings()
    assert s.interval("nhentai") >= 6.0  # 匿名の検索は 10 回/分
    assert s.interval("ehentai") >= 4.0
    assert s.sources == ("ndl", "mediaarts", "bangumi")  # 同人誌の取得元は利用者が有効にするまで使わない


def test_requests_are_spaced_by_the_gate(env):
    env.search(author=AUTHOR, sources=["nhentai"])
    # 検索 1 回 + 詳細 1 回 → 2 回目の前に最小間隔だけ待つ
    assert any(abs(s - env.settings.interval("nhentai")) < 0.01 for s in env.clock.sleeps)


# --- 結線(作品への反映) ---

def test_doujinshi_is_linked_from_both_sources_and_fills_fields(make_settings, tmp_path):
    from test_linking import Env as LinkEnv

    name = "(C70) [バルバロッサ (東雲太郎)] Flowers (ストリートファイター).cbz"
    env = LinkEnv(make_settings, tmp_path, doujin_network(), [name], sources=("nhentai", "ehentai"))
    w = env.fetch(env.work_id())
    assert {env.link(w, s)["status"] for s in ("nhentai", "ehentai")} == {"linked"}
    f = w["fields"]
    assert f["title"]["value"] == "Flowers"
    assert f["author"]["value"] == AUTHOR  # ファイル名の書式のまま。置き換えて揺らさない
    assert f["publisher"]["value"] == "バルバロッサ" and f["publisher"]["source"] == "public_db"
    assert f["kind"]["value"] == "manga"
    assert "同人誌" in f["genres"]["value"]
    assert "ストリートファイター" in f["tags"]["value"]
    assert "Flowers (ストリートファイター)" in {a["alias"] for a in w["aliases"]}
    # 取得元の英語の内容タグは、対応表に無ければ既定(hide)で入れない
    assert "schoolgirl uniform" not in f["tags"]["value"]
