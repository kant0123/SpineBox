"""`python -m spinebox` — 設定の host / port で起動する。

`--print-bind` は起動せず「host port」を 1 行出して終わる。常駐ラッパー(deploy/start_spinebox.ps1)が、
起動前に前回の孤児がポートを握っていないか調べるために使う(TOML を PowerShell で読み直さない)。
`--rebuild-search` は起動せず作品の検索の索引を作り直して終わる(索引は派生データ。SQLite を替えたとき・
索引を疑うときに使う。サーバーを止めずに走らせてよい)。
"""
from __future__ import annotations

import sys

from .config import ConfigError, load_settings

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2
    if "--print-bind" in args:
        print(f"{settings.host} {settings.port}")
        return 0
    if "--rebuild-search" in args:
        from . import db, search

        conn = db.open_db(settings.db_path)
        try:
            count = search.rebuild(conn)
            kind = "FTS5 trigram" if search.uses_fts(conn) else "LIKE(この SQLite は trigram を持たない)"
        finally:
            conn.close()
        print(f"検索の索引を作り直しました: {count} 作品({kind})")
        return 0
    import uvicorn

    from .app import create_app

    if settings.host not in LOOPBACK_HOSTS:
        print(f"注意: {settings.host}:{settings.port} で LAN に公開します。SpineBox は認証を持たないので、"
              "インターネットへ公開しないでください(ルーターのポート開放もしない)。", file=sys.stderr, flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
