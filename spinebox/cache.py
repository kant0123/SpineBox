"""展開キャッシュ。

`data_dir/cache/` の直下に、キーの sha256 を名前にしたファイルを置く。元の階層・名前は再現しない
(アーカイブ内の仮想パスは 257〜259 文字に達し、エントリ名をパスに使うのは危険なため)。
合計が上限を超えたら、最後に使ったのが古いものから消す(LRU)。キャッシュは消えてもよいもので、
消えたら次に要るとき作り直す。

最終使用の順はプロセス内のカウンタで持ち、起動時はファイルの mtime から復元する
(使うたびに mtime も触るので再起動後もおおむね順序が保たれる)。使用中(pin)のファイルは消さない。

`MemoryCache` は RAM 側の LRU(7z / rar のページと目次。使う側は spinebox/library.py)。再起動で消える。
"""
from __future__ import annotations

import hashlib
import itertools
import os
import threading
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

SUFFIX = ".bin"
TMP_PREFIX = ".tmp-"


class CacheTooLarge(Exception):
    """1 つで上限を超えるのでキャッシュに置けない。"""


class NotCached(Exception):
    """そのキーはまだキャッシュに無い(作らずに使う `use_existing()` で)。"""


def cache_key(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8", "surrogatepass"))
        h.update(b"\x00")
    return h.hexdigest()


class ExtractionCache:
    def __init__(self, directory: Path, limit_bytes: int):
        self.directory = Path(directory)
        self.limit_bytes = limit_bytes
        self._lock = threading.Lock()
        self._clock = itertools.count(1)
        self._last_used: dict[str, int] = {}
        self._sizes: dict[str, int] = {}
        self._pins: dict[str, int] = {}
        self._key_locks: dict[str, threading.Lock] = {}
        self.directory.mkdir(parents=True, exist_ok=True)
        self._load()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}{SUFFIX}"

    def _load(self) -> None:
        found = []
        for p in self.directory.iterdir():
            if p.name.startswith(TMP_PREFIX):
                # 前回の書きかけ
                try:
                    p.unlink()
                except OSError:
                    pass
                continue
            if p.is_file() and p.suffix == SUFFIX:
                st = p.stat()
                found.append((st.st_mtime_ns, p.stem, st.st_size))
        for _, key, size in sorted(found):
            self._last_used[key] = next(self._clock)
            self._sizes[key] = size

    @property
    def total_bytes(self) -> int:
        return sum(self._sizes.values())

    def keys(self) -> list[str]:
        """最後に使ったのが古い順。"""
        return sorted(self._last_used, key=self._last_used.__getitem__)

    def _touch(self, key: str) -> None:
        self._last_used[key] = next(self._clock)
        try:
            os.utime(self._path(key))
        except OSError:
            pass

    def _evict(self) -> None:
        for key in self.keys():
            if self.total_bytes <= self.limit_bytes:
                return
            if self._pins.get(key):
                continue
            try:
                self._path(key).unlink(missing_ok=True)
            except OSError:
                # Windows で他が開いている等。次の機会に消す
                continue
            self._last_used.pop(key, None)
            self._sizes.pop(key, None)

    @contextmanager
    def use(self, key: str, produce: Callable[[BinaryIO], None]) -> Iterator[Path]:
        """key のファイルのパスを、使っている間は消されない状態で渡す。無ければ produce で作る。

        produce は渡されたファイルに中身を書く。途中で例外が出たら書きかけは残さない。
        """
        path = self._path(key)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            self._pin_or_create(key, path, produce)
        try:
            yield path
        finally:
            with self._lock:
                self._pins[key] -= 1
                if not self._pins[key]:
                    del self._pins[key]
                self._evict()

    @contextmanager
    def use_existing(self, key: str) -> Iterator[Path]:
        """key のファイルがあれば `use()` と同じく消されない状態で渡す。無ければ作らずに NotCached。"""
        path = self._path(key)
        with self._lock:
            if key not in self._sizes or not path.is_file():
                raise NotCached(key)
            self._touch(key)
            self._pins[key] = self._pins.get(key, 0) + 1
        try:
            yield path
        finally:
            with self._lock:
                self._pins[key] -= 1
                if not self._pins[key]:
                    del self._pins[key]
                self._evict()

    def read(self, key: str) -> bytes | None:
        """key のファイルの中身(小さいもの用。表紙のサムネイル)。無ければ None。"""
        try:
            with self.use_existing(key) as path:
                return path.read_bytes()
        except (NotCached, OSError):
            return None

    def _pin_or_create(self, key: str, path: Path, produce: Callable[[BinaryIO], None]) -> None:
        """同じキーの作成は key ごとのロックで直列化される(Windows で置き換え先が開かれていると失敗するため)。"""
        with self._lock:
            if key in self._sizes and path.is_file():
                self._touch(key)
                self._pins[key] = self._pins.get(key, 0) + 1
                created = False
            else:
                self._sizes.pop(key, None)
                self._last_used.pop(key, None)
                created = True
        if created:
            # 書き出しは全体のロックの外(大きいと時間がかかる)
            tmp = self.directory / f"{TMP_PREFIX}{uuid.uuid4().hex}"
            try:
                with open(tmp, "wb") as f:
                    produce(f)
                size = tmp.stat().st_size
                if size > self.limit_bytes:
                    raise CacheTooLarge(f"キャッシュの上限({self.limit_bytes} バイト)より大きい: {size} バイト")
                os.replace(tmp, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            with self._lock:
                self._sizes[key] = size
                self._touch(key)
                self._pins[key] = self._pins.get(key, 0) + 1
                self._evict()


class MemoryCache:
    """RAM の LRU(ページのバイト列と 7z / rar の目次)。合計が上限を超えたら最後に使ったのが古いものから消す。

    展開キャッシュ(ディスク)と同じく消えてもよいもの。上限 0 なら何も持たない。1 つで上限を超えるものは置かない。
    大きさは値ごとに呼び出し側が渡す(bytes なら len。目次は見積もり)。
    """

    def __init__(self, limit_bytes: int):
        self.limit_bytes = limit_bytes
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[object, int]] = OrderedDict()
        self._total = 0

    @property
    def enabled(self) -> bool:
        return self.limit_bytes > 0

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def get(self, key: str):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: str, value: object, size: int | None = None) -> bool:
        """置けたかを返す。size を省くと len(value)。"""
        size = len(value) if size is None else size  # type: ignore[arg-type]
        if size > self.limit_bytes:
            return False
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._total -= old[1]
            self._items[key] = (value, size)
            self._total += size
            while self._total > self.limit_bytes:
                _, (_, evicted) = self._items.popitem(last=False)
                self._total -= evicted
        return True

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._total = 0
