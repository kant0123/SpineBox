"""`python -m spinebox` — 設定の host / port で起動する。"""
from __future__ import annotations

import sys

from .config import ConfigError, load_settings

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2
    import uvicorn

    from .app import create_app

    if settings.host not in LOOPBACK_HOSTS:
        print(f"注意: {settings.host}:{settings.port} で LAN に公開します。SpineBox は認証を持たないので、"
              "インターネットへ公開しないでください(ルーターのポート開放もしない)。", file=sys.stderr, flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
