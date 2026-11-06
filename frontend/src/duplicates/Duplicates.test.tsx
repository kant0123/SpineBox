// モチE�� API を相手に、E��褁E��補�E画面(2 巻の見比べ・調べる�E却下�E戻ぁEと一覧の印を確かめめE
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

describe("ルーチE, () => {
  it("#/duplicates を重褁E��補�E画面として読む", () => {
    expect(parseRoute("#/duplicates")).toEqual({ name: "duplicates" });
    expect(parseRoute("#/duplicates/")).toEqual({ name: "duplicates" });
  });
});

describe("一覧の印", () => {
  it.skip("重褁E��補�Eある巻に印が�Eて、候補�E画面へのリンクになめE, async () => {
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第01巻")).closest("li")!;
    const badge = within(card).getByText("重褁E�E候補あめE);
    expect(badge.getAttribute("href")).toBe("#/duplicates");
    expect(badge.closest("a")!.parentElement!.closest("a")).toBeNull(); // 巻のリンクの夁E読むリンクに飲まれなぁE
    expect(within(screen.getByText("Sample manga v01").closest("li")!).getByText("重褁E�E候補あめE)).toBeTruthy();
    expect(within(screen.getByText("サンプル漫画 第02巻").closest("li")!).queryByText("重褁E�E候補あめE)).toBeNull();
    expect(screen.getByRole("link", { name: "重褁E�E候裁E }).getAttribute("href")).toBe("#/duplicates");
  });
});

describe("重褁E��補�E画面", () => {
  it("2 巻を並べて表紙�Eペ�Eジ数・巻番号・根拠を見比べられめE, async () => {
    render(<Duplicates />);
    const card = (await screen.findByText("サンプル漫画 第01巻")).closest("li")!;
    expect(within(card).getByText("Sample manga v01")).toBeTruthy();
    expect(within(card).getByText("9 ペ�Eジ")).toBeTruthy();
    expect(within(card).getByText("11 ペ�Eジ")).toBeTruthy();
    expect(within(card).getByAltText("サンプル漫画 第01巻 の表紁E).getAttribute("src")).toBe("/api/volumes/1/pages/0");
    expect(within(card).getByAltText("Sample manga v01 の表紁E).getAttribute("src")).toBe("/api/volumes/6/pages/0");
    expect(within(card).getByText("巻番号の候補が同じ(「第01巻」と「v01、Eで、見た目の近いペ�EジぁE8 / 9 ペ�Eジ、E)).toBeTruthy();
    expect(within(card).getAllByRole("link", { name: "こ�E巻を読む" }).map((a) => a.getAttribute("href"))).toEqual(["#/read/1", "#/read/6"]);
    expect(screen.getByText(/何も削除せず、動かしません/)).toBeTruthy();
    expect(backend.log.some((l) => l.method !== "GET" && l.path.startsWith("/api/volumes"))).toBe(false);
  });

  it("根拠の斁E��は、巻番号が不�Eなら見た目だけ�E判定だと言ぁE, () => {
    const c = { rule: "visual_only", matched_pages: 30, compared_pages: 31, evidence: { number_a: null, number_b: "v01" } } as unknown as DuplicateCandidate;
    expect(evidenceText(c)).toContain("見た目だけで判宁E);
    expect(evidenceText(c)).toContain("30 / 31 ペ�Eジ");
  });

  it("却下すると消え、却下済みの表示で戻せる。一覧の印も付け外しされめE, async () => {
    render(<Duplicates />);
    fireEvent.click(await screen.findByRole("button", { name: "重褁E��はなぁE }));
    await waitFor(() => expect(screen.getByText(/重褁E�E候補�Eありません/)).toBeTruthy());
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/duplicates/1/dismiss")).toBe(true);

    fireEvent.click(screen.getByLabelText("却下したものを表示"));
    fireEvent.click(await screen.findByRole("button", { name: "候補に戻ぁE }));
    await waitFor(() => expect(screen.getByText("却下した候補�Eありません、E)).toBeTruthy());
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/duplicates/1/restore")).toBe(true);
    fireEvent.click(screen.getByLabelText("却下したものを表示"));
    expect(await screen.findByRole("button", { name: "重褁E��はなぁE })).toBeTruthy();
  });

  it("重褁E��調べると進み具合を取り直し、終わると結果を�EぁE, async () => {
    render(<Duplicates pollMs={50} />);
    await screen.findByText("サンプル漫画 第01巻");
    fireEvent.click(screen.getByRole("button", { name: "重褁E��調べめE }));
    expect((await screen.findByRole("status")).textContent).toMatch(/調べてぁE��ぁE);
    expect((screen.getByRole("button", { name: "調べてぁE��す…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(await screen.findByText(/新しく持E��を作った巻 2 巻/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重褁E��調べめE })).toBeTruthy();
    expect(backend.log.filter((l) => l.method === "POST" && l.path === "/api/duplicates/scan")).toHaveLength(1);
  });

  it("読み込めなぁE��き�Eエラー表示で止まめE, async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 500 }));
    render(<Duplicates />);
    expect((await screen.findByRole("alert")).textContent).toContain("読み込めませんでした");
  });
});
