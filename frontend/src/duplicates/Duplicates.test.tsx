// モック API を相手に、重複候補の画面(2 巻の見比べ・調べる・却下・戻す)と一覧の印を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { DuplicateCandidate } from "../api";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { Duplicates, evidenceText } from "./Duplicates";

let backend: ReturnType<typeof createMockBackend>;

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = "#/volumes";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ルート", () => {
  it("#/duplicates は確認の重複の候補タブを開く(#207)", () => {
    expect(parseRoute("#/duplicates")).toEqual({ name: "review", tab: "duplicates" });
    expect(parseRoute("#/duplicates/")).toEqual({ name: "review", tab: "duplicates" });
  });
});

describe("一覧の印", () => {
  it.skip("重複候補のある巻に印が出て、候補の画面へのリンクになる", async () => {
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第01巻")).closest("li")!;
    const badge = within(card).getByText("重複の候補あり");
    expect(badge.getAttribute("href")).toBe("#/duplicates");
    expect(badge.closest("a")!.parentElement!.closest("a")).toBeNull(); // 巻のリンクの外(読むリンクに飲まれない)
    expect(within(screen.getByText("Sample manga v01").closest("li")!).getByText("重複の候補あり")).toBeTruthy();
    expect(within(screen.getByText("サンプル漫画 第02巻").closest("li")!).queryByText("重複の候補あり")).toBeNull();
    expect(screen.getByRole("link", { name: "重複の候補" }).getAttribute("href")).toBe("#/duplicates");
  });
});

describe("重複候補の画面", () => {
  it("2 巻を並べて表紙・ページ数・巻番号・根拠を見比べられる", async () => {
    render(<Duplicates />);
    const card = (await screen.findByText("サンプル漫画 第01巻")).closest("li")!;
    expect(within(card).getByText("Sample manga v01")).toBeTruthy();
    expect(within(card).getByText("9 ページ")).toBeTruthy();
    expect(within(card).getByText("11 ページ")).toBeTruthy();
    expect(within(card).getByAltText("サンプル漫画 第01巻 の表紙").getAttribute("src")).toBe("/api/volumes/1/pages/0");
    expect(within(card).getByAltText("Sample manga v01 の表紙").getAttribute("src")).toBe("/api/volumes/6/pages/0");
    expect(within(card).getByText("巻番号の候補が同じ(「第01巻」と「v01」)で、見た目の近いページが 8 / 9 ページ。")).toBeTruthy();
    expect(within(card).getAllByRole("link", { name: "この巻を読む" }).map((a) => a.getAttribute("href"))).toEqual(["#/read/1", "#/read/6"]);
    expect(screen.getByText(/何も削除せず、動かしません/)).toBeTruthy();
    expect(backend.log.some((l) => l.method !== "GET" && l.path.startsWith("/api/volumes"))).toBe(false);
  });

  it("根拠の文面は、巻番号が不明なら見た目だけの判定だと言う", () => {
    const c = { rule: "visual_only", matched_pages: 30, compared_pages: 31, evidence: { number_a: null, number_b: "v01" } } as unknown as DuplicateCandidate;
    expect(evidenceText(c)).toContain("見た目だけで判定");
    expect(evidenceText(c)).toContain("30 / 31 ページ");
  });

  it("却下すると消え、却下済みの表示で戻せる。一覧の印も付け外しされる", async () => {
    render(<Duplicates />);
    fireEvent.click(await screen.findByRole("button", { name: "重複ではない" }));
    await waitFor(() => expect(screen.getByText(/重複の候補はありません/)).toBeTruthy());
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/duplicates/1/dismiss")).toBe(true);

    fireEvent.click(screen.getByLabelText("却下したものを表示"));
    fireEvent.click(await screen.findByRole("button", { name: "候補に戻す" }));
    await waitFor(() => expect(screen.getByText("却下した候補はありません。")).toBeTruthy());
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/duplicates/1/restore")).toBe(true);
    fireEvent.click(screen.getByLabelText("却下したものを表示"));
    expect(await screen.findByRole("button", { name: "重複ではない" })).toBeTruthy();
  });

  it("重複を調べると進み具合を取り直し、終わると結果を出す", async () => {
    render(<Duplicates pollMs={50} />);
    await screen.findByText("サンプル漫画 第01巻");
    fireEvent.click(screen.getByRole("button", { name: "重複を調べる" }));
    await waitFor(() => expect(document.querySelector(".notice")?.textContent).toMatch(/調べています/));
    expect((screen.getByRole("button", { name: "調べています…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/新しく指紋を作った巻 2 巻/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重複を調べる" })).toBeTruthy();
    expect(backend.log.filter((l) => l.method === "POST" && l.path === "/api/duplicates/scan")).toHaveLength(1);
  });

  it("読み込めないときはエラー表示で止まる", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 500 }));
    render(<Duplicates />);
    expect((await screen.findByRole("alert")).textContent).toContain("読み込めませんでした");
  });
});
