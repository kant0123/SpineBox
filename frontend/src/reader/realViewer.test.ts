// 実物の manga-viewer を jsdom で 1 度だけ作り、再開の受け渡し(保存位置の種まき → 確認の表示)が通ることを確かめる。
// 描画の見た目は実ブラウザで確かめる。npm 0.6.0 は構築中に種を 0 ページ目で上書きして確認が出なかったため、
// 修正を含むコミットに固定している。その固定が外れたときにここで気づけるようにする
import { afterEach, describe, expect, it, vi } from "vitest";
import MangaViewer from "@tokagemushi/manga-viewer";
import { progressKey } from "./MangaReader";
import { IMAGE_KEEP_SLOTS, PAST_END_SETTLE_MS, blankPages, canWindowImages, watchVolumeEdges, windowPageImages } from "./viewerHooks";

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

// 先頭・最後の画面からさらに前後へ進む操作はライブラリの内部メソッドを包んで拾う(#324, #338)。
// 固定したコミットを替えて内部の形が変わったら、ここで気づけるようにする
describe("実物の manga-viewer の先頭・最後の画面", () => {
  it("先頭・最後の画面からさらに前後へ進む操作(キー・タップ・スワイプ)を拾える", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const viewer = new MangaViewer({ container: host, pages: ["/a/0", "/a/1", "/a/2"], direction: "rtl" });
    const inner = viewer as unknown as {
      _slots: unknown[];
      _currentSlotIndex: number;
      _goNext(): void;
      _goPrev(): void;
      _navigateTo(i: number): void;
      _startDrag(x: number, y?: number): void;
      _moveDrag(x: number, y?: number): void;
      _endDrag(): void;
    };
    try {
      const onNext = vi.fn();
      const onPrev = vi.fn();
      let t = Date.now();
      expect(watchVolumeEdges(viewer, { onNext, onPrev }, () => t)).toBe(true);

      // 先頭(0)から前へ: キー / タップ / RTL 左スワイプ
      t += PAST_END_SETTLE_MS;
      inner._goPrev();
      inner._navigateTo(-1);
      inner._startDrag(200);
      inner._moveDrag(100);
      inner._endDrag();
      expect(onPrev).toHaveBeenCalledTimes(3);
      expect(onNext).not.toHaveBeenCalled();

      // 末尾へ移動
      const last = inner._slots.length - 1;
      expect(last).toBeGreaterThan(0);
      for (let i = 0; i < last; i++) inner._goNext();
      expect(inner._currentSlotIndex).toBe(last);
      expect(onNext).not.toHaveBeenCalled();

      // 末尾から次へ: キー / タップ / RTL 右スワイプ
      t += PAST_END_SETTLE_MS;
      inner._goNext();
      inner._navigateTo(last + 1);
      inner._startDrag(100);
      inner._moveDrag(200);
      inner._endDrag();
      expect(onNext).toHaveBeenCalledTimes(3);
    } finally {
      viewer.destroy();
      host.remove();
    }
  });
});

// ページ画像を近くのスロットにだけ持たせる結線(#343)もライブラリの内部(_preloadNearby・_slots・_slotTrack・data-slot)に頼る
describe("実物の manga-viewer のページ画像", () => {
  const setup = (landscape: boolean) => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("innerWidth", landscape ? 1100 : 400);
    vi.stubGlobal("innerHeight", landscape ? 600 : 800);
    // <img> に付いた URL を、付いた時点で全部控える(WebKit は src が付いた時点で読み込みを始める)
    const seen = new Set<string>();
    const setAttribute = Element.prototype.setAttribute;
    const spy = vi.spyOn(Element.prototype, "setAttribute").mockImplementation(function (this: Element, name: string, value: string) {
      if (this.tagName === "IMG" && name === "src" && value) seen.add(value);
      setAttribute.call(this, name, value);
    });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const urls = Array.from({ length: 40 }, (_, i) => `/api/volumes/77/pages/${i}`);
    const viewer = new MangaViewer({ container: host, pages: blankPages(urls.length), direction: "rtl", firstPageSingle: true });
    const inner = viewer as unknown as {
      _slots: Array<{ pages: number[] }>;
      _currentSlotIndex: number;
      _navigateTo(i: number): void;
      _renderSlots(): void;
      goToPage(n: number): void;
    };
    const imgs = () => [...host.shadowRoot!.querySelectorAll<HTMLImageElement>(".mv-page-slot img")];
    const slotOf = (img: HTMLImageElement) => Number(img.closest<HTMLElement>(".mv-page-slot")!.dataset.slot);
    /** 近くのスロットの <img> だけが URL を持ち、その URL は表示するページ(alt は「Page <ページ番号>」)のもの */
    const check = () => {
      const all = imgs();
      expect(all).toHaveLength(urls.length);
      for (const img of all) {
        const near = Math.abs(slotOf(img) - inner._currentSlotIndex) <= IMAGE_KEEP_SLOTS;
        expect(img.hasAttribute("src")).toBe(near);
        if (near) expect(img.getAttribute("src")).toBe(urls[Number(img.alt.replace("Page ", "")) - 1]);
      }
    };
    const done = () => {
      viewer.destroy();
      host.remove();
      spy.mockRestore();
    };
    return { viewer, inner, urls, seen, imgs, check, done };
  };

  it("近くのスロットだけが URL を持ち、移動・スロットの作り直しで入れ替わる。離れたページには一度も URL が付かない", () => {
    const { viewer, inner, urls, seen, imgs, check, done } = setup(false);
    try {
      expect(canWindowImages(MangaViewer)).toBe(true);
      expect(imgs().every((img) => !img.getAttribute("src"))).toBe(true); // URL なしで作られる
      expect(windowPageImages(viewer, urls)).toBe(true);
      check();
      expect(seen.size).toBe(IMAGE_KEEP_SLOTS + 1); // 開いた直後に読み込まれるのは先頭の近くだけ

      inner._navigateTo(inner._slots.length - 1); // 末尾へ
      check();
      inner.goToPage(15); // スライダー・再開での移動
      check();
      inner._renderSlots(); // 向きの切替ではスロットを作り直す
      check();
      // 先頭の近く・末尾の近く・15 ページ目の近く以外には、一度も URL が付いていない
      expect(seen.size).toBeLessThan(urls.length / 2);
      expect(seen.has(urls[25])).toBe(false);
    } finally {
      done();
    }
  });

  it("横向きの見開き(表紙は単独)でも、ページと URL が合う", () => {
    const { viewer, inner, urls, check, done } = setup(true);
    try {
      expect(windowPageImages(viewer, urls)).toBe(true);
      expect(inner._slots.some((slot) => slot.pages.length === 2)).toBe(true);
      check();
      inner.goToPage(20);
      check();
    } finally {
      done();
    }
  });
});
