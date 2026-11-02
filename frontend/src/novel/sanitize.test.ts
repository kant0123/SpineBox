import { afterEach, describe, expect, it } from "vitest";
import { sanitizeFragment } from "./sanitize";

declare global {
  interface Window {
    __sanitizePwned?: number;
  }
}

function render(html: string): HTMLDivElement {
  const div = document.createElement("div");
  document.body.appendChild(div);
  div.replaceChildren(sanitizeFragment(html));
  return div;
}

afterEach(() => {
  document.body.innerHTML = "";
  delete window.__sanitizePwned;
});

describe("ブラウザ側の無害化", () => {
  it("スクリプト・イベントハンドラ・外部の読み込みを落とし、本文とルビは残す", () => {
    const div = render(
      `<p>本文<script>window.__sanitizePwned = 1</script>続き</p>` +
        `<img src="x" onerror="window.__sanitizePwned = 2"><img src="http://evil.example/a.png">` +
        `<img src="/api/volumes/7/resources/0" alt="挿絵" onload="window.__sanitizePwned = 3">` +
        `<p onclick="window.__sanitizePwned = 4" style="background:url(http://evil.example/b.png)" class="tcy evil">強調</p>` +
        `<a href="javascript:alert(1)">リンク</a><iframe src="http://evil.example/"></iframe>` +
        `<svg><script>window.__sanitizePwned = 5</script><image href="http://evil.example/c.png"/></svg>` +
        `<style>@import url(http://evil.example/d.css);</style><!-- <script>x</script> -->` +
        `<ruby>漢字<rt>かんじ</rt></ruby>`,
    );
    expect(div.querySelectorAll("script, iframe, svg, style, a, object, link").length).toBe(0);
    const all = Array.from(div.querySelectorAll("*"));
    for (const el of all) {
      for (const attr of Array.from(el.attributes)) {
        expect(["src", "alt", "class", "colspan", "rowspan"]).toContain(attr.name);
      }
    }
    const imgs = div.querySelectorAll("img");
    expect(imgs.length).toBe(1);
    expect(imgs[0].getAttribute("src")).toBe("/api/volumes/7/resources/0");
    expect(div.querySelector("p.tcy")?.getAttribute("class")).toBe("tcy");
    expect(div.textContent).toContain("本文続き");
    expect(div.textContent).toContain("リンク");
    expect(div.querySelector("ruby rt")?.textContent).toBe("かんじ");
    expect(window.__sanitizePwned).toBeUndefined();
  });

  it("文字としての < や & はそのまま文字で出す", () => {
    const div = render("&lt;script&gt;alert(1)&lt;/script&gt; &amp;");
    expect(div.children.length).toBe(0);
    expect(div.textContent).toBe("<script>alert(1)</script> &");
  });
});
