// 開発・テスト用のモック API。`npm run dev:mock` の Vite サーバーと、単体テストの fetch 差し替えの両方で使う。
// アプリ本体からは import しない(本番のバンドルに入れないため)。

import type {
  BookmarkItem,
  DuplicateCandidate,
  DuplicateScanState,
  DuplicateVolume,
  Finding,
  OrganizeItem,
  OrganizeOp,
  ScanIssue,
  ThumbnailBuildState,
  VolumeDetail,
  VolumeSummary,
} from "../api";
import { createMockInbox } from "./mockInbox";
import { createMockWorks } from "./mockWorks";

export interface MockResponse {
  status: number;
  contentType: string;
  body: string;
  /** 応答を返すまで待つ時間。呼び出し側(Vite の middleware・テストの fetch 差し替え)が守る */
  delayMs?: number;
}

function defaultScanIssues(): ScanIssue[] {
  return [
    { path: "E:/本/作者A/壊れた巻.zip", container_path: null, entry_name: null, reason: "読めないアーカイブ: zip として開けない: File is not a zip file" },
    { path: "E:/本/作者B/まとめ.zip", container_path: null, entry_name: "../evil.jpg", reason: "親ディレクトリ参照(..)を含むエントリ名" },
  ];
}

export interface MockOptions {
  /** ページ数未確定(入れ子)の巻を初めて開くときの遅延。実機で内側を取り出す 3〜8 秒を再現する */
  firstOpenDelayMs?: number;
  /** 巻 ID ごとに `GET /api/volumes/{id}` を失敗させる。一覧に無い ID でもよい(`#/read/<id>` で直接開く) */
  failures?: Record<string, { status: number; detail: string }>;
  /** `POST /api/scan` の「読めなかったもの」。省略すると 2 件 */
  scanIssues?: ScanIssue[];
  /** `POST /api/scan` を失敗させる(サーバーの `detail` を画面に出す確認用) */
  scanFailure?: { status: number; detail: string };
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
    // 小説(#57)。区切りの本文は mockSections
    { ...base, id: 7, title: "縦書きの小説 第1巻", source_name: "novel_v01.epub", format: "epub", page_count: null, offline: false, progress: null,
      volume_number: { kind: "volume", start: 1, end: 1, evidence: "第1巻" } },
    { ...base, id: 8, title: "青空文庫のテキスト", source_name: "aozora.txt", format: "text", page_count: 2, offline: false,
      progress: { page: 1, finished: false, position: 0.5 } },
  ];
}

/**
 * 小説の区切りの本文(サーバーが無害化した後の形)。巻 7 の 2 つ目は、無害化をすり抜けた想定の危険な HTML を混ぜて、
 * ブラウザ側の 2 段目の無害化を試す(本物のサーバーはこれを返さない)
 */
export function mockSections(): Record<number, string[]> {
  const para = (n: number, text: string) => Array.from({ length: n }, (_, i) => `<p>${text}(${i + 1})</p>`).join("");
  return {
    7: [
      `<h1>第一章</h1>${para(30, "吾輩は<ruby>猫<rt>ねこ</rt></ruby>である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。")}`,
      `<h1>第二章</h1><p>危険<script>window.__novelPwned = 1</script>な<img src="x" onerror="window.__novelPwned = 2">本文</p>` +
        `<img src="/api/volumes/7/resources/0" alt="挿絵" onload="window.__novelPwned = 3"><p onclick="window.__novelPwned = 4">${para(20, "第二章の本文。")}</p>` +
        `<iframe src="http://evil.example/"></iframe><a href="javascript:alert(1)">リンク</a>`,
      `<h1>第三章</h1>${para(10, "最後の章。")}<p>おしまい</p>`,
    ],
    8: [para(150, "テキストの本文。"), `${para(150, "後半の本文。")}<p>テキストの終わり</p>`],
  };
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
  const mockWorks = createMockWorks(() => volumes, (kind, id) => marks[kind].has(id));
  const opened = new Set<number>();
  const candidates = defaultCandidates(volumes);
  const idleScan: DuplicateScanState = { running: false, started_at: null, finished_at: null, done: 0, total: 0, summary: null, error: null };
  let scanState: DuplicateScanState = { ...idleScan };
  let scanPolls = 0;
  // 表紙(#75): 入れ子の巻(page_count 未確定)は、開くか「表紙を作る」までは 404 pending
  const idleThumbs: ThumbnailBuildState = { running: false, started_at: null, finished_at: null, done: 0, total: 0, summary: null, error: null };
  let thumbState: ThumbnailBuildState = { ...idleThumbs };
  let thumbPolls = 0;
  let thumbsBuilt = false;
  // ページ数未確定の巻は、開いた時点で数が決まる(入れ子の内側を開いたとき)想定
  const pagesOf = (v: MockVolume) => v.page_count ?? 6;
  // 整理(CBZ 化)の操作。ページ数未確定の巻(入れ子)は読めない内側があるものとして実行不可にする
  const ops: OrganizeOp[] = [];
  let opSeq = 0;
  const inbox = createMockInbox(ops, () => `op-${++opSeq}`);
  const sections = mockSections();

  // テンプレートどおりの改名・移動(作品 1 だけ)。巻 1 と 6 は同じ名前になる衝突、3 は 1 巻 = 1 CBZ でない対象外
  function renamePlan(workId: number, exclude: number[]): OrganizeOp {
    const name = (n: number) => `サンプル漫画/サンプル漫画 第0${n}巻.cbz`;
    const conflictIds = [1, 6].filter((id) => !exclude.includes(id));
    const item = (id: number, from: string, n: number | null): OrganizeItem => {
      if (exclude.includes(id)) return { volume_id: id, title: volumes.find((v) => v.id === id)!.title, from_rel_path: from, output_rel_path: null, status: "skipped", reason: "外しました", cbz_volume_id: null };
      const base = { volume_id: id, title: volumes.find((v) => v.id === id)!.title, from_rel_path: from, cbz_volume_id: null };
      if (n === null) return { ...base, output_rel_path: null, status: "excluded", reason: "1 巻 = 1 CBZ ではありません(先に「CBZ に整理」をテンプレートの名前で実行してください)", cbz_volume_id: id };
      if (conflictIds.length > 1 && conflictIds.includes(id))
        return { ...base, output_rel_path: name(n), status: "conflict", reason: `「${volumes.find((v) => v.id === (id === 1 ? 6 : 1))!.title}」と同じ名前になります。どれかを外してください` };
      return { ...base, output_rel_path: name(n), status: "move", reason: null };
    };
    const items = workId === 1
      ? [item(1, "sample_v01.cbz", 1), item(6, "scans/sample_manga_v01s.cbz", 1), item(2, "sample_v02.cbz", 2), item(3, "done.7z", null)]
      : [];
    const moving = items.filter((i) => i.status === "move");
    return {
      op_id: moving.length ? `op-${++opSeq}` : null,
      state: moving.length ? "planned" : "blocked",
      kind: "rename", naming: "template", template: "<作品名>/<作品名> 第NN巻.cbz", work_id: workId, items,
      source_id: 0, source_rel_path: "", source_kind: "archive",
      volumes: moving.map((i) => ({ volume_id: i.volume_id, title: i.title, page_count: 8, output_rel_path: i.output_rel_path })),
      warnings: items.some((i) => i.status === "conflict") ? ["衝突している巻は改名しません(外すか、作品・巻の情報を直して計画を作り直してください)"] : [],
      problems: moving.length ? [] : ["改名・移動する巻がありません"],
      needs_analysis: [], created_at: "2026-11-01T00:00:00+00:00", executed_at: null, undone_at: null, error: null,
    };
  }

  function handleOrganize(method: string, path: string, parsed: { volume_id?: number; naming?: string; work_id?: number; exclude_volume_ids?: number[] } | undefined): MockResponse | null {
    if (method === "POST" && path === "/api/organize/rename-plan") {
      const op = renamePlan(parsed?.work_id ?? 0, parsed?.exclude_volume_ids ?? []);
      if (op.op_id) ops.unshift(op);
      return json(200, op);
    }
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
        kind: "cbz",
        naming: parsed?.naming === "template" ? "template" : "title",
        template: parsed?.naming === "template" ? "<作品名>/<作品名> 第NN巻.cbz" : null,
        volumes: [{ volume_id: v.id, title: v.title, page_count: v.page_count,
                    output_rel_path: parsed?.naming === "template" ? `サンプル漫画/サンプル漫画 第0${v.id}巻.cbz` : `${v.title}.cbz` }],
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

  // ブックマーク(#86)。付けた日時は呼び出しごとに 1 秒ずつ進める(並び順を確かめられるように)
  let markClock = Date.parse("2026-11-02T00:00:00Z");
  const marks = { work: new Map<number, string>(), volume: new Map<number, string>() };
  function handleBookmarks(method: string, path: string, query: string): MockResponse | null {
    if (method === "GET" && path === "/api/bookmarks") {
      const sort = new URLSearchParams(query).get("sort") ?? "added";
      if (sort !== "added" && sort !== "title") return json(422, { detail: `未対応の並び順: ${sort}` });
      const items: BookmarkItem[] = [];
      for (const [id, at] of marks.work) {
        const w = mockWorks.summaryOf(id);
        if (w) items.push({ kind: "work", created_at: at, work: w });
      }
      for (const [id, at] of marks.volume) {
        const v = volumes.find((x) => x.id === id && !x.hidden);
        if (!v) continue;
        const owner = v.work_id ?? mockWorks.works.find((w) => w.volumeIds.includes(v.id))?.id;
        const work = owner ? mockWorks.summaryOf(owner) : null;
        items.push({
          kind: "volume", created_at: at,
          volume: { id: v.id, title: v.title, parent_title: v.parent_title, format: v.format ?? "images", page_count: v.page_count,
                    offline: v.offline, progress: v.progress, volume_number: v.volume_number,
                    work_id: work ? work.id : null, work_title: work ? work.title : null },
        });
      }
      const titleOf = (i: BookmarkItem) => (i.kind === "work" ? i.work.title : (i.volume.work_title ?? i.volume.title));
      items.sort(sort === "added"
        ? (a, b) => b.created_at.localeCompare(a.created_at)
        : (a, b) => titleOf(a).localeCompare(titleOf(b), "ja") || (a.kind === "work" ? -1 : 1));
      return json(200, { sort, items });
    }
    const m = path.match(/^\/api\/bookmarks\/(works|volumes)\/(\d+)$/);
    if (!m || (method !== "PUT" && method !== "DELETE")) return null;
    const kind = m[1] === "works" ? "work" : "volume";
    const id = Number(m[2]);
    const exists = kind === "work" ? mockWorks.summaryOf(id) !== null : volumes.some((v) => v.id === id);
    if (!exists) return json(404, { detail: kind === "work" ? "作品が見つかりません" : "巻が見つかりません" });
    const map = marks[kind];
    if (method === "PUT" && !map.has(id)) map.set(id, new Date((markClock += 1000)).toISOString().replace(".000Z", "+00:00"));
    if (method === "DELETE") map.delete(id);
    const v = kind === "volume" ? volumes.find((x) => x.id === id) : undefined;
    if (v) v.bookmarked = map.has(id);
    return json(200, { [`${kind}_id`]: id, bookmarked: map.has(id), created_at: map.get(id) ?? null });
  }

  function handle(method: string, rawPath: string, body?: string): MockResponse {
    const path = rawPath.split("?")[0];
    const parsed = body ? JSON.parse(body) : undefined;
    log.push({ method, path, body: parsed });

    const b = handleBookmarks(method, path, rawPath.split("?")[1] ?? "");
    if (b) return b;
    const w = mockWorks.handle(method, path, rawPath.split("?")[1] ?? "", parsed);
    if (w) return json(w.status, w.body);

    const shown = () => volumes.filter((v) => !v.hidden);
    if (method === "GET" && path === "/api/volumes") {
      const list: VolumeSummary[] = shown().map(({ hidden: _hidden, ...v }) => ({ ...v }));
      return json(200, list);
    }
    if (path.startsWith("/api/inbox")) {
      const r = inbox.handle(method, path, parsed);
      if (r) return json(r.status, r.body);
    }
    if (path.startsWith("/api/organize")) {
      const r = handleOrganize(method, path, parsed);
      const undo = path.match(/^\/api\/organize\/([^/]+)\/undo$/);
      if (r && undo && r.status === 200) inbox.undone(decodeURIComponent(undo[1]));
      if (r) return r;
    }
    if (method === "POST" && path === "/api/scan") {
      if (options.scanFailure) return json(options.scanFailure.status, { detail: options.scanFailure.detail });
      const issues = options.scanIssues ?? defaultScanIssues();
      return json(200, {
        volumes: shown().length,
        offline_roots: volumes.some((v) => v.offline) ? ["E:/漫画"] : [],
        issues,
        issues_total: issues.length,
      });
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
    if (path === "/api/thumbnails" && method === "GET") {
      if (thumbState.running && ++thumbPolls >= 3) {
        thumbsBuilt = true;
        thumbState = { ...thumbState, running: false, finished_at: "now", done: 2, total: 2, summary: { total: 2, made: 1, existing: 1, skipped: 0 } };
      } else if (thumbState.running) {
        thumbState = { ...thumbState, done: 1, total: 2 };
      }
      return json(200, thumbState);
    }
    if (path === "/api/thumbnails/build" && method === "POST") {
      if (thumbState.running) return json(409, { detail: "表紙を作っています" });
      thumbPolls = 0;
      thumbState = { ...idleThumbs, running: true, started_at: "now" };
      return json(202, thumbState);
    }
    const m = path.match(/^\/api\/volumes\/([^/]+)(?:\/(progress|analyze|thumbnail|pages\/(\d+)|sections\/(\d+)|resources\/(\d+)))?$/);
    const failure = m && !m[2] && method === "GET" ? options.failures?.[decodeURIComponent(m[1])] : undefined;
    if (failure) return json(failure.status, { detail: failure.detail });
    const v = m ? volumes.find((x) => String(x.id) === decodeURIComponent(m[1])) : undefined;
    if (!m || !v) return json(404, { detail: "見つかりません" });

    const novel = sections[v.id];
    if (novel && !m[2] && method === "GET") {
      v.page_count = novel.length;
      const detail: VolumeDetail = {
        id: v.id, title: v.title, format: v.format, page_count: novel.length, pages: [],
        sections: novel.map((h, i) => ({ index: i, length: h.length })),
        encoding: v.format === "text" ? "cp932" : null,
        progress: v.progress, parent_id: null, volume_number: v.volume_number, findings: [],
        bookmarked: marks.volume.has(v.id),
      };
      return json(200, detail);
    }
    if (novel && m[4] !== undefined && method === "GET") {
      const i = Number(m[4]);
      return i < novel.length ? json(200, { index: i, html: novel[i] }) : json(404, { detail: "区切りがありません" });
    }
    if (novel && m[5] !== undefined && method === "GET") {
      return { status: 200, contentType: "image/svg+xml", body: pageSvg("挿絵", 0, 1) };
    }
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
        bookmarked: marks.volume.has(v.id),
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
      if (v.format && v.format !== "images" && typeof parsed.position === "number") v.progress.position = parsed.position;
      return json(200, v.progress);
    }
    if (m[2] === "thumbnail" && method === "GET") {
      if (v.offline) return json(409, { detail: "蔵書フォルダがオフラインです", offline: true });
      if (v.format && v.format !== "images") return json(404, { detail: "小説の巻には表紙のページがありません", pending: false });
      if (v.page_count === null && !opened.has(v.id) && !thumbsBuilt) return json(404, { detail: "表紙はまだ作っていません", pending: true });
      return { status: 200, contentType: "image/svg+xml", body: pageSvg(v.title, 0, pagesOf(v)) };
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
