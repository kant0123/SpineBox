// 確認(#207): 公開 DB の候補・統合の提案・重複の候補を 1 か所で決める
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";

let backend: ReturnType<typeof createMockBackend>;

async function navigate(hash: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

const tabs = () => within(screen.getByRole("navigation", { name: "確認のタブ" })).getAllByRole("link");
const reviewNav = () =>
  within(within(screen.getByRole("complementary", { name: "サイドバー" })).getByRole("navigation", { name: "メイン" }))
    .getByRole("link", { name: /^確認/ });

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = "";
  localStorage.clear();
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {},
  }));
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
  it("#/review のタブと、旧 URL の振り替え", () => {
    expect(parseRoute("#/review")).toEqual({ name: "review", tab: "links" });
    expect(parseRoute("#/review/proposals")).toEqual({ name: "review", tab: "proposals" });
    expect(parseRoute("#/review/duplicates/")).toEqual({ name: "review", tab: "duplicates" });
    expect(parseRoute("#/review/unknown")).toEqual({ name: "review", tab: "links" });
    expect(parseRoute("#/admin/proposals")).toEqual({ name: "review", tab: "proposals" });
    expect(parseRoute("#/works/proposals")).toEqual({ name: "review", tab: "proposals" });
    expect(parseRoute("#/duplicates")).toEqual({ name: "review", tab: "duplicates" });
  });
});

describe("確認", () => {
  it("公開DBの候補を作品ページを開かずに選ぶと、確認済みに畳まれ件数が減る", async () => {
    window.location.hash = "#/review";
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "確認" })).toBeTruthy();
    expect(tabs().map((t) => t.getAttribute("href"))).toEqual(["#/review", "#/review/proposals", "#/review/duplicates"]);
    expect(tabs()[0].getAttribute("aria-current")).toBe("page");
    expect(reviewNav().getAttribute("aria-current")).toBe("page");

    const item = (await screen.findByRole("heading", { level: 2, name: "サンプル漫画" })).closest("li")!;
    expect(screen.getByRole("status").textContent).toBe("残り 1 作品");
    await waitFor(() => expect(within(tabs()[0]).getByText("1")).toBeTruthy());
    const before = Number(reviewNav().querySelector(".count-badge")!.firstChild!.textContent);

    // 漫画の候補を採用する(同じ名前の小説と見分けられる名前のボタン)
    const ndl = within(item).getByRole("listitem", { name: /国立国会図書館/ });
    fireEvent.click(within(ndl).getAllByRole("button", { name: /漫画.*を採用/ })[0]);
    await waitFor(() => expect(within(item).getByText("確認済み")).toBeTruthy());
    expect(within(item).queryByRole("button", { name: /を採用/ })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("すべて確認しました。");
    // 押したボタンが消えるので、フォーカスは畳んだ行の見出しへ移る(body に落とさない)
    await waitFor(() => expect(document.activeElement?.textContent).toBe("サンプル漫画"));
    // 一覧は詰めない(押し間違い防止)。件数はその場で減る
    expect(screen.getByRole("heading", { level: 2, name: "サンプル漫画" })).toBeTruthy();
    await waitFor(() => expect(within(tabs()[0]).queryByText("1")).toBeNull());
    await waitFor(() => expect(Number(reviewNav().querySelector(".count-badge")?.firstChild?.textContent ?? 0)).toBe(before - 1));
  });

  it("作品ページに確認待ちがあれば、確認の画面へのリンクを出す(#210 で上部の案内にまとめた)", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    const link = await screen.findByRole("link", { name: "確認 > 公開DBの候補" });
    expect(link.getAttribute("href")).toBe("#/review");
  });

  it("統合の提案・重複の候補もタブで開ける(旧 URL から)", async () => {
    window.location.hash = "#/admin/proposals";
    render(<App />);
    expect(await screen.findByRole("button", { name: "同じ作品として統合" })).toBeTruthy();
    expect(tabs()[1].getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "別の作品" }));
    expect(await screen.findByText("確認待ちの提案はありません。")).toBeTruthy();
    await waitFor(() => expect(tabs()[1].querySelector(".count-badge")).toBeNull());

    await navigate("#/duplicates");
    expect(tabs()[2].getAttribute("aria-current")).toBe("page");
    // タブの中では「重複の候補」の見出しと「一覧へ戻る」を出さない
    expect(await screen.findByRole("button", { name: "重複を調べる" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "一覧へ戻る" })).toBeNull();
  });
});
