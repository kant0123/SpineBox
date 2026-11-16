// 実物の manga-viewer を jsdom で 1 度だけ作り、再開の受け渡し(保存位置の種まき → 確認の表示)が通ることを確かめる。
// 描画の見た目は実ブラウザで確かめる。npm 0.6.0 は構築中に種を 0 ページ目で上書きして確認が出なかったため、
// 修正を含むコミットに固定している。その固定が外れたときにここで気づけるようにする
import { afterEach, describe, expect, it, vi } from "vitest";
import MangaViewer from "@tokagemushi/manga-viewer";
import { progressKey } from "./MangaReader";
import { IMAGE_KEEP_SLOTS, PAST_END_SETTLE_MS, PARKED_SRC_ATTR, SPLIT_STYLE_ATTR, setupAdaptiveSpreads, watchVolumeEdges, windowPageImages } from "./viewerHooks";

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

describe("実物の manga-viewer の見開き画像対応(#347)", () => {
  const stubViewport = (w: number, h: number) => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
  };
  const fireImgLoad = (img: HTMLImageElement, w: number, h: number) => {
    Object.defineProperty(img, "naturalWidth", { configurable: true, value: w });
    Object.defineProperty(img, "naturalHeight", { configurable: true, value: h });
    img.dispatchEvent(new Event("load"));
  };

  it("縦長画面で本文の横長画像を右→左に分割し、1 ページ目表紙は分割せず、横長画面では単独表示する", () => {
    stubViewport(375, 812);
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const pages = Array.from({ length: 4 }, (_, i) => `/api/volumes/88/pages/${i}`);
    const viewer = new MangaViewer({ container: host, pages, direction: "rtl", firstPageSingle: true });
    const inner = viewer as unknown as {
      _slots: Array<{ pages: number[]; spread: boolean; half?: string }>;
      _currentSlotIndex: number;
      _currentPageEl: HTMLElement;
      _goNext(): void;
      _navigateTo(i: number): void;
      _checkOrientation(): void;
    };
    const ctrl = setupAdaptiveSpreads(viewer);
    windowPageImages(viewer);
    const onNext = vi.fn();
    let t = Date.now();
    watchVolumeEdges(viewer, { onNext }, () => t);

    try {
      expect(ctrl.active).toBe(true);
      expect(host.shadowRoot!.querySelector(`style[${SPLIT_STYLE_ATTR}]`)).not.toBeNull();

      // ページ 0(表紙・横長)・1(横長)・2(縦長)・3(横長) をロード
      const imgOf = (pageIdx: number) =>
        host.shadowRoot!.querySelector<HTMLImageElement>(`.mv-page-slot img[data-spinebox-page="${pageIdx}"]`)!;
      fireImgLoad(imgOf(0), 2000, 1400);
      fireImgLoad(imgOf(1), 2000, 1400);
      fireImgLoad(imgOf(2), 700, 1000);
      fireImgLoad(imgOf(3), 2000, 1400);

      // 0 は非分割、1 は right->left、2 は単、3 は right->left の計 6 スロット
      expect(inner._slots.map((s) => ({ p: s.pages, h: s.half }))).toEqual([
        { p: [0], h: undefined },
        { p: [1], h: "right" },
        { p: [1], h: "left" },
        { p: [2], h: undefined },
        { p: [3], h: "right" },
        { p: [3], h: "left" },
      ]);
      expect(ctrl.canToggleSplit()).toBe(true);

      // 2 ページ目(index 1)の右半分 → 左半分へ進み、フッター表示と DOM クラスを確認
      inner._goNext();
      expect(inner._currentSlotIndex).toBe(1);
      expect(inner._currentPageEl.textContent).toBe("2 (右) / 4");
      const rightImg = host.shadowRoot!.querySelector<HTMLImageElement>('.mv-page-slot[data-slot="1"] img')!;
      expect(rightImg.classList.contains("sb-split-right")).toBe(true);

      inner._goNext();
      expect(inner._currentSlotIndex).toBe(2);
      expect(inner._currentPageEl.textContent).toBe("2 (左) / 4");

      // トグルで全体表示(4 スロット)に切り替え、ページ 2(index 1)に留まる
      expect(ctrl.toggleSplit()).toBe(false);
      expect(inner._slots).toHaveLength(4);
      expect(inner._currentSlotIndex).toBe(1);
      expect(inner._currentPageEl.textContent).toBe("2 / 4");

      // 再度分割 ON にして最終ページへ移動: 右半分では次巻へ飛ばず、左半分到達後に飛ぶ
      expect(ctrl.toggleSplit()).toBe(true);
      inner._navigateTo(4); // ページ 4(index 3) の右半分
      t += PAST_END_SETTLE_MS;
      inner._goNext(); // 左半分(slot 5)へ
      expect(inner._currentSlotIndex).toBe(5);
      expect(onNext).not.toHaveBeenCalled();
      t += PAST_END_SETTLE_MS;
      inner._goNext(); // 左半分からさらに進むと次巻へ
      expect(onNext).toHaveBeenCalledTimes(1);

      // 横長画面へ回転すると、横長画像は単独フル幅(spread: false)になり分割トグルは不要になる
      stubViewport(1024, 768);
      inner._checkOrientation();
      expect(ctrl.canToggleSplit()).toBe(false);
      expect(inner._slots.every((s) => s.spread === false && s.half === undefined)).toBe(true);
      expect(inner._slots).toHaveLength(4);
      expect(inner._currentSlotIndex).toBe(3); // 元の最終ページに留まる
    } finally {
      ctrl.destroy();
      viewer.destroy();
      host.remove();
    }
  });
});

// 離れたページの画像を手放す結線(#343)もライブラリの内部(_preloadNearby・_slotTrack・data-slot)に頼る
describe("実物の manga-viewer のページ画像", () => {
  it("今のスロットの近くだけが src を持ち、移動・スロットの作り直しで入れ替わる", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const pages = Array.from({ length: 40 }, (_, i) => `/api/volumes/77/pages/${i}`);
    const viewer = new MangaViewer({ container: host, pages, direction: "rtl" });
    const inner = viewer as unknown as {
      _slots: unknown[];
      _currentSlotIndex: number;
      _navigateTo(i: number): void;
      _renderSlots(): void;
      goToPage(n: number): void;
    };
    const imgs = () => [...host.shadowRoot!.querySelectorAll<HTMLImageElement>(".mv-page-slot img")];
    const slotOf = (img: HTMLImageElement) => Number(img.closest<HTMLElement>(".mv-page-slot")!.dataset.slot);
    const check = () => {
      const all = imgs();
      expect(all).toHaveLength(pages.length);
      for (const img of all) {
        const near = Math.abs(slotOf(img) - inner._currentSlotIndex) <= IMAGE_KEEP_SLOTS;
        expect(img.hasAttribute("src")).toBe(near);
        expect(img.hasAttribute(PARKED_SRC_ATTR)).toBe(!near);
      }
      // 外した URL は控えに残り、どのページも失われない
      const urls = all.map((img) => img.getAttribute("src") ?? img.getAttribute(PARKED_SRC_ATTR)).sort();
      expect(urls).toEqual([...pages].sort());
    };
    try {
      expect(imgs().every((img) => img.hasAttribute("src"))).toBe(true); // ライブラリの既定は全ページ
      expect(windowPageImages(viewer)).toBe(true);
      check();
      expect(imgs().filter((img) => img.hasAttribute("src")).length).toBeLessThan(pages.length / 2);

      inner._navigateTo(inner._slots.length - 1); // 末尾へ
      check();
      inner.goToPage(15); // スライダー・再開での移動
      check();
      inner._renderSlots(); // 向きの切替ではスロットを作り直す(画像は全部 src つきで作られる)
      check();
    } finally {
      viewer.destroy();
      host.remove();
    }
  });
});

describe("実物の manga-viewer の見開き画像対応(#347)", () => {
  const stubViewport = (w: number, h: number) => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
  };
  const fireImgLoad = (img: HTMLImageElement, w: number, h: number) => {
    Object.defineProperty(img, "naturalWidth", { configurable: true, value: w });
    Object.defineProperty(img, "naturalHeight", { configurable: true, value: h });
    img.dispatchEvent(new Event("load"));
  };

  it("縦長画面で本文の横長画像を右→左に分割し、1 ページ目表紙は分割せず、横長画面では単独表示する", () => {
    stubViewport(375, 812);
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const pages = Array.from({ length: 4 }, (_, i) => `/api/volumes/88/pages/${i}`);
    const viewer = new MangaViewer({ container: host, pages, direction: "rtl", firstPageSingle: true });
    const inner = viewer as unknown as {
      _slots: Array<{ pages: number[]; spread: boolean; half?: string }>;
      _currentSlotIndex: number;
      _currentPageEl: HTMLElement;
      _goNext(): void;
      _navigateTo(i: number): void;
      _checkOrientation(): void;
    };
    const ctrl = setupAdaptiveSpreads(viewer);
    windowPageImages(viewer);
    const onNext = vi.fn();
    let t = Date.now();
    watchVolumeEdges(viewer, { onNext }, () => t);

    try {
      expect(ctrl.active).toBe(true);
      expect(host.shadowRoot!.querySelector(`style[${SPLIT_STYLE_ATTR}]`)).not.toBeNull();

      // ページ 0(表紙・横長)・1(横長)・2(縦長)・3(横長) をロード
      const imgOf = (pageIdx: number) =>
        host.shadowRoot!.querySelector<HTMLImageElement>(`.mv-page-slot img[data-spinebox-page="${pageIdx}"]`)!;
      fireImgLoad(imgOf(0), 2000, 1400);
      fireImgLoad(imgOf(1), 2000, 1400);
      fireImgLoad(imgOf(2), 700, 1000);
      fireImgLoad(imgOf(3), 2000, 1400);

      // 0 は非分割、1 は right->left、2 は単、3 は right->left の計 6 スロット
      expect(inner._slots.map((s) => ({ p: s.pages, h: s.half }))).toEqual([
        { p: [0], h: undefined },
        { p: [1], h: "right" },
        { p: [1], h: "left" },
        { p: [2], h: undefined },
        { p: [3], h: "right" },
        { p: [3], h: "left" },
      ]);
      expect(ctrl.canToggleSplit()).toBe(true);

      // 2 ページ目(index 1)の右半分 → 左半分へ進み、フッター表示と DOM クラスを確認
      inner._goNext();
      expect(inner._currentSlotIndex).toBe(1);
      expect(inner._currentPageEl.textContent).toBe("2 (右) / 4");
      const rightImg = host.shadowRoot!.querySelector<HTMLImageElement>('.mv-page-slot[data-slot="1"] img')!;
      expect(rightImg.classList.contains("sb-split-right")).toBe(true);

      inner._goNext();
      expect(inner._currentSlotIndex).toBe(2);
      expect(inner._currentPageEl.textContent).toBe("2 (左) / 4");

      // トグルで全体表示(4 スロット)に切り替え、ページ 2(index 1)に留まる
      expect(ctrl.toggleSplit()).toBe(false);
      expect(inner._slots).toHaveLength(4);
      expect(inner._currentSlotIndex).toBe(1);
      expect(inner._currentPageEl.textContent).toBe("2 / 4");

      // 再度分割 ON にして最終ページへ移動: 右半分では次巻へ飛ばず、左半分到達後に飛ぶ
      expect(ctrl.toggleSplit()).toBe(true);
      inner._navigateTo(4); // ページ 4(index 3) の右半分
      t += PAST_END_SETTLE_MS;
      inner._goNext(); // 左半分(slot 5)へ
      expect(inner._currentSlotIndex).toBe(5);
      expect(onNext).not.toHaveBeenCalled();
      t += PAST_END_SETTLE_MS;
      inner._goNext(); // 左半分からさらに進むと次巻へ
      expect(onNext).toHaveBeenCalledTimes(1);

      // 横長画面へ回転すると、横長画像は単独フル幅(spread: false)になり分割トグルは不要になる
      stubViewport(1024, 768);
      inner._checkOrientation();
      expect(ctrl.canToggleSplit()).toBe(false);
      expect(inner._slots.every((s) => s.spread === false && s.half === undefined)).toBe(true);
      expect(inner._slots).toHaveLength(4);
      expect(inner._currentSlotIndex).toBe(3); // 元の最終ページに留まる
    } finally {
      ctrl.destroy();
      viewer.destroy();
      host.remove();
    }
  });
});
