import { useCallback, useEffect, useState } from "react";
import { api, type WorkList } from "../api";
import { duplicatesHref, workHref, workProposalsHref } from "../route";
import { missingText } from "./labels";
import { ViewTabs } from "./ViewTabs";

/** 作品の一覧(作品カード → 作品ページ)。 */
export function Works() {
  const [data, setData] = useState<WorkList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(0);

  const load = useCallback(async () => {
    try {
      const [list, proposals] = await Promise.all([api.listWorks(), api.listProposals("open")]);
      setData(list);
      setPending(proposals.length);
      setError(null);
    } catch {
      setError("作品一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = async () => {
    setBusy(true);
    try {
      await api.refreshWorks();
      await load();
    } catch {
      setError("作品の割り当てを取り直せませんでした。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="library works">
      <header className="library-header">
        <h1>SpineBox</h1>
        <div className="header-actions">
          <a className="header-link" href={duplicatesHref}>
            重複の候補
          </a>
          <button type="button" onClick={refresh} disabled={busy}>
            {busy ? "取り直し中…" : "名寄せを取り直す"}
          </button>
        </div>
      </header>
      <ViewTabs current="works" />
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {pending > 0 && (
        <div className="notice" role="status">
          <a href={workProposalsHref}>統合の提案が {pending} 件あります</a>
          (同じ作品かもしれない組。承認するまで統合しません)
        </div>
      )}
      {data === null && !error && <p className="muted">読み込み中…</p>}
      {data?.works.length === 0 && <p className="muted">作品がありません。蔵書フォルダを設定してスキャンしてください。</p>}
      {data && data.unassigned > 0 && <p className="muted">作品名を読み取れなかった巻が {data.unassigned} 巻あります(「巻」の表示にあります)。</p>}
      <ul className="work-grid">
        {data?.works.map((w) => {
          const missing = missingText(w);
          return (
            <li key={w.id} className="work-card">
              <a className="work-link" href={workHref(w.id)}>
                <span className="volume-title">{w.title}</span>
                {w.author && <span className="volume-meta">{w.author}</span>}
                <span className="volume-meta">
                  {w.volume_count} 巻{w.max_volume ? `(〜${w.max_volume} 巻)` : ""}
                </span>
                {w.aliases.length > 0 && <span className="volume-meta work-aliases">別名: {w.aliases.map((a) => a.alias).join(" / ")}</span>}
                <span className="work-badges">
                  {missing && <span className="badge">欠け {missing}</span>}
                  {w.duplicate_slots > 0 && <span className="badge dup-badge">重複 {w.duplicate_slots}</span>}
                  {w.merged && <span className="badge dup-badge">統合済み</span>}
                  {w.pending_proposals > 0 && <span className="badge">統合の提案</span>}
                  {w.offline_count > 0 && <span className="badge">オフライン {w.offline_count}</span>}
                </span>
              </a>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
