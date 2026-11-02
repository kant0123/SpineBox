// データ管理のジョブ(#83)のモック。状態を見に行くたびに実行中のジョブが 1 つ進み、5 回で終わって次の待ちが動く。
import type { Job, JobKind, JobsSnapshot } from "../api";

const KINDS: JobKind[] = [
  ["daily_scan", "日常の同期", "新しいファイルの検出のみ", "新しいファイルをスキャンして登録します。", 12, "登録済み 12 巻"],
  ["daily_metadata", "日常の同期", "補完待ちの作品を補完", "公開データベースの情報がまだの作品だけを取得します。", 3, "登録済みの 3 作品が補完待ち"],
  ["maintenance", "ライブラリ全体", "ライブラリ全体のメンテナンス", "全ファイルの目次を確かめ直します。", 12, "12 巻を確かめ直します"],
  ["full_sync", "ライブラリ全体", "ライブラリ全体を同期", "足りない情報を順に補います。", null, null],
  ["scan", "個別", "ファイルのスキャンのみ", "ファイルの検出とページ数の読み取り。", 12, "登録済み 12 巻"],
  ["metadata_thumbs", "個別", "情報と表紙を補完", "公開データベースの情報と表紙を補います。", null, "補完待ち 3 作品・表紙なし 2 作品"],
  ["metadata", "個別", "作品情報のみ補完", "公開データベースの情報だけを補います。", 3, "補完待ち 3 作品"],
  ["thumbnails", "個別", "表紙のみ補完", "作品の表紙をまだ無いものだけ作ります。", 2, "表紙なし 2 作品"],
  ["analyze", "個別", "中の巻を調べる", "まだ調べていない内側のアーカイブを全部開きます。", 1, "未解析 1 巻"],
  ["duplicates", "個別", "重複を調べる", "見た目の指紋を作り、重複の候補を出します。", 4, "指紋が未作成 4 巻"],
  ["follow_check", "個別", "フォロー中の作品の続刊を調べる", "フォローした作品の続刊・欠けを調べます。", 2, "フォロー中 2 作品"],
  ["works_refresh", "個別", "作品の名寄せを計算し直す", "巻を作品にまとめ直します。", 5, "5 作品"],
  ["inbox_sync", "個別", "受け入れフォルダを同期", "受け入れフォルダの新しい本を検出します。", 0, "受け入れ待ち 0 件"],
].map(([kind, group, label, description, count, count_text]) => ({
  kind: kind as string, group: group as string, label: label as string, description: description as string,
  count: count as number | null, count_text: count_text as string | null, available: true, unavailable_reason: null,
  options: kind === "metadata" ? [{ name: "all", label: "全作品を取り直す" }] : [],
}));

const ACTIVE = new Set(["queued", "running"]);

export function createMockJobs() {
  let seq = 2;
  const history: Job[] = [
    job(2, "scan", "ファイルのスキャンのみ", "button", "succeeded", { volumes: 12, offline_roots: [], issues_total: 1 }),
    { ...job(1, "duplicates", "重複を調べる", "jobs", "failed", null), error: "RuntimeError: 読めない巻があります" },
  ];
  let current: Job | null = null;
  const queue: Job[] = [];
  let polls = 0;

  function job(id: number, kind: string, label: string, origin: Job["origin"], status: Job["status"], summary: Job["summary"]): Job {
    return {
      id, kind, label, origin, params: {}, status, queued_at: "2026-11-02T01:00:00+00:00",
      started_at: "2026-11-02T01:00:00+00:00", finished_at: ACTIVE.has(status) ? null : "2026-11-02T01:00:12+00:00",
      done: null, total: null, current: null, step: null, runs: 1, summary, error: null, cancel_requested: false,
      cancellable: ACTIVE.has(status) && origin !== "background",
    };
  }

  function finish(j: Job, status: Job["status"]) {
    j.status = status;
    j.finished_at = "2026-11-02T01:05:00+00:00";
    j.cancellable = false;
    if (status === "cancelled") j.error = "中止しました";
    if (status === "succeeded") j.summary = { volumes: 12 };
    history.unshift(j);
  }

  function advance() {
    if (!current && queue.length) {
      current = queue.shift()!;
      current.status = "running";
      current.step = "ファイルのスキャン";
      current.total = 5;
      current.done = 0;
      polls = 0;
    } else if (current) {
      polls += 1;
      if (current.cancel_requested || polls >= 5) {
        finish(current, current.cancel_requested ? "cancelled" : "succeeded");
        current = null;
        advance();
        return;
      }
      current.done = polls;
      current.current = `作品 ${polls}/サンプル 第0${polls}巻.cbz`;
    }
  }

  function snapshot(): JobsSnapshot {
    return { current, queue: [...queue], history: [...history] };
  }

  function handle(method: string, path: string, parsed: { kind?: string; params?: Record<string, boolean> } | undefined): { status: number; body: unknown } | null {
    if (method === "GET" && path === "/api/jobs") {
      advance();
      return { status: 200, body: snapshot() };
    }
    if (method === "GET" && path === "/api/jobs/catalog") return { status: 200, body: { kinds: KINDS } };
    if (method === "POST" && path === "/api/jobs") {
      const kind = KINDS.find((k) => k.kind === parsed?.kind);
      if (!kind) return { status: 422, body: { detail: `知らないジョブです: ${parsed?.kind}` } };
      const same = [current, ...queue].find((j) => j && j.kind === kind.kind);
      if (same) return { status: 202, body: same };
      const j = job(++seq, kind.kind, kind.label, "jobs", "queued", null);
      j.started_at = null;
      j.params = parsed?.params ?? {};
      queue.push(j);
      if (!current) advance();
      return { status: 202, body: current?.id === j.id ? current : j };
    }
    const cancel = path.match(/^\/api\/jobs\/(\d+)\/cancel$/);
    if (cancel && method === "POST") {
      const id = Number(cancel[1]);
      const i = queue.findIndex((j) => j.id === id);
      if (i >= 0) {
        const [j] = queue.splice(i, 1);
        finish(j, "cancelled");
        return { status: 200, body: j };
      }
      if (current?.id === id) {
        current.cancel_requested = true;
        return { status: 200, body: current };
      }
      return { status: 409, body: { detail: "このジョブは中止できません(終わっているか、裏の定期処理です)" } };
    }
    return null;
  }

  return { handle };
}
