// 開発・テスト用のモック API。`npm run dev:mock` の Vite サーバーと、単体テストの fetch 差し替えの両方で使う。
// アプリ本体からは import しない(本番のバンドルに入れないため)。

import type {
  DuplicateCandidate,
  DuplicateScanState,
  DuplicateVolume,
  Finding,
  OrganizeOp,
  VolumeDetail,
  VolumeSummary,
} from "../api";

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

const base = { parent_id: null, parent_title: null, volume_number: null, findings: [], can_analyze: false, duplicate_count: 0 };

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
    { ...base, id: 1, title: "サンプル漫画 第01巻", source_name: "sample_v01.zip", page_count: 9, offline: false, progress: null,
      volume_number: { kind: "volume", start: 1, end: 1, evidence: "第01巻" }, duplicate_count: 1 },
    { ...base, id: 2, title: "サンプル漫画 第02巻", source_name: "sample_v02.cbz", page_count: 8, offline: false, progress: { page: 3, finished: false } },
    { ...base, id: 3, title: "読了済みの巻", source_name: "done.7z", page_count: 5, offline: false, progress: { page: 4, finished: true } },
    { ...base, id: 4, title: "外付けドライブの巻", source_name: "E:/漫画/offline.rar", page_count: 12, offline: true, progress: null },
    // 未解析の内側アーカイブ。「中の巻を調べる」で 6・7 に分解される
    { ...base, id: 5, title: "v01-03", source_name: "outer.7z", page_count: null, offline: false, progress: null, can_analyze: true },
    // id 1 の別スキャン(ページ数が違う)。重複候補の相手
    { ...base, id: 6, title: "Sample manga v01", source_name: "sample_manga_v01s.zip", page_count: 11, offline: false, progress: null,
      volume_number: { kind: "volume", start: 1, end: 1, evidence: "v01" }, duplicate_count: 1 },
  ];
}

const dupSide = (v: MockVolume): DuplicateVolume => ({
  id: v.id, title: v.title, source_name: v.source_name, parent_title: v.parent_title, page_count: v.page_count,
  offline: v.offline, volume_number: v.volume_number, progress: v.progress,
});

/** 初期の重複候補: 巻 1 と巻 6 */
export function defaultCandidates(volumes: MockVolume[]): DuplicateCandidate[] {
  const a = volumes.find((v) => v.id === 1);
  const b = volumes.find((v) => v.id === 6);
  if (!a || !b) return [];
  return [{
    id: 1,
    rule: "same_number",
    matched_pages: 8,
    compared_pages: 9,
    evidence: {
      matched_pages: 8, compared_pages: 9, pages_a: 9, pages_b: 11, matched_a: 8, matched_b: 9, near_bits: 8,
      number_a: "第01巻", number_b: "v01",
      samples: [{ a_page: 0, b_page: 0, distance: 1 }, { a_page: 2, b_page: 3, distance: 0 }],
    },
    dismissed: false,
    a: dupSide(a),
    b: dupSide(b),
  }];
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
  const candidates = defaultCandidates(volumes);
  const idleScan: DuplicateScanState = { running: false, started_at: null, finished_at: null, done: 0, total: 0, summary: null, error: null };
  let scanState: DuplicateScanState = { ...idleScan };
  let scanPolls = 0;
  // ページ数未確定の巻は、開いた時点で数が決まる(入れ子の内側を開いたとき)想定
  const pagesOf = (v: MockVolume) => v.page_count ?? 6;
  // 整理(CBZ 化)の操作。ページ数未確定の巻(入れ子)は読めない内側があるものとして実行不可にする
  const ops: OrganizeOp[] = [];
  let opSeq = 0;

  function handleOrganize(method: string, path: string, parsed: { volume_id?: number } | undefined): MockResponse | null {
    if (method === "POST" && path === "/api/organize/plan") {
      const v = volumes.find((x) => x.id === parsed?.volume_id);
      if (!v) return json(404, { detail: "巻が見つかりません" });
      const unanalyzed = v.can_analyze; // 中の巻をまだ調べていない内側アーカイブ
      const blocked = v.page_count === null || unanalyzed;
      const op: OrganizeOp = {
        op_id: blocked ? null : `op-${++opSeq}`,
        state: blocked ? "blocked" : "planned",
        source_id: v.id,
        source_rel_path: v.source_name,
        source_kind: "archive",
        volumes: [{ volume_id: v.id, title: v.title, page_count: v.page_count, output_rel_path: `${v.title}.cbz` }],
        warnings: [],
        problems: unanalyzed
          ? [`内側アーカイブ「${v.title}」の中の巻をまだ調べていません。先に「中の巻を調べる」を実行してください`]
          : blocked
            ? ["内側アーカイブを読めません(7-Zip が見つからない、または未対応): inner.rar"]
            : [],
        needs_analysis: unanalyzed ? [v.id] : [],
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

    const shown = () => volumes.filter((v) => !v.hidden);
    if (method === "GET" && path === "/api/volumes") {
      const list: VolumeSummary[] = shown().map(({ hidden: _hidden, ...v }) => ({ ...v }));
      return json(200, list);
    }
    if (path.startsWith("/api/organize")) {
      const r = handleOrganize(method, path, parsed);
      if (r) return r;
    }
    if (method === "POST" && path === "/api/scan") {
      return json(200, { volumes: shown().length, offline_roots: volumes.some((v) => v.offline) ? ["E:/漫画"] : [] });
    }
    if (path === "/api/duplicates" && method === "GET") {
      const wantDismissed = rawPath.includes("status=dismissed");
      if (scanState.running && ++scanPolls >= 3) {
        // 3 回目の取得で終わる(進み具合 → 完了を見せる)
        scanState = { ...scanState, running: false, finished_at: "now", done: 2, total: 2,
                      summary: { fingerprinted: 2, errors: 0, candidates: candidates.filter((c) => !c.dismissed).length } };
      } else if (scanState.running) {
        scanState = { ...scanState, done: 1, total: 2 };
      }
      return json(200, { scan: scanState, candidates: candidates.filter((c) => c.dismissed === wantDismissed) });
    }
    if (path === "/api/duplicates/scan" && method === "POST") {
      if (scanState.running) return json(409, { detail: "重複候補を調べています" });
      scanPolls = 0;
      scanState = { ...idleScan, running: true, started_at: "now" };
      return json(202, scanState);
    }
    const dup = path.match(/^\/api\/duplicates\/(\d+)\/(dismiss|restore)$/);
    if (dup && method === "POST") {
      const c = candidates.find((x) => x.id === Number(dup[1]));
      if (!c) return json(404, { detail: "候補が見つかりません" });
      c.dismissed = dup[2] === "dismiss";
      for (const side of [c.a, c.b]) {
        const v = volumes.find((x) => x.id === side.id);
        if (v) v.duplicate_count = candidates.filter((x) => !x.dismissed && (x.a.id === v.id || x.b.id === v.id)).length;
      }
      return json(200, { id: c.id, dismissed: c.dismissed });
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

  return { handle, log, volumes, ops };
}
