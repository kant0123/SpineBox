from spinebox.pages import is_cover, media_type, natural_key, sort_pages


def test_natural_order():
    assert sort_pages(["10.jpg", "2.jpg", "1.jpg"]) == ["1.jpg", "2.jpg", "10.jpg"]
    assert natural_key("p2.jpg") < natural_key("p10.jpg")


def test_cover_first_and_non_images_excluded():
    names = ["002.jpg", "001.jpg", "cover.jpg", "info.txt", "Thumbs.db", "010.png"]
    assert sort_pages(names) == ["cover.jpg", "001.jpg", "002.jpg", "010.png"]


def test_cover_variants():
    assert is_cover("COVER.JPG")
    assert is_cover("DLRAW.TO_cover.jpg")
    assert is_cover("dir/book-cover.webp")
    assert not is_cover("discover.jpg")
    assert not is_cover("cover2.jpg")
    assert sort_pages(["01.jpg", "DLRAW.TO_cover.jpg"]) == ["DLRAW.TO_cover.jpg", "01.jpg"]


def test_image_extensions_and_media_type():
    names = [f"a.{e}" for e in ["jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "PNG", "pdf", "zip"]]
    assert len(sort_pages(names)) == 8
    assert media_type("x.AVIF") == "image/avif"
    assert media_type("x.jpeg") == "image/jpeg"
