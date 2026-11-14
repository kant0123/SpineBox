// manga-viewer(Shadow DOM に描く外部ライブラリ)へ、オプションでは届かない 2 点を外から足す(#324)。
// ライブラリのソースは改変しない。固定したコミットの内部名に頼るので、外れたら realViewer.test.ts が落ちる。

/** ページスライダーの見た目の差し替え。ライブラリの既定は軌道 6〜8px・つまみ 18〜22px で、タブレットで押しにくい */
export const SLIDER_STYLE_ATTR = "data-spinebox-slider";
const SLIDER_CSS = `
input.mv-page-slider.mv-page-slider {
  --sb-track: 12px;
  --sb-thumb: 32px;
  block-size: 44px; /* 押せる範囲。軌道は下の疑似要素で描く */
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}
input.mv-page-slider.mv-page-slider:focus-visible {
  outline: 2px solid var(--mv-accent);
  outline-offset: 2px;
}
input.mv-page-slider::-webkit-slider-runnable-track {
  block-size: var(--sb-track);
  border: 0;
  border-radius: 999px;
  background: var(--mv-slider-track);
}
input.mv-page-slider.mv-page-slider::-webkit-slider-thumb {
  inline-size: var(--sb-thumb);
  block-size: var(--sb-thumb);
  margin-block-start: calc((var(--sb-track) - var(--sb-thumb)) / 2);
}
input.mv-page-slider::-moz-range-track {
  block-size: var(--sb-track);
  border: 0;
  border-radius: 999px;
  background: var(--mv-slider-track);
}
input.mv-page-slider.mv-page-slider::-moz-range-thumb {
  inline-size: var(--sb-thumb);
  block-size: var(--sb-thumb);
}
`;

export function enlargeSlider(root: ShadowRoot | null | undefined) {
  if (!root || root.querySelector(`style[${SLIDER_STYLE_ATTR}]`)) return;
  const style = document.createElement("style");
  style.setAttribute(SLIDER_STYLE_ATTR, "");
  style.textContent = SLIDER_CSS;
  root.appendChild(style);
}

/**
 * 最後の画面に着いてから、この時間が過ぎるまでの「さらに進む」は数えない。
 * ホイール・トラックパッドの慣性や連打で、最後のページを見る前に次の巻へ飛ばないようにする
 */
export const PAST_END_SETTLE_MS = 500;

interface ViewerInternals {
  _goNext?: (animate?: boolean) => void;
  _navigateTo?: (idx: number, animate?: boolean) => void;
  _currentSlotIndex?: number;
  _slots?: unknown[];
}

/**
 * 最後の画面からさらに進む操作(キー・ホイール・スワイプは `_goNext`、タップは `_navigateTo(今 + 1)`)を拾う。
 * ライブラリはこの操作を外へ渡さない(onComplete は最後の画面に着いた時点)ので、インスタンスの 2 つのメソッドを包む。
 * 内部の形が変わっていたら何もせず false を返す(次の巻へはヘッダーのボタンで移れる)。
 */
export function watchPastEnd(viewer: unknown, onPastEnd: () => void, now: () => number = () => Date.now()): boolean {
  const v = viewer as ViewerInternals;
  const goNext = v._goNext;
  const navigateTo = v._navigateTo;
  if (typeof goNext !== "function" || typeof navigateTo !== "function" || !Array.isArray(v._slots)) return false;

  let arrived = now();
  const last = () => (v._slots?.length ?? 0) - 1;
  const atEnd = () => v._currentSlotIndex === last();
  const fire = () => {
    if (now() - arrived >= PAST_END_SETTLE_MS) onPastEnd();
  };
  v._navigateTo = function (this: unknown, idx: number, animate?: boolean) {
    const before = v._currentSlotIndex;
    const past = atEnd() && idx > last();
    navigateTo.call(this, idx, animate);
    if (v._currentSlotIndex !== before) arrived = now();
    if (past) fire();
  };
  v._goNext = function (this: unknown, animate?: boolean) {
    const end = atEnd();
    goNext.call(this, animate);
    if (end) fire();
  };
  return true;
}
