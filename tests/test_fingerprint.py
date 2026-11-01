"""見た目の指紋(spinebox/fingerprint.py)。"""
import pytest
from PIL import Image

from images import blank_page, jpeg, make_page, make_series, png
from spinebox import fingerprint as fp
from spinebox.duplicates import NEAR_BITS


def dist(a, b):
    return (a ^ b).bit_count()


def test_same_page_survives_rescale_and_recompress():
    for seed in range(20):
        page = make_page(seed)
        base = fp.image_hash(jpeg(page))
        assert base is not None
        for scale, quality in [(0.6, 50), (1.4, 70), (0.4, 30)]:
            assert dist(base, fp.image_hash(jpeg(page, scale=scale, quality=quality))) <= NEAR_BITS
        assert dist(base, fp.image_hash(png(page))) <= NEAR_BITS


def test_different_pages_are_far_apart():
    hashes = [fp.image_hash(jpeg(p)) for p in make_series(1, 40)]
    assert None not in hashes
    close = [(i, j) for i in range(40) for j in range(i + 1, 40) if dist(hashes[i], hashes[j]) <= NEAR_BITS]
    assert close == []


def test_color_and_gray_give_the_same_hash():
    page = make_page(3)
    rgb = Image.merge("RGB", (page, page, page))
    assert dist(fp.image_hash(jpeg(rgb)), fp.image_hash(jpeg(page))) <= NEAR_BITS


@pytest.mark.parametrize("data", [b"", b"not an image", b"\xff\xd8\xff\xe0FAKEJPEG", blank_page(255), blank_page(0)])
def test_unreadable_or_blank_images_have_no_hash(data):
    assert fp.image_hash(data) is None


def test_oversized_image_is_not_decoded(monkeypatch):
    monkeypatch.setattr(fp, "MAX_PIXELS", 100)
    assert fp.image_hash(jpeg(make_page(1))) is None


def test_sample_indexes_cap_keeps_ends():
    assert fp.sample_indexes(5) == [0, 1, 2, 3, 4]
    idx = fp.sample_indexes(5000, limit=100)
    assert len(idx) <= 100 and idx[0] == 0 and idx[-1] == 4999 and idx == sorted(set(idx))


def test_fingerprint_pages_skips_unreadable_and_failing_pages():
    pages = [jpeg(make_page(1)), b"junk", jpeg(make_page(2)), blank_page()]

    def read(i):
        if i == 2:
            raise OSError("boom")
        return pages[i]

    hashes, skipped = fp.fingerprint_pages(4, read)
    assert [i for i, _ in hashes] == [0] and skipped == 3


def test_pack_roundtrip():
    items = [(0, 0), (7, 2 ** 64 - 1), (123456, 0x8000000000000001)]
    assert fp.unpack(fp.pack(items)) == items
    assert fp.unpack(b"") == []
