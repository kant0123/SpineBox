// リーダーの見開き計算・ページ送り・操作判定。DOM に触れない純粋関数だけを置く。
// ページ番号は API の pages[].index と同じ 0 始まり。

/** 1 画面に出すページ。若い番号から順に並べる(右綴じなので画面では右から並ぶ)。 */
export type Spread = number[];

export interface LayoutOptions {
  /** 2 ページ並べるか(横長画面のとき) */
  spread: boolean;
  /** 見開きのとき 1 ページ目(表紙)だけ単独で出すか */
  coverAlone: boolean;
}

/** ページ数と表示設定から、画面ごとのページの並びを作る。 */
export function buildSpreads(pageCount: number, opts: LayoutOptions): Spread[] {
  const n = Number.isFinite(pageCount) ? Math.max(0, Math.floor(pageCount)) : 0;
  const spreads: Spread[] = [];
  if (!opts.spread) {
    for (let i = 0; i < n; i++) spreads.push([i]);
    return spreads;
  }
  let i = 0;
  if (opts.coverAlone && n > 0) {
    spreads.push([0]);
    i = 1;
  }
  for (; i < n; i += 2) {
    spreads.push(i + 1 < n ? [i, i + 1] : [i]);
  }
  return spreads;
}

/** そのページを含む画面の位置。範囲外のページは端に寄せる。画面が無ければ -1。 */
export function spreadIndexOf(spreads: Spread[], page: number): number {
  if (spreads.length === 0) return -1;
  const first = spreads[0][0];
  const lastSpread = spreads[spreads.length - 1];
  if (!Number.isFinite(page) || page <= first) return 0;
  if (page >= lastSpread[lastSpread.length - 1]) return spreads.length - 1;
  const idx = spreads.findIndex((s) => s.includes(Math.floor(page)));
  return idx < 0 ? 0 : idx;
}

/**
 * 隣の画面へ動いたときの位置(その画面の先頭ページ)。端で先が無ければ null。
 * 位置をページで持つのは、見開きの切り替え(画面の回転・表紙単独の切替)で並びが変わっても
 * 読んでいたページを保てるようにするため。
 */
export function stepPage(spreads: Spread[], page: number, delta: 1 | -1): number | null {
  const idx = spreadIndexOf(spreads, page);
  if (idx < 0) return null;
  const next = idx + delta;
  if (next < 0 || next >= spreads.length) return null;
  return spreads[next][0];
}

/** その画面が最後のページを含むか(= 読み終えた)。 */
export function isLastSpread(spreads: Spread[], page: number): boolean {
  const idx = spreadIndexOf(spreads, page);
  return idx >= 0 && idx === spreads.length - 1;
}

/** 先読みする前後の画面のページ。今の画面は含めない。近い順に並べる。 */
export function preloadPages(spreads: Spread[], page: number, radius = 2): number[] {
  const idx = spreadIndexOf(spreads, page);
  if (idx < 0) return [];
  const out: number[] = [];
  for (let d = 1; d <= radius; d++) {
    for (const j of [idx + d, idx - d]) {
      if (j >= 0 && j < spreads.length) out.push(...spreads[j]);
    }
  }
  return out;
}

/** 見開きにするか。スマホ縦は単ページ、横長でも狭すぎれば単ページ。 */
export function shouldSpread(width: number, height: number, minWidth = 600): boolean {
  return width > height && width >= minWidth;
}

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
