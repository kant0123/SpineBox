import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ReadingState, type SourceName, type WorkFilterName, type WorkKind, type WorkList, type WorkQuery } from "../api";
import { facetListHref, workProposalsHref, workTitlesHref, type WorksScope } from "../route";
import { useLibraryScope } from "../shell/libraryScope";
import { Credits } from "./Credits";
import { KIND_TEXT, missingLabel, READING_STATE_TEXT } from "./labels";
import { ReadLaterButton } from "./ReadLaterButton";
import { ViewTabs } from "./ViewTabs";
import { WorkCover, WorkCoverPlaceholder } from "./WorkCover";

/** 検索語の入力から一覧を引き直すまでの待ち(打鍵ごとに引かない) */
const SEARCH_DELAY_MS = 250;

const FILTER_LABELS: Record<WorkFilterName, string> = { author: "著者", kind: "種別", genre: "ジャンル", tag: "タグ", publisher: "出版社" };
const FILTER_NAMES: WorkFilterName[] = ["author", "kind", "genre", "tag", "publisher"];

/** サイドバーから開いた一覧に固定で掛ける絞り込み(#85。漫画・小説・著者・タグ・出版社) */
export function scopeQuery(scope: WorksScope): WorkQuery {
  if (scope.by === "all") return {};
  if (scope.by === "kind") return { kind: scope.kind };
  return { [scope.by]: scope.value };
}

/** 一覧の見出し */
export function scopeTitle(scope: WorksScope): string {
  if (scope.by === "all") return "ホーム";
  if (scope.by === "kind") return scope.kind === "manga" ? "漫画" : "小説";
  return `${FILTER_LABELS[scope.by]}: ${scope.value}`;
}

/**
 * 作品の一覧(作品カード → 作品ページ)。上に全文検索と絞り込み(#56)。
 * scope はサイドバーの漫画・小説・著者・タグ・出版社から開いたときの固定の条件(その項目の選択は出さない)、
 * 蔵書の切り替え(#85)はサイドバーで選んだ蔵書ルート。
 */
export function Works({ scope = { by: "all" } }: { scope?: WorksScope }) {
  const { root } = useLibraryScope();
  const fixed = scopeQuery(scope);
  const [data, setData] = useState<WorkList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [text, setText] = useState("");
  const [query, setQuery] = useState<WorkQuery>({});
  const [showFilters, setShowFilters] = useState(false);
  const [sources, setSources] = useState<SourceName[]>([]);
  const seq = useRef(0);

  const fixedKey = JSON.stringify(fixed);
  const load = useCallback(async (q: WorkQuery) => {
    const mine = ++seq.current;
    try {
      const list = await api.listWorks({ ...q, ...JSON.parse(fixedKey), ...(root ? { root } : {}) });
      if (mine !== seq.current) return; // 後から出した検索の結果を古い結果で上書きしない
      setData(list);
      setError(null);
    } catch {
      if (mine === seq.current) setError("作品一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [fixedKey, root]);

  const onReadLaterChange = useCallback((on: boolean) => {
    // 後で読むだけ表示のときに外した作品は一覧から消えるよう取り直す
    if (on === false && query.read_later) void load(query);
    // それ以外はカード上のボタンの表示だけで足りる(一覧の取り直しはしない)
  }, [load, query]);

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
    // 利用元の表示(#45)にだけ使う。取得・表紙・名寄せの実行と進み具合は管理のデータ管理タブ(#83)
    api.metadataStatus().then((m) => setSources(m.sources), () => setSources([]));
  }, [loadProposals]);

  // 検索語は少し待ってからクエリに入れる
  useEffect(() => {
    const t = window.setTimeout(() => setQuery((cur) => ((cur.q ?? "") === text ? cur : { ...cur, q: text })), SEARCH_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [text]);

  const setFilter = (name: keyof WorkQuery, value: string) => setQuery((cur) => ({ ...cur, [name]: value || undefined }));
  const setOnly = (name: "read_later" | "link_pending" | "pending_proposals", on: boolean) =>
    setQuery((cur) => (on ? { ...cur, [name]: true } : { ...cur, [name]: undefined }));
  const clearAll = () => {
    setText("");
    setQuery({});
  };

  const selectable = FILTER_NAMES.filter((n) => !(n in fixed));
  const activeFilters = selectable.filter((n) => query[n]).length + (query.status ? 1 : 0)
    + (query.read_later ? 1 : 0) + (query.link_pending ? 1 : 0) + (query.pending_proposals ? 1 : 0);
  const filtering = activeFilters > 0 || !!query.q?.trim();

  return (
    <div className="library works">
      <header className="library-header">
        <h1>{scopeTitle(scope)}</h1>
      </header>
      {scope.by === "all" && <ViewTabs current="works" />}
      {(scope.by === "author" || scope.by === "tag" || scope.by === "publisher") && (
        <p className="scope-back"><a className="back-link" href={facetListHref(scope.by)}>‹ {FILTER_LABELS[scope.by]}の一覧へ</a></p>
      )}
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
      <form className="work-search" role="search" onSubmit={(e) => { e.preventDefault(); setQuery((cur) => ({ ...cur, q: text })); }}>
        <p id="work-search-hint" className="muted organize-hint">作品名・別名・著者で検索できます</p>
        <div className="work-search-row">
          <input
            type="search"
            aria-label="作品を検索"
            aria-describedby="work-search-hint"
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
            {selectable.filter((n) => (data?.facets[n]?.length ?? 0) > 0 || query[n]).map((n) => (
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
            <label className="work-filter-check">
              <input
                type="checkbox"
                checked={!!query.link_pending}
                onChange={(e) => setOnly("link_pending", e.target.checked)}
              />
              候補の確認待ちだけ
            </label>
            <label className="work-filter-check">
              <input
                type="checkbox"
                checked={!!query.pending_proposals}
                onChange={(e) => setOnly("pending_proposals", e.target.checked)}
              />
              統合の提案だけ
            </label>
            <label className="work-filter-check">
              <input
                type="checkbox"
                checked={!!query.read_later}
                onChange={(e) => setOnly("read_later", e.target.checked)}
              />
              後で読むだけ
            </label>
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
      {data && data.total === 0 && !root && <p className="muted">作品がありません。蔵書フォルダを設定してスキャンしてください。</p>}
      {data && data.total === 0 && root && <p className="muted">この蔵書には作品がありません。</p>}
      {data && data.total > 0 && data.works.length === 0 && !filtering && scope.by !== "all" && (
        <p className="muted">この条件の作品はありません。</p>
      )}
      {data && data.unassigned > 0 && <p className="muted">作品名を読み取れなかった巻が {data.unassigned} 巻あります(「巻」の表示にあります)。</p>}
      <ul className="work-grid">
        {data?.works.map((w) => {
          const missing = missingLabel(w);
          return (
            <li key={w.id} className="work-card">
              <div className="work-cover-wrap">
                <a className="work-link work-cover-link" href={workTitlesHref(w.id)} aria-label={w.title}>
                  {w.cover_volume_id !== null ? (
                    <WorkCover volumeId={w.cover_volume_id} placeholder />
                  ) : (
                    <WorkCoverPlaceholder />
                  )}
                </a>
                <ReadLaterButton id={w.id} initial={w.read_later ?? false} label={w.title} onChange={onReadLaterChange} />
              </div>
              <a className="work-link work-text-link" href={workTitlesHref(w.id)}>
                <span className="work-text">
                  <span className="volume-title">{w.title}</span>
                  {w.author && <span className="volume-meta">{w.author}</span>}
                  {w.publisher && <span className="volume-meta">{w.publisher}</span>}
                  <span className="work-badges">
                    <span className="badge volume-count-badge">
                      全{w.volume_count}巻{w.max_volume ? `(〜${w.max_volume}巻)` : ""}
                    </span>
                    {missing.text && (
                      <span className="badge" title={missing.title}>
                        欠け {missing.text}
                      </span>
                    )}
                    {w.pending_proposals > 0 && <span className="badge">統合の提案</span>}
                    {w.link_pending > 0 && <span className="badge">候補の確認待ち</span>}
                    {w.offline_count > 0 && <span className="badge">オフライン {w.offline_count}</span>}
                    {w.reading_state !== "unread" && <span className="badge">{READING_STATE_TEXT[w.reading_state]}</span>}
                  </span>
                </span>
              </a>
            </li>
          );
        })}
      </ul>
      <Credits sources={sources} />
    </div>
  );
}
