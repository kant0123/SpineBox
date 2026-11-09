"""公開 DB クライアントの実通信の確認(手動実行用。CI では動かさない)。

作品名を各取得元で検索し、取れた候補と確からしさを表示する。`--record DIR` を付けると、使った生応答を
`DIR/<取得元>/<ラベル>-<search|detail-ID>.<json|xml>` に書き出す(tests/fixtures/metadata/ に置く記録の採取用)。
取得元のレート制限は守る(既定の間隔で直列に送る)。秘匿情報は使わない(今回の 3 取得元はキー不要)。

    py -3.12 scripts/metadata_probe.py okiraku=お気楽領主の楽しい領地防衛 kaku=かくかくしかじか
    py -3.12 scripts/metadata_probe.py --record tests/fixtures/metadata okiraku=お気楽領主の楽しい領地防衛
    py -3.12 scripts/metadata_probe.py --source ndl --author 東村アキコ --volume 1 かくかくしかじか
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spinebox.config import MetadataSettings  # noqa: E402
from spinebox.db import open_db  # noqa: E402
from spinebox.metadata import MetadataService, SearchQuery  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("queries", nargs="+", help="作品名、または ラベル=作品名")
    ap.add_argument("--source", action="append", help="使う取得元(省略時は全部)")
    ap.add_argument("--author")
    ap.add_argument("--volume", type=int)
    ap.add_argument("--kind", choices=["manga", "novel"])
    ap.add_argument("--limit", type=int, default=8, help="1 回の検索で取る候補の上限(既定 8)")
    ap.add_argument("--record", type=Path, help="生応答を書き出すフォルダ")
    ap.add_argument("--top", type=int, default=5, help="取得元ごとに表示する候補数")
    ns = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # 既定で無効の取得元(nhentai / ehentai)も、--source で名指しすれば使える
    sources = tuple(ns.source) if ns.source else MetadataSettings().sources
    settings = MetadataSettings(candidate_limit=ns.limit, sources=sources)
    with tempfile.TemporaryDirectory() as tmp:
        conn = open_db(Path(tmp) / "probe.db")
        service = MetadataService(settings, conn)
        try:
            for raw in ns.queries:
                label, _, title = raw.partition("=") if "=" in raw else ("q", "", raw)
                query = SearchQuery(title, author=ns.author, volume=ns.volume, kind=ns.kind)
                print(f"\n=== {label}: {title}")
                for outcome in service.search(query, ns.source):
                    report(outcome, ns.top)
                    if ns.record:
                        record(conn, service, outcome, label, ns.record)
        finally:
            conn.close()
    return 0


def report(outcome, top: int) -> None:
    if outcome.failure:
        print(f"[{outcome.source}] 失敗: {outcome.failure.kind} {outcome.failure.message}")
        return
    print(f"[{outcome.source}] 候補 {len(outcome.candidates)} 件"
          + (f"(注: {'; '.join(outcome.notes)})" if outcome.notes else ""))
    for c in outcome.candidates[:top]:
        i = c.item
        print(f"  {c.match.score:.2f} {i.title}" + (f" : {i.subtitle}" if i.subtitle else "")
              + f" | 巻={i.volume_label or i.volume} 著者={'/'.join(i.authors[:3])} 出版社={i.publisher} "
              f"レーベル={i.label} 発売={i.released} ISBN={i.isbn} 種別={i.kind} 全{i.total_volumes}巻")
        if i.romaji or i.aliases:
            print(f"       ローマ字={i.romaji} 別名={list(i.aliases[:6])}")
        if i.genres or i.tags:
            print(f"       ジャンル={list(i.genres)} タグ={list(i.tags[:6])}")
        print(f"       根拠={list(c.match.reasons)}")


def record(conn, service, outcome, label: str, out_dir: Path) -> None:
    for ref in outcome.responses:
        stored = service.store.get_by_key(outcome.source, ref.request_key)
        if stored is None:
            continue
        request = json.loads(conn.execute("SELECT request FROM metadata_responses WHERE source = ? AND request_key = ?",
                                          (outcome.source, ref.request_key)).fetchone()["request"])
        name = ref.label
        if ref.label == "detail" and outcome.source != "ehentai":  # E-Hentai の詳細は api.php への POST(ID は本文)
            name += "-" + request["url"].rsplit("/", 1)[-1]
        ext = "xml" if outcome.source == "ndl" else "html" if outcome.source == "ehentai" and ref.label == "search" else "json"
        path = out_dir / outcome.source / f"{label}-{name}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(stored.body)
        print(f"  記録: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
