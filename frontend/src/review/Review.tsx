// 確認(#207)。利用者の判断を待っているもの(公開 DB の候補・統合の提案・重複の候補)を 1 か所に集め、タブで切り替える。
// 処理を動かす・設定するものは管理に置く。
import { useEffect, useRef } from "react";
import { REVIEW_TABS, reviewHref, type ReviewTab } from "../route";
import { Duplicates } from "../duplicates/Duplicates";
import { WorkProposals } from "../works/WorkProposals";
import { ReviewLinks } from "./ReviewLinks";
import { useReviewCounts } from "./reviewCounts";

export const REVIEW_TAB_LABELS: Record<ReviewTab, string> = {
  links: "公開DBの候補",
  proposals: "統合の提案",
  duplicates: "重複の候補",
};

export function Review({ tab }: { tab: ReviewTab }) {
  const { counts, refresh } = useReviewCounts();
  const tabs = useRef<HTMLElement>(null);
  // 375px ではタブの列が横に流れるので、開いたタブが隠れない位置まで送る(管理のタブと同じ)
  useEffect(() => {
    const current = tabs.current?.querySelector('[aria-current="page"]');
    current?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [tab]);
  return (
    <div className="page admin review">
      <header className="page-header">
        <h1>確認</h1>
      </header>
      <nav className="admin-tabs" aria-label="確認のタブ" ref={tabs}>
        {REVIEW_TABS.map((t) => {
          const n = counts?.[t] ?? 0;
          return (
            <a key={t} href={reviewHref(t)} aria-current={t === tab ? "page" : undefined}>
              {REVIEW_TAB_LABELS[t]}
              {n > 0 && (
                <span className="count-badge">
                  {n}
                  <span className="sr-only">件</span>
                </span>
              )}
            </a>
          );
        })}
      </nav>
      <div className="admin-panel">
        {tab === "links" && <ReviewLinks onChanged={refresh} />}
        {tab === "proposals" && <WorkProposals embedded onChanged={refresh} />}
        {tab === "duplicates" && <Duplicates embedded onChanged={refresh} />}
      </div>
    </div>
  );
}
