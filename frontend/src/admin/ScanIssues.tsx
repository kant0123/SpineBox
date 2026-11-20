// 管理の「読めなかったもの」タブ(#364)。
// 前回スキャンの 200 件制限にとどまらず、DB に記録されているすべての記録(過去分・解析エラー・受け入れ含む)を表示する。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type ScanIssueKind,
  type ScanIssueList,
  type ScanIssueRecord,
  type ScanIssueScope,
  type ScanIssueStatus,
} from "../api";
import { formatCount } from "./format";

const PAGE_LIMIT = 100;

const SCOPES: ScanIssueScope[] = ["all", "active", "analysis", "inactive", "inbox"];

const SCOPE_LABELS: Record<ScanIssueScope, string> = {
  all: "すべて",
  active: "現在の蔵書",
  analysis: "中の巻の解析",
  inactive: "オフライン・削除済み等",
  inbox: "受け入れ",
};

const KIND_LABELS: Record<ScanIssueKind, string> = {
  scan: "スキャン",
  analysis: "解析",
};

const STATUS_LABELS: Record<ScanIssueStatus, string> = {
  active: "現在の蔵書",
  offline: "オフライン",
  missing: "見つからない",
  disabled: "外した蔵書",
  inbox: "受け入れ",
};

function fullIssuePath(item: ScanIssueRecord): string {
  return [item.path, item.container_path, item.entry_name].filter(Boolean).join(" › ");
}

function formatDateTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function summaryLabel(data: ScanIssueList, scope: ScanIssueScope, query: string): string {
  const start = data.total === 0 ? 0 : data.offset + 1;
  const end = Math.min(data.offset + data.items.length, data.total);
  const filtered = scope !== "all" || query.trim() !== "";
  if (data.total === 0) {
    return filtered ? `0 件(全 ${formatCount(data.counts.all)} 件中)` : "0 件";
  }
  const rangeText = data.total > data.limit ? `${formatCount(data.total)} 件中 ${formatCount(start)}〜${formatCount(end)} 件を表示` : `${formatCount(data.total)} 件`;
  return filtered ? `${rangeText}(全体 ${formatCount(data.counts.all)} 件)` : rangeText;
}

export function ScanIssues() {
  const [scope, setScope] = useState<ScanIssueScope>("all");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ScanIssueList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  // 入力が止まってから絞り込む(1 文字ごとにサーバーへ問い合わせない)
  useEffect(() => {
    if (input === query) return;
    const t = window.setTimeout(() => {
      setOffset(0);
      setQuery(input);
    }, 300);
    return () => window.clearTimeout(t);
  }, [input, query]);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    setLoading(true);
    try {
      const got = await api.listScanIssues({ scope, q: query, limit: PAGE_LIMIT, offset });
      if (mine !== latest.current) return;
      setData(got);
      setError(null);
    } catch {
      if (mine !== latest.current) return;
      setError("読めなかったものを読み込めませんでした。サーバーが動いているか確認してください。");
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }, [scope, query, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const clearSearch = () => {
    setInput("");
    setQuery("");
    setOffset(0);
  };

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.limit)) : 1;
  const currentPage = data ? Math.floor(data.offset / data.limit) + 1 : 1;
  const hasFilter = scope !== "all" || query.trim() !== "";

  return (
    <div className="issues-page">
      <h2>読めなかったもの</h2>
      <p className="muted">
        前回のスキャンで見つかった問題だけでなく、オフラインや見つからない蔵書・外した蔵書・受け入れ・中の巻の解析エラーも含めて、データベースに残っている記録を全件表示します。
      </p>

      <search className="issues-filters" aria-label="読めなかったものの絞り込み">
        <div className="issues-search-row">
          <div className="issues-search-field">
            <label htmlFor="issues-search-input">パス・理由で探す</label>
            <span id="issues-search-hint" className="muted issues-search-hint">
              空白で区切ると、すべてを含むものに絞ります
            </span>
            <div className="issues-search-input-wrap">
              <input
                id="issues-search-input"
                name="q"
                type="search"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="none"
                spellCheck={false}
                enterKeyHint="search"
                aria-describedby="issues-search-hint"
              />
              {input !== "" && (
                <button type="button" className="secondary" onClick={clearSearch}>
                  クリア
                </button>
              )}
              <button type="button" className="secondary" onClick={() => void load()} disabled={loading}>
                更新
              </button>
            </div>
          </div>
        </div>

        <fieldset className="issues-scope">
          <legend>表示する範囲</legend>
          <div className="issues-scope-radios">
            {SCOPES.map((s) => {
              const id = `issues-scope-${s}`;
              const count = data?.counts?.[s];
              return (
                <span key={s} className="issues-scope-option">
                  <input
                    type="radio"
                    id={id}
                    name="issues-scope"
                    value={s}
                    checked={scope === s}
                    onChange={() => {
                      setScope(s);
                      setOffset(0);
                    }}
                  />
                  <label htmlFor={id}>
                    {SCOPE_LABELS[s]}
                    {count !== undefined && ` (${formatCount(count)})`}
                  </label>
                </span>
              );
            })}
          </div>
        </fieldset>
      </search>

      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      {data === null && !error && <p className="muted">読み込み中…</p>}

      {data !== null && (
        <>
          <p className="muted issues-summary" role="status">
            {summaryLabel(data, scope, query)}
          </p>

          {data.items.length === 0 ? (
            <p className="muted">{hasFilter ? "条件に合う記録はありません。" : "読めなかったものはありません。"}</p>
          ) : (
            <div className="issues-table-wrapper" tabIndex={0} role="region" aria-labelledby="issues-table-caption">
              <table className="issues-table">
                <caption id="issues-table-caption" className="sr-only">
                  読めなかったものの一覧
                </caption>
                <thead>
                  <tr>
                    <th scope="col">パス</th>
                    <th scope="col">理由</th>
                    <th scope="col">区分</th>
                    <th scope="col">記録日時</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id}>
                      <th scope="row" className="issues-cell-path">
                        {fullIssuePath(item)}
                      </th>
                      <td className="issues-cell-reason">{item.reason}</td>
                      <td className="issues-cell-meta">
                        <span className="issues-badge">{KIND_LABELS[item.kind]}</span>
                        <span className={`issues-badge status-${item.status}`}>{STATUS_LABELS[item.status]}</span>
                      </td>
                      <td className="issues-cell-time">
                        <time dateTime={item.recorded_at}>{formatDateTime(item.recorded_at)}</time>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.total > data.limit && (
            <nav className="issues-pagination" aria-label="ページ移動">
              <button
                type="button"
                className="secondary"
                disabled={data.offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, data.offset - data.limit))}
              >
                前のページ
              </button>
              <span className="muted">
                ページ {formatCount(currentPage)} / {formatCount(totalPages)}
              </span>
              <button
                type="button"
                className="secondary"
                disabled={data.offset + data.limit >= data.total || loading}
                onClick={() => setOffset(data.offset + data.limit)}
              >
                次のページ
              </button>
            </nav>
          )}
        </>
      )}
    </div>
  );
}
