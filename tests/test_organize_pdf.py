"""PDF からの 1 巻 = 1 CBZ 変換および範囲指定整理のテスト。"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pypdf
import pytest
from PIL import Image

from conftest import page_bytes, snapshot
from spinebox import db, works
from spinebox.cache import ExtractionCache
from spinebox.library import Library
from spinebox.organize.cbz import verify_cbz
from spinebox.organize.organizer import Organizer
from spinebox.organize.pdf import inspect_pdf, write_pdf_cbz
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
from spinebox.scanner import Scanner


@pytest.fixture
def lib(tmp_path) -> Path:
    root = tmp_path / "lib"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def env(make_settings, lib):
    settings = make_settings(lib)
    conn = db.open_db(settings.db_path)
    Scanner(conn, settings).scan()
    works.sync(conn)
    library = Library(settings, ExtractionCache(settings.cache_dir, settings.cache_limit_bytes))
    root_id = conn.execute("SELECT id FROM library_roots").fetchone()["id"]
    return SimpleNamespace(conn=conn, settings=settings, library=library, root=lib, root_id=root_id)


def organizer(env, **kw) -> Organizer:
    return Organizer(env.conn, env.settings, env.library, **kw)


def vid_of(env, rel: str) -> int:
    return env.conn.execute(
        """SELECT v.id FROM volumes v JOIN sources s ON s.id = v.source_id
           WHERE s.rel_path = ? AND v.missing = 0""", (rel,)).fetchone()["id"]


def full_snapshot(root: Path) -> tuple[dict, set]:
    files = {k: v for k, v in snapshot(root).items() if not k.startswith(".spinebox")}
    dirs = {str(Path(d).relative_to(root)) for d, _, _ in os.walk(root)}
    return files, {d for d in dirs if not d.startswith(".spinebox")}


def make_simple_pdf(path: Path, num_pages: int = 3, rotation: int = 0) -> Path:
    """テスト用のシンプルな PDF を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(num_pages):
        im = Image.new("RGB", (100 + i * 10, 150 + i * 10), (i * 50, 100, 200))
        images.append(im)

    # PIL で PDF として保存
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    buf.seek(0)

    if rotation != 0:
        reader = pypdf.PdfReader(buf)
        writer = pypdf.PdfWriter()
        for p in reader.pages:
            p.rotation = rotation
            writer.add_page(p)
        with open(path, "wb") as f:
            writer.write(f)
    else:
        with open(path, "wb") as f:
            f.write(buf.getvalue())
    return path


def test_inspect_pdf(tmp_path):
    pdf = make_simple_pdf(tmp_path / "valid.pdf", num_pages=2)
    count, probs = inspect_pdf(pdf)
    assert count == 2
    assert probs == []

    # 暗号化 PDF
    enc_pdf = tmp_path / "enc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret")
    with open(enc_pdf, "wb") as f:
        writer.write(f)
    count, probs = inspect_pdf(enc_pdf)
    assert count == 0
    assert any("暗号化" in p for p in probs)

    # 壊れた PDF
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    count, probs = inspect_pdf(corrupt)
    assert count == 0
    assert any("読めない" in p for p in probs)


def test_write_pdf_cbz(tmp_path):
    pdf = make_simple_pdf(tmp_path / "test.pdf", num_pages=3)
    out_cbz = tmp_path / "out.cbz"
    records = write_pdf_cbz(pdf, out_cbz)

    assert len(records) == 3
    assert out_cbz.is_file()
    verify_cbz(out_cbz, records)

    with zipfile.ZipFile(out_cbz) as z:
        assert z.namelist() == ["0001.jpg", "0002.jpg", "0003.jpg"]
        for info in z.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
            data = z.read(info)
            # JPEG として開けるか確認
            im = Image.open(io.BytesIO(data))
            assert im.format == "JPEG"


def test_write_pdf_cbz_with_rotation_and_blank(tmp_path):
    """回転ページと白紙ページを含む PDF のハイブリッド抽出・生成テスト。"""
    pdf_path = tmp_path / "hybrid.pdf"
    writer = pypdf.PdfWriter()

    # 1 ページ目: 通常の画像 (RGB)
    im = Image.new("RGB", (120, 180), (255, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PDF")
    buf.seek(0)
    reader1 = pypdf.PdfReader(buf)
    writer.add_page(reader1.pages[0])

    # 2 ページ目: 90度回転あり
    page2 = writer.add_page(reader1.pages[0])
    page2.rotation = 90

    # 3 ページ目: 白紙 (画像なし)
    writer.add_blank_page(width=200, height=300)

    with open(pdf_path, "wb") as f:
        writer.write(f)

    out_cbz = tmp_path / "hybrid.cbz"
    records = write_pdf_cbz(pdf_path, out_cbz)
    assert len(records) == 3
    verify_cbz(out_cbz, records)

    with zipfile.ZipFile(out_cbz) as z:
        assert z.namelist() == ["0001.jpg", "0002.jpg", "0003.jpg"]
        # 2 ページ目は回転されたので、幅と高さが入れ替わっている (120x180 -> 180x120)
        im2 = Image.open(io.BytesIO(z.read("0002.jpg")))
        assert im2.size == (180, 120)

        # 3 ページ目は白紙 (200x300)
        im3 = Image.open(io.BytesIO(z.read("0003.jpg")))
        assert im3.size == (200, 300)


def test_scan_pdf(env):
    root = env.root
    pdf_path = root / "漫画" / "テスト 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=5)

    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)

    row = env.conn.execute("""SELECT v.id, v.format, v.page_count, s.rel_path
                              FROM volumes v JOIN sources s ON s.id = v.source_id
                              WHERE s.rel_path = '漫画/テスト 第1巻.pdf'""").fetchone()
    assert row is not None
    assert row["format"] == "pdf"
    assert row["page_count"] == 5


def test_range_organize_pdf(env):
    """範囲指定整理で include_cbz=True による PDF -> CBZ 変換、undo のテスト。"""
    root = env.root
    pdf_path = root / "自炊" / "作品A 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=4)

    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)

    old_vid = vid_of(env, "自炊/作品A 第1巻.pdf")
    before_files, before_dirs = full_snapshot(root)

    o = organizer(env)

    # 1. include_cbz=False では対象外 (理由つき)
    plan_no_cbz = o.plan_range(env.root_id, "自炊", include_cbz=False)
    item = next(it for it in plan_no_cbz["items"] if it["from_rel_path"] == "自炊/作品A 第1巻.pdf")
    assert item["status"] == "excluded"
    assert "PDF" in item["reason"]

    # 2. include_cbz=True では CBZ 化対象として計画される
    plan = o.plan_range(env.root_id, "自炊", include_cbz=True)
    item = next(it for it in plan["items"] if it["from_rel_path"] == "自炊/作品A 第1巻.pdf")
    assert item["status"] == "cbz"
    assert item["conversion"] is True
    assert item["page_count"] == 4
    assert item["output_rel_path"] == "自炊/作品A 第1巻.cbz"

    # プレビューではディスクは変わらない
    assert full_snapshot(root) == (before_files, before_dirs)

    # 3. 実行
    res = o.execute(plan["op_id"])
    assert res["state"] == "done"

    # 出力先に CBZ ができ、元 PDF は退避されている
    cbz_out = root / "自炊" / "作品A 第1巻.cbz"
    assert cbz_out.is_file()
    assert not pdf_path.exists()
    assert (env.settings.organize_base(root) / "trash" / plan["op_id"] / "original" / "自炊/作品A 第1巻.pdf").is_file()

    # CBZ の中身確認
    with zipfile.ZipFile(cbz_out) as z:
        assert len(z.namelist()) == 4
        assert z.namelist()[0] == "0001.jpg"

    # 新しい巻が登録され、元の巻は organized_op_id が付く
    new_vid = vid_of(env, "自炊/作品A 第1巻.cbz")
    assert new_vid != old_vid
    old_row = env.conn.execute("SELECT organized_op_id, missing FROM volumes WHERE id = ?", (old_vid,)).fetchone()
    assert old_row["organized_op_id"] == plan["op_id"]

    # 4. 元に戻す (undo)
    undo_res = o.undo(plan["op_id"])
    assert undo_res["state"] == "undone"

    # ディスク状態が元通りに復元される
    assert full_snapshot(root) == (before_files, before_dirs)
    assert pdf_path.is_file()
    assert not cbz_out.exists()


def test_range_organize_pdf_delete_originals(env):
    """delete_originals=True で実行した場合、退避フォルダの元 PDF が削除され undo できない。"""
    root = env.root
    pdf_path = root / "自炊" / "作品B 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=3)

    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)

    o = organizer(env)
    plan = o.plan_range(env.root_id, "自炊", include_cbz=True, delete_originals=True)
    res = o.execute(plan["op_id"])
    assert res["state"] == "done"

    # 作成された CBZ が存在
    assert (root / "自炊" / "作品B 第1巻.cbz").is_file()
    # 元は退避フォルダからも削除されている
    trashed = env.settings.organize_base(root) / "trash" / plan["op_id"] / "original" / "自炊/作品B 第1巻.pdf"
    assert not trashed.exists()

    # undo は拒否される (OrganizeError)
    with pytest.raises(Exception, match="削除したので"):
        o.undo(plan["op_id"])


def test_single_organize_pdf(env):
    """単巻整理 (Organizer.plan / execute) でも PDF を CBZ に整理できること。"""
    root = env.root
    pdf_path = root / "単巻" / "単体 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=2)

    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    vid = vid_of(env, "単巻/単体 第1巻.pdf")

    o = organizer(env)
    plan = o.plan(volume_id=vid)
    assert plan["problems"] == []
    assert len(plan["volumes"]) == 1
    assert plan["volumes"][0]["output_rel_path"] == "単巻/単体 第1巻.cbz"

    res = o.execute(plan["op_id"])
    assert res["state"] == "done"
    assert (root / "単巻" / "単体 第1巻.cbz").is_file()
    assert not pdf_path.exists()


def test_scan_unreadable_pdf_does_not_update_source_size_or_drop_existing(env):
    """読めない PDF は sources.size/mtime を記録せず、既存の巻を missing にしない。"""
    root = env.root
    pdf_path = root / "漫画" / "破損テスト 1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=2)

    # 1 回目: 正常スキャン
    Scanner(env.conn, env.settings).scan()
    src = env.conn.execute("SELECT id, size FROM sources WHERE rel_path = '漫画/破損テスト 1巻.pdf'").fetchone()
    assert src["size"] is not None
    vol = env.conn.execute("SELECT id, missing FROM volumes WHERE source_id = ?", (src["id"],)).fetchone()
    assert vol["missing"] == 0

    # ファイルを破損させる
    pdf_path.write_bytes(b"not a pdf anymore")
    # mtime を進める
    st = pdf_path.stat()
    os.utime(pdf_path, (st.st_atime + 10, st.st_mtime + 10))

    # 2 回目: 再スキャン。読めない PDF なので sources のサイズは更新されず既存巻も missing にならない
    res = Scanner(env.conn, env.settings).scan()
    assert any("読めない" in iss["reason"] for iss in res.issues)

    src_after = env.conn.execute("SELECT id, size, mtime_ns FROM sources WHERE id = ?", (src["id"],)).fetchone()
    # 失敗したため、新しいサイズや mtime_ns は反映されない
    assert src_after["size"] == src["size"]

    vol_after = env.conn.execute("SELECT id, missing FROM volumes WHERE id = ?", (vol["id"],)).fetchone()
    assert vol_after["missing"] == 0  # missing にされない


def make_png_pdf(path: Path) -> Path:
    """PNG 画像が埋め込まれた PDF を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    stream = DecodedStreamObject()
    raw_rgb = b"\x00\xff\x00" * 100
    stream._data = raw_rgb
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Image")
    stream[NameObject("/Width")] = NumberObject(10)
    stream[NameObject("/Height")] = NumberObject(10)
    stream[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)

    img_ref = writer._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject()
    page["/Resources"][NameObject("/XObject")] = DictionaryObject()
    page["/Resources"]["/XObject"][NameObject("/Im1")] = img_ref

    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_write_pdf_cbz_preserves_png_and_handles_raw_tiff(tmp_path):
    """PNG 画像は .png エントリ名になり、TIFF 等の生画像は .jpg に安全に変換されること。"""
    # 1. PNG 画像の PDF
    png_pdf = make_png_pdf(tmp_path / "png.pdf")
    out_cbz = tmp_path / "png.cbz"
    records = write_pdf_cbz(png_pdf, out_cbz)
    assert len(records) == 1
    assert records[0].name == "0001.png"
    with zipfile.ZipFile(out_cbz) as z:
        data = z.read("0001.png")
        assert data.startswith(b"\x89PNG")

    # 2. 1-bit (TIFF) 画像の PDF
    im_1bit = Image.new("1", (20, 20), 1)
    tiff_pdf = tmp_path / "1bit.pdf"
    buf = io.BytesIO()
    im_1bit.save(buf, format="PDF")
    tiff_pdf.write_bytes(buf.getvalue())

    out_tiff_cbz = tmp_path / "1bit.cbz"
    records2 = write_pdf_cbz(tiff_pdf, out_tiff_cbz)
    assert len(records2) == 1
    assert records2[0].name == "0001.jpg"
    with zipfile.ZipFile(out_tiff_cbz) as z:
        data2 = z.read("0001.jpg")
        assert data2.startswith(b"\xff\xd8")  # 有効な JPEG に変換されている


def test_pdf_volume_api_and_thumbs(env):
    """未整理の PDF の巻は API からページ・表紙とも読めること(#261)。"""
    from fastapi.testclient import TestClient
    from spinebox.app import create_app

    pdf_path = env.root / "漫画" / "未整理 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=2)
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    vid = vid_of(env, "漫画/未整理 第1巻.pdf")

    client = TestClient(create_app(env.settings))
    res = client.get(f"/api/volumes/{vid}")
    assert res.status_code == 200
    body = res.json()
    assert body["format"] == "images" and body["page_count"] == 2
    page = client.get(f"/api/volumes/{vid}/pages/1")
    assert page.status_code == 200 and page.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/volumes/{vid}/thumbnail").status_code == 200


def test_plan_range_cbz_preserves_unanalyzed_problems_with_pdf(env):
    """PDF が含まれるフォルダでも、未解析アーカイブの problems がシャドウイングされずに残ること。"""
    root = env.root
    # 正常な PDF
    make_simple_pdf(root / "混在" / "第1巻.pdf", num_pages=2)
    # 内側アーカイブを含む zip (未解析) を作成
    inner_zip = io.BytesIO()
    with zipfile.ZipFile(inner_zip, "w") as z:
        z.writestr("inner.zip", b"dummy")
    outer_path = root / "混在" / "第2巻.zip"
    outer_path.write_bytes(inner_zip.getvalue())

    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)

    o = organizer(env)
    plan = o.plan_range(env.root_id, "混在", include_cbz=True)
    # 未解析アーカイブがあるため needs_analysis に入っていること
    assert len(plan["needs_analysis"]) > 0


def test_read_unorganized_pdf_directly(env):
    """整理前の PDF の巻を、元を書き換えずにページ画像として読める(#261)。"""
    from spinebox.thumbs import Thumbnails
    pdf_path = env.root / "漫画" / "直読み 第1巻.pdf"
    make_simple_pdf(pdf_path, num_pages=3)
    before = pdf_path.read_bytes()
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    vid = vid_of(env, "漫画/直読み 第1巻.pdf")
    with env.library.open(env.conn, vid) as vol:
        assert vol.page_names == ["0001.jpg", "0002.jpg", "0003.jpg"]
        sizes = [Image.open(io.BytesIO(vol.read_page(i))).size for i in range(3)]
    assert sizes == [(100, 150), (110, 160), (120, 170)]
    key, thumb = Thumbnails(env.library, env.library.cache).get(env.conn, vid)
    assert thumb.startswith(b"\xff\xd8")
    assert pdf_path.read_bytes() == before


def test_read_encrypted_pdf_is_unreadable(env):
    from spinebox.library import VolumeUnreadable
    pdf_path = make_simple_pdf(env.root / "漫画" / "暗号 第1巻.pdf", num_pages=2)
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    vid = vid_of(env, "漫画/暗号 第1巻.pdf")
    w = pypdf.PdfWriter(clone_from=str(pdf_path))
    w.encrypt("pw")
    with open(pdf_path, "wb") as f:
        w.write(f)
    with pytest.raises(VolumeUnreadable):
        with env.library.open(env.conn, vid):
            pass


def test_pdf_volume_png_content_type_and_work_cover(env):
    """PNG を埋めた PDF のページは image/png で返り、作品の表紙候補・format も画像の巻として扱う(#261)。"""
    from fastapi.testclient import TestClient
    from spinebox.app import create_app

    make_png_pdf(env.root / "漫画" / "ぴーえぬじー" / "ぴーえぬじー 第1巻.pdf")
    make_png_pdf(env.root / "漫画" / "ぴーえぬじー" / "ぴーえぬじー 第2巻.pdf")
    Scanner(env.conn, env.settings).scan()
    works.sync(env.conn)
    vid = vid_of(env, "漫画/ぴーえぬじー/ぴーえぬじー 第1巻.pdf")

    client = TestClient(create_app(env.settings))
    page = client.get(f"/api/volumes/{vid}/pages/0")
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"

    listing = client.get("/api/works").json()
    text = str(listing)
    assert "'pdf'" not in text
    assert [w["cover_volume_id"] for w in listing["works"]] == [vid]
