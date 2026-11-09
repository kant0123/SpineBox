"""テスト用の小説(EPUB・テキスト)を動的に作る道具。"""
from __future__ import annotations

import io
import zipfile

from conftest import RawName

CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

# 1x1 の PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082")


def xhtml(body: str, *, vertical: bool = False, head: str = "") -> str:
    style = '<link rel="stylesheet" href="style.css"/>' if vertical else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja">
<head><title>t</title>{style}{head}</head>
<body>{body}</body></html>"""


def epub_bytes(chapters: list[str], *, vertical: bool = False, images: dict[str, bytes] | None = None,
               extra: list[tuple] | None = None, opf_extra_items: str = "", container: bytes | None = CONTAINER_XML,
               linear_no: tuple[int, ...] = (), title: str = "t", metadata_extra: str = "") -> bytes:
    """chapters: 各章の body の中身(XHTML の断片)。images: OEBPS/ からの名前 → バイト列。"""
    images = images or {}
    items, refs = [], []
    for i, _ in enumerate(chapters):
        items.append(f'<item id="c{i}" href="text/ch{i}.xhtml" media-type="application/xhtml+xml"/>')
        linear = ' linear="no"' if i in linear_no else ""
        refs.append(f'<itemref idref="c{i}"{linear}/>')
    for j, name in enumerate(images):
        media = "image/svg+xml" if name.endswith(".svg") else "image/png"
        items.append(f'<item id="img{j}" href="{name}" media-type="{media}"/>')
    items.append('<item id="css" href="text/style.css" media-type="text/css"/>')
    ppd = ' page-progression-direction="rtl"' if vertical else ""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title><dc:language>ja</dc:language>{metadata_extra}</metadata>
  <manifest>{''.join(items)}{opf_extra_items}</manifest>
  <spine{ppd}>{''.join(refs)}</spine>
</package>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")
        if container is not None:
            z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/text/style.css",
                   "html { writing-mode: vertical-rl; -epub-writing-mode: vertical-rl; }" if vertical
                   else "body { background: url(http://evil.example/x.png); }")
        for i, body in enumerate(chapters):
            z.writestr(f"OEBPS/text/ch{i}.xhtml", xhtml(body, vertical=vertical), compress_type=zipfile.ZIP_DEFLATED)
        for name, data in images.items():
            z.writestr(f"OEBPS/{name}", data)
        for name, data in extra or []:
            z.writestr(RawName(name) if isinstance(name, bytes) else name, data)
    return buf.getvalue()


MALICIOUS_BODY = """
<p>本文<script>window.__pwned = 1</script>の続き</p>
<p onclick="window.__pwned = 2">クリック</p>
<img src="../images/pic.png" onerror="window.__pwned = 3" alt="挿絵"/>
<img src="http://evil.example/track.png"/>
<img src="data:image/png;base64,AAAA"/>
<a href="javascript:alert(1)">リンク</a>
<iframe src="http://evil.example/"></iframe>
<object data="x.swf"><param name="a" value="b"/>代替</object>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
  <script>window.__pwned = 4</script><image xlink:href="../images/pic.png" onload="window.__pwned = 5"/>
  <foreignObject><p>中</p></foreignObject>
</svg>
<p style="background: url(http://evil.example/bg.png)">装飾</p>
<style>@import url(http://evil.example/a.css);</style>
<form action="http://evil.example/"><input name="q"/></form>
<!-- コメント <script>x</script> -->
"""
