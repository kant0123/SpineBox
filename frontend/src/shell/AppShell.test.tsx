// 画面構成(#85): サイドバー・蔵書の切り替え・ライト/ダーク・著者などの一覧・管理のタブ。モック API を相手に通す
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { loadSettings } from "../settings";
import { applyTheme, loadTheme } from "./theme";

let backend: ReturnType<typeof createMockBackend>;
let darkListeners: ((e: { matches: boolean }) => void)[] = [];
let osDark = false;

function stubMatchMedia() {
  darkListeners = [];
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return query.includes("dark") ? osDark : false;
    },
    media: query,
    addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => darkListeners.push(fn),
    removeEventListener: (_: string, fn: (e: { matches: boolean }) => void) => {
      darkListeners = darkListeners.filter((f) => f !== fn);
    },
  }));
}

async function navigate(hash: string) {
  await act(async () => {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
}

const sidebar = () => screen.getByRole("complementary", { name: "サイドバー" });
const nav = () => within(sidebar()).getByRole("navigation", { name: "メイン" });
const workTitles = () => Array.from(document.querySelectorAll(".work-card .volume-title")).map((e) => e.textContent);

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = "";
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  osDark = false;
  stubMatchMedia();
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("サイドバー", () => {
  it("ホームは作品一覧で、ナビが揃い、いまの画面に印が付く", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "ホーム" })).toBeTruthy();
    await waitFor(() => expect(workTitles()).toContain("サンプル漫画"));
    const links = within(nav()).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["ホーム", "フォロー", "漫画", "小説", "著者", "タグ", "出版社", "ブックマーク", "管理"]);
    expect(within(nav()).getByRole("link", { name: "ホーム" }).getAttribute("aria-current")).toBe("page");
    // 既存の画面は近いナビの下に収まる
    await navigate("#/duplicates");
    expect(within(nav()).getByRole("link", { name: "管理" }).getAttribute("aria-current")).toBe("page");
    await navigate("#/works/1");
    expect(within(nav()).getByRole("link", { name: "ホーム" }).getAttribute("aria-current")).toBe("page");
  });

  it("リーダーはサイドバーを出さず全画面、直リンクも開ける", async () => {
    window.location.hash = "#/read/2";
    render(<App />);
    await screen.findByRole("img", { name: "4 ページ" });
    expect(screen.queryByRole("complementary", { name: "サイドバー" })).toBeNull();
    await navigate("#/");
    expect(sidebar()).toBeTruthy();
  });

  it("横長では開閉ボタンで細くでき、記憶する", async () => {
    const { container } = render(<App />);
    const shell = container.querySelector(".app-shell")!;
    expect(shell.classList.contains("collapsed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "サイドバーを細くする" }));
    expect(shell.classList.contains("collapsed")).toBe(true);
    expect(localStorage.getItem("spinebox.sidebar")).toBe("collapsed");
    cleanup();
    const again = render(<App />);
    expect(again.container.querySelector(".app-shell")!.classList.contains("collapsed")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "サイドバーを広げる" }));
    expect(localStorage.getItem("spinebox.sidebar")).toBeNull();
  });

  it("スマホ幅のハンバーガーで引き出しを開き、ナビを選ぶ・Esc・背景で閉じる", async () => {
    const { container } = render(<App />);
    const shell = container.querySelector(".app-shell")!;
    const menu = screen.getByRole("button", { name: "メニューを開く" });
    fireEvent.click(menu);
    expect(shell.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("button", { name: "メニューを閉じる" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(within(nav()).getByRole("link", { name: "小説" }));
    expect(shell.classList.contains("drawer-open")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "メニューを開く" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(shell.classList.contains("drawer-open")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "メニューを開く" }));
    fireEvent.click(container.querySelector(".drawer-backdrop")!);
    expect(shell.classList.contains("drawer-open")).toBe(false);
  });

  it("漫画・小説は種別で絞った作品一覧", async () => {
    window.location.hash = "#/novels";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "小説" })).toBeTruthy();
    await waitFor(() => expect(workTitles()).toEqual(["別の読み物"]));
    expect(backend.log.some((l) => l.path === "/api/works")).toBe(true);
    await navigate("#/manga");
    expect(await screen.findByRole("heading", { name: "漫画" })).toBeTruthy();
    expect(await screen.findByText("この条件の作品はありません。")).toBeTruthy(); // モックに種別が漫画の作品は無い
  });
});

describe("蔵書の切り替え", () => {
  it("選んだ蔵書で作品・巻の一覧が絞られ、選んだものを記憶する", async () => {
    render(<App />);
    await waitFor(() => expect(workTitles()).toEqual(["サンプル漫画", "外付けの作品", "別の読み物"]));
    const select = within(sidebar()).getByLabelText("蔵書") as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(["すべての蔵書", "本(7 巻)", "漫画(オフライン)(1 巻)"]);
    fireEvent.change(select, { target: { value: "2" } });
    await waitFor(() => expect(workTitles()).toEqual(["外付けの作品"]));
    expect(localStorage.getItem("spinebox.library")).toBe("2");
    // 巻の一覧も同じ蔵書で絞る
    await navigate("#/volumes");
    expect(await screen.findByText("外付けドライブの巻")).toBeTruthy();
    expect(screen.queryByText("サンプル漫画 第02巻")).toBeNull();
    // 開き直しても覚えている
    cleanup();
    window.location.hash = "#/";
    render(<App />);
    await waitFor(() => expect(workTitles()).toEqual(["外付けの作品"]));
    fireEvent.change(within(sidebar()).getByLabelText("蔵書"), { target: { value: "" } });
    await waitFor(() => expect(workTitles()).toHaveLength(3));
    expect(localStorage.getItem("spinebox.library")).toBeNull();
  });

  it("設定から外した蔵書を覚えていたら、すべての蔵書に戻す", async () => {
    localStorage.setItem("spinebox.library", "99");
    render(<App />);
    await waitFor(() => expect(workTitles()).toHaveLength(3));
    expect(localStorage.getItem("spinebox.library")).toBeNull();
    expect((within(sidebar()).getByLabelText("蔵書") as HTMLSelectElement).value).toBe("");
  });
});

describe("ライト/ダーク", () => {
  it("既定は OS に従い、切り替えたものを記憶する", async () => {
    osDark = true;
    render(<App />);
    expect(document.documentElement.dataset.theme).toBeUndefined(); // OS の設定に任せる
    // OS がダークなので、切り替えはライトへ
    fireEvent.click(within(sidebar()).getByRole("button", { name: "ライトにする" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(loadTheme()).toBe("light");
    fireEvent.click(within(sidebar()).getByRole("button", { name: "ダークにする" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("spinebox.theme")).toBe("dark");
  });

  it("OS に従う間は、OS の設定の変化に追従する", async () => {
    render(<App />);
    expect(within(sidebar()).getByRole("button", { name: "ダークにする" })).toBeTruthy();
    osDark = true;
    act(() => darkListeners.forEach((f) => f({ matches: true })));
    expect(within(sidebar()).getByRole("button", { name: "ライトにする" })).toBeTruthy();
  });

  it("好みタブで OS に従うへ戻せる", async () => {
    localStorage.setItem("spinebox.theme", "dark");
    applyTheme(loadTheme());
    window.location.hash = "#/admin/preferences";
    render(<App />);
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByLabelText("OS の設定に従う"));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("spinebox.theme")).toBeNull();
    // サイドバーの切り替えも追従している(OS はライト)
    expect(within(sidebar()).getByRole("button", { name: "ダークにする" })).toBeTruthy();
  });
});

describe("著者・タグ・出版社の一覧", () => {
  it("値と作品数を出し、選ぶとその値で絞った作品一覧になる", async () => {
    window.location.hash = "#/authors";
    render(<App />);
    const link = (await screen.findByText("山田太郎")).closest("a")!;
    expect(link.textContent).toContain("1 作品");
    expect(link.getAttribute("href")).toBe(`#/authors/${encodeURIComponent("山田太郎")}`);
    fireEvent.click(link);
    await navigate(link.getAttribute("href")!);
    expect(await screen.findByRole("heading", { name: "著者: 山田太郎" })).toBeTruthy();
    await waitFor(() => expect(workTitles()).toEqual(["サンプル漫画"]));
    expect(backend.log.some((l) => l.path === "/api/works")).toBe(true);
    // 固定した項目は絞り込みの選択に出さない
    fireEvent.click(screen.getByRole("button", { name: /絞り込み/ }));
    expect(screen.queryByLabelText("著者")).toBeNull();
    expect(screen.getByRole("link", { name: /著者の一覧へ/ }).getAttribute("href")).toBe("#/authors");
  });

  it("値の名前で絞り込め、無ければ案内を出す", async () => {
    window.location.hash = "#/tags";
    render(<App />);
    expect(await screen.findByText(/タグの付いた作品がありません/)).toBeTruthy();
    await navigate("#/authors");
    await screen.findByText("山田太郎");
    fireEvent.change(screen.getByLabelText("著者を絞り込む"), { target: { value: "ﾔﾏﾀﾞ" } }); // 正規化しないと合わない語
    expect(screen.queryByText("山田太郎")).toBeNull();
    fireEvent.change(screen.getByLabelText("著者を絞り込む"), { target: { value: "山田" } });
    expect(screen.getByText("山田太郎")).toBeTruthy();
  });
});

describe("管理", () => {
  it("タブが統計・好み・データ管理・フォロー管理・タスクセンターの順に並ぶ", async () => {
    window.location.hash = "#/admin";
    render(<App />);
    const tabs = within(screen.getByRole("navigation", { name: "管理のタブ" })).getAllByRole("link");
    expect(tabs.map((t) => t.textContent)).toEqual(["統計", "好み", "データ管理", "フォロー管理", "タスクセンター"]);
    expect(tabs[0].getAttribute("aria-current")).toBe("page");
    await navigate("#/admin/data");
    expect(screen.getByRole("link", { name: "重複の候補" }).getAttribute("href")).toBe("#/duplicates");
    expect(screen.getByRole("link", { name: "受け入れ" }).getAttribute("href")).toBe("#/inbox");
    expect(await screen.findByRole("heading", { name: "ファイル名・フォルダーの整理" })).toBeTruthy(); // #84 の整理
    await navigate("#/admin/tasks");
    expect(screen.getByText(/準備中です.#83./)).toBeTruthy();
    await navigate("#/admin/follows");
    expect(await screen.findByRole("button", { name: "続刊を調べる" })).toBeTruthy(); // #87 のフォロー管理
  });

  it("統計は API の数を出し、蔵書の切り替えに従う", async () => {
    window.location.hash = "#/admin";
    render(<App />);
    const tile = async (label: string) => (await screen.findByText(label)).closest<HTMLElement>(".stat-tile")!;
    expect(within(await tile("作品")).getByText("3")).toBeTruthy();
    expect(within(await tile("巻")).getByText("8")).toBeTruthy();
    expect(within(await tile("オフラインの巻")).getByText("1")).toBeTruthy();
    expect(within(await tile("重複の候補")).getByText("1")).toBeTruthy();
    expect(within(await tile("読めなかったもの")).getByText("2")).toBeTruthy();
    expect(within(await tile("展開キャッシュ(ディスク)")).getByText("734 MB")).toBeTruthy();
    expect(screen.getByText("対象: すべての蔵書")).toBeTruthy();
    const select = within(sidebar()).getByLabelText("蔵書") as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    fireEvent.change(select, { target: { value: "2" } });
    expect(await screen.findByText("対象: 漫画")).toBeTruthy();
    expect(within(await tile("作品")).getByText("1")).toBeTruthy();
    expect(within(await tile("巻")).getByText("1")).toBeTruthy();
    expect(backend.log.filter((l) => l.path === "/api/stats")).toHaveLength(2);
  });

  it("統計が取れなければ理由を出す", async () => {
    backend = createMockBackend(undefined, { statsFailure: true });
    window.location.hash = "#/admin";
    render(<App />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("好みのリーダーの既定は端末に記憶し、サーバーの設定は読み取りだけで出す", async () => {
    window.location.hash = "#/admin/preferences";
    render(<App />);
    fireEvent.click(screen.getByLabelText("いつも 1 ページ"));
    fireEvent.click(screen.getByLabelText(/表紙を単独で表示する/));
    expect(loadSettings()).toEqual({ coverAlone: false, spreadMode: "single" });
    fireEvent.change(screen.getByLabelText("小説の文字サイズ"), { target: { value: "24" } });
    expect(JSON.parse(localStorage.getItem("spinebox.novel")!)).toEqual({ fontSize: 24 });
    expect(await screen.findByText("出さない(hide)")).toBeTruthy();
    expect(screen.getByText("2.0 GB")).toBeTruthy();
    expect(backend.log.filter((l) => l.method !== "GET")).toEqual([]); // サーバーの設定は変えない
  });
});
