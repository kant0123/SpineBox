// 実物の manga-viewer を jsdom で 1 度だけ作り、再開の受け渡し(保存位置の種まき → 確認の表示)が通ることを確かめる。
// 描画の見た目は実ブラウザで確かめる。npm 0.6.0 は構築中に種を 0 ページ目で上書きして確認が出なかったため、
// 修正を含むコミットに固定している。その固定が外れたときにここで気づけるようにする
import { afterEach, describe, expect, it, vi } from "vitest";
import MangaViewer from "@tokagemushi/manga-viewer";
import { progressKey } from "./MangaReader";

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
