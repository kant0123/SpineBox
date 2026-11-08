import { useCallback, useEffect, useRef, useState } from "react";
import { api, type WorkBrief, type WorkProposal } from "../api";
import { workHref, worksHref } from "../route";
import { confidenceText, evidenceLines } from "./labels";

function Side({ w }: { w: WorkBrief }) {
  return (
    <div className="dup-side">
      <a className="dup-title" href={workHref(w.id)}>
        {w.title}
      </a>
      <dl className="dup-facts">
        <dt>著者</dt>
        <dd>{w.author ?? "なし"}</dd>
        <dt>別名</dt>
        <dd>{w.aliases.length ? w.aliases.join(" / ") : "なし"}</dd>
        <dt>巻({w.volume_count})</dt>
        <dd>
          {w.volumes.map((v) => v.title).join("、")}
          {w.volume_count > w.volumes.length ? " …" : ""}
        </dd>
      </dl>
    </div>
  );
}

/** 統合の提案 1 件。承認 = 2 つの作品を 1 つにまとめる。却下 = 別の作品として、再計算でも戻さない。 */
export function ProposalCard({ p, dismissed = false, onChanged }: { p: WorkProposal; dismissed?: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const act = async (action: "approve" | "dismiss" | "restore") => {
    setBusy(true);
    setFailed(null);
    try {
      await api.decideProposal(p.id, action);
      onChanged();
    } catch {
      setFailed("操作に失敗しました。");
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="dup-card">
      <div className="dup-sides">
        <Side w={p.a} />
        <Side w={p.b} />
      </div>
      <p className="dup-evidence">
        確信度 <strong>{confidenceText(p.confidence)}</strong>(自動で統合する基準は 90% 以上で、手で直した作品が絡むものは常に確認待ち)
      </p>
      <ul className="evidence-lines">
        {evidenceLines(p.evidence).map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      <div className="dup-actions">
        {dismissed ? (
          <button type="button" className="small" onClick={() => act("restore")} disabled={busy}>
            提案に戻す
          </button>
        ) : (
          <>
            <button type="button" className="small" onClick={() => act("approve")} disabled={busy}>
              同じ作品として統合
            </button>
            <button type="button" className="small secondary" onClick={() => act("dismiss")} disabled={busy}>
              別の作品
            </button>
          </>
        )}
        {failed && <span className="notice error">{failed}</span>}
      </div>
    </li>
  );
}

/** onChanged: 承認・却下・戻したとき(確認の件数を取り直す。#207) */
export function WorkProposals({ embedded = false, onChanged }: { embedded?: boolean; onChanged?: () => void }) {
  const [showDismissed, setShowDismissed] = useState(false);
  const [items, setItems] = useState<WorkProposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");

  // 入力が止まってから絞り込む(1 文字ごとにサーバーへ問い合わせない)
  useEffect(() => {
    const t = setTimeout(() => setQuery(input), 300);
    return () => clearTimeout(t);
  }, [input]);

  const latest = useRef(0);
  const load = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const got = await api.listProposals(showDismissed ? "dismissed" : "open", query);
      if (mine !== latest.current) return; // 後から出した要求の結果を、古い応答で上書きしない
      setItems(got);
      setError(null);
    } catch {
      if (mine !== latest.current) return;
      setError("統合の提案を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [showDismissed, query]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="library duplicates">
      <header className="library-header">
        {embedded ? <span /> : <h1>作品の統合の提案</h1>}
        {!embedded && (
          <a className="back-link" href={worksHref}>
            作品へ戻る
          </a>
        )}
      </header>
      <p className="muted">
        別々の作品になっているが、同じ本かもしれない組です。承認するまで統合しません。名前は候補であり、確信度が低いものは自動で決めつけません。
        統合しても巻・読書位置は消えません(あとで巻を別の作品へ移して分けられます)。
      </p>
      <div className="dup-toolbar">
        <label className="dup-search">
          作品名・著者で探す
          <input type="search" value={input} onChange={(e) => setInput(e.target.value)} placeholder="空白区切りで AND" />
        </label>
        <label>
          <input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />
          却下・分けたものを表示
        </label>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {items === null && !error && <p className="muted">読み込み中…</p>}
      {items?.length === 0 && <p className="muted">{query.trim() ? "該当する提案はありません。" : showDismissed ? "却下した提案はありません。" : "確認待ちの提案はありません。"}</p>}
      <ul className="dup-list">
        {items?.map((p) => (
          <ProposalCard key={p.id} p={p} dismissed={showDismissed} onChanged={() => { void load(); onChanged?.(); }} />
        ))}
      </ul>
    </div>
  );
}
