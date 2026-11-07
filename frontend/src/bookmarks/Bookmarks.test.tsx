// モック API を相手に、ブックマーク(#86)の付け外し(巻の一覧・作品のページ。リーダーのボタンは reader/Reader.test.tsx)と一覧を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { WorkPage } from "../works/WorkPage";
import { Bookmarks } from "./Bookmarks";

let backend: ReturnType<typeof createMockBackend>;
let failPut = false;

beforeEach(() => {
  backend = createMockBackend();
  failPut = false;
  window.location.hash = "";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (failPut && init?.method === "PUT" && url.startsWith("/api/bookmarks")) return new Response("{}", { status: 500 });
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const titles = () => screen.getAllByRole("listitem").map((li) => li.querySelector(".volume-title")!.textContent);

describe("ルート", () => {
  it("#/bookmarks をブックマークの画面として読む", () => {
    expect(parseRoute("#/bookmarks")).toEqual({ name: "bookmarks" });
    expect(parseRoute("#/bookmarks/")).toEqual({ name: "bookmarks" });
  });
});

describe("付け外し", () => {
  it.skip("巻の一覧で付けた巻が、ブックマークの一覧に出る", async () => {
    window.location.hash = "#/volumes";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    const button = within(card).getByRole("button", { name: "サンプル漫画 第02巻 をブックマーク" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
    await waitFor(() => expect(button.getAttribute("aria-pressed")).toBe("true"));
    expect(backend.log.at(-1)).toMatchObject({ method: "PUT", path: "/api/bookmarks/volumes/2" });
    expect(screen.getByRole("link", { name: /ブックマーク/ }).getAttribute("href")).toBe("#/bookmarks");

    cleanup();
    render(<Bookmarks />);
    const row = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    expect(within(row).getByText("サンプル漫画")).toBeTruthy(); // 巻の作品名
    expect(within(row).getByText("巻")).toBeTruthy();
    expect(row.querySelector("a.bookmark-main")!.getAttribute("href")).toBe("#/read/2");
  });

  it("作品のページで作品に付け、巻の行でも付けられる", async () => {
    render(<WorkPage id="1" />);
    const workButton = await screen.findByRole("button", { name: "サンプル漫画 をブックマーク" });
    fireEvent.click(workButton);
    await waitFor(() => expect(workButton.getAttribute("aria-pressed")).toBe("true"));
    const volButton = screen.getByRole("button", { name: "サンプル漫画 第01巻 をブックマーク" });
    fireEvent.click(volButton);
    await waitFor(() => expect(volButton.getAttribute("aria-pressed")).toBe("true"));

    cleanup();
    render(<WorkPage id="1" />); // 開き直しても付いたまま
    expect((await screen.findByRole("button", { name: "サンプル漫画 をブックマーク" })).getAttribute("aria-pressed")).toBe("true");
  });

  it.skip("失敗したら表示を戻して理由を出す", async () => {
    failPut = true;
    window.location.hash = "#/volumes";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    const button = within(card).getByRole("button", { name: "サンプル漫画 第02巻 をブックマーク" });
    fireEvent.click(button);
    expect(await within(card).findByRole("alert")).toBeTruthy();
    expect(button.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("一覧", () => {
  it("付けた順(新しい順)と作品名順で並べ替えられ、オフラインの巻も出る", async () => {
    backend.handle("PUT", "/api/bookmarks/volumes/4"); // 外付けドライブの巻(オフライン)
    backend.handle("PUT", "/api/bookmarks/works/1");
    backend.handle("PUT", "/api/bookmarks/volumes/3");
    render(<Bookmarks />);
    await screen.findByText("読了済みの巻");
    expect(titles()).toEqual(["読了済みの巻", "サンプル漫画", "外付けドライブの巻"]);
    const offline = screen.getByText("外付けドライブの巻").closest("li")!;
    expect(within(offline).getByText("オフライン")).toBeTruthy();
    expect(offline.querySelector("a.bookmark-main")).toBeNull(); // 開けないのでリンクにしない

    fireEvent.click(screen.getByRole("button", { name: "作品名順" }));
    // 作品名で並べ、同じ作品(サンプル漫画)の巻は作品のすぐ後ろ
    await waitFor(() => expect(titles()).toEqual(["サンプル漫画", "読了済みの巻", "外付けドライブの巻"]));
    expect(backend.log.at(-1)).toMatchObject({ method: "GET", path: "/api/bookmarks" });
  });

  it("外した行は開き直すまで残り、付け直せる", async () => {
    backend.handle("PUT", "/api/bookmarks/volumes/3");
    render(<Bookmarks />);
    const button = await screen.findByRole("button", { name: "読了済みの巻 をブックマーク" });
    fireEvent.click(button);
    await waitFor(() => expect(button.getAttribute("aria-pressed")).toBe("false"));
    expect(screen.getByText("読了済みの巻")).toBeTruthy();
    cleanup();
    render(<Bookmarks />);
    expect(await screen.findByText(/ブックマークはまだありません/)).toBeTruthy();
  });
});
