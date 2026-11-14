// manga-viewer へ外から足す結線(#324): スライダーの見た目と、最後の画面からさらに進む操作の検出
import { describe, expect, it, vi } from "vitest";
import { PAST_END_SETTLE_MS, SLIDER_STYLE_ATTR, enlargeSlider, watchPastEnd } from "./viewerHooks";

/** ライブラリと同じ形の最小の偽物: _goNext は最後で何もしない、_navigateTo は範囲外を無視する */
function fakeViewer(slots: number, at = 0) {
  return {
    _slots: Array.from({ length: slots }),
    _currentSlotIndex: at,
    _navigateTo(idx: number) {
      if (idx < 0 || idx >= this._slots.length || idx === this._currentSlotIndex) return;
      this._currentSlotIndex = idx;
    },
    _goNext() {
      if (this._currentSlotIndex < this._slots.length - 1) this._navigateTo(this._currentSlotIndex + 1);
    },
  };
}

function watched(slots: number, at = 0) {
  const v = fakeViewer(slots, at);
  const onPastEnd = vi.fn();
  let t = 0;
  expect(watchPastEnd(v, onPastEnd, () => t)).toBe(true);
  return { v, onPastEnd, wait: (ms: number) => (t += ms) };
}

describe("watchPastEnd", () => {
  it("途中のページ送りでは呼ばない", () => {
    const { v, onPastEnd, wait } = watched(3);
    wait(PAST_END_SETTLE_MS);
    v._goNext();
    expect(v._currentSlotIndex).toBe(1);
    expect(onPastEnd).not.toHaveBeenCalled();
  });

  it("最後の画面からキー・ホイール・スワイプ(_goNext)でさらに進むと呼ぶ", () => {
    const { v, onPastEnd, wait } = watched(3, 2);
    wait(PAST_END_SETTLE_MS);
    v._goNext();
    expect(onPastEnd).toHaveBeenCalledTimes(1);
  });

  it("最後の画面からタップ(_navigateTo(今 + 1))でさらに進むと呼ぶ", () => {
    const { v, onPastEnd, wait } = watched(3, 2);
    wait(PAST_END_SETTLE_MS);
    v._navigateTo(3);
    expect(onPastEnd).toHaveBeenCalledTimes(1);
  });

  it("最後の画面に着いた直後の続けての操作(慣性・連打)では呼ばず、落ち着いてからの操作で呼ぶ", () => {
    const { v, onPastEnd, wait } = watched(3, 1);
    wait(PAST_END_SETTLE_MS);
    v._goNext(); // 最後の画面へ
    wait(PAST_END_SETTLE_MS - 1);
    v._goNext();
    v._navigateTo(3);
    expect(onPastEnd).not.toHaveBeenCalled();
    wait(1);
    v._goNext();
    expect(onPastEnd).toHaveBeenCalledTimes(1);
  });

  it("前へ戻る・最初の画面より前へは呼ばない", () => {
    const { v, onPastEnd, wait } = watched(1);
    wait(PAST_END_SETTLE_MS);
    v._navigateTo(-1);
    expect(onPastEnd).not.toHaveBeenCalled();
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
