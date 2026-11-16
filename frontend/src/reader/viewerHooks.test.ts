// manga-viewer へ外から足す結線(#324, #338): スライダーの見た目と、先頭・最後の画面からさらに前後へ進む操作の検出
import { describe, expect, it, vi } from "vitest";
import { PAST_END_SETTLE_MS, SLIDER_STYLE_ATTR, blankPages, canWindowImages, enlargeSlider, setupAdaptiveSpreads, watchPastEnd, watchVolumeEdges, windowPageImages } from "./viewerHooks";

/** ライブラリと同じ形の最小の偽物: _goNext / _goPrev は端で何もしない、_navigateTo は範囲外を無視する */
function fakeViewer(slots: number, at = 0, direction: "rtl" | "ltr" = "rtl") {
  return {
    _slots: Array.from({ length: slots }),
    _currentSlotIndex: at,
    _isDragging: false,
    _currentZoom: 1,
    _startX: 0,
    _currentX: 0,
    _dragStartTime: 0,
    _containerWidth: 400,
    opts: { direction },
    _navigateTo(idx: number) {
      if (idx < 0 || idx >= this._slots.length || idx === this._currentSlotIndex) return;
      this._currentSlotIndex = idx;
    },
    _goNext() {
      if (this._currentSlotIndex < this._slots.length - 1) this._navigateTo(this._currentSlotIndex + 1);
    },
    _goPrev() {
      if (this._currentSlotIndex > 0) this._navigateTo(this._currentSlotIndex - 1);
    },
    _endDrag() {
      if (!this._isDragging) return;
      this._isDragging = false;
      if (this._currentZoom > 1) return;
      const diff = this._currentX - this._startX;
      const rtl = this.opts.direction === "rtl";
      const step = diff < 0 ? (rtl ? -1 : 1) : diff > 0 ? (rtl ? 1 : -1) : 0;
      const target = this._currentSlotIndex + step;
      if (Math.abs(diff) > this._containerWidth * 0.15 && target >= 0 && target < this._slots.length) {
        this._currentSlotIndex = target;
      }
    },
  };
}

function swipe(v: ReturnType<typeof fakeViewer>, diff: number, startTime = 0) {
  v._isDragging = true;
  v._startX = 100;
  v._currentX = 100 + diff;
  v._dragStartTime = startTime;
  v._endDrag();
}

function watched(slots: number, at = 0, direction: "rtl" | "ltr" = "rtl") {
  const v = fakeViewer(slots, at, direction);
  const onNext = vi.fn();
  const onPrev = vi.fn();
  let t = 0;
  expect(watchVolumeEdges(v, { onNext, onPrev }, () => t)).toBe(true);
  return { v, onNext, onPrev, wait: (ms: number) => (t += ms), now: () => t };
}

describe("watchPastEnd / watchVolumeEdges", () => {
  it("途中のページ送りでは呼ばない", () => {
    const { v, onNext, onPrev, wait } = watched(3, 1);
    wait(PAST_END_SETTLE_MS);
    v._goNext();
    expect(v._currentSlotIndex).toBe(2);
    expect(onNext).not.toHaveBeenCalled();
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("最後の画面からキー・ホイール(_goNext)でさらに進むと呼ぶ", () => {
    const { v, onNext, wait } = watched(3, 2);
    wait(PAST_END_SETTLE_MS);
    v._goNext();
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("最後の画面からタップ(_navigateTo(今 + 1))でさらに進むと呼ぶ", () => {
    const { v, onNext, wait } = watched(3, 2);
    wait(PAST_END_SETTLE_MS);
    v._navigateTo(3);
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("最後の画面から RTL 右スワイプ(_endDrag)でさらに進むと onNext を呼ぶ", () => {
    const { v, onNext, onPrev, wait, now } = watched(3, 2);
    wait(PAST_END_SETTLE_MS);
    swipe(v, 100, now());
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("先頭の画面からキー・ホイール(_goPrev)、タップ(_navigateTo(-1))、RTL 左スワイプ(_endDrag)でさらに戻ると onPrev を呼ぶ", () => {
    const { v, onNext, onPrev, wait, now } = watched(3, 0);
    wait(PAST_END_SETTLE_MS);
    v._goPrev();
    v._navigateTo(-1);
    swipe(v, -100, now());
    expect(onPrev).toHaveBeenCalledTimes(3);
    expect(onNext).not.toHaveBeenCalled();
  });

  it("LTR でも方向に応じて onNext / onPrev を呼ぶ", () => {
    const end = watched(3, 2, "ltr");
    end.wait(PAST_END_SETTLE_MS);
    swipe(end.v, -100, end.now());
    expect(end.onNext).toHaveBeenCalledTimes(1);

    const start = watched(3, 0, "ltr");
    start.wait(PAST_END_SETTLE_MS);
    swipe(start.v, 100, start.now());
    expect(start.onPrev).toHaveBeenCalledTimes(1);
  });

  it("スワイプで最後の画面に着いた直後は呼ばず、500ms 後のスワイプで呼ぶ", () => {
    const { v, onNext, wait, now } = watched(3, 1);
    wait(PAST_END_SETTLE_MS);
    swipe(v, 100, now()); // 1 -> 2 へスワイプで移動
    expect(v._currentSlotIndex).toBe(2);
    expect(onNext).not.toHaveBeenCalled();
    wait(PAST_END_SETTLE_MS - 1);
    swipe(v, 100, now());
    expect(onNext).not.toHaveBeenCalled();
    wait(1);
    swipe(v, 100, now());
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("しきい値未満の短いドラッグやズーム中は呼ばない", () => {
    const { v, onNext, onPrev, wait, now } = watched(1, 0);
    wait(PAST_END_SETTLE_MS);
    swipe(v, 20, now()); // しきい値(400*0.15=60)未満かつ 30px 以下
    v._currentZoom = 2;
    swipe(v, 100, now());
    expect(onNext).not.toHaveBeenCalled();
    expect(onPrev).not.toHaveBeenCalled();
  });

  it("最後の画面に着いた直後の続けての操作(慣性・連打)では呼ばず、落ち着いてからの操作で呼ぶ", () => {
    const { v, onNext, wait } = watched(3, 1);
    wait(PAST_END_SETTLE_MS);
    v._goNext(); // 最後の画面へ
    wait(PAST_END_SETTLE_MS - 1);
    v._goNext();
    v._navigateTo(3);
    expect(onNext).not.toHaveBeenCalled();
    wait(1);
    v._goNext();
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("内部の形が違うライブラリには何もしない", () => {
    const onPastEnd = vi.fn();
    expect(watchPastEnd({ destroy() {} }, onPastEnd)).toBe(false);
    expect(watchPastEnd({ _goNext() {}, _navigateTo() {} }, onPastEnd)).toBe(false);
  });
});

describe("enlargeSlider", () => {
  it("Shadow DOM に 1 度だけスタイルを足す", () => {
    const root = document.createElement("div").attachShadow({ mode: "open" });
    enlargeSlider(root);
    enlargeSlider(root);
    const styles = root.querySelectorAll(`style[${SLIDER_STYLE_ATTR}]`);
    expect(styles).toHaveLength(1);
    expect(styles[0].textContent).toContain(".mv-page-slider");
    expect(() => enlargeSlider(null)).not.toThrow();
  });
});

describe("windowPageImages", () => {
  // ライブラリと同じ形: URL なしで作られた <img src="">。perSlot が 2 なら見開き(1 スロットに 2 ページ)
  const build = (count: number, perSlot = 1) => {
    const track = document.createElement("div");
    const slots: Array<{ pages: number[] }> = [];
    for (let i = 0; i < count; i++) {
      const slot = document.createElement("div");
      slot.className = "mv-page-slot";
      slot.dataset.slot = String(i);
      const pages = Array.from({ length: perSlot }, (_, k) => i * perSlot + k);
      slots.push({ pages });
      pages.forEach(() => {
        const img = document.createElement("img");
        img.setAttribute("src", "");
        slot.appendChild(img);
      });
      track.appendChild(slot);
    }
    const urls = Array.from({ length: count * perSlot }, (_, i) => `/p/${i}`);
    const calls: string[][] = [];
    const withSrc = () => [...track.querySelectorAll("img")].filter((i) => i.hasAttribute("src")).map((i) => i.getAttribute("src")!);
    // 先読み(元の処理)が呼ばれた時点で、近くの画像には既に URL が付いている
    const v = { _currentSlotIndex: 0, _slots: slots, _slotTrack: track, _preloadNearby() { calls.push(withSrc()); } };
    return { track, v, urls, calls, withSrc };
  };

  it("近くのスロットにだけ URL を付け、離れたら外し、付けてから元の先読みを呼ぶ", () => {
    const { track, v, urls, calls, withSrc } = build(10);
    expect(windowPageImages(v, urls, 1)).toBe(true);
    expect(withSrc()).toEqual(["/p/0", "/p/1"]);
    expect(calls).toHaveLength(0);

    const first = track.querySelectorAll("img")[0];
    first.dataset.preloaded = "true";
    first.dataset.mvDecoded = "1";
    v._currentSlotIndex = 5;
    v._preloadNearby();
    expect(withSrc()).toEqual(["/p/4", "/p/5", "/p/6"]);
    expect(calls).toEqual([["/p/4", "/p/5", "/p/6"]]);
    // 外した画像は、ライブラリの「先読み済み・デコード済み」の印も失う
    expect(first.dataset.preloaded).toBeUndefined();
    expect(first.dataset.mvDecoded).toBeUndefined();
  });

  it("見開きのスロットでは、スロットのページの並びどおりに URL を付ける", () => {
    const { v, urls, withSrc } = build(6, 2);
    v._currentSlotIndex = 3;
    expect(windowPageImages(v, urls, 0)).toBe(true);
    expect(withSrc()).toEqual(["/p/6", "/p/7"]);
  });

  it("空白ページのあるスロットでは、読み込んだ画像の縦横比を空白ページへ渡す", () => {
    const { track, v, urls } = build(3);
    const blank = document.createElement("div");
    blank.className = "mv-blank-page";
    track.querySelector(".mv-page-slot")!.prepend(blank);
    windowPageImages(v, urls, 0);
    const img = track.querySelector("img")!;
    Object.defineProperty(img, "naturalWidth", { value: 984 });
    Object.defineProperty(img, "naturalHeight", { value: 1400 });
    img.dispatchEvent(new Event("load"));
    expect(blank.style.getPropertyValue("--mv-blank-aspect-ratio")).toBe("984 / 1400");
  });

  it("内部の形が違えば何もしない", () => {
    expect(windowPageImages({}, [])).toBe(false);
    expect(windowPageImages({ _preloadNearby() {}, _currentSlotIndex: 0 }, [])).toBe(false);
    const { v, urls, withSrc } = build(10);
    expect(windowPageImages({ ...v, _currentSlotIndex: undefined }, urls)).toBe(false);
    expect(windowPageImages({ ...v, _slots: undefined }, urls)).toBe(false);
    expect(withSrc()).toEqual(Array(10).fill(""));
  });

  it("blankPages は URL を持たないページを数ぶん返す", () => {
    expect(blankPages(2)).toEqual([{ type: "image", src: "" }, { type: "image", src: "" }]);
  });

  it("canWindowImages は、頼る内部メソッドを持つクラスだけを通す", () => {
    expect(canWindowImages(class { _preloadNearby() {} _renderSlots() {} })).toBe(true);
    expect(canWindowImages(class { _preloadNearby() {} })).toBe(false);
    expect(canWindowImages(class {})).toBe(false);
    expect(canWindowImages(undefined)).toBe(false);
  });
});

describe("setupAdaptiveSpreads", () => {
  it("内部の形が違えば active: false のコントローラを返す", () => {
    const ctrl = setupAdaptiveSpreads({});
    expect(ctrl.active).toBe(false);
    expect(ctrl.toggleSplit()).toBe(false);
    expect(ctrl.canToggleSplit()).toBe(false);
    expect(() => ctrl.destroy()).not.toThrow();
  });

  it("ドラッグ中の画像ロードは _endDrag の後までスロット再構築を遅延する", () => {
    const track = document.createElement("div");
    const slot0 = document.createElement("div");
    slot0.className = "mv-page-slot";
    slot0.dataset.slot = "0";
    slot0.innerHTML = '<div class="mv-zoom-container" data-zoom-slot="0"><img src="/p/0"></div>';
    const slot1 = document.createElement("div");
    slot1.className = "mv-page-slot";
    slot1.dataset.slot = "1";
    slot1.innerHTML = '<div class="mv-zoom-container" data-zoom-slot="1"><img src="/p/1"></div>';
    track.append(slot0, slot1);

    const v = {
      _buildSlots() {},
      _renderSlots() {},
      _updateUI() {},
      _checkOrientation() {},
      _endDrag() { this._isDragging = false; },
      _updateTrackPosition() {},
      _preloadNearby() {},
      _slotTrack: track,
      _slots: [{ pages: [0], spread: false }, { pages: [1], spread: false }],
      _pages: [{ type: "image" }, { type: "image" }],
      _spreadMode: false,
      _isDragging: true,
      _currentSlotIndex: 0,
      _totalPages: 2,
      opts: { direction: "rtl", firstPageSingle: true, lastPageAlign: "start" },
    };

    const ctrl = setupAdaptiveSpreads(v);
    expect(ctrl.active).toBe(true);
    const img1 = slot1.querySelector("img")!;
    Object.defineProperty(img1, "naturalWidth", { configurable: true, value: 2000 });
    Object.defineProperty(img1, "naturalHeight", { configurable: true, value: 1400 });
    img1.dispatchEvent(new Event("load"));

    // ドラッグ中はまだ 2 スロットのまま
    expect(v._slots).toHaveLength(2);
    expect(ctrl.canToggleSplit()).toBe(true);

    // _endDrag 後に 3 スロット([0], [1 right], [1 left])へ反映される
    v._endDrag();
    expect(v._slots).toHaveLength(3);
    ctrl.destroy();
  });
});
