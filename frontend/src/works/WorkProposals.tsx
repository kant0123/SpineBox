import { useCallback, useEffect, useRef, useState } from "react";
import { api, type WorkBrief, type WorkProposal } from "../api";
import { workHref, worksHref } from "../route";
import { confidenceText } from "./labels";
import { distinctAliases, proposalEvidence, titleDiff, volumeChips, type TitleParts } from "./proposalView";

/** 別名をそのまま並べる上限。これより多い分は折りたたむ */
const ALIASES_SHOWN = 1;

function Title({ w, parts }: { w: WorkBrief; parts: TitleParts | null }) {
  return (
    <a className="dup-title" href={workHref(w.id)}>
      {parts ? (
        <>
          {parts.head}
          {parts.diff && <mark className="proposal-diff">{parts.diff}</mark>}
          {parts.tail}
        </>
      ) : (
        w.title
      )}
    </a>
  );
}

function Side({ w, parts, titles }: { w: WorkBrief; parts: TitleParts | null; titles: string[] }) {
  const aliases = distinctAliases(w.aliases, titles);
  const shown = aliases.slice(0, ALIASES_SHOWN);
  const more = aliases.slice(ALIASES_SHOWN);
  const chips = volumeChips(w);
  const unseen = w.volume_count - w.volumes.length;
  return (
    <div className="dup-side proposal-side">
      <Title w={w} parts={parts} />
      <dl className="dup-facts proposal-facts">
        <dt>著者</dt>
        <dd>{w.author ?? "なし"}</dd>
        <dt>別名</dt>
        <dd>
          {aliases.length === 0 ? (
            "なし"
          ) : (
            <>
              <ul className="proposal-aliases">
                {shown.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
              {more.length > 0 && (
                <details className="proposal-more">
                  <summary>ほか {more.length} 件</summary>
                  <ul className="proposal-aliases">
                    {more.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </dd>
        <dt>巻</dt>
        <dd>
          <span className="proposal-count">{w.volume_count} 冊</span>
          {chips.length > 0 && (
            <ul className="proposal-chips" aria-label="巻の内訳">
              {chips.map((c) => (
                <li key={c.label}>
                  {c.label}
                  {c.count > 1 && <span className="proposal-chip-count"> ×{c.count}</span>}
                </li>
              ))}
              {unseen > 0 && <li className="proposal-chip-more">ほか {unseen} 冊</li>}
            </ul>
          )}
        </dd>
      </dl>
    </div>
  );
}

/** 確信度の基準の説明。カードごとに繰り返さず、一覧の上に 1 回だけ出す */
export const PROPOSAL_RULE = "自動で統合する基準は確信度 90% 以上です。手で直した作品が絡むものは、確信度にかかわらず確認待ちになります。";

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
  const parts = titleDiff(p.a.title, p.b.title);
  return (
    <li className="dup-card proposal-card">
      <div className="dup-sides proposal-sides">
        <Side w={p.a} parts={parts?.[0] ?? null} titles={[p.a.title, p.b.title]} />
        <Side w={p.b} parts={parts?.[1] ?? null} titles={[p.a.title, p.b.title]} />
      </div>
      <div className="proposal-reason">
        <p className="proposal-confidence">
          確信度 <strong>{confidenceText(p.confidence)}</strong>
        </p>
        <ul className="evidence-lines">
          {proposalEvidence(p).map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </div>
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
        {failed && <span className="notice error" role="alert">{failed}</span>}
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
      <p className="muted">{PROPOSAL_RULE}</p>
      <div className="dup-toolbar">
        <search className="dup-search">
          <label htmlFor="proposal-search-input">作品名・著者で探す</label>
          <span id="proposal-search-hint" className="muted dup-search-hint">空白で区切ると、すべてを含むものに絞ります</span>
          <input
            id="proposal-search-input"
            name="q"
            autoComplete="off"
            type="search"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            aria-describedby="proposal-search-hint"
            enterKeyHint="search"
          />
        </search>
        <label>
          <input type="checkbox" name="dismissed" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />
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
