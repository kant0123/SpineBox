"""`_参考/サンプル本` の実データでの結合テスト(GOAL.md フェーズ 1 の完了条件)。

サンプルはコミットしないので、無い環境と 7z の無い環境では skip する。置き場所は環境変数
`SPINEBOX_SAMPLE_DIR`(無ければリポジトリ直下の `_参考/サンプル本`)。**原本には読み取りしかしない**
ことを、前後のサイズ・mtime・sha256 で確かめる。

ページは各巻の先頭・中ほど・最終を読む。全ページ(約 2000 枚、2 分前後)は `SPINEBOX_SAMPLE_FULL=1`。
"""
import hashlib
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spinebox.app import create_app

SAMPLE_DIR = Path(os.environ.get("SPINEBOX_SAMPLE_DIR") or Path(__file__).resolve().parents[1] / "_参考" / "サンプル本")
EXPECTED_VOLUMES = 10
FULL = os.environ.get("SPINEBOX_SAMPLE_FULL") == "1"


def fingerprint(root: Path) -> dict:
    result = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
            result[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns, h.hexdigest())
    return result


@pytest.fixture(scope="module")
def sample_dir():
    if not SAMPLE_DIR.is_dir() or not any(SAMPLE_DIR.iterdir()):
        pytest.skip(f"サンプルが無い: {SAMPLE_DIR}")
    return SAMPLE_DIR


def test_reference_samples_scan_and_read_every_page(sample_dir, sevenzip, make_settings, capsys):
    before = fingerprint(sample_dir)
    settings = make_settings(sample_dir, sevenzip_path=sevenzip)
    client = TestClient(create_app(settings, frontend_dist=None))

    t0 = time.perf_counter()
    scan = client.post("/api/scan").json()
    scan_sec = time.perf_counter() - t0
    assert scan == {"volumes": EXPECTED_VOLUMES, "offline_roots": []}
    assert list(settings.cache_dir.iterdir()) == []  # スキャンは内側アーカイブを 1 つも取り出さない

    volumes = client.get("/api/volumes").json()
    titles = [v["title"] for v in volumes]
    assert len(volumes) == EXPECTED_VOLUMES
    assert any("第01巻" in t for t in titles) and any("第03巻" in t for t in titles)  # cp932 名が化けない
    assert not any("�" in t for t in titles)

    report = []
    for v in volumes:
        t = time.perf_counter()
        detail = client.get(f"/api/volumes/{v['id']}")
        assert detail.status_code == 200, (v["title"], detail.text)
        pages = detail.json()["pages"]
        assert pages, v["title"]
        picked = pages if FULL else [pages[i] for i in sorted({0, len(pages) // 2, len(pages) - 1})]
        for p in picked:
            r = client.get(f"/api/volumes/{v['id']}/pages/{p['index']}")
            assert r.status_code == 200 and r.content, (v["title"], p)
            assert r.headers["content-type"].startswith("image/")
        report.append((v["title"], len(pages), time.perf_counter() - t))
    with capsys.disabled():
        print(f"\nscan {scan_sec:.2f}s / {EXPECTED_VOLUMES} volumes")
        for title, n, sec in report:
            print(f"  {title}: {n} pages, {sec:.1f}s")

    # 全巻を開いた後(#30): v01-03(中に v01-02.rar{v01,v02} と v03)は 1・2・3 巻に分解され、食い違いは無い。
    # お気楽領主の zip(名前 v01-06、中は 1,1,2,2,3,4,5,6 巻)は重複を食い違いにしない。
    listed = client.get("/api/volumes").json()
    kaku = [v for v in listed if v["title"].startswith("Kakukaku Shikajika v0") and v["parent_id"] is not None]
    assert [v["volume_number"]["start"] for v in kaku] == [1, 2, 3]
    assert sum(v["page_count"] for v in kaku) == 472
    assert not any(v["findings"] for v in listed)
    v04 = [v for v in listed if v["title"] == "Kakukaku Shikajika v04"]
    assert len(v04) == 1 and v04[0]["parent_id"] is None
    assert fingerprint(sample_dir) == before  # 原本は 1 バイトも変わらない
