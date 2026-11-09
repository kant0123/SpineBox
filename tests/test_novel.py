"""小説(EPUB・テキスト)の巻: 文字コード判定・分割・無害化・スキャン・閲覧 API・読書位置(#57)。"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import page_bytes, snapshot, write_zip, zip_bytes
from novels import MALICIOUS_BODY, PNG, epub_bytes
from spinebox import db
from spinebox.app import create_app
from spinebox.novel.epub import EpubError, parse_xml, resolve_href
from spinebox.novel.sanitize import sanitize_html
from spinebox.novel.text import (SECTION_CHARS, TextDecodeError, decode_text, line_to_html, section_to_html,
                                 split_sections)

JP = "吾輩は猫である。名前はまだ無い。\nどこで生れたかとんと見当がつかぬ。"


# --- 文字コード -----------------------------------------------------------------

@pytest.mark.parametrize("data, enc", [
    (JP.encode("utf-8"), "utf-8"),
    (b"\xef\xbb\xbf" + JP.encode("utf-8"), "utf-8-sig"),
    (JP.encode("cp932"), "cp932"),
    (JP.encode("utf-16"), "utf-16"),                     # BOM 付き(LE)
    (b"\xfe\xff" + JP.encode("utf-16-be"), "utf-16"),    # BOM 付き(BE)
    (b"plain ascii", "utf-8"),
])
def test_decode_text_detects_encoding(data, enc):
    text, used = decode_text(data)
    assert used == enc
    assert text == (JP if data != b"plain ascii" else "plain ascii")


@pytest.mark.parametrize("data, reason", [
    (JP.encode("utf-16-le"), "NUL"),                     # BOM の無い UTF-16 は判定しない
    (b"\x81\x20\xff\xfe\x80", "判定できない"),           # UTF-8 でも cp932 でもない
    (b"\xef\xbb\xbf\xff\xfe", "UTF-8(BOM 付き)"),     # BOM と中身が食い違う
])
def test_decode_text_reports_reason(data, reason):
    with pytest.raises(TextDecodeError, match=re.escape(reason)):
        decode_text(data)


# --- 分割・青空文庫形式 ---------------------------------------------------------

def test_split_sections_keeps_all_text_and_bounds_size():
    lines = [f"{i:05d}" + "あ" * 300 for i in range(400)]  # 約 12 万文字
    text = "\r\n".join(lines)
    sections = split_sections(text)
    assert len(sections) > 3
    assert all(len(s) <= SECTION_CHARS for s in sections)
    assert "\n".join(sections) == "\n".join(lines)  # 行の切れ目で分けるので、つなげれば元に戻る
    assert split_sections(text) == sections          # 同じ中身なら同じ区切り(位置の基準)


def test_split_sections_cuts_overlong_line_outside_ruby():
    line = "あ" * (SECTION_CHARS - 3) + "｜漢字《かんじ》" + "い" * 10
    sections = split_sections(line)
    assert "".join(sections) == line
    assert all(s.count("《") == s.count("》") for s in sections)
    assert split_sections("") == [""]


def test_aozora_ruby_and_notes():
    assert line_to_html("｜吾輩《わがはい》は猫") == "<ruby>吾輩<rt>わがはい</rt></ruby>は猫"
    assert line_to_html("|吾輩《わがはい》") == "<ruby>吾輩<rt>わがはい</rt></ruby>"
    assert line_to_html("彼は東京《とうきょう》へ") == "彼は<ruby>東京<rt>とうきょう</rt></ruby>へ"
    assert line_to_html("見出し［＃「見出し」は大見出し］") == "見出し"
    # 本文の HTML らしきものは文字としてエスケープする
    assert line_to_html("<script>x</script>《<b>》") == "&lt;script&gt;x&lt;/script&gt;《&lt;b&gt;》"
    assert section_to_html("一行目\n\n三行目") == "<p>一行目</p>\n<p><br></p>\n<p>三行目</p>"


# --- 無害化 ----------------------------------------------------------------------

def _resolve(href: str):
    return "/api/volumes/1/resources/0" if href == "../images/pic.png" else None


def test_sanitize_drops_scripts_handlers_and_external_loads():
    out = sanitize_html(MALICIOUS_BODY, _resolve)
    lowered = out.lower()
    for bad in ("<script", "onclick", "onerror", "onload", "javascript:", "http:", "data:", "<iframe", "<object",
                "<svg", "style", "<form", "<input", "@import", "<!--", "<a", "__pwned", "<link"):
        assert bad not in lowered, bad
    assert "本文の続き" in out and "クリック" in out and "リンク" in out and "装飾" in out
    # 同じ EPUB の画像だけが API の URL に書き換わって残る(SVG の image も img になる)
    assert out.count('<img src="/api/volumes/1/resources/0"') == 2
    assert 'alt="挿絵"' in out


def test_sanitize_keeps_ruby_and_closes_tags():
    out = sanitize_html('<p class="main tcy-x"><ruby>漢字<rp>(</rp><rt>かんじ</rt></ruby><em>強調<p>次', _resolve)
    assert out == '<p class="tcy"><ruby>漢字<rp>(</rp><rt>かんじ</rt></ruby><em>強調<p>次</p></em></p>'
    assert sanitize_html("&lt;script&gt;&amp;</p></div>", _resolve) == "&lt;script&gt;&amp;"
    assert sanitize_html('<td colspan="2" rowspan="x">a</td>', _resolve) == '<td colspan="2">a</td>'


def test_resolve_href_rejects_external_and_escaping_refs():
    assert resolve_href("OEBPS/text", "../images/a%20b.png") == "OEBPS/images/a b.png"
    assert resolve_href("OEBPS/text", "ch1.xhtml#frag") == "OEBPS/text/ch1.xhtml"
    for bad in ("http://x/a.png", "//x/a.png", "data:image/png;base64,AA", "javascript:x", "/etc/passwd",
                "../../../x.png", "#frag", "", "a\\b.png"):
        assert resolve_href("OEBPS/text", bad) is None, bad


def test_xml_with_entity_declaration_is_refused():
    with pytest.raises(EpubError, match="実体宣言"):
        parse_xml(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')


# --- スキャン・閲覧 API ----------------------------------------------------------

@pytest.fixture
def make_client(make_settings):
    def make(*roots, **kw):
        settings = make_settings(*roots, **kw)
        return TestClient(create_app(settings, frontend_dist=None)), settings
    return make


def novel_library(tmp_path):
    lib = tmp_path / "lib"
    novels = lib / "小説"
    novels.mkdir(parents=True)
    (novels / "縦書き 第1巻.epub").write_bytes(epub_bytes(
        ["<h1>第一章</h1><p>縦書きの<ruby>本文<rt>ほんぶん</rt></ruby>。</p>", "<p>第二章の最後の文。</p>"],
        vertical=True))
    (novels / "横書き.epub").write_bytes(epub_bytes(
        ['<p>挿絵の前</p><img src="../images/pic.png" alt="挿絵"/>', "<p>横書きの終わり</p>"],
        images={"images/pic.png": PNG}))
    (novels / "危険.epub").write_bytes(epub_bytes([MALICIOUS_BODY], images={"images/pic.png": PNG}))
    (novels / "utf8.txt").write_text("｜吾輩《わがはい》は猫である。\n" + "本文\n" * 10 + "UTF-8 の終わり", encoding="utf-8")
    (novels / "sjis.txt").write_bytes("シフトJISの本文です。\n表示できる".encode("cp932"))
    (novels / "utf16.txt").write_bytes("UTF-16 の本文".encode("utf-16"))
    return lib


def scan(c):
    r = c.post("/api/scan")
    assert r.status_code == 200
    return {v["title"]: v for v in c.get("/api/volumes").json()}


def read_all(c, vid):
    detail = c.get(f"/api/volumes/{vid}").json()
    return detail, [c.get(f"/api/volumes/{vid}/sections/{s['index']}").json()["html"] for s in detail["sections"]]


def test_novels_are_listed_and_readable_to_the_end(make_client, tmp_path):
    c, _ = make_client(novel_library(tmp_path))
    vols = scan(c)
    assert {"縦書き 第1巻", "横書き", "危険", "utf8", "sjis", "utf16"} <= set(vols)
    v = vols["縦書き 第1巻"]
    assert v["format"] == "epub" and v["page_count"] is None  # スキャンでは本文を開かない
    assert v["volume_number"]["start"] == 1
    assert vols["utf8"]["format"] == "text"

    detail, html = read_all(c, v["id"])
    assert detail["format"] == "epub" and detail["pages"] == [] and len(detail["sections"]) == 2
    assert "<ruby>本文<rt>ほんぶん</rt></ruby>" in html[0] and "第二章の最後の文" in html[1]
    assert c.get(f"/api/volumes/{v['id']}/sections/2").status_code == 404
    assert by_id(c, v["id"])["page_count"] == 2  # 開いたら区切りの数が入る

    detail, html = read_all(c, vols["横書き"]["id"])
    m = re.search(r'<img src="(/api/volumes/\d+/resources/(\d+))"', html[0])
    assert m and "横書きの終わり" in html[1]
    r = c.get(m.group(1))
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert "sandbox" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert c.get(f"/api/volumes/{vols['横書き']['id']}/resources/99").status_code == 404

    for title, first, last, enc in [("utf8", "<ruby>吾輩<rt>わがはい</rt></ruby>は猫", "UTF-8 の終わり", "utf-8"),
                                    ("sjis", "シフトJISの本文です。", "表示できる", "cp932"),
                                    ("utf16", "UTF-16 の本文", "UTF-16 の本文", "utf-16")]:
        detail, html = read_all(c, vols[title]["id"])
        assert detail["format"] == "text" and detail["encoding"] == enc
        assert first in html[0] and last in html[-1]
    # 小説にページ画像は無い
    assert c.get(f"/api/volumes/{v['id']}/pages/0").status_code == 422


def by_id(c, vid):
    return next(v for v in c.get("/api/volumes").json() if v["id"] == vid)


def test_epub_scripts_handlers_and_external_urls_never_reach_the_browser(make_client, tmp_path):
    c, _ = make_client(novel_library(tmp_path))
    vols = scan(c)
    _, html = read_all(c, vols["危険"]["id"])
    lowered = html[0].lower()
    for bad in ("<script", " on", "javascript:", "http://", "https://", "data:", "<iframe", "<object", "style=",
                "__pwned"):
        assert bad not in lowered, bad
    # 画面そのものにも CSP(インラインのスクリプト・外部の読み込みを止める)
    csp = c.get("/api/volumes").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "object-src 'none'" in csp and "unsafe-inline" not in csp.split(";")[1]


def test_epub_with_bad_names_or_over_limits_is_skipped_with_reason(make_client, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "ok.epub").write_bytes(epub_bytes(["<p>正常</p>"]))
    (lib / "evil.epub").write_bytes(epub_bytes(["<p>x</p>"], extra=[(b"../../escape.xhtml", b"x")]))
    (lib / "nocontainer.epub").write_bytes(epub_bytes(["<p>x</p>"], container=None))
    (lib / "huge.epub").write_bytes(epub_bytes(["<p>" + "あ" * 5000 + "</p>"]))
    (lib / "notzip.epub").write_bytes(b"not a zip")
    c, settings = make_client(lib, max_entry_bytes=10_000, max_total_bytes=12_000)
    vols = scan(c)
    assert set(vols) == {"ok"}
    conn = db.connect(settings.db_path)
    issues = {}
    for r in conn.execute("SELECT s.rel_path, i.entry_name, i.reason FROM scan_issues i JOIN sources s ON s.id = i.source_id"):
        issues.setdefault(r["rel_path"], []).append((r["entry_name"], r["reason"]))
    assert ("../../escape.xhtml", "親ディレクトリ参照(..)を含むエントリ名") in issues["evil.epub"]
    assert any("危険な名前" in reason for _, reason in issues["evil.epub"])
    assert any("container.xml が無い" in reason for _, reason in issues["nocontainer.epub"])
    assert any("展開上限" in reason for _, reason in issues["huge.epub"])
    assert any("読めない EPUB" in reason for _, reason in issues["notzip.epub"])


def test_undecodable_text_is_listed_but_unreadable_with_reason(make_client, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "broken.txt").write_bytes(b"\x81\x20\xff\xfe\x80")
    c, settings = make_client(lib)
    vols = scan(c)  # スキャンは中身を読まないので一覧には出る
    r = c.get(f"/api/volumes/{vols['broken']['id']}")
    assert r.status_code == 422 and "文字コードを判定できない" in r.json()["detail"]
    conn = db.connect(settings.db_path)
    reasons = [row[0] for row in conn.execute("SELECT reason FROM scan_issues")]
    assert any("文字コードを判定できない" in x for x in reasons)


def test_text_beside_images_or_inside_archives_is_not_a_novel(make_client, tmp_path):
    lib = tmp_path / "lib"
    (lib / "漫画").mkdir(parents=True)
    (lib / "漫画" / "001.jpg").write_bytes(page_bytes("1"))
    (lib / "漫画" / "readme.txt").write_text("説明", encoding="utf-8")
    write_zip(lib / "pack.zip", [("001.jpg", page_bytes("p")), ("story.txt", b"x"),
                                 ("book.epub", epub_bytes(["<p>x</p>"]))])
    c, _ = make_client(lib)
    vols = scan(c)
    assert set(vols) == {"漫画", "pack"}
    assert all(v["format"] == "images" for v in vols.values())


def test_novel_progress_keeps_section_and_position(make_client, tmp_path):
    c, _ = make_client(novel_library(tmp_path))
    vols = scan(c)
    vid = vols["縦書き 第1巻"]["id"]
    c.get(f"/api/volumes/{vid}")  # 区切りの数が決まる
    r = c.put(f"/api/volumes/{vid}/progress", json={"page": 1, "finished": False, "position": 0.42})
    assert r.status_code == 200 and r.json() == {"page": 1, "finished": False, "position": 0.42}
    assert c.get(f"/api/volumes/{vid}").json()["progress"] == {"page": 1, "finished": False, "position": 0.42}
    assert by_id(c, vid)["progress"]["position"] == 0.42
    for bad in ({"page": 2, "finished": False, "position": 0.1}, {"page": 0, "finished": False, "position": 1.5},
                {"page": 0, "finished": False, "position": -0.1}):
        assert c.put(f"/api/volumes/{vid}/progress", json=bad).status_code == 422, bad
    # 画像の巻の割合は捨てる(API の形は今までどおり)
    lib2 = tmp_path / "lib"
    write_zip(lib2 / "comic.cbz", [("1.jpg", page_bytes("1")), ("2.jpg", page_bytes("2"))])
    vols = scan(c)
    comic = vols["comic"]["id"]
    r = c.put(f"/api/volumes/{comic}/progress", json={"page": 1, "finished": True, "position": 0.5})
    assert r.json() == {"page": 1, "finished": True}


def test_organize_and_duplicates_leave_novels_alone(make_client, tmp_path):
    c, _ = make_client(novel_library(tmp_path))
    vols = scan(c)
    plan = c.post("/api/organize/plan", json={"volume_id": vols["utf8"]["id"]}).json()
    assert plan["state"] == "blocked" and any("小説" in p for p in plan["problems"])
    r = c.post("/api/duplicates/scan?wait=true").json()
    assert r["error"] is None and r["summary"]["errors"] == 0 and r["summary"]["fingerprinted"] == 0


def test_library_bytes_unchanged_by_scanning_and_reading_novels(make_client, tmp_path):
    lib = novel_library(tmp_path)
    write_zip(lib / "comic.cbz", [("1.jpg", page_bytes("1"))])
    before = snapshot(lib)
    c, _ = make_client(lib)
    for v in scan(c).values():
        if v["format"] == "images":
            assert c.get(f"/api/volumes/{v['id']}/pages/0").status_code == 200
            continue
        detail, html = read_all(c, v["id"])
        for m in re.finditer(r'src="(/api/volumes/[^"]+)"', "".join(html)):
            assert c.get(m.group(1)).status_code == 200
        c.put(f"/api/volumes/{v['id']}/progress", json={"page": 0, "finished": True, "position": 1.0})
    scan(c)
    assert snapshot(lib) == before


def test_offline_novel_is_409_and_keeps_progress(make_client, tmp_path):
    lib = novel_library(tmp_path)
    c, _ = make_client(lib)
    vid = scan(c)["utf8"]["id"]
    c.get(f"/api/volumes/{vid}")
    c.put(f"/api/volumes/{vid}/progress", json={"page": 0, "finished": False, "position": 0.3})
    lib.rename(tmp_path / "unplugged")
    assert c.get(f"/api/volumes/{vid}").status_code == 409
    assert c.get(f"/api/volumes/{vid}/sections/0").status_code == 409
    scan(c)
    (tmp_path / "unplugged").rename(lib)
    vols = scan(c)
    assert vols["utf8"]["id"] == vid and vols["utf8"]["progress"]["position"] == 0.3


def test_epub_linear_no_and_svg_images_are_not_served(make_client, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    (lib / "b.epub").write_bytes(epub_bytes(['<img src="../img/x.svg"/><p>本文</p>', "<p>付録</p>"],
                                            images={"img/x.svg": svg}, linear_no=(1,)))
    c, _ = make_client(lib)
    vid = scan(c)["b"]["id"]
    detail, html = read_all(c, vid)
    assert len(detail["sections"]) == 1 and "<img" not in html[0]
    assert c.get(f"/api/volumes/{vid}/resources/0").status_code == 404


def test_text_over_size_limit_is_unreadable_with_reason(make_client, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "big.txt").write_text("あ" * 2000, encoding="utf-8")
    c, settings = make_client(lib, max_entry_bytes=1000)
    vid = scan(c)["big"]["id"]
    r = c.get(f"/api/volumes/{vid}")
    assert r.status_code == 422 and "上限" in r.json()["detail"]
    reasons = [row[0] for row in db.connect(settings.db_path).execute("SELECT reason FROM scan_issues")]
    assert any("上限" in x for x in reasons)


def test_epub_without_readable_body_is_422(make_client, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "empty.epub").write_bytes(epub_bytes(["<p>x</p>"], linear_no=(0,)))  # 本文が linear="no" だけ
    bad_container = b'<?xml version="1.0"?><container><rootfiles/></container>'
    (lib / "noopf.epub").write_bytes(epub_bytes(["<p>x</p>"], container=bad_container))
    c, _ = make_client(lib)
    vols = scan(c)  # スキャンは目次だけなので、どちらも一覧には出る
    assert "読める本文" in c.get(f"/api/volumes/{vols['empty']['id']}").json()["detail"]
    r = c.get(f"/api/volumes/{vols['noopf']['id']}")
    assert r.status_code == 422 and "OPF" in r.json()["detail"]


def test_xhtml_encoding_follows_bom_and_xml_declaration():
    from spinebox.novel.epub import decode_markup
    assert decode_markup('<?xml version="1.0" encoding="Shift_JIS"?><p>日本語</p>'.encode("shift_jis")).endswith("日本語</p>")
    assert decode_markup(b"\xef\xbb\xbf<p>\xe3\x81\x82</p>") == "<p>あ</p>"
    assert decode_markup("<p>あ</p>".encode("utf-16")) == "<p>あ</p>"
    assert decode_markup(b'<?xml version="1.0" encoding="x-unknown"?><p>\xe3\x81\x82</p>').endswith("<p>あ</p>")


def _scan_works(make_settings, lib):
    client = TestClient(create_app(make_settings(lib), frontend_dist=None))
    assert client.post("/api/scan").status_code == 200
    return client, {w["title"]: w for w in client.get("/api/works").json()["works"]}


def test_epub_opf_title_replaces_romaji_filename(make_settings, tmp_path):
    """ファイル名がローマ字で dc:title が日本語なら、作品名・著者・出版社の候補にする(#251)。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Ki Hon Jou Hou - Tokyo Interactive.epub").write_bytes(epub_bytes(
        ["<p>本文</p>"], title="基本情報技術者 科目A 一問一答問題集 2025年度版",
        metadata_extra="<dc:creator>Tokyo Interactive</dc:creator><dc:publisher>Tokyo Interactive</dc:publisher>"))
    client, by_title = _scan_works(make_settings, lib)
    assert "基本情報技術者 科目A 一問一答問題集 2025年度版" in by_title
    w = by_title["基本情報技術者 科目A 一問一答問題集 2025年度版"]
    detail = client.get(f"/api/works/{w['id']}").json()
    assert detail["author"] == "Tokyo Interactive"
    vid = client.get("/api/volumes").json()[0]["id"]
    conn = db.connect(make_settings(lib).db_path)
    row = conn.execute("SELECT value, source FROM volume_fields WHERE volume_id = ? AND field = 'publisher'",
                       (vid,)).fetchone()
    assert (row["value"], row["source"]) == ("Tokyo Interactive", "epub")


def test_epub_opf_title_does_not_replace_japanese_filename(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "吾輩は猫である.epub").write_bytes(epub_bytes(["<p>本文</p>"], title="別の題名"))
    _, by_title = _scan_works(make_settings, lib)
    assert "吾輩は猫である" in by_title and "別の題名" not in by_title


def test_epub_opf_title_ignored_when_not_japanese(make_settings, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "Kaze no tani.epub").write_bytes(epub_bytes(["<p>本文</p>"], title="Wind Valley"))
    _, by_title = _scan_works(make_settings, lib)
    assert "Kaze no tani" in by_title
