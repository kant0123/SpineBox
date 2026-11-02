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
        t = 10**18 + i * 10**9  # NTFS の時刻分解能(100ns)より十分離す
        os.utime(d / f"{key}{SUFFIX}", ns=(t, t))
    (d / f"{TMP_PREFIX}abc").write_bytes(b"partial")
    cache = ExtractionCache(d, 15)
    assert cache.keys() == ["old", "new"]
    assert not (d / f"{TMP_PREFIX}abc").exists()
    _put(cache, "x", 1)  # 21 > 15 → 最古の old が消える
    assert _files(d) == ["new" + SUFFIX, "x" + SUFFIX]


def test_contains_does_not_read_or_touch_lru(tmp_path):
    """統計で全件を見る contains は中身を読まず、最後に使った順も変えない(#85)。消えたファイルは無いと答える。"""
    cache = ExtractionCache(tmp_path / "c", 25)
    _put(cache, "a", 10)
    _put(cache, "b", 10)
    assert cache.contains("a") and not cache.contains("zz")
    _put(cache, "c", 10)        # contains("a") で a を使い直していないので、最古の a が消える
    assert cache.keys() == ["b", "c"] and not cache.contains("a")
    (tmp_path / "c" / ("b" + SUFFIX)).unlink()
    assert not cache.contains("b")
