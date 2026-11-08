// 確認(#207)の件数。サイドバーのバッジと確認のタブで同じ数を出す。
// 画面が変わるたびと、確認の画面で何かを決めたとき(refresh)に取り直す。
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type ReviewCounts } from "../api";

interface ReviewCountsValue {
  counts: ReviewCounts | null;
  refresh: () => void;
}

const Ctx = createContext<ReviewCountsValue>({ counts: null, refresh: () => {} });

/** `trigger` が変わるたび(画面の切り替え)に取り直す */
export function ReviewCountsProvider({ trigger, children }: { trigger?: unknown; children: ReactNode }) {
  const [counts, setCounts] = useState<ReviewCounts | null>(null);
  const refresh = useCallback(() => {
    api.reviewCounts().then(setCounts, () => {
      // 件数は補助の表示なので、取れなくても画面は出す
    });
  }, []);
  useEffect(() => refresh(), [refresh, trigger]);
  return <Ctx.Provider value={{ counts, refresh }}>{children}</Ctx.Provider>;
}

export function useReviewCounts(): ReviewCountsValue {
  return useContext(Ctx);
}
