// 確認 > 公開データベースの候補(#207)。確認待ちの作品を並べ、作品ページを開かずにその場で候補を選ぶ。
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type WorkDetail } from "../api";
import { workHref } from "../route";
import { WorkCover } from "../works/WorkCover";
import { WorkLinks, chooseLink, type LinkPicks } from "../works/WorkLinks";

interface Item {
  /** 一覧を開いたときの作品 id(選んで統合されても行の鍵は変えない) */
  key: number;
  work: WorkDetail;
}

/** 行の鍵ごとの、選んだがまだ反映が終わっていない候補(#309) */
type Picks = Record<number, LinkPicks>;

const noop = async () => {};

/** 確認待ちの取得元をすべて選び終えたか(反映はまだでもよい)。種別の保留は NDL の選択で解けるので待たない */
function settled(work: WorkDetail, picks: LinkPicks | undefined): boolean {
  if (!picks) return false;
  const pending = work.links.filter((l) => l.status === "pending");
  return pending.some((l) => l.source in picks) && pending.every((l) => l.source in picks || l.hold === "kind_unresolved");
}

function ReviewWork({ item, picks, running, failed, onPick, headingRef }: {
  item: Item;
  picks: LinkPicks | undefined;
  running: boolean;
  failed: string | undefined;
  onPick: (source: string, sourceId: string | null) => void;
  headingRef: (el: HTMLHeadingElement | null) => void;
}) {
  const { work } = item;
  const done = work.link_pending === 0;
  // 選び終えた行は反映を待たずに畳む(#309)
  const waiting = !done && settled(work, picks);
  const folded = done || waiting;
  const headingId = `review-work-${item.key}`;
  return (
    <li className={`review-work${folded ? " done" : ""}`} aria-labelledby={headingId} aria-busy={waiting || undefined}>
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
        {waiting && <span className="badge quiet">{running ? "反映しています…" : "反映待ち"}</span>}
      </div>
      {failed && (
        <div className="notice error" role="alert">
          {failed}
        </div>
      )}
      {!folded && (
        // 確認待ちの取得元だけを出す(連携済み・該当なしは作品ページで見る)
        <WorkLinks
          work={{ ...work, links: work.links.filter((l) => l.status === "pending") }}
          busy={false}
          run={noop}
          onWork={noop}
          picks={picks ?? {}}
          onPick={onPick}
        />
      )}
    </li>
  );
}

export function ReviewLinks({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<Item[] | null>(null);
  const [picks, setPicks] = useState<Picks>({});
  const [failed, setFailed] = useState<Record<number, string>>({});
  const [running, setRunning] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headings = useRef(new Map<number, HTMLHeadingElement>());
  // 反映の結果は押した時点ではなく最新の一覧へ入れる(閉じ込めた古い一覧で先の反映を巻き戻さない。#254)
  const itemsRef = useRef<Item[] | null>(null);
  const picksRef = useRef<Picks>({});
  /** 反映を 1 件ずつ順に流す列。画面は終わるのを待たない(#254, #309) */
  const queue = useRef<Promise<void>>(Promise.resolve());

  const load = useCallback(async () => {
    try {
      const r = await api.reviewLinks();
      itemsRef.current = r.works.map((w) => ({ key: w.id, work: w }));
      setItems(itemsRef.current);
      setError(null);
    } catch {
      setError("確認待ちの作品を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const putPicks = (next: Picks) => {
    picksRef.current = next;
    setPicks(next);
  };

  /** 反映が終わった(成功・失敗とも)選択を外す */
  const dropPick = (key: number, source: string) => {
    const rest = { ...picksRef.current[key] };
    delete rest[source];
    putPicks({ ...picksRef.current, [key]: rest });
  };

  const isOpen = (it: Item, all: Picks) => it.work.link_pending > 0 && !settled(it.work, all[it.key]);

  /** 押したボタンが消えるので、次の確認待ちの作品へスクロールして見出しへフォーカスを移す(最後なら畳んだこの行の見出しへ) */
  const moveOn = (key: number, all: Picks) => {
    const cur = itemsRef.current ?? [];
    const at = cur.findIndex((it) => it.key === key);
    const following = [...cur.slice(at + 1), ...cur.slice(0, at)].find((it) => isOpen(it, all));
    window.setTimeout(() => {
      const el = headings.current.get(following ? following.key : key);
      if (!el) return;
      el.focus({ preventScroll: true });
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      el.closest("li")?.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" });
    }, 0);
  };

  /** 候補を選ぶ。画面はすぐ進め(選び終えた行は畳んで次へ)、反映は列に積んで裏で流す(#309) */
  const pick = (key: number, source: string, sourceId: string | null) => {
    const item = itemsRef.current?.find((it) => it.key === key);
    if (!item || source in (picksRef.current[key] ?? {})) return;
    const all = { ...picksRef.current, [key]: { ...picksRef.current[key], [source]: sourceId } };
    putPicks(all);
    setFailed((f) => {
      const rest = { ...f };
      delete rest[key];
      return rest;
    });
    if (settled(item.work, all[key])) moveOn(key, all);
    queue.current = queue.current.then(async () => {
      // 先の反映で統合されて id が変わっていることがあるので、送る時点の作品を引く
      const now = itemsRef.current?.find((it) => it.key === key);
      if (!now) return;
      setRunning(key);
      try {
        const w = await chooseLink(now.work.id, source, sourceId);
        itemsRef.current = (itemsRef.current ?? []).map((it) => (it.key === key ? { ...it, work: w } : it));
        setItems(itemsRef.current);
        onChanged();
      } catch (e) {
        // 確認待ちへ戻す(行が開き直り、理由を出す)
        const reason = e instanceof ApiError && e.detail ? e.detail : "反映できませんでした。もう一度選んでください。";
        setFailed((f) => ({ ...f, [key]: reason }));
      }
      dropPick(key, source);
      setRunning(null);
    });
  };

  const remaining = items?.filter((it) => isOpen(it, picks)).length ?? 0;
  const waiting = items?.filter((it) => it.work.link_pending > 0 && settled(it.work, picks[it.key])).length ?? 0;
  const failures = Object.keys(failed).length;
  const progress =
    (remaining > 0
      ? `残り ${remaining} 作品${waiting > 0 ? `(反映待ち ${waiting} 作品)` : ""}`
      : waiting > 0
        ? `すべて選びました。反映しています…(あと ${waiting} 作品)`
        : "すべて確認しました。") + (failures > 0 ? ` 反映できなかった作品が ${failures} 件あります。` : "");
  return (
    <div className="review-links">
      <p className="muted">
        公開データベースで同じ名前の候補が複数見つかり、自動では決めなかった作品です。正しいものを「これを採用」、無ければ「どれでもない」を選んでください。
        選ぶとすぐ次の作品へ進み、反映は裏で順に行います。選んだ値は空欄だけを埋め、手で直した項目は上書きしません。
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
          {progress}
        </p>
      )}
      <ul className="review-list">
        {items?.map((it) => (
          <ReviewWork
            key={it.key}
            item={it}
            picks={picks[it.key]}
            running={running === it.key}
            failed={failed[it.key]}
            onPick={(source, sourceId) => pick(it.key, source, sourceId)}
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
