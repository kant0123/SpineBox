// モック API を相手に、フォロー(続刊・欠けの一覧と確認済み)・フォロー管理・作品ページのフォローを確かめる(#87)
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { releasedText } from "./Follows";

let backend: ReturnType<typeof createMockBackend>;

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = "";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("フォロー", () => {
  it("ルートと発売日の文面", () => {
    expect(parseRoute("#/follows")).toEqual({ name: "follows" });
    expect(parseRoute("#/follows/manage")).toEqual({ name: "admin", tab: "follows" });
    expect(releasedText("2015-03")).toBe("2015年3月");
    expect(releasedText("2015")).toBe("2015年");
    expect(releasedText(null)).toBe("発売日不明");
  });

  it("続刊・欠けが新しい発売日順に出て、確認済みにすると消える", async () => {
    window.location.hash = "#/follows";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    const lines = within(card).getAllByRole("listitem").map((li) => li.textContent);
    expect(lines[0]).toContain("続刊");
    expect(lines[0]).toContain("5 巻");
    expect(lines[0]).toContain("2015年3月");
    expect(lines[1]).toContain("欠け");
    expect(lines[1]).toContain("3 巻");
    fireEvent.click(within(card).getByRole("button", { name: "確認済みにする" }));
    await screen.findByText(/新しい続刊・欠けはありません/);
    expect(backend.log.some((e) => e.method === "POST" && e.path === "/api/follows/releases/seen")).toBe(true);
    // 確認済みの巻も表示に切り替えると、確認済みとして出る
    fireEvent.click(screen.getByLabelText("確認済みの巻も表示"));
    const again = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(again).getAllByText("確認済み")).toHaveLength(2);
  });

  it("フォロー管理: 一覧・続刊を調べる・フォローを外す", async () => {
    window.location.hash = "#/follows/manage";
    render(<App />);
    const entry = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(entry).getByText("手元に無い巻 2(未確認 2)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "すべての続刊を調べる" }));
    await screen.findByText("1 作品を調べました。");
    expect(backend.log.some((e) => e.method === "POST" && e.path === "/api/follows/check")).toBe(true);
    fireEvent.click(within(entry).getByRole("button", { name: "フォローを外す" }));
    await screen.findByText("フォロー中の作品はありません。");
  });

  it("作品ページからフォローでき、見つけた巻が出る", async () => {
    window.location.hash = "#/works/2";
    render(<App />);
    const button = await screen.findByRole("button", { name: "フォローする" });
    fireEvent.click(button);
    await screen.findByRole("button", { name: "フォロー中(外す)" });
    await waitFor(() => expect(backend.log.some((e) => e.method === "PUT" && e.path === "/api/works/2/follow")).toBe(true));

    cleanup();
    window.location.hash = "#/works/1";
    render(<App />);
    const panel = await screen.findByRole("group", { name: "フォロー" });
    expect(within(panel).getByRole("button", { name: "フォロー中(外す)" })).toBeTruthy();
    expect(within(panel).getByText("5 巻")).toBeTruthy();
  });

  it("サイドバーからフォローへ行け、フォロー管理は管理のタブとして開く", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const link = await screen.findByRole("link", { name: "フォロー" });
    expect(link.getAttribute("href")).toBe("#/follows");
    window.location.hash = "#/follows/manage";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    const tab = await screen.findByRole("link", { name: "フォロー管理" });
    expect(tab.getAttribute("aria-current")).toBe("page");
    expect(tab.getAttribute("href")).toBe("#/admin/follows");
  });
});
