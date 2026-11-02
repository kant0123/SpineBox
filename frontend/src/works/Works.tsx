import { useCallback, useEffect, useRef, useState } from "react";
import { api, type MetadataStatus, type ReadingState, type WorkFilterName, type WorkKind, type WorkList, type WorkQuery } from "../api";
import { duplicatesHref, workHref, workProposalsHref } from "../route";
import { Credits } from "./Credits";
import { KIND_TEXT, missingText, READING_STATE_TEXT } from "./labels";
import { ViewTabs } from "./ViewTabs";

/** 検索語の入力から一覧を引き直すまでの待ち(打鍵ごとに引かない) */
const SEARCH_DELAY_MS = 250;

const FILTER_LABELS: Record<WorkFilterName, string> = { author: "著者", kind: "種別", genre: "ジャンル", tag: "タグ" };
const FILTER_NAMES: WorkFilterName[] = ["author", "kind", "genre", "tag"];

/** 作品の一覧(作品カード → 作品ページ)。上に全文検索と絞り込み(#56)。 */
export function Works() {
  const [data, setData] = useState<WorkList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(0);
  const [text, setText] = useState("");
  const [query, setQuery] = useState<WorkQuery>({});
  const [showFilters, setShowFilters] = useState(false);
  const [meta, setMeta] = useState<MetadataStatus | null>(null);
  const seq = useRef(0);

  const load = useCallback(async (q: WorkQuery) => {
    const mine = ++seq.current;
    try {
      const list = await api.listWorks(q);
      if (mine !== seq.current) return; // 後から出した検索の結果を古い結果で上書きしない
      setData(list);
      setError(null);
    } catch {
      if (mine === seq.current) setError("作品一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  const loadProposals = useCallback(async () => {
    try {
      setPending((await api.listProposals("open")).length);
    } catch {
      // 提案の件数は補助の表示なので、取れなくても一覧は出す
    }
  }, []);

  useEffect(() => {
    void load(query);
  }, [load, query]);

  useEffect(() => {
    void loadProposals();
    api.metadataStatus().then(setMeta, () => setMeta(null));
  }, [loadProposals]);

  // 裏で情報を取っている間は、状態を見に行く(終わったら一覧を取り直す)
  const running = meta?.state.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => {
      api.metadataStatus().then((m) => {
        setMeta(m);
        if (!m.state.running) setQuery((cur) => ({ ...cur })); // 同じ条件でもう一度引く
      }, () => undefined);
    }, 2000);
    return () => window.clearInterval(t);
  }, [running]);

  const fetchAll = async () => {
    try {
      const state = await api.fetchAllMetadata();
      setMeta((m) => (m ? { ...m, state } : m));
    } catch {
      setError("公開データベースの情報を取得できませんでした(取得元が無効か、すでに実行中です)。");
    }
  };

  // 検索語は少し待ってからクエリに入れる
  useEffect(() => {
    const t = window.setTimeout(() => setQuery((cur) => (cur.q === text ? cur : { ...cur, q: text })), SEARCH_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [text]);

  const setFilter = (name: keyof WorkQuery, value: string) => setQuery((cur) => ({ ...cur, [name]: value || undefined }));
  const clearAll = () => {
    setText("");
    setQuery({});
  };

  const activeFilters = FILTER_NAMES.filter((n) => query[n]).length + (query.status ? 1 : 0);
  const filtering = activeFilters > 0 || !!query.q?.trim();

  const refresh = async () => {
    setBusy(true);
    try {
      await api.refreshWorks();
      await Promise.all([load(query), loadProposals()]);
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
          {meta && meta.sources.length > 0 && (
            <button type="button" onClick={() => void fetchAll()} disabled={running}>
              {running ? `情報を取得中… ${meta.state.done} / ${meta.state.total}` : "情報を補完"}
            </button>
          )}
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
      {meta?.state.error && (
        <div className="notice warn" role="status">
          前回の取得は途中で止まりました: {meta.state.error}
        </div>
      )}
      <form className="work-search" role="search" onSubmit={(e) => { e.preventDefault(); setQuery((cur) => ({ ...cur, q: text })); }}>
        <div className="work-search-row">
          <input
            type="search"
            aria-label="作品を検索"
            placeholder="作品名・別名・著者で検索"
            value={text}
            onChange={(e) => setText(e.target.value)}
            enterKeyHint="search"
          />
          <button type="button" className="work-filter-toggle" aria-expanded={showFilters} aria-controls="work-filters" onClick={() => setShowFilters((v) => !v)}>
            絞り込み{activeFilters > 0 ? `(${activeFilters})` : ""}
          </button>
        </div>
        {showFilters && (
          <div className="work-filters" id="work-filters">
            <label>
              読書状態
              <select value={query.status ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
                <option value="">すべて</option>
                {(Object.keys(READING_STATE_TEXT) as ReadingState[]).map((st) => (
                  <option key={st} value={st}>{READING_STATE_TEXT[st]}</option>
                ))}
              </select>
            </label>
            {FILTER_NAMES.filter((n) => (data?.facets[n].length ?? 0) > 0 || query[n]).map((n) => (
              <label key={n}>
                {FILTER_LABELS[n]}
                <select value={query[n] ?? ""} onChange={(e) => setFilter(n, e.target.value)}>
                  <option value="">すべて</option>
                  {[...new Set([...(data?.facets[n] ?? []), ...(query[n] ? [query[n]!] : [])])].map((v) => (
                    <option key={v} value={v}>{n === "kind" ? KIND_TEXT[v as WorkKind] ?? v : v}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
      </form>
      {data && filtering && (
        <p className="muted work-count" role="status">
          {data.works.length} 件 / 全 {data.total} 作品
          {" "}
          <button type="button" className="link-button" onClick={clearAll}>条件を消す</button>
        </p>
      )}
      {data === null && !error && <p className="muted">読み込み中…</p>}
      {data && data.works.length === 0 && filtering && data.total > 0 && (
        <p className="muted">条件に合う作品がありません。語を短くするか、絞り込みを外してください。</p>
      )}
      {data && data.total === 0 && <p className="muted">作品がありません。蔵書フォルダを設定してスキャンしてください。</p>}
      {data && data.unassigned > 0 && <p className="muted">作品名を読み取れなかった巻が {data.unassigned} 巻あります(「巻」の表示にあります)。</p>}
      <ul className="work-grid">
        {data?.works.map((w) => {
          const missing = missingText(w);
          return (
            <li key={w.id} className="work-card">
              <a className="work-link" href={workHref(w.id)}>
                <span className="volume-title">{w.title}</span>
                {w.author && <span className="volume-meta">{w.author}</span>}
                {(w.kind || w.publisher) && (
                  <span className="volume-meta">{[w.kind ? KIND_TEXT[w.kind] : "", w.publisher ?? ""].filter(Boolean).join(" / ")}</span>
                )}
                {w.genres.length > 0 && <span className="volume-meta">{w.genres.slice(0, 4).join(" / ")}</span>}
                <span className="volume-meta">
                  {w.volume_count} 巻{w.max_volume ? `(〜${w.max_volume} 巻)` : ""}
                </span>
                {w.aliases.length > 0 && <span className="volume-meta work-aliases">別名: {w.aliases.map((a) => a.alias).join(" / ")}</span>}
                <span className="work-badges">
                  {missing && <span className="badge">欠け {missing}</span>}
                  {w.duplicate_slots > 0 && <span className="badge dup-badge">重複 {w.duplicate_slots}</span>}
                  {w.merged && <span className="badge dup-badge">統合済み</span>}
                  {w.pending_proposals > 0 && <span className="badge">統合の提案</span>}
                  {w.link_pending > 0 && <span className="badge">候補の確認待ち</span>}
                  {w.offline_count > 0 && <span className="badge">オフライン {w.offline_count}</span>}
                  {w.reading_state !== "unread" && <span className="badge">{READING_STATE_TEXT[w.reading_state]}</span>}
                </span>
              </a>
            </li>
          );
        })}
      </ul>
      <Credits sources={meta?.sources ?? []} />
    </div>
  );
}
