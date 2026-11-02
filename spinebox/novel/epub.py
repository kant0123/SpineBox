"""EPUB の読み取り。zip としての読み取りは既存の安全な仕組み(`list_entries` のエントリ名検証・展開上限)を通す。

`META-INF/container.xml` → OPF(package)→ manifest / spine の順に読み、spine の本文(XHTML)を区切りに、
manifest の画像を参照できる資源にする。参照は OPF・本文の場所からの相対パスを、エントリ名と照らして解決する
(ディスク上のパスには使わない)。XML は DOCTYPE の実体宣言を持つものを拒む(展開爆弾・外部実体の対策)。
"""
from __future__ import annotations

import dataclasses
import posixpath
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from ..archive import ArchiveEntry, ArchiveError, ArchiveReader, list_entries
from ..archive.safety import Skip
from ..config import ExtractionLimits

CONTAINER = "META-INF/container.xml"
MAX_XML_BYTES = 4 * 1024 * 1024        # container.xml・OPF
MAX_SECTION_BYTES = 16 * 1024 * 1024   # 1 本文(XHTML)
XHTML_TYPES = frozenset({"application/xhtml+xml", "text/html", "application/x-dtbook+xml"})
# 資源として返す画像(SVG はスクリプトを持てるので返さない)
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


class EpubError(ArchiveError):
    """EPUB として読めない(理由はメッセージ)。"""


@dataclass(frozen=True)
class Section:
    entry: ArchiveEntry
    length: int  # 目安の長さ(展開後のバイト数)


@dataclass(frozen=True)
class Resource:
    entry: ArchiveEntry
    media_type: str


@dataclass
class EpubBook:
    sections: list[Section]
    resources: list[Resource]
    resource_index: dict[str, int]  # エントリ名 → resources の位置


def scan_problems(entries: list[ArchiveEntry], skipped: list[Skip]) -> list[Skip]:
    """スキャン(目次だけ)で分かる、EPUB として扱わない理由。空なら巻として登録してよい。

    危険な名前のエントリを 1 つでも含む EPUB は、まともな作成ツールの出力ではないので丸ごと扱わない。
    """
    problems = list(skipped)
    if skipped:
        problems.append(Skip(None, "危険な名前のエントリを含む EPUB なので読まない"))
    elif not any(e.name == CONTAINER for e in entries):
        problems.append(Skip(None, f"EPUB の目次に {CONTAINER} が無い"))
    return problems


def _limits(limits: ExtractionLimits, cap: int) -> ExtractionLimits:
    return dataclasses.replace(limits, max_entry_bytes=min(limits.max_entry_bytes, cap))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_ENTITY_DECL = re.compile(rb"<!ENTITY", re.I)


def parse_xml(data: bytes) -> ET.Element:
    if _ENTITY_DECL.search(data):
        raise EpubError("XML に実体宣言(<!ENTITY)がある")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise EpubError(f"XML を解釈できない: {e}") from e


def resolve_href(base_dir: str, href: str) -> str | None:
    """base_dir(エントリのフォルダ)からの相対参照をエントリ名にする。外部 URL・外へ出る参照は None。"""
    if not href:
        return None
    parts = urlsplit(href.strip())
    if parts.scheme or parts.netloc or not parts.path:
        return None  # http: / data: / javascript: / //host、断片だけの参照
    path = unquote(parts.path)
    if path.startswith("/") or "\\" in path or "\x00" in path:
        return None
    joined = posixpath.normpath(posixpath.join(base_dir, path))
    if joined.startswith("../") or joined in ("..", ".") or joined.startswith("/"):
        return None
    return joined


def open_book(reader: ArchiveReader, limits: ExtractionLimits) -> EpubBook:
    listing = list_entries(reader, limits)
    problems = scan_problems(listing.entries, listing.skipped)
    if problems:
        raise EpubError(problems[-1].reason)
    by_name: dict[str, ArchiveEntry] = {}
    for e in listing.entries:
        by_name.setdefault(e.name, e)
    xml_limits = _limits(limits, MAX_XML_BYTES)

    def read_xml(name: str) -> ET.Element:
        entry = by_name.get(name)
        if entry is None:
            raise EpubError(f"{name} が無い")
        return parse_xml(reader.read_entry(entry, xml_limits))

    container = read_xml(CONTAINER)
    opf_path = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"
                     and el.get("full-path")), None)
    opf_name = resolve_href("", opf_path or "")
    if opf_name is None:
        raise EpubError("container.xml に OPF の場所が無い")
    package = read_xml(opf_name)
    opf_dir = posixpath.dirname(opf_name)

    manifest: dict[str, tuple[str, str]] = {}  # id → (エントリ名, media-type)
    resources: list[Resource] = []
    resource_index: dict[str, int] = {}
    for el in package.iter():
        if _local(el.tag) != "item":
            continue
        name = resolve_href(opf_dir, el.get("href") or "")
        media = (el.get("media-type") or "").strip().lower()
        if name is None or name not in by_name:
            continue
        if el.get("id"):
            manifest.setdefault(el.get("id"), (name, media))
        if media in IMAGE_TYPES and name not in resource_index:
            resource_index[name] = len(resources)
            resources.append(Resource(by_name[name], media))

    sections: list[Section] = []
    for el in package.iter():
        if _local(el.tag) != "itemref" or (el.get("linear") or "").strip().lower() == "no":
            continue
        item = manifest.get(el.get("idref") or "")
        if item is None or item[1] not in XHTML_TYPES:
            continue
        entry = by_name[item[0]]
        sections.append(Section(entry, max(entry.size, 1)))
    if not sections:
        raise EpubError("spine に読める本文(XHTML)が無い")
    return EpubBook(sections, resources, resource_index)


_XML_ENCODING = re.compile(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']")


def decode_markup(data: bytes) -> str:
    """本文のバイト列を文字列にする。BOM → XML 宣言の encoding → UTF-8 の順。読めない文字は置換する。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    m = _XML_ENCODING.match(data[:200])
    if m:
        try:
            return data.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    return data.decode("utf-8", "replace")


def read_section(reader: ArchiveReader, book: EpubBook, index: int, limits: ExtractionLimits) -> tuple[str, str]:
    """(本文の文字列, そのエントリ名) を返す。"""
    section = book.sections[index]
    data = reader.read_entry(section.entry, _limits(limits, MAX_SECTION_BYTES))
    return decode_markup(data), section.entry.name
