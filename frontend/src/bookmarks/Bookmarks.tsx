import { useCallback, useEffect, useState } from "react";
import { api, isNovel, type BookmarkItem, type BookmarkList, type BookmarkSort } from "../api";
import { progressLabel, sizeLabel } from "../library/Library";
import { libraryHref, readHref, workHref, worksHref } from "../route";
import { WorkCover } from "../works/WorkCover";
import { BookmarkButton } from "./BookmarkButton";

const SORTS: { value: BookmarkSort; label: string }[] = [
  { value: "added", label: "付けた順" },
  { value: "title", label: "作品名順" },
];

/** 付けた日(端末の時刻で、日付だけ) */
export function addedText(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} に追加`;
}

function itemKey(i: BookmarkItem): string {
  return i.kind === "work" ? `w${i.work.id}` : `v${i.volume.id}`;
}

function Row({ item }: { item: BookmarkItem }) {
  if (item.kind === "work") {
    const w = item.work;
    return (
      <li className="bookmark-row">
        <a className="bookmark-main" href={workHref(w.id)}>
          {w.cover_volume_id !== null && <WorkCover volumeId={w.cover_volume_id} kind={w.kind} />}
          <span className="work-text">
            <span className="badge kind-badge">作品</span>
            <span className="volume-title">{w.title}</span>
            {w.author && <span className="volume-meta">{w.author}</span>}
            <span className="volume-meta">
              {w.volume_count} 巻{w.offline_count > 0 ? `(オフライン ${w.offline_count})` : ""}
            </span>
            <span className="volume-meta">{addedText(item.created_at)}</span>
          </span>
        </a>
        <BookmarkButton target="work" id={w.id} initial label={w.title} />
      </li>
    );
  }
  const v = item.volume;
  const body = (
    <span className="work-text">
      <span className="badge kind-badge">巻</span>
      <span className="volume-title">{v.title}</span>
      {v.work_title && <span className="volume-meta">{v.work_title}</span>}
      {v.parent_title && <span className="volume-meta">「{v.parent_title}」の中</span>}
      <span className="volume-meta">
        {sizeLabel(v)} / {progressLabel(v)}
      </span>
      <span className="volume-meta">{addedText(item.created_at)}</span>
      <span className="work-badges">
        {isNovel(v.format) && <span className="badge novel-badge">小説</span>}
        {v.offline && <span className="badge">オフライン</span>}
      </span>
    </span>
  );
  return (
    <li className={`bookmark-row${v.offline ? " offline" : ""}`}>
      {v.offline ? (
        <div className="bookmark-main" aria-disabled="true">
          {body}
        </div>
      ) : (
        <a className="bookmark-main" href={readHref(v.id)}>
          {body}
        </a>
      )}
      <div className="bookmark-side">
        <BookmarkButton target="volume" id={v.id} initial label={v.title} />
        {v.work_id !== null && (
          <a className="header-link" href={workHref(v.work_id)}>
            作品へ
          </a>
        )}
      </div>
    </li>
  );
}

/** ブックマークの一覧(#86)。サイドバー(#85)の「ブックマーク」から開く。外した行は開き直すまで残す(付け直せる) */
export function Bookmarks() {
  const [sort, setSort] = useState<BookmarkSort>("added");
  const [data, setData] = useState<BookmarkList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (s: BookmarkSort) => {
    try {
      setData(await api.listBookmarks(s));
      setError(null);
    } catch {
      setError("ブックマークを読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load(sort);
  }, [load, sort]);

  return (
    <div className="library bookmarks-page">
      <header className="library-header">
        <h1>ブックマーク</h1>
        <div className="header-actions">
          <a href={worksHref}>作品</a>
          <a href={libraryHref}>巻</a>
        </div>
      </header>
      <div className="sort-switch" role="group" aria-label="並び順">
        {SORTS.map((s) => (
          <button key={s.value} type="button" className="secondary small" aria-pressed={sort === s.value} onClick={() => setSort(s.value)}>
            {s.label}
          </button>
        ))}
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {data === null && !error && <p className="muted">読み込み中…</p>}
      {data?.items.length === 0 && <p className="muted">ブックマークはまだありません。作品のページ・巻の一覧・リーダーのメニューの「☆ ブックマーク」で付けられます。</p>}
      <ul className="bookmark-list">
        {data?.items.map((i) => (
          <Row key={itemKey(i)} item={i} />
        ))}
      </ul>
    </div>
  );
}
