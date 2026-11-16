// manga-viewer(Shadow DOM に描く外部ライブラリ)へ、オプションでは届かない結線を外から足す(#324, #347)。
// ライブラリのソースは改変しない。固定したコミットの内部名に頼るので、外れたら realViewer.test.ts が落ちる。
import {
  buildAdaptiveSlots,
  computeDisplayProgress,
  findSlotForAnchor,
  formatPageDisplay,
  isLandscapeAspect,
  slotKey,
  type AdaptiveSlot,
  type SlotHalf,
} from "./spreadPages";

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

export const SPLIT_STYLE_ATTR = "data-spinebox-split";
const SPLIT_CSS = `
.mv-zoom-container.sb-split-container {
  container-type: size;
}
.mv-page-slot img.sb-split-half {
  width: min(100cqw, calc(100cqh * var(--sb-half-ar, 0.707)));
  height: min(100cqh, calc(100cqw / var(--sb-half-ar, 0.707)));
  max-width: none;
  max-height: none;
  object-fit: cover;
}
.mv-page-slot img.sb-split-right {
  object-position: 100% 50%;
}
.mv-page-slot img.sb-split-left {
  object-position: 0% 50%;
}
.mv-header-btn.spinebox-split-toggle[hidden] {
  display: none;
}
.mv-header-btn.spinebox-split-toggle[aria-pressed="true"] {
  color: #facc15;
}
:host(.mv-theme-light) .mv-header-btn.spinebox-split-toggle[aria-pressed="true"] {
  color: #b45309;
}
@media (max-width: 768px) {
  :host(:not(.mv-theme-dark)) .mv-header-btn.spinebox-split-toggle[aria-pressed="true"] {
    color: #b45309;
  }
}
`;

export interface AdaptiveSpreadOptions {
  initialSplitEnabled?: boolean;
  onStateChange?: () => void;
}

export interface AdaptiveSpreadController {
  active: boolean;
  toggleSplit(): boolean;
  isSplitEnabled(): boolean;
  canToggleSplit(): boolean;
  destroy(): void;
}

interface AdaptiveViewerInternals {
  _buildSlots?: () => void;
  _renderSlots?: () => void;
  _updateUI?: () => void;
  _checkOrientation?: () => void;
  _endDrag?: () => void;
  _updateTrackPosition?: (animate?: boolean) => void;
  _preloadNearby?: () => void;
  _createPageNode?: (pageIdx: number, loadingAttr: string) => HTMLElement;
  resetZoom?: () => void;
  _slotTrack?: HTMLElement;
  _slots?: AdaptiveSlot[];
  _pages?: Array<{ type?: string }>;
  _spreadMode?: boolean;
  _isDragging?: boolean;
  _currentZoom?: number;
  _currentSlotIndex?: number;
  _totalPages?: number;
  _currentPageEl?: HTMLElement;
  _progressEl?: HTMLElement;
  _slider?: HTMLInputElement;
  _liveRegion?: HTMLElement;
  shadowRoot?: ShadowRoot | null;
  opts?: {
    direction?: string;
    firstPageSingle?: boolean;
    lastPageAlign?: string;
    viewMode?: string;
  };
}

const NOOP_CONTROLLER: AdaptiveSpreadController = {
  active: false,
  toggleSplit: () => false,
  isSplitEnabled: () => false,
  canToggleSplit: () => false,
  destroy: () => {},
};

/**
 * 見開きスキャン画像(横長画像)の縦画面左右分割と横画面単独表示を外から結線する(#347)。
 * 内部の形が変わっていたら何もせず active: false のコントローラを返す。
 */
export function setupAdaptiveSpreads(
  viewer: unknown,
  options: AdaptiveSpreadOptions = {},
): AdaptiveSpreadController {
  const v = viewer as AdaptiveViewerInternals;
  const origBuildSlots = v._buildSlots;
  const origRenderSlots = v._renderSlots;
  const origUpdateUI = v._updateUI;
  const origCheckOrientation = v._checkOrientation;
  const origEndDrag = v._endDrag;
  const origUpdateTrack = v._updateTrackPosition;

  if (
    typeof origBuildSlots !== "function"
    || typeof origRenderSlots !== "function"
    || typeof origUpdateUI !== "function"
    || typeof origCheckOrientation !== "function"
    || typeof origEndDrag !== "function"
    || typeof origUpdateTrack !== "function"
    || typeof v._preloadNearby !== "function"
    || !v._slotTrack
    || !Array.isArray(v._slots)
    || !Array.isArray(v._pages)
    || typeof v._currentSlotIndex !== "number"
    || typeof v._totalPages !== "number"
  ) {
    return NOOP_CONTROLLER;
  }

  const track = v._slotTrack;
  const root = v.shadowRoot;
  if (root && !root.querySelector(`style[${SPLIT_STYLE_ATTR}]`)) {
    const style = document.createElement("style");
    style.setAttribute(SPLIT_STYLE_ATTR, "");
    style.textContent = SPLIT_CSS;
    root.appendChild(style);
  }

  const nonImageIndices = new Set<number>();
  v._pages.forEach((p, idx) => {
    if (p?.type === "adsense" || p?.type === "purchase") nonImageIndices.add(idx);
  });

  const pageAspects = new Map<number, number>();
  let splitEnabled = options.initialSplitEnabled ?? true;
  let hasSplittableWidePage = false;
  let pendingSync = false;

  const getDir = (): "rtl" | "ltr" => (v.opts?.direction === "ltr" ? "ltr" : "rtl");
  const getLastAlign = (): "center" | "start" | "end" => {
    const a = v.opts?.lastPageAlign;
    return a === "center" || a === "end" ? a : "start";
  };

  const buildCurrentSlots = (): AdaptiveSlot[] =>
    buildAdaptiveSlots({
      pageCount: v._pages?.length ?? 0,
      direction: getDir(),
      spreadMode: !!v._spreadMode,
      firstPageSingle: v.opts?.firstPageSingle ?? true,
      lastPageAlign: getLastAlign(),
      pageAspects,
      splitEnabled,
      nonImageIndices,
    });

  const decorateSlotEl = (slotEl: HTMLElement, slot: AdaptiveSlot, realIdx: number, dir: "rtl" | "ltr") => {
    slotEl.dataset.slot = String(realIdx);
    slotEl.classList.toggle("mv-spread-slot", !!slot.spread);
    slotEl.classList.toggle("mv-rtl-slot", !!slot.spread && dir === "rtl");
    const zoomEl = slotEl.querySelector<HTMLElement>(".mv-zoom-container");
    if (zoomEl) {
      zoomEl.dataset.zoomSlot = String(realIdx);
      zoomEl.classList.toggle("sb-split-container", !!slot.half);
    }
    const imgs = slotEl.querySelectorAll<HTMLImageElement>("img");
    imgs.forEach((img, idx) => {
      const pageIdx = slot.pages[idx];
      if (typeof pageIdx === "number") img.dataset.spineboxPage = String(pageIdx);
      img.classList.toggle("sb-split-half", !!slot.half);
      img.classList.toggle("sb-split-right", slot.half === "right");
      img.classList.toggle("sb-split-left", slot.half === "left");
      if (slot.half && slot.aspect) {
        img.style.setProperty("--sb-half-ar", String(slot.aspect / 2));
      } else {
        img.style.removeProperty("--sb-half-ar");
      }
    });
  };

  const createSlotEl = (slot: AdaptiveSlot, realIdx: number, dir: "rtl" | "ltr"): HTMLElement => {
    const slotEl = document.createElement("div");
    slotEl.className = "mv-page-slot";
    const zoomEl = document.createElement("div");
    zoomEl.className = "mv-zoom-container";
    slotEl.appendChild(zoomEl);

    const loadingAttr = realIdx <= 2 ? "eager" : "lazy";
    const pageNodes: HTMLElement[] = slot.pages.map((pageIdx) => {
      if (typeof v._createPageNode === "function") return v._createPageNode(pageIdx, loadingAttr);
      const img = document.createElement("img");
      img.loading = loadingAttr as "eager" | "lazy";
      return img;
    });
    if (slot.hasBlank) {
      const blank = document.createElement("div");
      blank.className = "mv-blank-page";
      blank.setAttribute("aria-hidden", "true");
      if (slot.blankAlign === "start") pageNodes.push(blank);
      else pageNodes.unshift(blank);
    }
    pageNodes.forEach((n) => zoomEl.appendChild(n));
    decorateSlotEl(slotEl, slot, realIdx, dir);
    return slotEl;
  };

  const currentAnchor = (): { pageIdx: number; half?: SlotHalf } => {
    const cur = v._slots?.[v._currentSlotIndex ?? 0];
    if (!cur || cur.pages.length === 0) return { pageIdx: 0 };
    return { pageIdx: cur.pages[0], half: cur.half };
  };

  const syncSlots = (forceAnchor?: { pageIdx: number; half?: SlotHalf }) => {
    if (v.opts?.viewMode === "scroll") return;
    pendingSync = false;
    const anchor = forceAnchor ?? currentAnchor();
    const dir = getDir();
    const newSlots = buildCurrentSlots();

    // 既存スロット DOM をキーでプールし、単一ページのスロット構成変化時は既存の img を再利用してチラつきを防ぐ
    const oldSlots = v._slots ?? [];
    const pool = new Map<string, HTMLElement[]>();
    const singlePagePool = new Map<number, HTMLElement[]>();
    track.querySelectorAll<HTMLElement>(".mv-page-slot[data-slot]").forEach((el) => {
      const idx = Number(el.dataset.slot);
      const old = oldSlots[idx];
      if (!old) return;
      const k = slotKey(old);
      const list = pool.get(k) ?? [];
      list.push(el);
      pool.set(k, list);
      if (!old.spread && !old.hasBlank && old.pages.length === 1 && el.querySelector("img")) {
        const p = old.pages[0];
        const sList = singlePagePool.get(p) ?? [];
        sList.push(el);
        singlePagePool.set(p, sList);
      }
    });

    const takeFromPool = (slot: AdaptiveSlot): HTMLElement | null => {
      const k = slotKey(slot);
      const exact = pool.get(k)?.shift();
      if (exact) {
        if (!slot.spread && !slot.hasBlank && slot.pages.length === 1) {
          const sList = singlePagePool.get(slot.pages[0]);
          if (sList) {
            const pos = sList.indexOf(exact);
            if (pos >= 0) sList.splice(pos, 1);
          }
        }
        return exact;
      }
      if (!slot.spread && !slot.hasBlank && slot.pages.length === 1) {
        const reused = singlePagePool.get(slot.pages[0])?.shift();
        if (reused) return reused;
      }
      return null;
    };

    const shouldReverse = dir === "rtl";
    const displayIndices = newSlots.map((_, idx) => idx);
    if (shouldReverse) displayIndices.reverse();

    const desiredEls = displayIndices.map((realIdx) => {
      const slot = newSlots[realIdx];
      const existing = takeFromPool(slot);
      if (existing) {
        decorateSlotEl(existing, slot, realIdx, dir);
        return existing;
      }
      return createSlotEl(slot, realIdx, dir);
    });

    desiredEls.forEach((el, pos) => {
      const curAtPos = track.children[pos] ?? null;
      if (curAtPos !== el) {
        track.insertBefore(el, curAtPos);
      }
    });
    while (track.children.length > desiredEls.length) {
      track.lastElementChild?.remove();
    }

    v._slots = newSlots;
    v._currentSlotIndex = findSlotForAnchor(newSlots, anchor.pageIdx, anchor.half);
    origUpdateTrack.call(v, false);
    v._updateUI?.();
  };

  const inspectImage = (img: HTMLImageElement): boolean => {
    const pageStr = img.dataset.spineboxPage;
    if (pageStr === undefined) return false;
    const pageIdx = Number(pageStr);
    if (!Number.isInteger(pageIdx) || img.naturalWidth <= 0 || img.naturalHeight <= 0) return false;
    const ar = img.naturalWidth / img.naturalHeight;
    const prev = pageAspects.get(pageIdx);
    const prevWide = isLandscapeAspect(prev);
    const nextWide = isLandscapeAspect(ar);
    pageAspects.set(pageIdx, ar);
    if (pageIdx > 0 && nextWide && !hasSplittableWidePage) {
      hasSplittableWidePage = true;
      options.onStateChange?.();
    }
    return prev === undefined ? nextWide : prevWide !== nextWide;
  };

  const onImgLoad = (e: Event) => {
    const target = e.target;
    if (!(target instanceof HTMLImageElement)) return;
    if (!inspectImage(target)) return;
    if (v._isDragging) {
      pendingSync = true;
    } else {
      syncSlots();
    }
  };
  track.addEventListener("load", onImgLoad, true);

  v._buildSlots = function (this: AdaptiveViewerInternals) {
    if (this.opts?.viewMode === "scroll") {
      origBuildSlots.call(this);
      return;
    }
    this._slots = buildCurrentSlots();
  };

  v._renderSlots = function (this: AdaptiveViewerInternals) {
    origRenderSlots.call(this);
    const dir = getDir();
    const slots = this._slots ?? [];
    let changed = false;
    track.querySelectorAll<HTMLElement>(".mv-page-slot[data-slot]").forEach((el) => {
      const idx = Number(el.dataset.slot);
      const slot = slots[idx];
      if (!slot) return;
      decorateSlotEl(el, slot, idx, dir);
      el.querySelectorAll<HTMLImageElement>("img").forEach((img) => {
        if (img.complete && img.naturalWidth > 0 && inspectImage(img)) changed = true;
      });
    });
    if (changed) {
      if (v._isDragging) pendingSync = true;
      else syncSlots();
    }
  };

  v._updateUI = function (this: AdaptiveViewerInternals) {
    origUpdateUI.call(this);
    const slots = this._slots ?? [];
    const idx = this._currentSlotIndex ?? 0;
    const slot = slots[idx];
    const total = this._totalPages ?? 0;
    const dir = getDir();
    const display = formatPageDisplay(slot, total);
    if (this._currentPageEl) this._currentPageEl.textContent = display;
    const { progress, sliderValue } = computeDisplayProgress(slots, idx, total, dir);
    if (this._progressEl) this._progressEl.textContent = `${progress}%`;
    if (this._slider) this._slider.value = String(sliderValue);
    if (this._liveRegion && slot?.half) {
      this._liveRegion.textContent = `${display} ページ`;
    }
  };

  v._checkOrientation = function (this: AdaptiveViewerInternals) {
    const beforeSpread = !!this._spreadMode;
    origCheckOrientation.call(this);
    if (beforeSpread !== !!this._spreadMode) {
      options.onStateChange?.();
    }
  };

  v._endDrag = function (this: AdaptiveViewerInternals) {
    origEndDrag.call(this);
    if (pendingSync && !this._isDragging) {
      syncSlots();
    }
  };

  // 初回に既に読み込み済みの画像があれば検査し、無ければ現在の装飾・UI 表示を確定する
  let initialChanged = false;
  const initialSlots = v._slots;
  track.querySelectorAll<HTMLElement>(".mv-page-slot[data-slot]").forEach((el) => {
    const idx = Number(el.dataset.slot);
    const slot = initialSlots[idx];
    if (!slot) return;
    decorateSlotEl(el, slot, idx, getDir());
    el.querySelectorAll<HTMLImageElement>("img").forEach((img) => {
      if (img.complete && img.naturalWidth > 0 && inspectImage(img)) initialChanged = true;
    });
  });
  if (initialChanged) syncSlots();
  else v._updateUI();

  return {
    active: true,
    toggleSplit() {
      if ((v._currentZoom ?? 1) > 1 && typeof v.resetZoom === "function") {
        v.resetZoom();
      }
      splitEnabled = !splitEnabled;
      syncSlots();
      options.onStateChange?.();
      return splitEnabled;
    },
    isSplitEnabled: () => splitEnabled,
    canToggleSplit: () => !v._spreadMode && hasSplittableWidePage,
    destroy() {
      track.removeEventListener("load", onImgLoad, true);
    },
  };
}
