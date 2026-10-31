// 開発・テスト用のモック API。`npm run dev:mock` の Vite サーバーと、単体テストの fetch 差し替えの両方で使う。
// アプリ本体からは import しない(本番のバンドルに入れないため)。

import type { Progress, VolumeDetail, VolumeSummary } from "../api";

export interface MockResponse {
  status: number;
  contentType: string;
  body: string;
}

interface MockVolume {
  id: number;
  title: string;
  source_name: string;
  page_count: number | null;
  offline: boolean;
  progress: Progress | null;
}

export function defaultVolumes(): MockVolume[] {
  return [
    { id: 1, title: "サンプル漫画 第01巻", source_name: "sample_v01.zip", page_count: 9, offline: false, progress: null },
    { id: 2, title: "サンプル漫画 第02巻", source_name: "sample_v02.cbz", page_count: 8, offline: false, progress: { page: 3, finished: false } },
    { id: 3, title: "読了済みの巻", source_name: "done.7z", page_count: 5, offline: false, progress: { page: 4, finished: true } },
    { id: 4, title: "外付けドライブの巻", source_name: "E:/漫画/offline.rar", page_count: 12, offline: true, progress: null },
    { id: 5, title: "ページ数未確定の巻(入れ子)", source_name: "outer.7z/inner.rar", page_count: null, offline: false, progress: null },
  ];
}

const json = (status: number, data: unknown): MockResponse => ({
  status,
  contentType: "application/json; charset=utf-8",
  body: JSON.stringify(data),
});

function pageSvg(title: string, index: number, total: number): string {
  const hue = (index * 47) % 360;
  const esc = title.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="700" height="1000" viewBox="0 0 700 1000">
<rect width="700" height="1000" fill="hsl(${hue},40%,85%)"/>
<text x="350" y="480" font-size="160" text-anchor="middle" font-family="sans-serif">${index + 1}</text>
<text x="350" y="600" font-size="36" text-anchor="middle" font-family="sans-serif">/ ${total}</text>
<text x="350" y="80" font-size="32" text-anchor="middle" font-family="sans-serif">${esc}</text>
</svg>`;
}

/** 状態を持つモック。progress の保存は次の GET に反映される。 */
export function createMockBackend(volumes: MockVolume[] = defaultVolumes()) {
  const log: { method: string; path: string; body?: unknown }[] = [];
  // ページ数未確定の巻は、開いた時点で数が決まる(入れ子の内側を開いたとき)想定
  const pagesOf = (v: MockVolume) => v.page_count ?? 6;

  function handle(method: string, rawPath: string, body?: string): MockResponse {
    const path = rawPath.split("?")[0];
    const parsed = body ? JSON.parse(body) : undefined;
    log.push({ method, path, body: parsed });

    if (method === "GET" && path === "/api/volumes") {
      const list: VolumeSummary[] = volumes.map((v) => ({ ...v }));
      return json(200, list);
    }
    if (method === "POST" && path === "/api/scan") {
      return json(200, { volumes: volumes.length, offline_roots: volumes.some((v) => v.offline) ? ["E:/漫画"] : [] });
    }
    const m = path.match(/^\/api\/volumes\/([^/]+)(?:\/(progress|pages\/(\d+)))?$/);
    const v = m ? volumes.find((x) => String(x.id) === decodeURIComponent(m[1])) : undefined;
    if (!m || !v) return json(404, { detail: "見つかりません" });

    if (!m[2] && method === "GET") {
      if (v.offline) return json(409, { detail: "蔵書フォルダがオフラインです" });
      const n = pagesOf(v);
      const detail: VolumeDetail = {
        id: v.id,
        title: v.title,
        page_count: n,
        pages: Array.from({ length: n }, (_, i) => ({ index: i, name: `${String(i + 1).padStart(3, "0")}.jpg` })),
        progress: v.progress,
      };
      return json(200, detail);
    }
    if (m[2] === "progress" && method === "PUT") {
      if (typeof parsed?.page !== "number" || typeof parsed?.finished !== "boolean") return json(422, { detail: "形式が不正" });
      v.progress = { page: parsed.page, finished: parsed.finished };
      return json(200, v.progress);
    }
    if (m[3] !== undefined && method === "GET") {
      const i = Number(m[3]);
      const n = pagesOf(v);
      if (i >= n) return json(404, { detail: "ページがありません" });
      return { status: 200, contentType: "image/svg+xml", body: pageSvg(v.title, i, n) };
    }
    return json(405, { detail: "未対応" });
  }

  return { handle, log, volumes };
}
