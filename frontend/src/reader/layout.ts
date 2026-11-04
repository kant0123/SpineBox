// リーダー(小説)の操作判定。DOM に触れない純粋関数だけを置く。
// 漫画の見開き・ページ送りは manga-viewer に任せている。

export type Action = "next" | "prev" | "menu";

/** タップ位置の判定。右綴じなので左 1/3 が次、右 1/3 が前、中央はメニュー。 */
export function tapAction(x: number, width: number): Action {
  if (!(width > 0)) return "menu";
  const r = x / width;
  if (r < 1 / 3) return "next";
  if (r > 2 / 3) return "prev";
  return "menu";
}

/**
 * スワイプの判定。右綴じでは次のページが左から来るので、右へ払う(dx > 0)と次。
 * 縦方向の動きが大きいとき・短いときはスワイプとみなさない(null)。
 */
export function swipeAction(dx: number, dy: number, threshold = 40): Action | null {
  if (Math.abs(dx) < threshold || Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  return dx > 0 ? "next" : "prev";
}

/** キーボードの判定。右綴じなので ← が次、→ が前。 */
export function keyAction(key: string): Action | null {
  switch (key) {
    case "ArrowLeft":
      return "next";
    case "ArrowRight":
      return "prev";
    case "Escape":
      return "menu";
    default:
      return null;
  }
}
