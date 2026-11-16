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
 * 先頭・最後の画面に着いてから、この時間が過ぎるまでの「さらに進む/戻る」は数えない。
 * ホイール・トラックパッドの慣性や連打で、端のページを見る前に別巻へ飛ばないようにする
 */
export const PAST_END_SETTLE_MS = 500;

interface ViewerInternals {
  _goNext?: (animate?: boolean) => void;
  _goPrev?: (animate?: boolean) => void;
  _navigateTo?: (idx: number, animate?: boolean) => void;
  _endDrag?: () => void;
  _currentSlotIndex?: number;
  _slots?: unknown[];
  _isDragging?: boolean;
  _currentZoom?: number;
  _startX?: number;
  _currentX?: number;
  _dragStartTime?: number;
  _containerWidth?: number;
  opts?: { direction?: string };
}

export interface EdgeHandlers {
  onNext?: () => void;
  onPrev?: () => void;
}

/**
 * 先頭・最後の画面からさらに前・次へ進む操作を拾う(#324, #338)。
 * - キー・ホイール・ズーム中の端の引っ張り: `_goNext` / `_goPrev`
 * - タップ: `_navigateTo(今 ± 1)`
 * - 通常スワイプ・マウスドラッグ: `_endDrag`
 * ライブラリはこの操作を外へ渡さない(onComplete は最後の画面に着いた時点)ので、インスタンスの 4 つのメソッドを包む。
 * 内部の形が変わっていたら何もせず false を返す(次の巻へはヘッダーのボタンで移れる)。
 */
export function watchVolumeEdges(
  viewer: unknown,
  handlers: EdgeHandlers,
  now: () => number = () => Date.now(),
): boolean {
  const v = viewer as ViewerInternals;
  const goNext = v._goNext;
  const goPrev = v._goPrev;
  const navigateTo = v._navigateTo;
  const endDrag = v._endDrag;
  if (
    typeof goNext !== "function"
    || typeof goPrev !== "function"
    || typeof navigateTo !== "function"
    || typeof endDrag !== "function"
    || !Array.isArray(v._slots)
  ) {
    return false;
  }

  let arrived = now();
  const last = () => (v._slots?.length ?? 0) - 1;
  const atStart = () => v._currentSlotIndex === 0;
  const atEnd = () => v._currentSlotIndex === last();
  const fire = (cb?: () => void) => {
    if (cb && now() - arrived >= PAST_END_SETTLE_MS) cb();
  };

  v._navigateTo = function (this: unknown, idx: number, animate?: boolean) {
    const before = v._currentSlotIndex;
    const pastEnd = atEnd() && idx > last();
    const beforeStart = atStart() && idx < 0;
    navigateTo.call(this, idx, animate);
    if (v._currentSlotIndex !== before) arrived = now();
    if (pastEnd) fire(handlers.onNext);
    else if (beforeStart) fire(handlers.onPrev);
  };

  v._goNext = function (this: unknown, animate?: boolean) {
    const end = atEnd();
    goNext.call(this, animate);
    if (end) fire(handlers.onNext);
  };

  v._goPrev = function (this: unknown, animate?: boolean) {
    const start = atStart();
    goPrev.call(this, animate);
    if (start) fire(handlers.onPrev);
  };

  v._endDrag = function (this: unknown) {
    const before = v._currentSlotIndex;
    let edgeDir: "next" | "prev" | null = null;
    if (v._isDragging && (v._currentZoom ?? 1) <= 1) {
      const diff = (v._currentX ?? 0) - (v._startX ?? 0);
      const elapsed = v._dragStartTime !== undefined ? now() - v._dragStartTime : 0;
      const w = v._containerWidth ?? 0;
      const threshold = w > 0 ? w * 0.15 : 30;
      const isQuickSwipe = elapsed >= 0 && elapsed < 300 && Math.abs(diff) > 30;
      if (diff !== 0 && (Math.abs(diff) > threshold || isQuickSwipe)) {
        const rtl = (v.opts?.direction ?? "rtl") === "rtl";
        const forward = rtl ? diff > 0 : diff < 0;
        if (forward && atEnd()) edgeDir = "next";
        else if (!forward && atStart()) edgeDir = "prev";
      }
    }
    endDrag.call(this);
    if (v._currentSlotIndex !== before) arrived = now();
    if (edgeDir === "next") fire(handlers.onNext);
    else if (edgeDir === "prev") fire(handlers.onPrev);
  };

  return true;
}

export function watchPastEnd(viewer: unknown, onPastEnd: () => void, now: () => number = () => Date.now()): boolean {
  return watchVolumeEdges(viewer, { onNext: onPastEnd }, now);
}

/** 今のスロットの前後、この数のスロットまでページ画像を持つ(#343) */
export const IMAGE_KEEP_SLOTS = 3;
/** 外した画像の URL の控え。近づいたら `src` へ戻す */
export const PARKED_SRC_ATTR = "data-spinebox-src";

interface ImageWindowInternals {
  _preloadNearby?: () => void;
  _currentSlotIndex?: number;
  _slotTrack?: { querySelectorAll?: unknown };
}

/**
 * 今のスロットから離れたページの画像を手放す(#343)。
 * ライブラリは全ページぶんの `<img src>` を作って持ち続けるので、長い巻ではデコード済みの画像がたまり、
 * タブレットのブラウザがメモリ不足でページごと読み直す(読み直すたびに「続きから読みますか?」が出る)。
 * 離れたスロットの `src` を控えへ移して外し、近づいたら戻す。ライブラリがページ移動・スロットの作り直しのたびに
 * 呼ぶ `_preloadNearby` を包んで、その直前に行う(戻した画像はそのまま先読みの対象になる)。
 * 内部の形が変わっていたら何もせず false を返す(画像は従来どおり全部持つ)。
 */
export function windowPageImages(viewer: unknown, keep: number = IMAGE_KEEP_SLOTS): boolean {
  const v = viewer as ImageWindowInternals;
  const preloadNearby = v._preloadNearby;
  if (
    typeof preloadNearby !== "function"
    || typeof v._currentSlotIndex !== "number"
    || typeof v._slotTrack?.querySelectorAll !== "function"
  ) {
    return false;
  }

  const apply = () => {
    const track = v._slotTrack as unknown as ParentNode | undefined;
    const current = v._currentSlotIndex;
    if (!track || typeof current !== "number") return;
    track.querySelectorAll<HTMLElement>(".mv-page-slot[data-slot]").forEach((slot) => {
      const index = Number(slot.dataset.slot);
      if (!Number.isInteger(index)) return;
      const near = Math.abs(index - current) <= keep;
      slot.querySelectorAll("img").forEach((img) => {
        const parked = img.getAttribute(PARKED_SRC_ATTR);
        if (near) {
          if (parked === null) return;
          img.setAttribute("src", parked);
          img.removeAttribute(PARKED_SRC_ATTR);
        } else if (parked === null) {
          const src = img.getAttribute("src");
          if (!src) return;
          img.setAttribute(PARKED_SRC_ATTR, src);
          img.removeAttribute("src");
          // ライブラリが付けた「先読み済み・デコード済み」の印も外す(戻したときにもう一度対象になるように)
          delete img.dataset.preloaded;
          delete img.dataset.mvDecoded;
        }
      });
    });
  };

  v._preloadNearby = function (this: unknown) {
    apply();
    preloadNearby.call(this);
  };
  apply();
  return true;
}
