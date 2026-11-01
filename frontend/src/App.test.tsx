// モック API を相手に、一覧 → リーダー → ページ送り → 位置保存 → 再開の流れを通す
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBackend } from "./mock/mockBackend";
import { readHref } from "./route";

let backend: ReturnType<typeof createMockBackend>;

function setViewport(w: number, h: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
}

async function navigate(hash: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

const press = (key: string) => fireEvent.keyDown(window, { key });
const shownPages = () => screen.queryAllByRole("img").map((i) => i.getAttribute("alt"));
const puts = () => backend.log.filter((l) => l.method === "PUT").map((l) => ({ path: l.path, body: l.body }));

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = "";
  localStorage.clear();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("一覧", () => {
  it("巻・ページ数・読書状態・オフラインを出し、スキャンできる", async () => {
    setViewport(375, 812);
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    expect(within(card).getByText("8 ページ")).toBeTruthy();
    expect(within(card).getByText("4 / 8 ページ")).toBeTruthy();
    expect(within(screen.getByText("読了済みの巻").closest("li")!).getByText("既読")).toBeTruthy();
    expect(screen.getByText("ページ数未確定")).toBeTruthy();

    const offline = screen.getByText("外付けドライブの巻").closest("li")!;
    expect(within(offline).getByText("オフライン")).toBeTruthy();
    expect(offline.querySelector("a")).toBeNull(); // 開けない巻はリンクにしない

    fireEvent.click(screen.getByRole("button", { name: "スキャン" }));
    expect(await screen.findByText(/6 巻を検出しました/)).toBeTruthy();
    expect(screen.getByText(/E:\/漫画/)).toBeTruthy();
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/scan")).toBe(true);
  });

  it("未解析の内側アーカイブを調べると子の巻に分かれ、食い違いのバッジと説明が出る", async () => {
    setViewport(375, 812);
    render(<App />);
    const parent = (await screen.findByText("v01-03")).closest("li")!;
    expect(screen.queryByText("巻の食い違い")).toBeNull();
    fireEvent.click(within(parent).getByRole("button", { name: "中の巻を調べる" }));

    const child = (await screen.findByText("v01")).closest("li")!;
    expect(screen.queryByText("v01-03")).toBeNull(); // 親(まとめ)は一覧から消え、子が並ぶ
    expect(screen.getByText("v03")).toBeTruthy();
    expect(within(child).getByText("「v01-03」の中")).toBeTruthy();
    expect(within(child).getByText("巻の食い違い")).toBeTruthy();
    expect(screen.getAllByText("巻の食い違い")).toHaveLength(2);
    // 説明はリンクの外(開いてもリーダーへ飛ばない)
    const details = within(child).getByText("食い違いの説明").closest("details")!;
    expect(details.closest("a")).toBeNull();
    expect(within(details).getByText(/2 巻が見当たらない/)).toBeTruthy();
    expect(within(details).getByText("1〜3 巻(「v01-03」の「v01-03」)")).toBeTruthy();
    expect(within(details).getByText("1・3")).toBeTruthy();
    expect(within(details).getByText("欠け").nextElementSibling!.textContent).toBe("2");
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/volumes/5/analyze")).toBe(true);
  });

  it("API が落ちていてもエラー表示で止まる", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 500 }));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("読み込めませんでした");
  });
});

describe("リーダー(スマホ縦 375px)", () => {
  beforeEach(() => setViewport(375, 812));

  it("保存位置から再開し、最終ページまで読むと既読で保存され、一覧に反映される", async () => {
    render(<App />);
    await screen.findByText("サンプル漫画 第02巻");
    expect(screen.getByText("サンプル漫画 第02巻").closest("a")!.getAttribute("href")).toBe(readHref(2));
    await navigate(readHref(2));

    // progress.page = 3 (0 始まり) から開く。スマホ縦は単ページ
    await waitFor(() => expect(shownPages()).toEqual(["4 ページ"]));

    press("ArrowLeft"); // 右綴じ: ← が次
    expect(shownPages()).toEqual(["5 ページ"]);
    await waitFor(() => expect(puts()).toEqual([{ path: "/api/volumes/2/progress", body: { page: 4, finished: false } }]), {
      timeout: 2000,
    });

    press("ArrowRight"); // → が前
    expect(shownPages()).toEqual(["4 ページ"]);
    for (let i = 0; i < 4; i++) press("ArrowLeft");
    expect(shownPages()).toEqual(["8 ページ"]);
    press("ArrowLeft");
    expect(await screen.findByText("最後のページです")).toBeTruthy();

    // 待たずに一覧へ戻っても最後の位置が送られる
    await navigate("#/");
    expect(puts().at(-1)).toEqual({ path: "/api/volumes/2/progress", body: { page: 7, finished: true } });
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    await waitFor(() => expect(within(card).getByText("既読")).toBeTruthy());

    // 再開すると最終ページから。前へ戻っても既読のまま
    await navigate(readHref(2));
    await waitFor(() => expect(shownPages()).toEqual(["8 ページ"]));
    press("ArrowRight");
    await navigate("#/");
    expect(puts().at(-1)?.body).toEqual({ page: 6, finished: true });
  });

  it("開けない巻はメッセージを出す", async () => {
    window.location.hash = readHref(4);
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("開けませんでした");
  });

  it("中央でメニューが開き、ページ数と一覧へのリンクが出る", async () => {
    window.location.hash = readHref(1);
    render(<App />);
    await waitFor(() => expect(shownPages()).toEqual(["1 ページ"]));
    press("Escape");
    expect(screen.getByTestId("page-label").textContent).toBe("1 / 9");
    expect(screen.getByRole("link", { name: /一覧へ/ }).getAttribute("href")).toBe("#/");
  });
});

describe("リーダー(横長画面の見開き)", () => {
  beforeEach(() => setViewport(1024, 768));

  it("表紙だけ単独で、以降は 2 ページずつ。表紙単独を切ると組み直す", async () => {
    window.location.hash = readHref(1); // 9 ページ・未読
    render(<App />);
    await waitFor(() => expect(shownPages()).toEqual(["1 ページ"]));

    press("ArrowLeft");
    // DOM は若い順。CSS の row-reverse で 2 ページ目が右に来る
    expect(shownPages()).toEqual(["2 ページ", "3 ページ"]);
    expect(screen.getByTestId("stage").className).toContain("is-spread");

    press("Escape");
    fireEvent.click(screen.getByLabelText("表紙を単独で表示"));
    expect(shownPages()).toEqual(["1 ページ", "2 ページ"]);
    expect(JSON.parse(localStorage.getItem("spinebox.reader")!)).toMatchObject({ coverAlone: false });

    fireEvent.click(screen.getByLabelText("横長画面で見開き"));
    expect(shownPages()).toEqual(["2 ページ"]);
  });
});
