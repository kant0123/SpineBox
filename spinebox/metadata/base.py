"""取得元クライアントの共通インターフェースと、保存済み応答つきの取得。

取得元は `MetadataSource` を継承して `_search()` を書く。`search()` は例外を外へ出さず、
失敗は `SearchOutcome.failure` に入れて返す(スキャンや閲覧を止めない。CLAUDE.md の取得方針)。
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, TypeVar

from ..config import MetadataSettings
from ..db import now
from .http import FetchError, HttpFetcher, SourceRequest
from .match import score_item
from .model import (
    FAIL_INTERNAL, FAIL_INVALID, Candidate, Failure, MetadataItem, ResponseRef, SearchOutcome, SearchQuery,
)
from .store import ResponseStore

log = logging.getLogger(__name__)

T = TypeVar("T")


class ParseError(Exception):
    """応答が想定した形でない(JSON / XML として不正、必要な要素が無い)。"""


class MetadataSource:
    """公開 DB 1 つ分のクライアント。

    - `search(query)`: 作品名(+著者・巻番号)で検索して候補の列を返す。確からしさの高い順
    - 候補は `MetadataItem`(正規化した項目)と `Match`(一致度の数値と根拠)
    """
    name = ""

    def __init__(self, settings: MetadataSettings, fetcher: HttpFetcher, store: ResponseStore):
        self.settings = settings
        self.fetcher = fetcher
        self.store = store

    # --- 取得元ごとに書くところ ---
    def _search(self, query: SearchQuery, run: "_Run") -> list[tuple[MetadataItem, SourceRequest]]:
        """候補の項目と、その項目を取り出した要求(生応答を引き直す鍵になる)の組を返す。"""
        raise NotImplementedError

    # --- 共通 ---
    def search(self, query: SearchQuery) -> SearchOutcome:
        run = _Run(self)
        try:
            items = self._search(query, run)
        except FetchError as e:
            return self._failed(run, e.failure)
        except ParseError as e:
            return self._failed(run, Failure(FAIL_INVALID, f"応答を解釈できない: {e}"))
        except Exception as e:  # noqa: BLE001 例外は外へ漏らさない
            log.error("metadata search internal error: source=%s type=%s", self.name, type(e).__name__)
            return self._failed(run, Failure(FAIL_INTERNAL, f"検索中の想定外のエラー({type(e).__name__})"))
        candidates = [Candidate(item, score_item(query, item), request.key()) for item, request in items]
        candidates.sort(key=lambda c: c.match.score, reverse=True)
        return SearchOutcome(self.name, tuple(candidates), None, tuple(run.refs), tuple(run.notes))

    def _failed(self, run: "_Run", failure: Failure) -> SearchOutcome:
        return SearchOutcome(self.name, (), failure, tuple(run.refs), tuple(run.notes))


class _Run:
    """1 回の検索の中で使った生応答の記録と、保存済み応答つきの取得。"""

    def __init__(self, source: MetadataSource):
        self.source = source
        self.refs: list[ResponseRef] = []
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)

    def fetch(self, request: SourceRequest, parse: Callable[[bytes], T]) -> T:
        """保存済みの応答(有効期限内)があればそれを、無ければ取得して保存する。

        取得に失敗したとき、有効期限が切れた保存済み応答があればそれを使う(`stale`)。
        解釈できなかった応答は保存しない(保存済みのものが解釈できなければ捨てて取り直す)。
        """
        src = self.source
        stored = src.store.get(request)
        if stored is not None and src.store.is_fresh(stored, src.settings.cache_ttl_days):
            try:
                parsed = parse(stored.body)
            except ParseError:
                log.warning("metadata cached response unparsable, refetching: source=%s", request.source)
                src.store.delete(request)
            else:
                self.refs.append(ResponseRef(request.key(), request.label, stored.fetched_at, True))
                return parsed
        try:
            response = src.fetcher.fetch(request)
            parsed = parse(response.body)
        except (FetchError, ParseError) as e:
            if stored is not None:
                try:
                    parsed = parse(stored.body)
                except ParseError:
                    raise e from None
                log.warning("metadata source unavailable, using stale response: source=%s", request.source)
                self.refs.append(ResponseRef(request.key(), request.label, stored.fetched_at, True, stale=True))
                self.note(f"{request.label}: 取得に失敗したため有効期限切れの保存済み応答を使った")
                return parsed
            raise
        try:
            saved = src.store.put(request, response.status, response.body)
        except sqlite3.Error as e:
            # 保存に失敗しても取得結果は使う(ベストエフォート)
            log.warning("metadata response not saved: source=%s type=%s", request.source, type(e).__name__)
            self.refs.append(ResponseRef(request.key(), request.label, now(), False))
        else:
            self.refs.append(ResponseRef(request.key(), request.label, saved.fetched_at, False))
        return parsed
