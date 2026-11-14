// 実物の manga-viewer を jsdom で 1 度だけ作り、再開の受け渡し(保存位置の種まき → 確認の表示)が通ることを確かめる。
// 描画の見た目は実ブラウザで確かめる。npm 0.6.0 は構築中に種を 0 ページ目で上書きして確認が出なかったため、
// 修正を含むコミットに固定している。その固定が外れたときにここで気づけるようにする
import { afterEach, describe, expect, it, vi } from "vitest";
import MangaViewer from "@tokagemushi/manga-viewer";
import { progressKey } from "./MangaReader";
import { PAST_END_SETTLE_MS, watchPastEnd } from "./viewerHooks";

afterEach(() => vi.unstubAllGlobals());

describe("実物の manga-viewer の再開", () => {
  it("種まきした位置で「続きから」の確認が出て、答えるまで種は上書きされない", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const key = progressKey("77");
    const seed = { slotIndex: 0, pageIndex: 5, timestamp: Date.now() };
    localStorage.setItem(key, JSON.stringify(seed));
    const host = document.createElement("div");
    document.body.appendChild(host);
    const viewer = new MangaViewer({
      container: host,
      pages: Array.from({ length: 8 }, (_, i) => `/api/volumes/77/pages/${i}`),
      storageKey: key,
    });
    try {
      expect(host.shadowRoot!.querySelector(".mv-resume-dialog")).not.toBeNull();
      expect(JSON.parse(localStorage.getItem(key)!)).toMatchObject({ pageIndex: 5 });
    } finally {
      viewer.destroy();
      host.remove();
      localStorage.removeItem(key);
    }
  });
});

// 最後の画面からさらに進む操作はライブラリの内部メソッドを包んで拾う(#324)。
// 固定したコミットを替えて内部の形が変わったら、ここで気づけるようにする
describe("実物の manga-viewer の最後の画面", () => {
  it("最後の画面からさらに進む操作(キー・スワイプの経路とタップの経路)を拾える", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const viewer = new MangaViewer({ container: host, pages: ["/a/0", "/a/1", "/a/2"], direction: "rtl" });
    const inner = viewer as unknown as { _slots: unknown[]; _currentSlotIndex: number; _goNext(): void; _navigateTo(i: number): void };
    try {
      const onPastEnd = vi.fn();
      let t = 0;
      expect(watchPastEnd(viewer, onPastEnd, () => t)).toBe(true);
      const last = inner._slots.length - 1;
      expect(last).toBeGreaterThan(0);
      for (let i = 0; i < last; i++) inner._goNext();
      expect(inner._currentSlotIndex).toBe(last);
      expect(onPastEnd).not.toHaveBeenCalled();
      t += PAST_END_SETTLE_MS;
      inner._goNext();
      expect(onPastEnd).toHaveBeenCalledTimes(1);
      inner._navigateTo(last + 1);
      expect(onPastEnd).toHaveBeenCalledTimes(2);
    } finally {
      viewer.destroy();
      host.remove();
    }
  });
});
