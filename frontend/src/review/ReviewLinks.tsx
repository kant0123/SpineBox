// 確認 > 公開データベースの候補(#207)。確認待ちの作品を並べ、作品ページを開かずにその場で候補を選ぶ。
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type WorkDetail } from "../api";
import { workHref } from "../route";
import { WorkCover } from "../works/WorkCover";
import { WorkLinks } from "../works/WorkLinks";

interface Item {
  /** 一覧を開いたときの作品 id(選んで統合されても行の鍵は変えない) */
  key: number;
  work: WorkDetail;
}

function ReviewWork({ item, onWork, headingRef }: {
  item: Item;
  onWork: (w: WorkDetail) => void;
  headingRef: (el: HTMLHeadingElement | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const { work } = item;
  const done = work.link_pending === 0;
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setFailed(null);
    try {
      await fn();
    } catch (e) {
      setFailed(e instanceof ApiError && e.detail ? e.detail : "選べませんでした。もう一度お試しください。");
    } finally {
      setBusy(false);
    }
  };
  const headingId = `review-work-${item.key}`;
  return (
    <li className={`review-work${done ? " done" : ""}`} aria-labelledby={headingId} aria-busy={busy || undefined}>
      <div className="review-work-head">
        {work.cover_volume_id !== null && <WorkCover volumeId={work.cover_volume_id} />}
        <div className="review-work-title">
          <h2 id={headingId} ref={headingRef} tabIndex={-1}>{work.title}</h2>
          <p className="muted">
            {[work.author, `${work.volume_count} 巻`].filter(Boolean).join(" / ")}
            {" / "}
            <a href={workHref(work.id)}>作品ページ</a>
          </p>
        </div>
        {done && <span className="badge quiet">確認済み</span>}
      </div>
      {busy && <p className="muted">反映しています…</p>}
      {failed && (
        <div className="notice error" role="alert">
          {failed}
        </div>
      )}
      {!done && (
        // 確認待ちの取得元だけを出す(連携済み・該当なしは作品ページで見る)
        <WorkLinks work={{ ...work, links: work.links.filter((l) => l.status === "pending") }} busy={busy} run={run} onWork={onWork} />
      )}
    </li>
  );
}

export function ReviewLinks({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headings = useRef(new Map<number, HTMLHeadingElement>());

  const load = useCallback(async () => {
    try {
      const r = await api.reviewLinks();
      setItems(r.works.map((w) => ({ key: w.id, work: w })));
      setError(null);
    } catch {
      setError("確認待ちの作品を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** 選んだ結果を行に入れる。確認待ちが無くなった行は「確認済み」に畳み(一覧は詰めない)、次の確認待ちへフォーカスを移す */
  const update = (key: number, w: WorkDetail) => {
    if (!items) return;
    const next = items.map((it) => (it.key === key ? { ...it, work: w } : it));
    setItems(next);
    onChanged();
    if (w.link_pending > 0) return;
    const at = next.findIndex((it) => it.key === key);
    const following = [...next.slice(at + 1), ...next.slice(0, at)].find((it) => it.work.link_pending > 0);
    // 押したボタンが消えるので、次の確認待ちの見出しへ(最後なら畳んだこの行の見出しへ)フォーカスを移す
    window.setTimeout(() => headings.current.get(following ? following.key : key)?.focus(), 0);
  };

  const remaining = items?.filter((it) => it.work.link_pending > 0).length ?? 0;
  return (
    <div className="review-links">
      <p className="muted">
        公開データベースで同じ名前の候補が複数見つかり、自動では決めなかった作品です。正しいものを「これを採用」、無ければ「どれでもない」を選んでください。
        選んだ値は空欄だけを埋め、手で直した項目は上書きしません。
      </p>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {items === null && !error && <p className="muted">読み込み中…</p>}
      {items?.length === 0 && <p className="muted">確認待ちの候補はありません。</p>}
      {items && items.length > 0 && (
        <p className="review-progress" role="status">
          {remaining > 0 ? `残り ${remaining} 作品` : "すべて確認しました。"}
        </p>
      )}
      <ul className="review-list">
        {items?.map((it) => (
          <ReviewWork
            key={it.key}
            item={it}
            onWork={(w) => update(it.key, w)}
            headingRef={(el) => {
              if (el) headings.current.set(it.key, el);
              else headings.current.delete(it.key);
            }}
          />
        ))}
      </ul>
    </div>
  );
}
