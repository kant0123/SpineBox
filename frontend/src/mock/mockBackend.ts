// 開発・テスト用のモック API。`npm run dev:mock` の Vite サーバーと、単体テストの fetch 差し替えの両方で使う。
// アプリ本体からは import しない(本番のバンドルに入れないため)。

import type { OrganizeOp, Progress, VolumeDetail, VolumeSummary } from "../api";

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
  // 整理(CBZ 化)の操作。ページ数未確定の巻(入れ子)は読めない内側があるものとして実行不可にする
  const ops: OrganizeOp[] = [];
  let opSeq = 0;

  function handleOrganize(method: string, path: string, parsed: { volume_id?: number } | undefined): MockResponse | null {
    if (method === "POST" && path === "/api/organize/plan") {
      const v = volumes.find((x) => x.id === parsed?.volume_id);
      if (!v) return json(404, { detail: "巻が見つかりません" });
      const blocked = v.page_count === null;
      const op: OrganizeOp = {
        op_id: blocked ? null : `op-${++opSeq}`,
        state: blocked ? "blocked" : "planned",
        source_id: v.id,
        source_rel_path: v.source_name,
        source_kind: "archive",
        volumes: [{ volume_id: v.id, title: v.title, page_count: v.page_count, output_rel_path: `${v.title}.cbz` }],
        warnings: [],
        problems: blocked ? ["内側アーカイブを読めません(7-Zip が見つからない、または未対応): inner.rar"] : [],
        created_at: "2026-11-01T00:00:00+00:00",
        executed_at: null,
        undone_at: null,
        error: null,
      };
      if (op.op_id) ops.unshift(op);
      return json(200, op);
    }
    if (method === "GET" && path === "/api/organize") return json(200, ops.filter((o) => o.state !== "planned"));
    const m = path.match(/^\/api\/organize\/([^/]+)\/(execute|undo)$/);
    if (!m || method !== "POST") return null;
    const op = ops.find((o) => o.op_id === decodeURIComponent(m[1]));
    if (!op) return json(404, { detail: "整理の操作が見つかりません" });
    if (m[2] === "execute") {
      if (op.state !== "planned") return json(409, { detail: `この操作は実行できる状態ではありません(${op.state})`, op });
      op.state = "done";
      op.executed_at = "2026-11-01T00:00:05+00:00";
    } else {
      if (op.state !== "done") return json(409, { detail: `この操作は元に戻せる状態ではありません(${op.state})`, op });
      op.state = "undone";
      op.undone_at = "2026-11-01T00:00:09+00:00";
    }
    return json(200, op);
  }

  function handle(method: string, rawPath: string, body?: string): MockResponse {
    const path = rawPath.split("?")[0];
    const parsed = body ? JSON.parse(body) : undefined;
    log.push({ method, path, body: parsed });

    if (method === "GET" && path === "/api/volumes") {
      const list: VolumeSummary[] = volumes.map((v) => ({ ...v }));
      return json(200, list);
    }
    if (path.startsWith("/api/organize")) {
      const r = handleOrganize(method, path, parsed);
      if (r) return r;
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

  return { handle, log, volumes, ops };
}
