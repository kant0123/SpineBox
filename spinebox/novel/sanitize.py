"""EPUB の本文(XHTML)の無害化。EPUB の中の HTML / CSS / SVG は信用しない。

許可リスト方式で組み立て直す(元の文字列を手直しするのではなく、許した要素・属性だけを出力し直す)。

- 要素: 文章の構造(段落・見出し・ルビ・強調・リスト・表・画像)だけを残す。知らない要素はタグを外して中身だけ残す
- 中身ごと捨てる: script / style / head / iframe / object / embed / form 部品 / audio / video / math / template など
- SVG: 中の `image` だけを `<img>` に置き換え、ほかは捨てる(表紙を SVG で包む EPUB が多いため)
- 属性: `img` の `src`・`alt`、表の `colspan` / `rowspan`、縦中横などの決まった class だけ。`on*`・`style`・`href` は出さない
- 参照(`src` / `xlink:href`): resolve が返した URL(同じ EPUB の中の画像の API の URL)だけ。外部 URL・`data:`・
  `javascript:`・見つからない参照の画像は出さない
- EPUB の CSS は使わない(`url()` での外部読み込み・`@import`・画面を覆う配置を止めるため。見た目はリーダーが決める)

出力はタグの対応が取れた HTML 断片(閉じていないタグは最後に閉じる)。
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Callable

# 中身ごと捨てる要素
DROP = frozenset({
    "script", "style", "head", "title", "iframe", "frame", "frameset", "object", "embed", "applet", "noscript",
    "template", "form", "input", "button", "select", "textarea", "option", "audio", "video", "source", "track",
    "canvas", "math", "link", "meta", "base", "param", "noembed", "noframes", "portal", "dialog",
})
# そのまま残す要素
KEEP = frozenset({
    "p", "div", "span", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ruby", "rb", "rt", "rp", "rtc",
    "em", "strong", "b", "i", "u", "s", "sub", "sup", "small", "big", "blockquote", "q", "cite", "code", "pre",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
    "figure", "figcaption", "img", "section", "article", "aside", "header", "footer", "nav", "main", "wbr",
})
VOID = frozenset({"br", "hr", "img", "wbr"})
# 縦書きの表現で意味のある class(EPUB の CSS は使わないので、リーダーの CSS が当てる名前に寄せる)
_CLASS_MAP = (
    (re.compile(r"tcy|tate-?chu-?yoko|text-combine", re.I), "tcy"),
    (re.compile(r"upright", re.I), "upright"),
    (re.compile(r"sideways|yoko", re.I), "sideways"),
    (re.compile(r"sesame|boten|em-dot", re.I), "em-sesame"),
)

Resolve = Callable[[str], str | None]


def _class(value: str | None) -> str | None:
    if not value:
        return None
    found = []
    for token in value.split():
        for pattern, name in _CLASS_MAP:
            if pattern.search(token) and name not in found:
                found.append(name)
                break
    return " ".join(found) or None


def _span(value: str | None) -> str | None:
    return value if value and value.isdigit() and 0 < int(value) <= 100 else None


class _Sanitizer(HTMLParser):
    def __init__(self, resolve: Resolve):
        super().__init__(convert_charrefs=True)
        self.resolve = resolve
        self.out: list[str] = []
        self.stack: list[str] = []
        self.drop: list[str] = []  # 中身ごと捨てている要素の入れ子
        self.svg = 0

    # --- 出力 ---------------------------------------------------------------

    def _img(self, attrs: dict[str, str | None], *keys: str) -> None:
        url = None
        for k in keys:
            if attrs.get(k):
                url = self.resolve(attrs[k] or "")
                break
        if url is None:
            return  # 外部・見つからない参照の画像は出さない
        alt = attrs.get("alt") or ""
        self.out.append(f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}">')

    def _open(self, tag: str, attrs: dict[str, str | None], *, self_closing: bool) -> None:
        if tag == "img":
            self._img(attrs, "src")
            return
        parts = [tag]
        cls = _class(attrs.get("class"))
        if cls:
            parts.append(f'class="{cls}"')
        if tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                v = _span(attrs.get(k))
                if v:
                    parts.append(f'{k}="{v}"')
        self.out.append("<" + " ".join(parts) + ">")
        if tag in VOID:
            return
        if self_closing:
            self.out.append(f"</{tag}>")
        else:
            self.stack.append(tag)

    # --- HTMLParser ---------------------------------------------------------

    def _start(self, tag: str, attrs_list, *, self_closing: bool) -> None:
        tag = tag.lower()
        attrs = {k.lower(): v for k, v in attrs_list}
        if self.drop:
            if tag in DROP and not self_closing and tag not in VOID:
                self.drop.append(tag)
            return
        if tag in DROP:
            if not self_closing:
                self.drop.append(tag)
            return
        if self.svg:
            if tag == "svg" and not self_closing:
                self.svg += 1
            elif tag == "image":
                self._img(attrs, "xlink:href", "href", "src")
            return
        if tag == "svg":
            if not self_closing:
                self.svg = 1
            return
        if tag in KEEP:
            self._open(tag, attrs, self_closing=self_closing)
        # 知らない要素(html / body / a など)はタグを外して中身を残す

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop:
            if tag == self.drop[-1]:
                self.drop.pop()
            elif tag in self.drop:
                while self.drop and self.drop.pop() != tag:
                    pass
            return
        if self.svg:
            if tag == "svg":
                self.svg -= 1
            return
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.drop or self.svg:
            return
        self.out.append(html.escape(data, quote=False))

    # コメント・処理命令・宣言(DOCTYPE)・CDATA は出さない
    def handle_comment(self, data):
        pass

    def handle_pi(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def unknown_decl(self, data):
        pass

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(markup: str, resolve: Resolve) -> str:
    """許可リストで組み立て直した HTML 断片を返す。resolve は参照(相対パス)を安全な URL にする(できなければ None)。"""
    s = _Sanitizer(resolve)
    s.feed(markup)
    return s.result()
