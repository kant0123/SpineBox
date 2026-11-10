"""公開 DB クライアントのテスト用の道具。ネットワークには出ない(記録した応答 + httpx.MockTransport)。"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

FIXTURES = Path(__file__).parent / "fixtures" / "metadata"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeClock:
    """時計と sleep を差し替える。sleep は時計を進めるだけで、実際には待たない。"""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeNetwork:
    """URL ごとに応答を返し、受けた要求を記録する。route は `(method, url の接頭辞) → 応答の元`。

    応答の元は bytes(200)、httpx.Response、例外(送信時に投げる)、または `(request) -> 上のどれか`。
    """

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def route(self, method: str, prefix: str, response) -> None:
        self.routes.insert(0, (method, prefix, response))  # 後から足したものを優先

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, prefix, response in self.routes:
            if request.method == method and str(request.url).startswith(prefix):
                if callable(response):
                    response = response(request)
                if isinstance(response, Exception):
                    raise response
                if isinstance(response, httpx.Response):
                    return response
                return httpx.Response(200, content=response)
        return httpx.Response(404, content=b"no route")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def count(self, prefix: str = "") -> int:
        return sum(1 for r in self.requests if str(r.url).startswith(prefix))


NDL = "https://ndlsearch.ndl.go.jp/api/opensearch"
MEDIAARTS = "https://mediaarts-db.artmuseums.go.jp/sparql"
BANGUMI_SEARCH = "https://api.bgm.tv/search/subject/"
BANGUMI_DETAIL = "https://api.bgm.tv/v0/subjects/"


def recorded_network(label: str = "okiraku") -> FakeNetwork:
    """記録した応答を返す偽の取得元 3 つ。label は fixtures のファイル名の接頭辞。

    NDL は creator 付きの要求にだけ「著者で絞った」記録(kakuauthor)を返す。
    """
    net = FakeNetwork()

    def ndl(request: httpx.Request):
        query = parse_qs(urlparse(str(request.url)).query)
        if "creator" in query:
            return fixture("ndl/kakuauthor-search.xml")
        return fixture(f"ndl/{label}-search.xml")

    net.route("GET", NDL, ndl)
    net.route("GET", MEDIAARTS, fixture(f"mediaarts/{label}-search.json"))
    net.route("GET", BANGUMI_SEARCH, fixture(f"bangumi/{label}-search.json"))
    for path in (FIXTURES / "bangumi").glob(f"{label}-detail-*.json"):
        subject_id = path.stem.rsplit("-", 1)[1]
        net.route("GET", BANGUMI_DETAIL + subject_id, path.read_bytes())
    return net


def json_body(request: httpx.Request) -> dict:
    return json.loads(request.content)
