// 開発・テスト用のモック API。`npm run dev:mock` の Vite サーバーと、単体テストの fetch 差し替えの両方で使う。
// アプリ本体からは import しない(本番のバンドルに入れないため)。

import type { Finding, VolumeDetail, VolumeSummary } from "../api";

export interface MockResponse {
  status: number;
  contentType: string;
  body: string;
  /** 応答を返すまで待つ時間。呼び出し側(Vite の middleware・テストの fetch 差し替え)が守る */
  delayMs?: number;
}

export interface MockOptions {
  /** ページ数未確定(入れ子)の巻を初めて開くときの遅延。実機で内側を取り出す 3〜8 秒を再現する */
  firstOpenDelayMs?: number;
  /** 巻 ID ごとに `GET /api/volumes/{id}` を失敗させる。一覧に無い ID でもよい(`#/read/<id>` で直接開く) */
  failures?: Record<string, { status: number; detail: string }>;
}

type MockVolume = Omit<VolumeSummary, "id"> & {
  id: number;
  /** 分解済みの親(一覧に出さない) */
  hidden?: boolean;
};

const base = { parent_id: null, parent_title: null, volume_number: null, findings: [], can_analyze: false };

/** 「v01-03」の中に 1・3 巻しか無い、という食い違い */
export const missingSecond: Finding = {
  scope: "volume",
  subject: "v01-03",
  name_range: { kind: "volume", start: 1, end: 3, evidence: "v01-03" },
  found: [1, 3],
  missing: [2],
  extra: [],
  duplicates: [],
  unnumbered: 0,
  message: "名前「v01-03」は 1〜3 巻だが、2 巻が見当たらない",
};

export function defaultVolumes(): MockVolume[] {
  return [
    { ...base, id: 1, title: "サンプル漫画 第01巻", source_name: "sample_v01.zip", page_count: 9, offline: false, progress: null },
    { ...base, id: 2, title: "サンプル漫画 第02巻", source_name: "sample_v02.cbz", page_count: 8, offline: false, progress: { page: 3, finished: false } },
    { ...base, id: 3, title: "読了済みの巻", source_name: "done.7z", page_count: 5, offline: false, progress: { page: 4, finished: true } },
    { ...base, id: 4, title: "外付けドライブの巻", source_name: "E:/漫画/offline.rar", page_count: 12, offline: true, progress: null },
    // 未解析の内側アーカイブ。「中の巻を調べる」で 6・7 に分解される
    { ...base, id: 5, title: "v01-03", source_name: "outer.7z", page_count: null, offline: false, progress: null, can_analyze: true },
  ];
}

function splitChildren(parent: MockVolume): MockVolume[] {
  const child = (id: number, n: number): MockVolume => ({
    ...base,
    id,
    title: `v0${n}`,
    source_name: parent.source_name,
    page_count: 4,
    offline: false,
    progress: null,
    parent_id: parent.id,
    parent_title: parent.title,
    volume_number: { kind: "volume", start: n, end: n, evidence: `v0${n}` },
    findings: [missingSecond],
  });
  return [child(100 + parent.id * 10 + 1, 1), child(100 + parent.id * 10 + 3, 3)];
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
export function createMockBackend(volumes: MockVolume[] = defaultVolumes(), options: MockOptions = {}) {
  const log: { method: string; path: string; body?: unknown }[] = [];
  const opened = new Set<number>();
  // ページ数未確定の巻は、開いた時点で数が決まる(入れ子の内側を開いたとき)想定
  const pagesOf = (v: MockVolume) => v.page_count ?? 6;

  function handle(method: string, rawPath: string, body?: string): MockResponse {
    const path = rawPath.split("?")[0];
    const parsed = body ? JSON.parse(body) : undefined;
    log.push({ method, path, body: parsed });

    const shown = () => volumes.filter((v) => !v.hidden);
    if (method === "GET" && path === "/api/volumes") {
      const list: VolumeSummary[] = shown().map(({ hidden: _hidden, ...v }) => ({ ...v }));
      return json(200, list);
    }
    if (method === "POST" && path === "/api/scan") {
      return json(200, { volumes: shown().length, offline_roots: volumes.some((v) => v.offline) ? ["E:/漫画"] : [] });
    }
    const m = path.match(/^\/api\/volumes\/([^/]+)(?:\/(progress|analyze|pages\/(\d+)))?$/);
    const failure = m && !m[2] && method === "GET" ? options.failures?.[decodeURIComponent(m[1])] : undefined;
    if (failure) return json(failure.status, { detail: failure.detail });
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
        parent_id: v.parent_id,
        volume_number: v.volume_number,
        findings: v.findings,
      };
      const first = v.page_count === null && !opened.has(v.id);
      opened.add(v.id);
      return { ...json(200, detail), delayMs: first ? options.firstOpenDelayMs : undefined };
    }
    if (m[2] === "analyze" && method === "POST") {
      if (!v.can_analyze) return json(200, { id: v.id, analyzed: false, children: [], findings: [] });
      const children = splitChildren(v);
      v.hidden = true;
      v.can_analyze = false;
      volumes.splice(volumes.indexOf(v) + 1, 0, ...children);
      return json(200, {
        id: v.id,
        analyzed: true,
        children: children.map((c) => ({ id: c.id, title: c.title, page_count: c.page_count, volume_number: c.volume_number })),
        findings: [missingSecond],
      });
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
