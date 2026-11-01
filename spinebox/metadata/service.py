"""有効な取得元をまとめて検索する入口。

設定の `[metadata] sources` で有効にした取得元を、書いてある順に 1 つずつ検索する(取得元ごとに
レート制限があり、同時に叩いても速くならない)。取得元を足すときは `SOURCE_CLASSES` に登録する
(楽天ブックスなど。API キーは `settings.metadata.keys` から読む)。
"""
from __future__ import annotations

import logging
import sqlite3

import httpx

from ..config import MetadataSettings
from .anilist import AniListSource
from .base import MetadataSource
from .http import HttpFetcher
from .mangaupdates import MangaUpdatesSource
from .model import FAIL_DISABLED, Failure, SearchOutcome, SearchQuery
from .ndl import NdlSource
from .store import ResponseStore

log = logging.getLogger(__name__)

SOURCE_CLASSES: dict[str, type[MetadataSource]] = {
    NdlSource.name: NdlSource,
    AniListSource.name: AniListSource,
    MangaUpdatesSource.name: MangaUpdatesSource,
}


class MetadataService:
    """`search(query)` は取得元ごとの `SearchOutcome` を返す。例外は投げない。"""

    def __init__(self, settings: MetadataSettings, conn: sqlite3.Connection, *,
                 fetcher: HttpFetcher | None = None, transport: httpx.BaseTransport | None = None,
                 store: ResponseStore | None = None):
        self.settings = settings
        self.store = store or ResponseStore(conn)
        self.fetcher = fetcher or HttpFetcher(settings, transport=transport)
        self.sources: dict[str, MetadataSource] = {}
        self.unknown: list[str] = []
        for name in settings.sources:
            cls = SOURCE_CLASSES.get(name)
            if cls is None:
                log.warning("metadata: unknown source in settings: %s", name)
                self.unknown.append(name)
            elif name not in self.sources:
                self.sources[name] = cls(settings, self.fetcher, self.store)

    def search(self, query: SearchQuery, sources: list[str] | None = None) -> list[SearchOutcome]:
        outcomes = []
        for name in sources if sources is not None else list(self.sources):
            source = self.sources.get(name)
            if source is None:
                outcomes.append(SearchOutcome(name, failure=Failure(FAIL_DISABLED, "この取得元は有効になっていない")))
            else:
                outcomes.append(source.search(query))
        return outcomes

    def close(self) -> None:
        self.fetcher.close()
