"""生応答の保存(`metadata_responses`)。

要求(取得元・鍵・秘匿を除いた記述)・応答本文のバイト列そのまま・取得日時を残し、
同じ要求は有効期限内なら保存済みの応答を使う(取得元への再取得を減らす)。
有効期限が切れた行も消さない: 取得元が落ちているときの代わりに使える(`stale`)。
作品・巻への結線はしない(後続)。ここに入るのは公開 API の応答だけで、秘匿情報は入らない。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import transaction
from .http import SourceRequest


@dataclass(frozen=True)
class StoredResponse:
    source: str
    request_key: str
    status: int
    body: bytes
    fetched_at: str

    def age(self, at: datetime) -> timedelta:
        return at - datetime.fromisoformat(self.fetched_at)


class ResponseStore:
    def __init__(self, conn: sqlite3.Connection, *, clock=lambda: datetime.now(timezone.utc)):
        self.conn = conn
        self._clock = clock
        self._lock = threading.Lock()  # 接続は複数スレッドから使われうる

    def get(self, request: SourceRequest) -> StoredResponse | None:
        return self.get_by_key(request.source, request.key())

    def is_fresh(self, stored: StoredResponse, ttl_days: float) -> bool:
        return stored.age(self._clock()) <= timedelta(days=ttl_days)

    def put(self, request: SourceRequest, status: int, body: bytes) -> StoredResponse:
        fetched_at = self._clock().isoformat(timespec="seconds")
        description = json.dumps(request.describe(), ensure_ascii=False, sort_keys=True)
        with self._lock, transaction(self.conn):
            self.conn.execute(
                "INSERT INTO metadata_responses (source, request_key, request, status, body, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source, request_key) DO UPDATE SET "
                "request = excluded.request, status = excluded.status, body = excluded.body, "
                "fetched_at = excluded.fetched_at",
                (request.source, request.key(), description, status, body, fetched_at))
        return StoredResponse(request.source, request.key(), status, body, fetched_at)

    def delete(self, request: SourceRequest) -> None:
        """解釈できなかった保存済み応答を捨てる(次の検索で取り直す)。"""
        with self._lock, transaction(self.conn):
            self.conn.execute("DELETE FROM metadata_responses WHERE source = ? AND request_key = ?",
                              (request.source, request.key()))

    def get_by_key(self, source: str, request_key: str) -> StoredResponse | None:
        """候補の `response_key` から生応答を引き直す。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT source, request_key, status, body, fetched_at FROM metadata_responses "
                "WHERE source = ? AND request_key = ?", (source, request_key)).fetchone()
        return None if row is None else StoredResponse(
            row["source"], row["request_key"], row["status"], bytes(row["body"]), row["fetched_at"])

