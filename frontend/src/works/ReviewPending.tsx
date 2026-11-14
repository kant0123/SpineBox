// 作品ごとの確認待ち(#210)。作品カードでは通知ドット 1 つにまとめ、作品ページ・タイトル一覧では確認(#207)の該当タブへのリンクを出す。
import type { WorkSummary } from "../api";
import { reviewHref } from "../route";

type Pending = Pick<WorkSummary, "pending_proposals" | "link_pending">;

interface PendingItem {
  key: string;
  /** 読み上げ・ツールチップに使う短い名前 */
  label: string;
  /** 確認の画面での名前(リンクの文字) */
  tabLabel: string;
  href: string;
  count: number;
}

export function pendingItems(w: Pending): PendingItem[] {
  const items: PendingItem[] = [];
  if (w.pending_proposals > 0) {
    items.push({ key: "proposals", label: "統合の提案", tabLabel: "確認 > 統合の提案", href: reviewHref("proposals"), count: w.pending_proposals });
  }
  if (w.link_pending > 0) {
    items.push({ key: "links", label: "公開DBの候補", tabLabel: "確認 > 公開DBの候補", href: reviewHref("links"), count: w.link_pending });
  }
  return items;
}

/** 作品カードの通知ドット。色だけに頼らないよう、何が待っているかを読み上げ名に持たせる */
export function NotifyDot({ work }: { work: Pending }) {
  const items = pendingItems(work);
  if (items.length === 0) return null;
  const text = `確認待ちあり(${items.map((it) => it.label).join("・")})`;
  return <span className="notify-dot" role="img" aria-label={text} />;
}

/** 作品ページ・タイトル一覧の案内。確認待ちの種類ごとに、確認の該当タブへのリンクを並べる */
export function ReviewPendingNotice({ work }: { work: Pending }) {
  const items = pendingItems(work);
  if (items.length === 0) return null;
  return (
    <section className="notice warn review-pending" aria-label="この作品の確認待ち">
      <p className="review-pending-head">
        <span className="notify-dot inline" aria-hidden="true" />
        この作品に確認待ちがあります(選ぶまで何も変えません)
      </p>
      <ul>
        {items.map((it) => (
          <li key={it.key}>
            {it.label}
            {it.key === "proposals" ? ` ${it.count} 件` : `(取得元 ${it.count} つ)`}: <a href={it.href}>{it.tabLabel}</a>
          </li>
        ))}
      </ul>
    </section>
  );
}
