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

describe("公開DBの候補を続けて選ぶ(#254, #309)", () => {
  type Listed = { id: number; title: string; link_pending: number; links: { source: string; status: string; hold: string | null }[] };
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  /** 確認待ちの取得元が 1 つだけの作品を 2 つ並べ、choose の応答を手で進める */
  async function setup(opts: { fail?: boolean; twoSources?: boolean } = {}) {
    const { ReviewLinks } = await import("./ReviewLinks");
    const listed: Listed = JSON.parse(backend.handle("GET", "/api/review/links").body as string).works[0];
    const link = { ...listed.links.find((l) => l.status === "pending")!, hold: null };
    const links = opts.twoSources ? [link, { ...structuredClone(link), source: "anilist" }] : [link];
    const first = { ...listed, links };
    const second = { ...structuredClone(first), id: 9001, title: "別の漫画" };
    const doneOf = (w: Listed) => ({ ...w, link_pending: 0, links: [] });
    const waiting: { url: string; resolve: () => void }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/review/links")) return json({ works: [first, second] });
      const m = url.match(/\/api\/works\/(\d+)\/links\/\w+\/choose$/);
      if (m) {
        const w = Number(m[1]) === second.id ? second : first;
        await new Promise<void>((resolve) => waiting.push({ url, resolve }));
        return opts.fail ? json({ detail: "取得元が応答しません" }, 500) : json(doneOf(w));
      }
      const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
      return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
    });
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = function (this: Element) { scrolled.push(this); };
    render(<ReviewLinks onChanged={() => {}} />);
    const itemOf = async (title: string) => (await screen.findByRole("heading", { level: 2, name: title })).closest("li")!;
    return { one: await itemOf(first.title), two: await itemOf("別の漫画"), first, waiting, scrolled };
  }
  const adopt = (el: HTMLElement) => within(el).queryAllByRole("button", { name: /を採用/ });

  it("押したらすぐ畳んで次の作品へ進み、反映は裏で 1 件ずつ順に流れる。先に終わった作品の結果を巻き戻さない", async () => {
    const { one, two, first, waiting, scrolled } = await setup();
    fireEvent.click(adopt(one)[0]);
    // 反映を待たずに畳み、候補は押せなくなり、次の作品へスクロールする
    expect(adopt(one)).toEqual([]);
    expect(within(one).getByText(/反映/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("残り 1 作品(反映待ち 1 作品)");
    await waitFor(() => expect(scrolled).toContain(two));
    expect(waiting.length).toBe(1);

    fireEvent.click(adopt(two)[0]);
    expect(adopt(two)).toEqual([]);
    expect(within(two).getByText("反映待ち")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("すべて選びました");
    await act(async () => {});
    expect(waiting.length).toBe(1); // 2 件目は 1 件目が終わるまで送らない
    expect(waiting[0].url).toContain(`/api/works/${first.id}/`);

    await act(async () => waiting[0].resolve());
    await waitFor(() => expect(within(one).getByText("確認済み")).toBeTruthy());
    await waitFor(() => expect(waiting.length).toBe(2));
    await act(async () => waiting[1].resolve());
    await waitFor(() => expect(within(two).getByText("確認済み")).toBeTruthy());
    // 後の反映で先の作品が確認待ちに戻らない
    expect(within(one).getByText("確認済み")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("すべて確認しました。");
  });

  it("取得元が複数あるとき、選んだ取得元は選んだ 1 件だけになり、残りの取得元を選ぶまで畳まない", async () => {
    const { one, waiting, scrolled } = await setup({ twoSources: true });
    const cards = () => within(one).getAllByRole("listitem");
    const total = adopt(one).length;
    const inFirst = adopt(cards()[0]).length;
    expect(inFirst).toBeGreaterThan(1); // 候補が複数ある取得元
    fireEvent.click(adopt(cards()[0])[0]);
    expect(adopt(cards()[0])).toEqual([]);
    expect(within(cards()[0]).queryByRole("button", { name: "どれでもない" })).toBeNull();
    expect(within(cards()[0]).getByText("選択済み(反映待ち)")).toBeTruthy();
    expect(adopt(one).length).toBe(total - inFirst);
    expect(screen.getByRole("status").textContent).toBe("残り 2 作品");
    await act(async () => {});
    expect(scrolled).toEqual([]);

    fireEvent.click(within(cards()[1]).getByRole("button", { name: "どれでもない" }));
    expect(adopt(one)).toEqual([]);
    expect(screen.getByRole("status").textContent).toBe("残り 1 作品(反映待ち 1 作品)");
    await act(async () => {});
    expect(waiting.length).toBe(1); // 同じ作品の 2 つ目も順に送る
  });

  it("反映に失敗した作品は確認待ちへ戻り、理由を出す", async () => {
    const { one, waiting } = await setup({ fail: true });
    const total = adopt(one).length;
    fireEvent.click(adopt(one)[0]);
    expect(adopt(one)).toEqual([]);
    await waitFor(() => expect(waiting.length).toBe(1));
    await act(async () => waiting[0].resolve());
    expect((await within(one).findByRole("alert")).textContent).toBe("取得元が応答しません");
    expect(adopt(one).length).toBe(total);
    expect(screen.getByRole("status").textContent).toBe("残り 2 作品 反映できなかった作品が 1 件あります。");
  });

  it("候補には漫画・小説のタグが付き、採用のボタンは大きい", async () => {
    window.location.hash = "#/review";
    render(<App />);
    const item = (await screen.findByRole("heading", { level: 2, name: "サンプル漫画" })).closest("li")!;
    const ndl = within(item).getByRole("listitem", { name: /国立国会図書館/ });
    expect(within(ndl).getAllByText("漫画", { selector: ".kind-tag" }).length).toBeGreaterThan(0);
    expect(within(ndl).getAllByText("小説", { selector: ".kind-tag" }).length).toBeGreaterThan(0);
    expect(within(ndl).getAllByRole("button", { name: /を採用/ })[0].className).toContain("link-choose");
  });
});
