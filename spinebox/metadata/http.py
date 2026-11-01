"""外部取得元への HTTP 層。タイムアウト・取得元ごとのレート制限・再試行の上限・User-Agent を持つ。

失敗は `FetchError` に畳んで呼び出し側(取得元クライアント)へ渡し、そこで `Failure` として結果に載せる。
例外はこの層の外(スキャン・閲覧)へ漏らさない。メッセージには URL・クエリ・応答本文を入れない
(クエリに API キーが入る取得元を後から足しても、ログと保存物に出ないようにするため)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from ..config import MetadataSettings
from .model import (
    FAIL_CONNECT, FAIL_HTTP, FAIL_INTERNAL, FAIL_RATE_LIMITED, FAIL_TIMEOUT, FAIL_TOO_LARGE, Failure,
)

log = logging.getLogger(__name__)

# httpx / httpcore は要求の URL(クエリを含む)を INFO・DEBUG で出す。API キーをクエリで渡す取得元を
# 足したときにログへ漏れないよう、ここで WARNING 以上に固定する
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

APP_NAME = "SpineBox"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# 429 で Retry-After が無いときに待つ秒数(AniList は超過後 1 分間止まる)
DEFAULT_RATE_LIMIT_BLOCK = 60.0
# 429 の Retry-After の上限(これより長く止められても、記録するのはこの秒数まで)
MAX_RATE_LIMIT_BLOCK = 15 * 60.0
# 次の要求を出せるまでこれより長く待つなら、待たずに「レート制限」として失敗させる(呼び出しを長く塞がない)
MAX_GATE_WAIT = 10.0
# 5xx・タイムアウト・接続不可の再試行の待ち(1 回目 1 秒、2 回目 2 秒…)
BACKOFF_BASE = 1.0


class FetchError(Exception):
    """取得に失敗した。`failure` を結果に載せる。str() は Failure.message と同じで、URL を含まない。"""

    def __init__(self, failure: Failure):
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class SourceRequest:
    """取得元への 1 要求。

    key() は秘匿を除いた内容の SHA-256 で、保存済み応答の鍵になる。`secret_params` / `secret_headers`
    は送信時にだけ付け、鍵・保存する要求の記述・ログ・repr に出さない。
    """
    source: str
    method: str
    url: str
    params: tuple[tuple[str, str], ...] = ()
    json_body: Any = None
    label: str = "request"  # 結果の ResponseRef に載せる用途の名前(search / detail など)
    secret_params: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)
    secret_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)

    def describe(self) -> dict:
        """保存・ログ用の記述。秘匿は含まない。"""
        return {"source": self.source, "method": self.method, "url": self.url,
                "params": [list(p) for p in self.params], "json": self.json_body, "label": self.label}

    def key(self) -> str:
        canonical = json.dumps({k: v for k, v in self.describe().items() if k != "label"},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    retry_after: str | None = None  # Retry-After ヘッダの値そのまま


class Gate:
    """取得元ごとの関門。要求を直列にし、前の要求が終わってから最小間隔を空ける。

    429 を受けたら `block()` で一定時間は要求を出さない(待つ時間が長すぎれば待たずに失敗させる)。
    時計と sleep は差し替えられる(テストで時間を進める)。
    """

    def __init__(self, min_interval: float, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_end: float | None = None
        self._blocked_until = 0.0

    def block(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)

    def blocked_for(self) -> float:
        return max(0.0, self._blocked_until - self._clock())

    def __enter__(self) -> "Gate":
        self._lock.acquire()
        try:
            now = self._clock()
            blocked = self._blocked_until - now
            # 429 で止められている残りが長いときは、待たずに失敗させる(呼び出しを長く塞がない)。
            # 最小間隔の待ちは設定した値なので、長くても待つ
            if blocked > MAX_GATE_WAIT:
                raise FetchError(Failure(FAIL_RATE_LIMITED, "取得元のレート制限のため、しばらく要求を出さない",
                                         retry_after=round(blocked, 1)))
            wait = blocked
            if self._last_end is not None:
                wait = max(wait, self._last_end + self.min_interval - now)
            if wait > 0:
                self._sleep(wait)
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self._last_end = self._clock()
        self._lock.release()


def user_agent(contact: str = "") -> str:
    ua = f"{APP_NAME}/0.1 (personal library manager; non-commercial)"
    return f"{ua} {contact}" if contact else ua


def parse_retry_after(value: str | None, now_epoch: Callable[[], float] = time.time) -> float | None:
    """Retry-After(秒数または HTTP 日付)を秒にする。読めなければ None。"""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now_epoch()
        except (TypeError, ValueError):
            return None
    return min(max(seconds, 0.0), MAX_RATE_LIMIT_BLOCK)


class HttpFetcher:
    """取得元の要求を、関門・タイムアウト・再試行つきで送る。"""

    def __init__(self, settings: MetadataSettings, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self._clock = clock
        self._sleep = sleep
        self._gates: dict[str, Gate] = {}
        self._gates_lock = threading.Lock()
        self._client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(settings.timeout_seconds),
            headers={"User-Agent": user_agent(settings.contact), "Accept": "*/*"}, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def gate(self, source: str) -> Gate:
        with self._gates_lock:
            if source not in self._gates:
                self._gates[source] = Gate(self.settings.interval(source), self._clock, self._sleep)
            return self._gates[source]

    def fetch(self, request: SourceRequest) -> HttpResponse:
        """200 の応答を返す。それ以外・失敗は FetchError。"""
        gate = self.gate(request.source)
        retries = self.settings.max_retries
        last: Failure | None = None
        for attempt in range(retries + 1):
            if attempt:
                self._sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
            with gate:  # 試行ごとに最小間隔を守る
                try:
                    response = self._send(request)
                except FetchError as e:
                    last = e.failure
                    if last.kind == FAIL_TOO_LARGE:
                        raise
                    log.warning("metadata fetch failed: source=%s kind=%s (attempt %d/%d)",
                                request.source, last.kind, attempt + 1, retries + 1)
                    continue
            status = response.status
            if status == 200:
                return response
            if status == 429:
                retry_after = parse_retry_after(response.retry_after)
                seconds = retry_after if retry_after is not None else DEFAULT_RATE_LIMIT_BLOCK
                gate.block(seconds)
                log.warning("metadata rate limited: source=%s retry_after=%s", request.source, seconds)
                raise FetchError(Failure(FAIL_RATE_LIMITED, "取得元に 429(レート制限)を返された",
                                         status=429, retry_after=seconds))
            last = Failure(FAIL_HTTP, f"取得元が HTTP {status} を返した", status=status)
            if status < 500:
                raise FetchError(last)  # 4xx は再試行しても変わらない
            log.warning("metadata fetch http %s: source=%s (attempt %d/%d)", status, request.source,
                        attempt + 1, retries + 1)
        raise FetchError(last or Failure(FAIL_INTERNAL, "取得に失敗した"))

    def _send(self, request: SourceRequest) -> HttpResponse:
        params = [*request.params, *request.secret_params]
        headers = dict(request.secret_headers)
        try:
            with self._client.stream(request.method, request.url, params=params or None,
                                     json=request.json_body, headers=headers or None) as resp:
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise FetchError(Failure(FAIL_TOO_LARGE, "応答が大きすぎる"))
                    chunks.append(chunk)
                return HttpResponse(resp.status_code, b"".join(chunks), resp.headers.get("Retry-After"))
        except FetchError:
            raise
        except httpx.TimeoutException:
            raise FetchError(Failure(FAIL_TIMEOUT, "取得元の応答がタイムアウトした")) from None
        except httpx.TransportError as e:
            # str(e) は URL を含みうるので、型名だけを残す
            raise FetchError(Failure(FAIL_CONNECT, f"取得元に接続できない({type(e).__name__})")) from None
        except Exception as e:  # noqa: BLE001 例外は外へ漏らさない
            # traceback は出さない(例外メッセージに URL・クエリが入りうる)
            log.error("metadata fetch internal error: source=%s type=%s", request.source, type(e).__name__)
            raise FetchError(Failure(FAIL_INTERNAL, f"取得中の想定外のエラー({type(e).__name__})")) from None
