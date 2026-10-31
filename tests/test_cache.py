import os

import pytest

from spinebox.cache import SUFFIX, TMP_PREFIX, CacheTooLarge, ExtractionCache, cache_key


def _put(cache, key, size):
    with cache.use(key, lambda f: f.write(b"x" * size)) as p:
        return p


def _files(d):
    return sorted(p.name for p in d.iterdir())


def test_hash_names_not_original_paths(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 100)
    key = cache_key("inner", "E:/本/../x.zip", "a/b.zip")
    p = _put(cache, key, 10)
    assert p.parent == tmp_path / "c"
    assert p.name == key + SUFFIX and len(key) == 64


def test_lru_eviction(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 25)
    _put(cache, "a", 10)
    _put(cache, "b", 10)
    _put(cache, "a", 10)        # a を使い直す → 最古は b
    _put(cache, "c", 10)        # 30 > 25 で b が消える
    assert _files(tmp_path / "c") == ["a" + SUFFIX, "c" + SUFFIX]
    assert cache.total_bytes == 20
    assert cache.keys() == ["a", "c"]


def test_hit_does_not_call_producer(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 100)
    _put(cache, "a", 10)
    with cache.use("a", lambda f: pytest.fail("作り直さない")) as p:
        assert p.read_bytes() == b"x" * 10


def test_pinned_file_is_not_evicted(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 15)
    with cache.use("a", lambda f: f.write(b"x" * 10)) as pa:
        _put(cache, "b", 10)    # 合計 20 > 15。最古は a だが使用中なので、使い終わった b が消える
        assert pa.is_file()
        assert _files(tmp_path / "c") == ["a" + SUFFIX]
    _put(cache, "c", 10)        # a はもう使用中でないので、最古の a が消える
    assert _files(tmp_path / "c") == ["c" + SUFFIX]


def test_failed_producer_leaves_nothing(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 100)

    def boom(f):
        f.write(b"partial")
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        with cache.use("a", boom):
            pass
    assert _files(tmp_path / "c") == []


def test_too_large_is_refused(tmp_path):
    cache = ExtractionCache(tmp_path / "c", 5)
    with pytest.raises(CacheTooLarge):
        _put(cache, "a", 10)
    assert _files(tmp_path / "c") == []


def test_reload_restores_order_and_cleans_tmp(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    for i, key in enumerate(["old", "new"]):
        (d / f"{key}{SUFFIX}").write_bytes(b"x" * 10)
        os.utime(d / f"{key}{SUFFIX}", ns=(10**18 + i, 10**18 + i))
    (d / f"{TMP_PREFIX}abc").write_bytes(b"partial")
    cache = ExtractionCache(d, 15)
    assert cache.keys() == ["old", "new"]
    assert not (d / f"{TMP_PREFIX}abc").exists()
    _put(cache, "x", 1)  # 21 > 15 → 最古の old が消える
    assert _files(d) == ["new" + SUFFIX, "x" + SUFFIX]
