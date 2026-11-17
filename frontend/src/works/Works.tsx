import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, type SourceName, type WorkFilterName, type WorkKind, type WorkList, type WorkQuery, type WorkSort, type WorkSummary } from "../api";
import { facetListHref, reviewHref, workTitlesHref, type WorksScope } from "../route";
import { useLibraryScope } from "../shell/libraryScope";
import { Credits } from "./Credits";
import { KIND_TEXT, WORK_STATUS_FILTER_OPTIONS, WORK_STATUS_FILTER_TEXT } from "./labels";
import { ReadLaterButton } from "./ReadLaterButton";
import { NotifyDot } from "./ReviewPending";
import { WorkCover, WorkCoverPlaceholder } from "./WorkCover";

/** 検索語の入力から一覧を引き直すまでの待ち(打鍵ごとに引かない) */
const SEARCH_DELAY_MS = 250;

/** 一覧の先頭からこの枚数の表紙は遅延させずに読む(最初の画面に入る分。残りは lazy) */
const EAGER_COVERS = 4;

const FILTER_LABELS: Record<WorkFilterName, string> = { author: "著者", kind: "種別", genre: "ジャンル", tag: "タグ", publisher: "出版社" };
const FILTER_NAMES: WorkFilterName[] = ["author", "kind", "genre", "tag", "publisher"];

const SORT_STORAGE_KEY = "spinebox.works.sort";
export const WORK_SORTS: { value: WorkSort; label: string }[] = [
  { value: "title", label: "作品名順" },
  { value: "recent_read", label: "最近読んだ順" },
  { value: "added_desc", label: "追加が新しい順" },
  { value: "added_asc", label: "追加が古い順" },
  { value: "released_desc", label: "発売日順が新しい順" },
  { value: "released_asc", label: "発売日順が古い順" },
  { value: "random", label: "ランダム" },
];

export function loadWorkSort(): WorkSort {
  try {
    const v = localStorage.getItem(SORT_STORAGE_KEY);
    return WORK_SORTS.some((s) => s.value === v) ? (v as WorkSort) : "title";
  } catch {
    return "title";
  }
}

export function saveWorkSort(sort: WorkSort): void {
  try {
    if (sort === "title") localStorage.removeItem(SORT_STORAGE_KEY);
    else localStorage.setItem(SORT_STORAGE_KEY, sort);
  } catch {
    // 保存できなくても今の画面では効かせる
  }
}

/** サイドバーから開いた一覧に固定で掛ける絞り込み(#85。漫画・小説・著者・タグ・出版社) */
export function scopeQuery(scope: WorksScope): WorkQuery {
  if (scope.by === "all") return {};
  if (scope.by === "kind") return { kind: scope.kind };
  return { [scope.by]: scope.value };
}

/** 一覧の見出し */
export function scopeTitle(scope: WorksScope): string {
  if (scope.by === "all") return "ホーム";
  if (scope.by === "kind") return KIND_TEXT[scope.kind];
  return `${FILTER_LABELS[scope.by]}: ${scope.value}`;
}

/**
 * 作品の一覧(作品カード → 作品ページ)。上に全文検索と絞り込み(#56)。
 * scope はサイドバーの漫画・小説・著者・タグ・出版社から開いたときの固定の条件(その項目の選択は出さない)、
 * 蔵書の切り替え(#85)はサイドバーで選んだ蔵書ルート。
 */
interface WorkCardProps {
  work: WorkSummary;
  onReadLaterChange?: (on: boolean) => void;
  /** 先頭の数枚は画面の最初に見える表紙なので、遅延読み込みにしない */
  eager?: boolean;
}

export function WorkCard({ work, onReadLaterChange, eager = false }: WorkCardProps) {
  const cardRef = useRef<HTMLLIElement>(null);

  const startMarquee = () => {
    const card = cardRef.current;
    if (!card) return;
    const titleEl = card.querySelector<HTMLElement>(".work-card-title");
    const inner = card.querySelector<HTMLElement>(".work-card-title-inner");
    if (titleEl && inner) {
      const diff = inner.scrollWidth - titleEl.clientWidth;
      if (diff > 0) {
        card.style.setProperty("--marquee-distance", `${diff}`);
        card.classList.add("is-marquee");
      }
    }
  };

  const stopMarquee = () => {
    const card = cardRef.current;
    if (!card) return;
    card.classList.remove("is-marquee");
  };

  return (
    <li
      ref={cardRef}
      className="work-card"
      onMouseEnter={startMarquee}
      onMouseLeave={stopMarquee}
      onFocusCapture={startMarquee}
      onBlurCapture={stopMarquee}
    >
      <div className="work-cover-wrap">
        <a className="work-link work-cover-link" href={workTitlesHref(work.id)} aria-label={work.title}>
          {work.cover_volume_id !== null ? (
            <WorkCover volumeId={work.cover_volume_id} placeholder eager={eager} />
          ) : (
            <WorkCoverPlaceholder />
          )}
        </a>
        <NotifyDot work={work} />
        {(work.reading_state === "reading" || work.reading_state === "finished") && (
          <span className={`work-reading-badge${work.reading_state === "finished" ? " finished" : ""}`}>
            {work.reading_state === "finished" ? "既読" : "読書中"}
          </span>
        )}
        <ReadLaterButton id={work.id} initial={work.read_later ?? false} label={work.title} onChange={onReadLaterChange} />
      </div>
      <a className="work-link work-text-link" href={workTitlesHref(work.id)}>
        <span className="work-text">
          <span className="volume-title work-card-title">
            <span className="work-card-title-inner">{work.title}</span>
          </span>
          <span className="volume-meta">
            {[work.author, work.publisher].filter(Boolean).join(" ・ ") || "\u00A0"}
          </span>
        </span>
      </a>
    </li>
  );
}

export function Works({ scope = { by: "all" } }: { scope?: WorksScope }) {
  const { root } = useLibraryScope();
  const fixed = scopeQuery(scope);
  const [data, setData] = useState<WorkList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [text, setText] = useState("");
  const [query, setQuery] = useState<WorkQuery>(() => (scope.by === "all" ? { status: "not_finished" } : {}));
  const [sort, setSort] = useState<WorkSort>(loadWorkSort);
  const [showFilters, setShowFilters] = useState(false);
  const [sources, setSources] = useState<SourceName[]>([]);
  const seq = useRef(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const idPrefix = useId();
  const searchId = `${idPrefix}-search`;
  const hintId = `${idPrefix}-search-hint`;
  const sortId = `${idPrefix}-sort`;
  const filtersId = `${idPrefix}-filters`;
  const statusId = `${idPrefix}-status`;
  const readLaterId = `${idPrefix}-read-later`;

  const fixedKey = JSON.stringify(fixed);
  const load = useCallback(async (q: WorkQuery, s: WorkSort) => {
    const mine = ++seq.current;
    try {
      const list = await api.listWorks({ ...q, sort: s, ...JSON.parse(fixedKey), ...(root ? { library: root } : {}) });
      if (mine !== seq.current) return; // 後から出した検索の結果を古い結果で上書きしない
      setData(list);
      setError(null);
    } catch {
      if (mine === seq.current) setError("作品一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [fixedKey, root]);

  const onReadLaterChange = useCallback((on: boolean) => {
    // 後で読むだけ表示のときに外した作品は一覧から消えるよう取り直す
    if (on === false && query.read_later) void load(query, sort);
    // それ以外はカード上のボタンの表示だけで足りる(一覧の取り直しはしない)
  }, [load, query, sort]);

  const loadProposals = useCallback(async () => {
    try {
      setPending((await api.reviewCounts()).total);
    } catch {
      // 提案の件数は補助の表示なので、取れなくても一覧は出す
    }
  }, []);

  useEffect(() => {
    void load(query, sort);
  }, [load, query, sort]);

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
  const setReadLaterOnly = (on: boolean) => setQuery((cur) => (on ? { ...cur, read_later: true } : { ...cur, read_later: undefined }));
  const changeSort = (next: WorkSort) => {
    saveWorkSort(next);
    setSort(next);
  };
  const clearSearch = () => {
    setText("");
    setQuery((cur) => ({ ...cur, q: undefined }));
    searchInputRef.current?.focus();
  };
  const clearAll = () => {
    setText("");
    setQuery({});
  };

  const selectable = FILTER_NAMES.filter((n) => !(n in fixed));
  const activeFilters = selectable.filter((n) => query[n]).length + (query.status ? 1 : 0) + (query.read_later ? 1 : 0);
  const filtering = activeFilters > 0 || !!query.q?.trim();

  const activeChips: { key: string; label: string; ariaLabel: string; onClear: () => void }[] = [];
  if (query.q?.trim()) {
    const qText = query.q.trim();
    activeChips.push({ key: "q", label: `検索: ${qText}`, ariaLabel: `検索「${qText}」を解除`, onClear: clearSearch });
  }
  if (query.status) {
    const statusLabel = WORK_STATUS_FILTER_TEXT[query.status] ?? query.status;
    activeChips.push({ key: "status", label: `読書状態: ${statusLabel}`, ariaLabel: `読書状態「${statusLabel}」を解除`, onClear: () => setFilter("status", "") });
  }
  for (const n of selectable) {
    const val = query[n];
    if (!val) continue;
    const displayVal = n === "kind" ? KIND_TEXT[val as WorkKind] ?? val : val;
    activeChips.push({ key: n, label: `${FILTER_LABELS[n]}: ${displayVal}`, ariaLabel: `${FILTER_LABELS[n]}「${displayVal}」を解除`, onClear: () => setFilter(n, "") });
  }
  if (query.read_later) {
    activeChips.push({ key: "read_later", label: "後で読むだけ", ariaLabel: "後で読むだけの絞り込みを解除", onClear: () => setReadLaterOnly(false) });
  }

  return (
    <div className="library works">
      <header className="library-header">
        <h1>{scopeTitle(scope)}</h1>
      </header>

      {(scope.by === "author" || scope.by === "tag" || scope.by === "publisher") && (
        <p className="scope-back"><a className="back-link" href={facetListHref(scope.by)}>‹ {FILTER_LABELS[scope.by]}の一覧へ</a></p>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {pending > 0 && (
        <div className="notice">
          <a href={reviewHref()}>確認待ちが {pending} 件あります</a>
          (公開データベースの候補・統合の提案・重複の候補。選ぶまで何も変えません)
        </div>
      )}
      <search>
      <form className="work-search" onSubmit={(e) => { e.preventDefault(); setQuery((cur) => ({ ...cur, q: text })); }}>
        <p id={hintId} className="muted organize-hint">作品名・別名・著者で検索できます</p>
        <div className="work-search-row">
          <div className="work-search-field">
            <label htmlFor={searchId} className="visually-hidden">作品を検索</label>
            <div className="work-search-input-wrap">
              <input
                ref={searchInputRef}
                id={searchId}
                type="search"
                name="q"
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                placeholder="作品名・別名・著者で検索"
                aria-describedby={hintId}
                value={text}
                onChange={(e) => setText(e.target.value)}
                enterKeyHint="search"
              />
              {text.length > 0 && (
                <button
                  type="button"
                  className="work-search-clear"
                  aria-label="検索をクリア"
                  onClick={clearSearch}
                >
                  <span aria-hidden="true">×</span>
                </button>
              )}
            </div>
          </div>
          <div className="work-toolbar-actions">
            <div className="work-sort">
              <label htmlFor={sortId} className="work-sort-label">並び順</label>
              <select id={sortId} name="sort" value={sort} onChange={(e) => changeSort(e.target.value as WorkSort)}>
                {WORK_SORTS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className={`work-filter-toggle${activeFilters > 0 ? " is-active" : ""}`}
              aria-expanded={showFilters}
              aria-controls={filtersId}
              onClick={() => setShowFilters((v) => !v)}
            >
              絞り込み{activeFilters > 0 ? `(${activeFilters})` : ""}
            </button>
          </div>
        </div>
        {showFilters && (
          <fieldset className="work-filters" id={filtersId}>
            <legend className="visually-hidden">絞り込み条件</legend>
            <div className="work-filter-item">
              <label htmlFor={statusId}>読書状態</label>
              <select id={statusId} name="status" value={query.status ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
                <option value="">すべて</option>
                {WORK_STATUS_FILTER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            {selectable.filter((n) => (data?.facets[n]?.length ?? 0) > 0 || query[n]).map((n) => {
              const fieldId = `${idPrefix}-filter-${n}`;
              return (
                <div className="work-filter-item" key={n}>
                  <label htmlFor={fieldId}>{FILTER_LABELS[n]}</label>
                  <select id={fieldId} name={n} value={query[n] ?? ""} onChange={(e) => setFilter(n, e.target.value)}>
                    <option value="">すべて</option>
                    {[...new Set([...(data?.facets[n] ?? []), ...(query[n] ? [query[n]!] : [])])].map((v) => (
                      <option key={v} value={v}>{n === "kind" ? KIND_TEXT[v as WorkKind] ?? v : v}</option>
                    ))}
                  </select>
                </div>
              );
            })}
            <div className="work-filter-check-wrap">
              <input
                type="checkbox"
                id={readLaterId}
                name="read_later"
                checked={!!query.read_later}
                onChange={(e) => setReadLaterOnly(e.target.checked)}
              />
              <label htmlFor={readLaterId}>後で読むだけ</label>
            </div>
          </fieldset>
        )}
      </form>
      </search>
      {activeChips.length > 0 && (
        <div className="work-active-filters" aria-label="適用中の絞り込み条件">
          {activeChips.map((chip) => (
            <button key={chip.key} type="button" className="work-filter-chip" aria-label={chip.ariaLabel} onClick={chip.onClear}>
              <span>{chip.label}</span>
              <span className="work-filter-chip-x" aria-hidden="true">×</span>
            </button>
          ))}
        </div>
      )}
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
      {data && data.total === 0 && !root && <p className="muted">作品がありません。管理の「蔵書」で蔵書フォルダを追加して、スキャンしてください。</p>}
      {data && data.total === 0 && root && <p className="muted">この蔵書には作品がありません。</p>}
      {data && data.total > 0 && data.works.length === 0 && !filtering && scope.by !== "all" && (
        <p className="muted">この条件の作品はありません。</p>
      )}
      {data && data.unassigned > 0 && <p className="muted">作品名を読み取れなかった巻が {data.unassigned} 巻あります。</p>}
      <ul className="work-grid">
        {data?.works.map((w, i) => (
          <WorkCard key={w.id} work={w} onReadLaterChange={onReadLaterChange} eager={i < EAGER_COVERS} />
        ))}
      </ul>
      <Credits sources={sources} />
    </div>
  );
}
