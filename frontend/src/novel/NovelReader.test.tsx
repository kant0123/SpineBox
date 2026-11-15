// 縦書きリーダーをモック API と通す。jsdom には描画が無いので、本文の帯の幅(scrollWidth)を
// 文字数・文字サイズ・高さから計算する差し替えで、ページ割りと文字サイズの変更を再現する
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { VolumeDetail } from "../api";
import { createMockBackend, defaultVolumes, mockSections } from "../mock/mockBackend";
import { readHref } from "../route";
import { NovelReader } from "./NovelReader";

declare global {
  interface Window {
    __novelPwned?: number;
  }
}

let backend: ReturnType<typeof createMockBackend>;
const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollWidth");

function setViewport(w: number, h: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
}

async function open(id: number) {
  await act(async () => {
    window.location.hash = readHref(id);
  });
  render(<App />);
}

const text = () => screen.getByTestId("novel-text");
const at = () => ({ section: Number(text().dataset.section), page: Number(text().dataset.page), pages: Number(text().dataset.pages) });
/** 今のページが覆う割合の範囲 [from, to) */
const span = () => ({ from: Number(text().dataset.from), to: Number(text().dataset.to) });
const press = (key: string) => fireEvent.keyDown(window, { key });
const puts = () => backend.log.filter((l) => l.method === "PUT").map((l) => l.body as { page: number; finished: boolean; position?: number });

async function ready() {
  await waitFor(() => expect(text().style.visibility).toBe("visible"));
}

beforeEach(() => {
  backend = createMockBackend();
  localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get(this: HTMLElement) {
      if (!this.classList.contains("novel-text")) return 0;
      const fs = parseFloat(this.style.fontSize);
      const perColumn = Math.max(1, Math.floor(parseFloat(this.style.height) / fs));
      return Math.ceil((this.textContent ?? "").length / perColumn) * Math.round(fs * 2);
    },
  });
  setViewport(375, 812);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if (original) Object.defineProperty(HTMLElement.prototype, "scrollWidth", original);
  delete window.__novelPwned;
});

describe("縦書きリーダー", () => {
  it.skip("一覧に小説として出て、縦書きで最初から最後まで読め、既読で保存される", async () => {
    await act(async () => {
      window.location.hash = "#/volumes";
    });
    render(<App />);
    const card = (await screen.findByText("縦書きの小説 第1巻")).closest("li")!;
    expect(card.textContent).toContain("小説");
    expect(card.textContent).toContain("EPUB");
    expect(card.textContent).not.toContain("CBZ に整理");
    cleanup();

    await open(7);
    await ready();
    expect(at()).toMatchObject({ section: 0, page: 0 });
    expect(at().pages).toBeGreaterThan(1);
    expect(text().textContent).toContain("第一章");
    expect(text().querySelector("ruby rt")?.textContent).toBe("ねこ");

    const seen = new Set<number>();
    for (let i = 0; i < 200; i++) {
      seen.add(at().section);
      const before = at();
      press("ArrowLeft");
      if (screen.queryByText("最後のページです")) break;
      await waitFor(() => expect(at()).not.toEqual(before));
      await ready();
    }
    expect(screen.getByText("最後のページです")).toBeTruthy();
    expect([...seen]).toEqual([0, 1, 2]);
    expect(text().textContent).toContain("おしまい");
    expect(at().page).toBe(at().pages - 1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    const last = puts().at(-1)!;
    expect(last.page).toBe(2);
    expect(last.finished).toBe(true);
    expect(last.position).toBe(span().from); // 最後のページの先頭の割合
  });

  it("本文の危険な HTML(スクリプト・イベントハンドラ・外部 URL)は実行も読み込みもされない", async () => {
    backend.handle("PUT", "/api/volumes/7/progress", JSON.stringify({ page: 1, finished: false, position: 0 }));
    await open(7);
    await ready();
    expect(at().section).toBe(1);
    const el = text();
    expect(el.textContent).toContain("危険な本文");
    expect(el.querySelectorAll("script, iframe, a").length).toBe(0);
    for (const node of Array.from(el.querySelectorAll("*"))) {
      for (const attr of Array.from(node.attributes)) expect(attr.name.startsWith("on")).toBe(false);
    }
    const srcs = Array.from(el.querySelectorAll("img")).map((i) => i.getAttribute("src"));
    expect(srcs).toEqual(["/api/volumes/7/resources/0"]);
    expect(window.__novelPwned).toBeUndefined();
  });

  it("保存した位置(区切り+割合)から再開し、文字サイズを変えても近い位置に留まる", async () => {
    await open(8); // 区切り 2 の 2 つ目、割合 0.5
    await ready();
    const first = at();
    expect(first.section).toBe(1);
    expect(span().from).toBeLessThanOrEqual(0.5);
    expect(span().to).toBeGreaterThan(0.5);

    press("Escape"); // jsdom に PointerEvent が無いので、メニューはキーで開く
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "大きく" }));
    await ready();
    const bigger = at();
    expect(bigger.section).toBe(1);
    expect(bigger.pages).toBeGreaterThan(first.pages);
    // 文字サイズを変えても、同じ割合(0.5)を含むページにいる
    expect(span().from).toBeLessThanOrEqual(0.5);
    expect(span().to).toBeGreaterThan(0.5);
    expect(localStorage.getItem("spinebox.novel")).toContain("24");
  });

  it("区切りの先頭で戻ると、前の区切りの最後のページへ行く", async () => {
    backend.handle("PUT", "/api/volumes/8/progress", JSON.stringify({ page: 1, finished: false, position: 0 }));
    await open(8);
    await ready();
    expect(at()).toMatchObject({ section: 1, page: 0 });
    press("ArrowRight");
    await waitFor(() => expect(at().section).toBe(0));
    await ready();
    expect(at().page).toBe(at().pages - 1);
  });

  it("メニューの戻るは巻の作品のタイトル一覧へ(#324)。作品が無ければ一覧へ", async () => {
    const base: VolumeDetail = {
      id: 7, title: "x", format: "epub", page_count: 1, pages: [],
      sections: [{ index: 0, length: mockSections()[7][0].length }], progress: null,
    };
    const r = render(<NovelReader id="7" detail={{ ...base, work_id: 4 }} />);
    await ready();
    press("Escape");
    expect((await screen.findByRole("link", { name: "作品へ戻る" })).getAttribute("href")).toBe("#/works/4/titles");
    r.unmount();
    render(<NovelReader id="7" detail={{ ...base, work_id: null }} />);
    await ready();
    press("Escape");
    expect((await screen.findByRole("link", { name: "一覧へ戻る" })).getAttribute("href")).toBe("#/");
  });

  it("最終ページからさらに進むと、同じ作品の次の巻へ移る(#113)", async () => {
    window.location.hash = "";
    const v = defaultVolumes().find((x) => x.id === 7)!;
    const html = mockSections()[7];
    const detail: VolumeDetail = {
      id: 7, title: v.title, format: "epub", page_count: html.length, pages: [],
      sections: html.map((h, i) => ({ index: i, length: h.length })),
      progress: { page: 2, finished: false, position: 0 }, // 最後の区切りから
      next_volume: { id: 8, title: "青空文庫のテキスト", volume_number: null, offline: false },
    };
    render(<NovelReader id="7" detail={detail} />);
    await ready();

    for (let i = 0; i < 100; i++) {
      if (at().section === 2 && at().page === at().pages - 1) break;
      const before = at();
      press("ArrowLeft");
      await waitFor(() => expect(at()).not.toEqual(before));
      await ready();
    }
    expect(at().section).toBe(2);
    expect(at().page).toBe(at().pages - 1);

    press("ArrowLeft");
    expect(window.location.hash).toBe("#/read/8");
  });

  it("最初のページからさらに戻ると、同じ作品の前の巻へ移る(#338)", async () => {
    window.location.hash = "";
    const v = defaultVolumes().find((x) => x.id === 7)!;
    const html = mockSections()[7];
    const detail: VolumeDetail = {
      id: 7, title: v.title, format: "epub", page_count: html.length, pages: [],
      sections: html.map((h, i) => ({ index: i, length: h.length })),
      progress: null,
      prev_volume: { id: 6, title: "前の巻", volume_number: null, offline: false },
    };
    render(<NovelReader id="7" detail={detail} />);
    await ready();

    press("ArrowRight");
    expect(window.location.hash).toBe("#/read/6");
  });

  it("ヘッダーのブックマーク★・次の巻へ・全画面・ヘルプボタンが漫画ビューワー同様に動く", async () => {
    window.location.hash = "";
    const v = defaultVolumes().find((x) => x.id === 7)!;
    const html = mockSections()[7];
    const detail: VolumeDetail = {
      id: 7,
      title: v.title,
      format: "epub",
      page_count: html.length,
      pages: [],
      sections: html.map((h, i) => ({ index: i, length: h.length })),
      progress: null,
      bookmarked: false,
      next_volume: { id: 8, title: "青空文庫のテキスト", volume_number: null, offline: false },
    };
    render(<NovelReader id="7" detail={detail} />);
    await ready();

    // 初期状態はメニュー非表示(is-hidden)
    const header = document.querySelector(".novel-header")!;
    const footer = document.querySelector(".novel-footer")!;
    expect(header.classList.contains("is-hidden")).toBe(true);
    expect(footer.classList.contains("is-hidden")).toBe(true);

    // メニューを開く(M キーでも開閉できる)
    press("m");
    expect(header.classList.contains("is-hidden")).toBe(false);
    expect(footer.classList.contains("is-hidden")).toBe(false);
    press("M");
    expect(header.classList.contains("is-hidden")).toBe(true);
    press("Escape");
    expect(header.classList.contains("is-hidden")).toBe(false);

    // 巻ブックマーク ★ のトグル
    const bmBtn = screen.getByRole("button", { name: "ブックマークに追加" });
    expect(bmBtn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(bmBtn);
    await waitFor(() => expect(screen.getByRole("button", { name: "ブックマークを外す" }).getAttribute("aria-pressed")).toBe("true"));

    // 全画面ボタン(PC向けボタン、押すと疑似全画面に切り替わる)
    const fsBtn = screen.getByRole("button", { name: "全画面" });
    fireEvent.click(fsBtn);
    expect(screen.getByRole("button", { name: "全画面を終了" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "全画面を終了" }));
    expect(screen.getByRole("button", { name: "全画面" }).getAttribute("aria-pressed")).toBe("false");

    // ヘルプダイアログを開閉でき、開いている間は矢印キーでページ送りしない
    const beforeHelp = at();
    fireEvent.click(screen.getByRole("button", { name: "ヘルプ" }));
    expect(screen.getByRole("heading", { name: "ヘルプ" })).toBeTruthy();
    press("ArrowLeft");
    expect(at()).toEqual(beforeHelp);
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(document.querySelector(".novel-help-dialog")?.hasAttribute("open")).toBe(false);

    // 次の巻へボタン
    fireEvent.click(screen.getByRole("button", { name: "次の巻へ" }));
    expect(window.location.hash).toBe("#/read/8");
  });

  it("次巻がオフラインなら次の巻へボタンで理由を出し、ブックマーク更新失敗時は状態を戻す", async () => {
    const html = mockSections()[7];
    const detail: VolumeDetail = {
      id: 7,
      title: "テスト小説",
      format: "epub",
      page_count: html.length,
      pages: [],
      sections: html.map((h, i) => ({ index: i, length: h.length })),
      progress: null,
      bookmarked: false,
      next_volume: { id: 9, title: "オフラインの巻", volume_number: null, offline: true },
    };
    render(<NovelReader id="7" detail={detail} />);
    await ready();
    press("Escape");

    fireEvent.click(screen.getByRole("button", { name: "次の巻へ" }));
    expect(screen.getByRole("status").textContent).toBe("次の巻「オフラインの巻」はオフラインです");

    // fetch を一時的に失敗させてブックマーク変更エラーを確かめる
    vi.stubGlobal("fetch", async () => new Response("error", { status: 500 }));
    const bmBtn = screen.getByRole("button", { name: "ブックマークに追加" });
    fireEvent.click(bmBtn);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("ブックマークを変更できませんでした"));
    expect(screen.getByRole("button", { name: "ブックマークに追加" }).getAttribute("aria-pressed")).toBe("false");
  });
});
