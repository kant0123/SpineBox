// リーダーの読み込み待ち・取得失敗の表示(#26)。結線の通しは App.test.tsx
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { createMockBackend, type MockOptions } from "../mock/mockBackend";
import { volumeErrorMessage } from "./loadError";
import { Reader, SLOW_HINT_MS } from "./Reader";

let backend: ReturnType<typeof createMockBackend>;

function useBackend(options: MockOptions = {}) {
  backend = createMockBackend(undefined, options);
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    if (r.delayMs) await new Promise((ok) => setTimeout(ok, r.delayMs));
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
}

const backLink = () => screen.getByRole("link", { name: "一覧へ戻る" }).getAttribute("href");

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 812 });
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("巻情報の取得待ち", () => {
  it("取得中は読み込み中と一覧へ戻るリンクを出し、長引くと理由の補足を足す", async () => {
    vi.useFakeTimers();
    useBackend({ firstOpenDelayMs: 5000 });
    render(<Reader id="5" />); // ページ数未確定(入れ子)の巻

    expect(screen.getByRole("status").textContent).toContain("読み込み中");
    expect(backLink()).toBe("#/");
    expect(screen.queryByText(/展開に数秒/)).toBeNull();

    await act(async () => vi.advanceTimersByTime(SLOW_HINT_MS));
    expect(screen.getByText(/展開に数秒/)).toBeTruthy();

    await act(async () => vi.advanceTimersByTime(5000));
    expect(screen.getByRole("img").getAttribute("alt")).toBe("1 ページ");
    expect(screen.queryByText(/展開に数秒/)).toBeNull();
  });

  it("2 回目以降(遅延なし)はそのまま開く", async () => {
    useBackend({ firstOpenDelayMs: 5000 });
    backend.handle("GET", "/api/volumes/5"); // 1 回目は済んだことにする
    render(<Reader id="5" />);
    expect(await screen.findByRole("img")).toBeTruthy();
  });
});

describe("ページ画像の読み込み待ち", () => {
  it("画像が読み終わるまで読み込み中を出し、load で消す", async () => {
    useBackend();
    render(<Reader id="1" />);
    const img = await screen.findByRole("img");
    expect(screen.getByRole("status").textContent).toContain("読み込み中");

    fireEvent.load(img);
    expect(screen.queryByRole("status")).toBeNull();

    // 次のページはまた待つ。読み込めなかったページは待ち続けずに理由を出す
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    const next = screen.getByRole("img");
    expect(next.getAttribute("alt")).toBe("2 ページ");
    expect(screen.getByRole("status").textContent).toContain("読み込み中");
    fireEvent.error(next);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("2 ページ目を読み込めませんでした")).toBeTruthy();

    // 読み終えたページへ戻ったときは出さない
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("取得失敗", () => {
  it.each([
    ["4", "オフライン"], // 409
    ["999", "蔵書から無くなっています"], // 404
    ["too-big", "キャッシュの上限"], // 422 はバックエンドの文言をそのまま
  ])("巻 %s は理由を出し、一覧へ戻れる", async (id, text) => {
    useBackend({ failures: { "too-big": { status: 422, detail: "この巻を読めません: キャッシュの上限より大きい" } } });
    render(<Reader id={id} />);
    expect((await screen.findByRole("alert")).textContent).toContain(text);
    expect(backLink()).toBe("#/");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("サーバーに繋がらないときも一覧へ戻れる", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    render(<Reader id="1" />);
    expect((await screen.findByRole("alert")).textContent).toContain("サーバーに接続");
    expect(backLink()).toBe("#/");
  });
});

describe("volumeErrorMessage", () => {
  it("422 で detail が無い・JSON でない応答は既定の文言にする", async () => {
    expect(volumeErrorMessage(new ApiError("x", 422))).toContain("アーカイブを読めません");
    expect(volumeErrorMessage(new ApiError("x", 500))).toContain("サーバーエラー 500");
  });

  it("api は JSON でないエラー本文でも ApiError にする", async () => {
    vi.stubGlobal("fetch", async () => new Response("Bad Gateway", { status: 502 }));
    const { api } = await import("../api");
    const err = await api.getVolume(1).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.detail).toBeUndefined();
  });
});

describe("先読み済み(complete)の画像でのページ送り(#51)", () => {
  // 実ブラウザでの無限更新(React #185)は jsdom では再現しない。ここでは load が来ない先読み済みの画像が
  // 読み込み済みとして扱われ、ページを送っても表示が続くことを確かめる
  it("load が来なくても読み込み済みとして扱い、見開きで何度ページを送っても表示が続く", async () => {
    // 先読みで読み終わった画像を再現する: 描画した時点で complete・naturalWidth > 0
    const complete = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "complete");
    const natural = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "naturalWidth");
    Object.defineProperty(HTMLImageElement.prototype, "complete", { configurable: true, get: () => true });
    Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", { configurable: true, get: () => 800 });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 768 });
    try {
      useBackend();
      render(<Reader id="1" />);
      await screen.findAllByRole("img");
      for (let k = 0; k < 4; k++) {
        fireEvent.keyDown(window, { key: "ArrowLeft" });
        expect(screen.getAllByRole("img").length).toBeGreaterThan(0);
      }
      expect(screen.queryByRole("status")).toBeNull(); // 読み込み済みとして扱われる
    } finally {
      if (complete) Object.defineProperty(HTMLImageElement.prototype, "complete", complete);
      if (natural) Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", natural);
    }
  });
});
