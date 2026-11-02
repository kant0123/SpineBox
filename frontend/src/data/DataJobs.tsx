// データ管理の画面(#83)。スキャン・補完・表紙などのジョブを選んで依頼し、実行中の進み具合・待ち行列・履歴を見る。
// ジョブはどれも蔵書を読むだけ。整理(CBZ 化・改名)と受け入れの承認は、それぞれの画面でプレビューを見てから実行する。
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Job, type JobKind, type JobOrigin, type JobsSnapshot, type JobStatus } from "../api";
import { OrganizeFilesSection } from "../organize/OrganizeFiles";
import { dataHref, tasksHref } from "../route";

/** 実行中・待ちがあるあいだ状態を見に行く間隔 */
const POLL_MS = 1500;

export const STATUS_TEXT: Record<JobStatus, string> = {
  queued: "待ち",
  running: "実行中",
  succeeded: "成功",
  failed: "失敗",
  cancelled: "中止",
};

const ORIGIN_TEXT: Record<JobOrigin, string> = {
  jobs: "データ管理から",
  button: "各画面のボタンから",
  background: "裏の定期処理",
};

// 要約の項目名(サーバーのジョブが返す数)。知らない項目は出さない
const SUMMARY_TEXT: Record<string, (n: number) => string> = {
  volumes: (n) => `${n} 巻`,
  issues_total: (n) => `読めなかったもの ${n} 件`,
  analyzed: (n) => `解析 ${n} 巻`,
  errors: (n) => `読めない ${n} 件`,
  skipped: (n) => `飛ばした ${n} 件`,
  fingerprinted: (n) => `指紋 ${n} 巻`,
  candidates: (n) => `重複の候補 ${n} 組`,
  made: (n) => `作成 ${n}`,
  existing: (n) => `作成済み ${n}`,
  works: (n) => `${n} 作品`,
  fetched: (n) => `取得 ${n} 作品`,
  works_created: (n) => `新しい作品 ${n}`,
  merged: (n) => `統合 ${n}`,
  proposals_open: (n) => `統合の提案 ${n} 件`,
};

const STEP_TEXT: Record<string, string> = {
  scan: "スキャン",
  analyze: "中の巻",
  works: "名寄せ",
  metadata: "公開データベース",
  thumbnails: "表紙",
};

/** 履歴に出す結果の要約。段ごとの要約(全体の同期など)は「段: …」で並べる */
export function summaryText(summary: Record<string, unknown> | null): string {
  if (!summary) return "";
  const flat = Object.entries(summary)
    .filter(([k, v]) => typeof v === "number" && SUMMARY_TEXT[k] && !(k !== "volumes" && k !== "works" && v === 0))
    .map(([k, v]) => SUMMARY_TEXT[k](v as number));
  const offline = Array.isArray(summary.offline_roots) && summary.offline_roots.length > 0 ? [`オフライン ${summary.offline_roots.length} か所`] : [];
  const nested = Object.entries(summary)
    .filter(([k, v]) => STEP_TEXT[k] && v !== null && typeof v === "object" && !Array.isArray(v))
    .map(([k, v]) => `${STEP_TEXT[k]}: ${summaryText(v as Record<string, unknown>) || "なし"}`);
  return [...flat, ...offline, ...nested].join("・");
}

function timeText(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function durationText(job: Job): string {
  if (!job.started_at || !job.finished_at) return "";
  const s = Math.round((new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000);
  if (!Number.isFinite(s) || s < 0) return "";
  return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

function progressText(job: Job): string {
  const parts = [];
  if (job.step) parts.push(job.step);
  if (job.total) parts.push(`${job.done ?? 0} / ${job.total}`);
  else if (job.done) parts.push(`${job.done} 件`);
  return parts.join(" ");
}

function RunningJob({ job, onCancel }: { job: Job; onCancel: (job: Job) => void }) {
  const pct = job.total ? Math.min(100, Math.round(((job.done ?? 0) / job.total) * 100)) : null;
  return (
    <div className="job-running" aria-live="polite">
      <div className="job-title">
        <strong>{job.label}</strong>
        <span className="muted">{ORIGIN_TEXT[job.origin]}</span>
      </div>
      <div className="job-progress-text">{progressText(job) || "準備中…"}</div>
      <div className="job-bar" role="progressbar" aria-label={`${job.label}の進み具合`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
        <span className={pct === null ? "indeterminate" : undefined} style={pct === null ? undefined : { width: `${pct}%` }} />
      </div>
      {job.current && <div className="job-current muted">{job.current}</div>}
      {job.cancellable && (
        <button type="button" className="secondary" onClick={() => onCancel(job)} disabled={job.cancel_requested}>
          {job.cancel_requested ? "中止しています…" : "中止"}
        </button>
      )}
    </div>
  );
}

function HistoryRow({ job }: { job: Job }) {
  const summary = summaryText(job.summary);
  return (
    <li className={`job-history-item ${job.status}`}>
      <div className="job-title">
        <span className={`job-status ${job.status}`}>{STATUS_TEXT[job.status]}</span>
        <strong>{job.label}</strong>
        {job.runs > 1 && <span className="muted">({job.runs} 回)</span>}
      </div>
      <div className="muted job-when">
        {timeText(job.started_at ?? job.queued_at)}
        {durationText(job) && `(${durationText(job)})`} ・ {ORIGIN_TEXT[job.origin]}
      </div>
      {summary && <div className="job-summary">{summary}</div>}
      {job.error && job.status !== "cancelled" && <div className="job-error">理由: {job.error}</div>}
    </li>
  );
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

/**
 * 管理(#85)の「データ管理」と「タスクセンター」の中身。同じジョブの状態を、依頼する側(data)と
 * 実行中・待ち・履歴を見る側(tasks)に分けて出す(all は両方。単体テストで流れを通すため)。
 * focus="organize" は単独の入口 #/data/organize。データ管理の「整理」節へ移す。
 */
export function DataJobs({ pollMs = POLL_MS, focus, view = "all" }: { pollMs?: number; focus?: "organize"; view?: "all" | "data" | "tasks" }) {
  const organizeRef = useRef<HTMLElement>(null);
  const scrolled = useRef(false);
  const [snap, setSnap] = useState<JobsSnapshot | null>(null);
  const [kinds, setKinds] = useState<JobKind[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, Record<string, boolean>>>({});
  const wasActive = useRef(false);

  const loadCatalog = useCallback(async () => {
    try {
      setKinds((await api.jobCatalog()).kinds);
    } catch {
      setError("ジョブの一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  const loadJobs = useCallback(async () => {
    try {
      const s = await api.listJobs();
      setSnap(s);
      const active = s.current !== null || s.queue.length > 0;
      if (wasActive.current && !active) void loadCatalog(); // 終わったら対象の件数を数え直す
      wasActive.current = active;
    } catch {
      setError("ジョブの状態を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [loadCatalog]);

  useEffect(() => {
    void loadCatalog();
    void loadJobs();
  }, [loadCatalog, loadJobs]);

  const active = snap !== null && (snap.current !== null || snap.queue.length > 0);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void loadJobs(), pollMs);
    return () => window.clearInterval(t);
  }, [active, loadJobs, pollMs]);

  const start = async (kind: JobKind) => {
    setError(null);
    try {
      await api.startJob(kind.kind, options[kind.kind] ?? {});
      await loadJobs();
    } catch (e) {
      setError(errorText(e, `「${kind.label}」を依頼できませんでした。`));
    }
  };

  const cancel = async (job: Job) => {
    if (job.id === null) return;
    try {
      await api.cancelJob(job.id);
      await loadJobs();
    } catch (e) {
      setError(errorText(e, "中止できませんでした。"));
    }
  };

  // ジョブの一覧が並んで「整理」節の位置が決まってから、一度だけそこへ移す
  const settled = kinds !== null && snap !== null;
  useEffect(() => {
    if (focus === "organize" && settled && !scrolled.current) {
      scrolled.current = true;
      organizeRef.current?.scrollIntoView?.();
    }
  }, [focus, settled]);

  const activeKinds = new Set([...(snap?.current ? [snap.current] : []), ...(snap?.queue ?? [])].map((j) => j.kind));
  const groups = [...new Set((kinds ?? []).map((k) => k.group))];

  return (
    <div className="data-jobs">
      {view !== "tasks" ? (
        <p className="muted">
          スキャンや情報の補完をジョブとして 1 本ずつ動かします。実行中に依頼したものは待ち行列に入り、順に動きます(進み具合・履歴は<a href={tasksHref}>タスクセンター</a>)。
          ここのジョブは蔵書を読むだけで、ファイルを動かしたり消したりしません(蔵書を動かす整理は、下の「整理」節と受け入れの画面で、プレビューを見てから実行します)。
        </p>
      ) : (
        <p className="muted">
          ジョブの実行中・待ち行列・履歴です。ジョブは<a href={dataHref}>データ管理</a>から依頼します。
        </p>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      <section className="job-section" aria-labelledby="job-running-title">
        <h2 id="job-running-title">実行中</h2>
        {snap?.current ? <RunningJob job={snap.current} onCancel={cancel} /> : <p className="muted">{snap ? "動いているジョブはありません。" : "読み込み中…"}</p>}
        {snap && snap.queue.length > 0 && (
          <>
            <h3>待ち行列</h3>
            <ol className="job-queue">
              {snap.queue.map((j) => (
                <li key={j.id}>
                  <span>{j.label}</span>
                  {j.cancellable && (
                    <button type="button" className="secondary" onClick={() => void cancel(j)}>
                      取り下げ
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </section>

      {view !== "tasks" && groups.map((g) => (
        <section className="job-section" key={g} aria-label={g}>
          <h2>{g}</h2>
          <ul className="job-kinds">
            {kinds!.filter((k) => k.group === g).map((k) => {
              const busy = activeKinds.has(k.kind);
              return (
                <li key={k.kind} className="job-kind">
                  <div className="job-kind-text">
                    <strong>{k.label}</strong>
                    <span className="muted">{k.description}</span>
                    {k.count_text && <span className="job-count">{k.count_text}</span>}
                    {!k.available && k.unavailable_reason && <span className="muted">{k.unavailable_reason}</span>}
                    {k.options.map((o) => (
                      <label key={o.name} className="job-option">
                        <input
                          type="checkbox"
                          checked={options[k.kind]?.[o.name] ?? false}
                          onChange={(e) => setOptions((cur) => ({ ...cur, [k.kind]: { ...cur[k.kind], [o.name]: e.target.checked } }))}
                        />
                        {o.label}
                      </label>
                    ))}
                  </div>
                  <button type="button" onClick={() => void start(k)} disabled={!k.available || busy} aria-label={`${k.label}を実行`}>
                    {busy ? "依頼済み" : "実行"}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {view !== "data" && (
      <section className="job-section" aria-labelledby="job-history-title">
        <h2 id="job-history-title">履歴</h2>
        {snap && snap.history.length === 0 && <p className="muted">まだ何も実行していません。</p>}
        <ul className="job-history">
          {snap?.history.map((j) => <HistoryRow key={j.id} job={j} />)}
        </ul>
      </section>
      )}

      {view !== "tasks" && (
      <section className="job-section" id="data-organize" ref={organizeRef} aria-labelledby="job-organize-title">
        <h2 id="job-organize-title">整理</h2>
        <p className="muted">ファイル名・フォルダーの整理です。蔵書を動かすのでジョブには入れず、プレビューを見てから実行し、まとめて元に戻せます。</p>
        <OrganizeFilesSection />
      </section>
      )}
    </div>
  );
}
