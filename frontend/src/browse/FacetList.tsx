// 著者・タグ・出版社の一覧(#85)。値ごとの作品数を出し、選ぶとその値で絞った作品一覧へ。蔵書の切り替えに従う。
import { useEffect, useMemo, useState } from "react";
import { api, type FacetValue } from "../api";
import { facetWorksHref, type FacetField } from "../route";
import { useLibraryScope } from "../shell/libraryScope";
import { normalizeSearch } from "../util/normalize";

export const FACET_TITLES: Record<FacetField, string> = { author: "著者", tag: "タグ", publisher: "出版社" };
const EMPTY_TEXT: Record<FacetField, string> = {
  author: "著者の分かる作品がありません。",
  tag: "タグの付いた作品がありません。公開データベースの情報を補完するか、作品ページで手で付けてください。",
  publisher: "出版社の分かる作品がありません。公開データベースの情報を補完するか、作品ページで手で付けてください。",
};

type Order = "name" | "count";

export function FacetList({ field }: { field: FacetField }) {
  const { root } = useLibraryScope();
  const [values, setValues] = useState<FacetValue[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [order, setOrder] = useState<Order>("name");

  useEffect(() => {
    let alive = true;
    setValues(null);
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

  const shown = useMemo(() => {
    const key = normalizeSearch(filter);
    const list = (values ?? []).filter((v) => !key || normalizeSearch(v.value).includes(key));
    return order === "count" ? [...list].sort((a, b) => b.count - a.count || a.value.localeCompare(b.value, "ja")) : list;
  }, [values, filter, order]);

  const title = FACET_TITLES[field];
  return (
    <div className="page facets">
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="facet-toolbar">
        <input type="search" aria-label={`${title}を絞り込む`} placeholder={`${title}の名前で絞り込む`} value={filter}
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
        {shown.map((v) => (
          <li key={v.value}>
            <a href={facetWorksHref(field, v.value)}>
              <span className="facet-value">{v.value}</span>
              <span className="facet-n">{v.count} 作品</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
