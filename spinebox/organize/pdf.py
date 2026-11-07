"""PDF からのページ画像抽出と CBZ 作成。

自炊漫画などの PDF からページ画像を抽出し、1 巻 = 1 CBZ(ZIP_STORED)を作成する。
基本は埋め込み画像の無劣化抽出(バイト列をそのまま保存)を行い、
ページ回転や CMYK 色空間の補正、白紙ページや複数画像ページの合成レンダリングを行う
ハイブリッド抽出方式を採用している。
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import pypdf
from PIL import Image

from .cbz import PageRecord, page_entry_names, verify_cbz, write_cbz

log = logging.getLogger(__name__)


class PdfError(Exception):
    """PDF 処理の基本例外。"""


class PdfEncrypted(PdfError):
    """暗号化された PDF。"""


class PdfEmpty(PdfError):
    """ページが存在しない PDF。"""


def inspect_pdf(path: Path) -> tuple[int, list[str]]:
    """PDF のページ数と、CBZ 化できない問題点を検査する。

    戻り値: (ページ数, 問題点リスト)
    """
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            return 0, ["暗号化された PDF は CBZ にできません"]
        count = len(reader.pages)
        if count == 0:
            return 0, ["ページがありません"]
        return count, []
    except Exception as e:  # noqa: BLE001
        return 0, [f"読めない PDF: {type(e).__name__}: {e}"]


def _render_page_with_pdfium(pdf_path: Path, page_index: int, scale: float = 2.0) -> bytes | None:
    """pypdfium2 が利用可能な場合、指定ページを高解像度でレンダリングする。"""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
        page = doc[page_index]
        pil_image = page.render(scale=scale).to_pil()
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        buf = io.BytesIO()
        pil_image.save(buf, format="JPEG", quality=95)
        return buf.getvalue()
    except ImportError:
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("pypdfium2 でのレンダリングに失敗(page %d): %s", page_index, e)
        return None


def extract_pdf_page_image(
    page: pypdf.PageObject,
    page_index: int,
    pdf_path: Path | None = None,
) -> tuple[str, bytes]:
    """PDF の 1 ページから画像データ(拡張子付き名, バイト列)を抽出・生成する。

    - 1 ページ 1 画像・回転なし・非 CMYK: 生バイト列をそのまま無劣化抽出
    - 回転あり: 正立するようにロスレス/再保存回転
    - CMYK: RGB に変換
    - 0 画像(白紙): mediabox サイズの白画像を生成
    - 複数画像: pypdfium2 があればレンダリング、なければ Pillow で合成
    """
    rotation = int(page.rotation or 0) % 360
    images = list(page.images)

    # 1. 画像が 1 枚の場合 (自炊 PDF の大多数)
    if len(images) == 1:
        img = images[0]
        raw_bytes = img.data
        is_jpeg = raw_bytes.startswith(b"\xff\xd8")
        is_png = raw_bytes.startswith(b"\x89PNG")
        ext = "jpg" if is_jpeg else ("png" if is_png else "jpg")

        pil_img = None
        needs_convert = False

        # CMYK チェック
        try:
            pil_img = img.image
            if pil_img.mode == "CMYK":
                needs_convert = True
        except Exception:  # noqa: BLE001
            pass

        if rotation != 0 or needs_convert:
            if pil_img is None:
                pil_img = Image.open(io.BytesIO(raw_bytes))
            if pil_img.mode == "CMYK":
                pil_img = pil_img.convert("RGB")
            elif pil_img.mode not in ("RGB", "L"):
                pil_img = pil_img.convert("RGB")
            if rotation != 0:
                # pypdf の rotation は時計回り。PIL rotate は反時計回り。
                pil_img = pil_img.rotate(360 - rotation, expand=True)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=95)
            return (f"{page_index + 1:04d}.jpg", buf.getvalue())

        # 無劣化抽出
        return (f"{page_index + 1:04d}.{ext}", raw_bytes)

    # 2. 白紙ページ (画像が 0 枚)
    if len(images) == 0:
        # pypdfium2 があればテキストやベクターを含めてレンダリング
        if pdf_path is not None:
            rendered = _render_page_with_pdfium(pdf_path, page_index)
            if rendered is not None:
                return (f"{page_index + 1:04d}.jpg", rendered)

        # 白紙画像生成
        try:
            w = int(float(page.mediabox.width))
            h = int(float(page.mediabox.height))
        except Exception:  # noqa: BLE001
            w, h = 1200, 1800
        w = max(100, min(w, 8000))
        h = max(100, min(h, 8000))
        blank = Image.new("RGB", (w, h), (255, 255, 255))
        buf = io.BytesIO()
        blank.save(buf, format="JPEG", quality=95)
        return (f"{page_index + 1:04d}.jpg", buf.getvalue())

    # 3. 複数画像ページ (画像が 2 枚以上)
    if pdf_path is not None:
        rendered = _render_page_with_pdfium(pdf_path, page_index)
        if rendered is not None:
            return (f"{page_index + 1:04d}.jpg", rendered)

    # Pillow 合成フォールバック:
    # 最も面積が大きいメイン画像を探し、それをベースに他の画像を合成または配置
    try:
        pil_images = [img.image for img in images]
    except Exception:  # noqa: BLE001
        pil_images = [Image.open(io.BytesIO(img.data)) for img in images]

    # 面積順にソート (降順)
    sorted_imgs = sorted(pil_images, key=lambda im: im.width * im.height, reverse=True)
    base_img = sorted_imgs[0]
    if base_img.mode != "RGB":
        base_img = base_img.convert("RGB")

    # サブ画像があれば順次合成
    canvas = base_img.copy()
    for sub_img in sorted_imgs[1:]:
        if sub_img.mode != "RGB":
            sub_img = sub_img.convert("RGB")
        # 小さい画像を中央または右下に重ねる等の簡易合成
        # ここでははみ出さない範囲で左上/中央にペースト
        canvas.paste(sub_img, (0, 0))

    if rotation != 0:
        canvas = canvas.rotate(360 - rotation, expand=True)

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return (f"{page_index + 1:04d}.jpg", buf.getvalue())


def write_pdf_cbz(pdf_path: Path, out_path: Path) -> list[PageRecord]:
    """PDF から画像を抽出し、out_path に 1 巻 = 1 CBZ(ZIP_STORED)を作成・検証する。"""
    reader = pypdf.PdfReader(str(pdf_path))
    if reader.is_encrypted:
        raise PdfEncrypted(f"暗号化された PDF は CBZ にできません: {pdf_path.name}")
    num_pages = len(reader.pages)
    if num_pages == 0:
        raise PdfEmpty(f"ページがありません: {pdf_path.name}")

    # オンデマンドでページ画像を抽出するクロージャ
    # 各ページの拡張子を決定するため、まず名前リストを準備
    # write_cbz は source_names の拡張子を保つため、デフォルトは .jpg
    source_names = [f"page_{i + 1:04d}.jpg" for i in range(num_pages)]

    # ページごとのキャッシュまたは逐次読み込み
    page_data_cache: dict[int, bytes] = {}

    def read_page(idx: int) -> bytes:
        if idx in page_data_cache:
            return page_data_cache.pop(idx)
        page = reader.pages[idx]
        _, data = extract_pdf_page_image(page, idx, pdf_path=pdf_path)
        return data

    records = write_cbz(out_path, source_names, read_page)
    verify_cbz(out_path, records)
    return records
