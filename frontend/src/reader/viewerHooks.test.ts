// manga-viewer へ外から足す結線(#324, #338): スライダーの見た目と、先頭・最後の画面からさらに前後へ進む操作の検出
import { describe, expect, it, vi } from "vitest";
import { PAST_END_SETTLE_MS, SLIDER_STYLE_ATTR, enlargeSlider, watchPastEnd, watchVolumeEdges } from "./viewerHooks";

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
