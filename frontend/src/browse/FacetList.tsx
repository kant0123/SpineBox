// 著者・タグ・出版社の一覧(#85 / #117)。値ごとの作品数とスター(お気に入り)を出し、選ぶとその値で絞った作品一覧へ。蔵書の切り替えに従う。
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type FacetValue } from "../api";
import { facetWorksHref, type FacetField } from "../route";
import { useLibraryScope } from "../shell/libraryScope";
import { normalizeSearch } from "../util/normalize";

export const FACET_TITLES: Record<FacetField, string> = { author: "著者", tag: "タグ", publisher: "出版社" };
const EMPTY_TEXT: Record<FacetField, string> = {
  author: "著者の分かる作品がありません。",
  tag: "タグの付いた作品がありません。管理の「スキャンと補完」で作品情報を補うか、作品ページで手で付けてください。",
  publisher: "出版社の分かる作品がありません。管理の「スキャンと補完」で作品情報を補うか、作品ページで手で付けてください。",
};

type Order = "name" | "count";

const facetKey = (v: string) => normalizeSearch(v) || v.trim();

export function FacetList({ field }: { field: FacetField }) {
  const { root } = useLibraryScope();
  const [values, setValues] = useState<FacetValue[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starError, setStarError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [order, setOrder] = useState<Order>("name");
  const busyRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    setValues(null);
    setStarError(null);
    busyRef.current.clear();
    api.listFacet(field, root).then(
      (r) => {
        if (!alive) return;
        setValues(r.values);
        setError(null);
      },
      () => alive && setError(`${FACET_TITLES[field]}の一覧を読み込めませんでした。サーバーが動いているか確認してください。`),
    );
    return () => {
      alive = false;
    };
  }, [field, root]);

  const toggleStar = async (value: string, next: boolean) => {
    const targetKey = facetKey(value);
    if (busyRef.current.has(targetKey)) return;
    busyRef.current.add(targetKey);
    setStarError(null);
    setValues((prev) => prev?.map((v) => (facetKey(v.value) === targetKey ? { ...v, starred: next } : v)) ?? null);
    try {
      const r = await api.setFacetStar(field, value, next);
      setValues((prev) => prev?.map((v) => (facetKey(v.value) === targetKey ? { ...v, starred: r.starred } : v)) ?? null);
    } catch {
      setValues((prev) => prev?.map((v) => (facetKey(v.value) === targetKey ? { ...v, starred: !next } : v)) ?? null);
      setStarError("スターを変更できませんでした。");
    } finally {
      busyRef.current.delete(targetKey);
    }
  };

  const shown = useMemo(() => {
    const key = normalizeSearch(filter);
    const list = (values ?? []).filter((v) => !key || normalizeSearch(v.value).includes(key));
    return [...list].sort((a, b) => {
      const starDiff = Number(Boolean(b.starred)) - Number(Boolean(a.starred));
      if (starDiff !== 0) return starDiff;
      return order === "count" ? b.count - a.count || a.value.localeCompare(b.value, "ja") : 0;
    });
  }, [values, filter, order]);

  const title = FACET_TITLES[field];
  return (
    <div className="page facets">
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      {error && <div className="notice error" role="alert">{error}</div>}
      {starError && <div className="notice error" role="alert">{starError}</div>}
      <div className="facet-toolbar">
        <input type="search" aria-label={`${title}を絞り込む`} value={filter}
          onChange={(e) => setFilter(e.target.value)} />
        <select aria-label="並び順" value={order} onChange={(e) => setOrder(e.target.value as Order)}>
          <option value="name">名前順</option>
          <option value="count">作品の多い順</option>
        </select>
      </div>
      {values === null && !error && <p className="muted">読み込み中…</p>}
      {values && values.length === 0 && <p className="muted">{EMPTY_TEXT[field]}</p>}
      {values && values.length > 0 && shown.length === 0 && <p className="muted">合う{title}がありません。</p>}
      {values && values.length > 0 && <p className="muted facet-count">{shown.length} 件</p>}
      <ul className="facet-list">
        {shown.map((v) => {
          const starred = Boolean(v.starred);
          return (
            <li key={v.value} className={`facet-item${starred ? " starred" : ""}`}>
              <a className="facet-link" href={facetWorksHref(field, v.value)}>
                <span className="facet-value">{v.value}</span>
                <span className="facet-n">{v.count} 作品</span>
              </a>
              <button
                type="button"
                className={`facet-star${starred ? " on" : ""}`}
                aria-pressed={starred}
                aria-label={`${v.value} にスターを付ける`}
                title={starred ? "スターを外す" : "スターを付ける"}
                onClick={() => void toggleStar(v.value, !starred)}
              >
                <span aria-hidden="true">{starred ? "★" : "☆"}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
