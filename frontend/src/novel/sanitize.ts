// 小説の本文 HTML を画面に入れる前の、ブラウザ側の無害化(2 段目)。
// 1 段目はサーバー(spinebox/novel/sanitize.py)で、同じ許可リストで組み立て直している。ここはすり抜けへの備えで、
// サーバーが返したものも信用せず、許した要素・属性だけを新しく作り直す。innerHTML は使わない。
// DOMParser で作った文書はスクリプトを実行せず画像も読み込まないので、解析の段階では何も動かない。

const KEEP = new Set([
  "p", "div", "span", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ruby", "rb", "rt", "rp", "rtc",
  "em", "strong", "b", "i", "u", "s", "sub", "sup", "small", "big", "blockquote", "q", "cite", "code", "pre",
  "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption",
  "figure", "figcaption", "img", "section", "article", "aside", "header", "footer", "nav", "main", "wbr",
]);
/** 中身ごと捨てる要素(KEEP に無い要素は、タグを外して中身だけ残す) */
const DROP = new Set([
  "script", "style", "head", "title", "iframe", "frame", "frameset", "object", "embed", "applet", "noscript",
  "template", "form", "input", "button", "select", "textarea", "option", "audio", "video", "source", "track",
  "canvas", "math", "svg", "link", "meta", "base", "param", "noembed", "noframes", "portal", "dialog",
]);
const CLASSES = new Set(["tcy", "upright", "sideways", "em-sesame"]);
/** 画像はサーバーが書き換えた同じ巻の資源の URL だけ */
const RESOURCE_SRC = /^\/api\/volumes\/[^/?#\s]+\/resources\/\d+$/;
const HTML_NS = "http://www.w3.org/1999/xhtml";

function copyChildren(src: Node, dst: Node, doc: Document): void {
  src.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) {
      dst.appendChild(doc.createTextNode(n.textContent ?? ""));
      return;
    }
    if (n.nodeType !== Node.ELEMENT_NODE) return; // コメント・処理命令は捨てる
    const el = n as Element;
    const tag = el.localName.toLowerCase();
    if (el.namespaceURI !== HTML_NS || DROP.has(tag)) return;
    if (!KEEP.has(tag)) {
      copyChildren(el, dst, doc);
      return;
    }
    const out = doc.createElement(tag);
    if (tag === "img") {
      const src = el.getAttribute("src") ?? "";
      if (!RESOURCE_SRC.test(src)) return;
      out.setAttribute("src", src);
      out.setAttribute("alt", el.getAttribute("alt") ?? "");
    }
    const cls = (el.getAttribute("class") ?? "").split(/\s+/).filter((c) => CLASSES.has(c));
    if (cls.length) out.setAttribute("class", cls.join(" "));
    if (tag === "td" || tag === "th") {
      for (const k of ["colspan", "rowspan"]) {
        const v = el.getAttribute(k);
        if (v && /^\d{1,3}$/.test(v)) out.setAttribute(k, v);
      }
    }
    copyChildren(el, out, doc);
    dst.appendChild(out);
  });
}

/** 許可リストで作り直した断片。doc はそれを入れる先の文書 */
export function sanitizeFragment(html: string, doc: Document = document): DocumentFragment {
  const parsed = new DOMParser().parseFromString(`<!doctype html><html><body>${html}</body></html>`, "text/html");
  const out = doc.createDocumentFragment();
  copyChildren(parsed.body, out, doc);
  return out;
}
