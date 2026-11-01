"""巻を開いて読むときの時間の計測(手動実行用。CI では動かさない)。

RAM 層(設定 `memory_cache_limit_bytes`)の有無で、次の 5 つの時間を比べる。各設定ごとに data_dir を
新しく作るので、展開キャッシュ(ディスク)も空から始まる。計測は API(TestClient)越しで、実際の閲覧と同じく
要求ごとに巻を開き直す。

- 開く: `GET /api/volumes/{id}`(内側アーカイブの巻は、ここで展開キャッシュへ取り出す)
- 1 ページ目: `GET .../pages/0`
- ページ送り: `GET .../pages/1..N` の平均と最大(まとめ取り出しが走ったページで待つ長さ)
- 同じページ: `GET .../pages/0` をもう一度(ブラウザのキャッシュが効かなかった場合)
- 開き直す: `GET /api/volumes/{id}` をもう一度

蔵書を指定しなければ合成データ(cbz・zip 入り zip・ソリッド 7z・7z 入り rar・zip 入り 7z)を一時フォルダに作って測る。
`--root` で実データ(開発機では `_参考/`)を測れる。蔵書には読み取りしかしない(DB と展開キャッシュは一時フォルダ)。

    python scripts/bench_reading.py
    python scripts/bench_reading.py --memory-limits 0,256M,1G --forward 20
    py -3.12 scripts/bench_reading.py --root _参考 --sevenzip "C:/Program Files/7-Zip/7z.exe" --max-volumes 10
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from spinebox import db  # noqa: E402
from spinebox.app import create_app  # noqa: E402
from spinebox.archive.sevenzip import find_sevenzip  # noqa: E402
from spinebox.config import Settings  # noqa: E402
from spinebox.scanner import Scanner  # noqa: E402

UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


def parse_size(text: str) -> int:
    text = text.strip().upper().removesuffix("IB").removesuffix("B")
    unit = text[-1:] if text[-1:] in UNITS else ""
    return int(float(text[:len(text) - len(unit)]) * UNITS[unit])


def fmt_size(n: int) -> str:
    for unit in ("G", "M", "K"):
        if n >= UNITS[unit] and n % UNITS[unit] == 0:
            return f"{n // UNITS[unit]}{unit}iB"
    return f"{n}B"


# --- 合成データ ---------------------------------------------------------------


# 乱数を 256 → 128 値に寄せる表。完全な乱数だと 7z(LZMA2)は無圧縮の塊で持ち、展開が単なる複写になって
# 実データ(JPEG はわずかに縮み、ソリッド 7z の展開は数十 MB/s)より速く見えてしまう
_LOW_ENTROPY = bytes(range(128)) * 2


def _pages(prefix: str, count: int, size: int) -> dict[str, bytes]:
    return {f"{prefix}{i:03d}.jpg": b"\xff\xd8\xff\xe0" + os.urandom(size - 4).translate(_LOW_ENTROPY)
            for i in range(1, count + 1)}


def _zip(files: dict[str, bytes], method=zipfile.ZIP_STORED) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _7z(exe: str, dest: Path, files: dict[str, bytes]) -> None:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td)
        for rel, data in files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        dest.parent.mkdir(parents=True, exist_ok=True)
        items = [p.name for p in sorted(src.iterdir())]
        # 既定(LZMA2・ソリッド)。実データの 7z もソリッドが多い
        subprocess.run([exe, "a", "-bso0", "-bsp0", "-y", str(dest), "--", *items], cwd=src, check=True)


def make_synthetic(root: Path, exe: str | None, pages: int, page_size: int) -> None:
    sys.path.insert(0, str(ROOT / "tests"))
    from archives7z import rar5_bytes  # noqa: E402 (テストの手組み RAR5)

    p = lambda prefix: _pages(prefix, pages, page_size)  # noqa: E731
    (root / "cbz").mkdir(parents=True)
    (root / "cbz" / "plain v01.cbz").write_bytes(_zip(p("p")))
    (root / "nested").mkdir()
    inner = _zip(p("p"))
    (root / "nested" / "outer.zip").write_bytes(_zip({"vol 01.zip": inner}))
    if exe is None:
        print("7-Zip が見つからないので 7z / rar の合成データは作らない", file=sys.stderr)
        return
    _7z(exe, root / "solid7z" / "solid v01.7z", p("p"))
    rar = rar5_bytes(list(p("p").items()))
    _7z(exe, root / "rar_in_7z" / "outer.7z", {"vol 01.rar": rar})
    _7z(exe, root / "7z_in_zip" / "_tmp.7z", p("p"))
    seven = (root / "7z_in_zip" / "_tmp.7z").read_bytes()
    (root / "7z_in_zip" / "_tmp.7z").unlink()
    (root / "7z_in_zip" / "outer.zip").write_bytes(_zip({"vol 01.7z": seven}))


# --- 計測 ---------------------------------------------------------------------


def _timed(fn) -> float:
    t = time.perf_counter()
    r = fn()
    elapsed = time.perf_counter() - t
    if r.status_code != 200:
        raise RuntimeError(f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:200]}")
    return elapsed


def measure(library_root: Path, memory_limit: int, exe: str, forward: int, max_volumes: int) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix="spinebox-bench-") as td:
        settings = Settings(library_roots=(library_root,), data_dir=Path(td), sevenzip_path=exe)
        if "memory_cache_limit_bytes" in {f.name for f in dataclasses.fields(Settings)}:
            settings = dataclasses.replace(settings, memory_cache_limit_bytes=memory_limit)
        elif memory_limit:
            raise SystemExit("この版には memory_cache_limit_bytes が無い(RAM 層なしの版では 0 だけを測れる)")
        conn = db.open_db(settings.db_path)
        Scanner(conn, settings).scan()
        rows = conn.execute(
            """SELECT v.id, v.title, s.rel_path, s.kind, v.container_path FROM volumes v
               JOIN sources s ON s.id = v.source_id
               WHERE v.parent_id IS NULL AND v.missing = 0 ORDER BY s.rel_path, v.id""").fetchall()
        conn.close()
        results = []
        with TestClient(create_app(settings, frontend_dist=None)) as client:
            for row in rows[:max_volumes]:
                vid = row["id"]
                url = f"/api/volumes/{vid}"
                try:
                    t_open = _timed(lambda: client.get(url))
                    count = client.get(url).json()["page_count"]
                    if not count:
                        continue
                    t_first = _timed(lambda: client.get(f"{url}/pages/0"))
                    nexts = [_timed(lambda i=i: client.get(f"{url}/pages/{i}")) for i in range(1, min(count, forward + 1))]
                    t_again = _timed(lambda: client.get(f"{url}/pages/0"))
                    t_reopen = _timed(lambda: client.get(url))
                except RuntimeError as e:
                    print(f"スキップ: {row['rel_path']}: {e}", file=sys.stderr)
                    continue
                label = row["rel_path"] + (f" :: {row['container_path']}" if row["container_path"] else "")
                results.append({"label": label, "pages": count, "open": t_open, "first": t_first,
                                "next": statistics.mean(nexts) if nexts else float("nan"),
                                "worst": max(nexts) if nexts else float("nan"),
                                "again": t_again, "reopen": t_reopen})
        return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, help="測る蔵書フォルダ(省略時は合成データ)。読み取りしかしない")
    ap.add_argument("--memory-limits", default="0,256M", help="比べる memory_cache_limit_bytes(カンマ区切り。0 = RAM 層なし)")
    ap.add_argument("--sevenzip", default="7z", help="7-Zip CLI のパス")
    ap.add_argument("--forward", type=int, default=10, help="ページ送りで読むページ数")
    ap.add_argument("--max-volumes", type=int, default=20, help="測る巻の上限(親の巻だけ)")
    ap.add_argument("--repeat", type=int, default=1, help="繰り返して中央値を取る回数")
    ap.add_argument("--pages", type=int, default=60, help="合成データの 1 巻のページ数")
    ap.add_argument("--page-kb", type=int, default=400, help="合成データの 1 ページの大きさ(KiB)")
    args = ap.parse_args()

    exe = find_sevenzip(args.sevenzip)
    limits = [parse_size(s) for s in args.memory_limits.split(",")]
    workdir = None
    if args.root:
        library_root = args.root.resolve()
    else:
        workdir = Path(tempfile.mkdtemp(prefix="spinebox-bench-lib-"))
        library_root = workdir / "lib"
        make_synthetic(library_root, exe, args.pages, args.page_kb * 1024)
    try:
        print(f"蔵書: {library_root}  7-Zip: {exe or '(なし)'}  ページ送り: {args.forward}  繰り返し: {args.repeat}")
        print()
        print("| 巻 | ページ | RAM 層 | 開く | 1 ページ目 | ページ送り(平均) | ページ送り(最大) | 同じページ | 開き直す |")
        print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        table: dict[str, dict[int, list[dict]]] = {}
        for _ in range(args.repeat):
            for limit in limits:
                for r in measure(library_root, limit, exe or args.sevenzip, args.forward, args.max_volumes):
                    table.setdefault(r["label"], {}).setdefault(limit, []).append(r)
        ms = lambda v: f"{v * 1000:.1f} ms"  # noqa: E731
        for label, by_limit in table.items():
            for limit, runs in by_limit.items():
                med = {k: statistics.median(r[k] for r in runs)
                       for k in ("open", "first", "next", "worst", "again", "reopen")}
                print(f"| {label} | {runs[0]['pages']} | {fmt_size(limit) if limit else 'なし'} | {ms(med['open'])} | "
                      f"{ms(med['first'])} | {ms(med['next'])} | {ms(med['worst'])} | {ms(med['again'])} | "
                      f"{ms(med['reopen'])} |")
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
