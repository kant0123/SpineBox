import type { ReactNode } from "react";
import { api, type LinkCandidate, type SourceName, type WorkDetail, type WorkLink } from "../api";
import { KIND_TEXT, confidenceText, dbName, failureText, linkStatusText } from "./labels";

type Run = (fn: () => Promise<unknown>) => Promise<void>;

function candidateMeta(c: LinkCandidate): string {
  const parts: string[] = [];
  if (c.authors.length) parts.push(c.authors.join("、"));
  if (c.kind) parts.push(KIND_TEXT[c.kind]);
  if (c.publisher) parts.push(c.publisher);
  if (c.label) parts.push(c.label);
  if (c.released) parts.push(c.released);
  if (c.total_volumes) parts.push(`全 ${c.total_volumes} 巻`);
  if (c.record_count) {
    const vols = c.volumes?.length ? `(${c.volumes.length === 1 ? `${c.volumes[0]} 巻` : `${c.volumes[0]}〜${c.volumes[c.volumes.length - 1]} 巻`})` : "";
    parts.push(`書誌 ${c.record_count} 件${vols}`);
  }
  return parts.join(" / ");
}

function Candidate({ c, action }: { c: LinkCandidate; action?: ReactNode }) {
  return (
    <div className="link-candidate">
      <div className="link-candidate-body">
        <span className="volume-title">
          {c.title}
          {c.subtitle ? ` ${c.subtitle}` : ""}
        </span>
        {c.romaji && <span className="volume-meta">{c.romaji}</span>}
        <span className="volume-meta">{candidateMeta(c)}</span>
        <span className="volume-meta">
          一致度 {confidenceText(c.score)}
          {c.url && (
            <>
              {" / "}
              <a href={c.url} target="_blank" rel="noreferrer noopener">
                出典を開く
              </a>
            </>
          )}
        </span>
      </div>
      {action}
    </div>
  );
}

function LinkCard({ l, busy, run, workId, onWork }: { l: WorkLink; busy: boolean; run: Run; workId: number; onWork: (w: WorkDetail) => void }) {
  const choose = (sourceId: string | null) =>
    run(async () => {
      const chosen = await api.chooseLink(workId, l.source as SourceName, sourceId);
      // 種別が決まって保留が解けた取得元は、保存済みの応答で連携し直す(新しい問い合わせは出ない)
      const held = chosen.links.some((x) => x.hold === "kind_unresolved") && chosen.kind !== null;
      onWork(held ? await api.fetchWorkMetadata(workId) : chosen);
    });
  const reset = () => run(async () => onWork(await api.resetLink(workId, l.source as SourceName)));
  const others = l.candidates.filter((c) => c.source_id !== l.source_id);
  return (
    <li className={`link-card link-${l.status ?? "unfetched"}`} aria-label={`${dbName(l.source)}との連携`}>
      <div className="link-head">
        <strong>{l.label || dbName(l.source)}</strong>
        <span className={`badge${l.status === "pending" ? "" : " quiet"}`}>{linkStatusText(l)}</span>
      </div>
      {l.failure && (
        <div className="notice warn" role="status">
          取得できませんでした: {failureText(l.failure)}。すでに入っている値はそのままです。
        </div>
      )}
      {l.hold === "kind_unresolved" && (
        <p className="volume-meta">同じ名前の漫画と小説があり、どちらか決められません。国立国会図書館サーチの候補を選ぶと、ほかの取得元も自動で決まります。</p>
      )}
      {l.status === "linked" && l.linked && (
        <Candidate
          c={l.linked}
          action={
            <div className="link-actions">
              <button type="button" className="small secondary" disabled={busy} onClick={() => void choose(null)}>
                どれでもない
              </button>
              {l.manual && (
                <button type="button" className="small secondary" disabled={busy} onClick={() => void reset()}>
                  手動の選択を解除
                </button>
              )}
            </div>
          }
        />
      )}
      {l.status === "none" && l.manual && (
        <div className="link-actions">
          <button type="button" className="small secondary" disabled={busy} onClick={() => void reset()}>
            手動の選択を解除
          </button>
        </div>
      )}
      {others.length > 0 && (
        <details open={l.status === "pending"}>
          <summary>{l.status === "pending" ? `候補(${others.length})` : `ほかの候補(${others.length})`}</summary>
          <div className="link-candidates">
            {others.map((c) => (
              <Candidate
                key={c.source_id}
                c={c}
                action={
                  <button type="button" className="small" disabled={busy} onClick={() => void choose(c.source_id)} aria-label={`${c.title}(${[c.kind ? KIND_TEXT[c.kind] : "", c.publisher ?? ""].filter(Boolean).join("・")}) を採用`}>
                    これを採用
                  </button>
                }
              />
            ))}
            {l.status === "pending" && (
              <button type="button" className="small secondary" disabled={busy} onClick={() => void choose(null)}>
                どれでもない
              </button>
            )}
          </div>
        </details>
      )}
      {l.responses.length > 0 && (
        <p className="volume-meta">
          最終取得: {l.responses.map((r) => r.fetched_at).sort().slice(-1)[0].slice(0, 16).replace("T", " ")} (UTC)
          {l.responses.some((r) => r.stale) ? "・古い保存分を使用" : l.responses.every((r) => r.from_cache) ? "・保存分を使用" : ""}
        </p>
      )}
    </li>
  );
}

/** 公開データベースとの連携(取得元ごと): 状態・失敗の理由・候補の確認 */
export function WorkLinks({ work, busy, run, onWork }: { work: WorkDetail; busy: boolean; run: Run; onWork: (w: WorkDetail) => void }) {
  if (work.links.length === 0) {
    return <p className="muted">公開データベースの取得元が設定で有効になっていません。</p>;
  }
  return (
    <>
      <ul className="link-list">
        {work.links.map((l) => (
          <LinkCard key={l.source} l={l} busy={busy} run={run} workId={work.id} onWork={onWork} />
        ))}
      </ul>
      <p className="muted">公開データベースの値は、空欄だけを埋めます。手で直した項目は上書きしません。確信度の低い候補は、選ぶまで反映しません。</p>
    </>
  );
}
