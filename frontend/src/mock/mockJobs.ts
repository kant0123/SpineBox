// データ管理のジョブ(#83)のモック。状態を見に行くたびに実行中のジョブが 1 つ進み、5 回で終わって次の待ちが動く。
import type { Job, JobKind, JobsSnapshot } from "../api";

const KINDS: JobKind[] = [
  ["daily_scan", "いつもの更新", "新しいファイルを取り込む", "新しいファイルをスキャンして登録します。", 12, "登録済み 12 巻"],
  ["daily_metadata", "いつもの更新", "足りない作品情報を補う", "公開データベースの情報がまだの作品だけを取得します。", 3, "登録済みの 3 作品が補完待ち"],
  ["full_sync", "いつもの更新", "ライブラリ全体を同期", "足りない情報を順に補います。", null, null],
  ["maintenance", "個別の処理", "全ファイルを確かめ直す", "全ファイルの目次を確かめ直します。", 12, "12 巻を確かめ直します"],
  ["metadata_thumbs", "個別の処理", "情報と表紙を補完", "公開データベースの情報と表紙を補います。", null, "補完待ち 3 作品・表紙なし 2 作品"],
  ["metadata", "個別の処理", "作品情報のみ補完", "公開データベースの情報だけを補います。", 3, "補完待ち 3 作品"],
  ["thumbnails", "個別の処理", "表紙のみ補完", "作品の表紙をまだ無いものだけ作ります。", 2, "表紙なし 2 作品"],
  ["analyze", "個別の処理", "中の巻を調べる", "まだ調べていない内側のアーカイブを全部開きます。", 1, "未解析 1 巻"],
  ["duplicates", "個別の処理", "重複を調べる", "見た目の指紋を作り、重複の候補を出します。", 4, "指紋が未作成 4 巻"],
  ["follow_check", "個別の処理", "フォロー中の作品の続刊を調べる", "フォローした作品の続刊・欠けを調べます。", 2, "フォロー中 2 作品"],
  ["works_refresh", "個別の処理", "作品の名寄せを計算し直す", "巻を作品にまとめ直します。", 5, "5 作品"],
  ["inbox_sync", "個別の処理", "受け入れフォルダを同期", "受け入れフォルダの新しい本を検出します。", 0, "受け入れ待ち 0 件"],
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
      done: null, total: null, current: null, step: null, step_index: null, step_count: null, runs: 1, summary, error: null, cancel_requested: false,
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
      if (current.kind === "full_sync") {
        // 段ごとに進む(#207)。スキャンの段は総数を出さない(実物と同じ)
        current.step_count = 5;
        current.step_index = 1;
        current.total = null;
      }
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
      if (current.kind === "full_sync") {
        const steps = ["ファイルのスキャン", "中の巻を調べる", "作品の名寄せ", "公開データベースの取得", "表紙の作成"];
        current.step_index = polls + 1;
        current.step = steps[polls];
        current.done = 1;
        current.total = 3;
      }
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
      // 一覧に出さない scan(新しいファイルを取り込むと同じ処理)も依頼はできる
      const kind = [...KINDS, { kind: "scan", label: "ファイルのスキャンのみ" }].find((k) => k.kind === parsed?.kind);
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

  function record(kind: string, label: string, summary: Job["summary"], status: Job["status"] = "succeeded", error: string | null = null): Job {
    const j = job(++seq, kind, label, "button", status, summary);
    j.error = error;
    history.unshift(j);
    return j;
  }

  return { handle, record };
}
