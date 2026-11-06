// 画面構�E(#85): サイドバー・蔵書の刁E��替え�EライチEダーク・著老E��どの一覧・管琁E�Eタブ。モチE�� API を相手に通す
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { loadSettings } from "../settings";
import { applyTheme, loadTheme } from "./theme";

// リーダーの manga-viewer は実ブラウザで確かめる外部ライブラリ。ここでは作られたことだけ見る偽物にする
const viewers = vi.hoisted(() => [] as unknown[]);
vi.mock("@tokagemushi/manga-viewer", () => ({
  default: class {
    destroy() {}
    goToPage() {}
    constructor() {
      viewers.push(this);
    }
  },
}));

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
  viewers.length = 0;
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
  it("ホ�Eムは作品一覧で、ナビが揁E��、いまの画面に印が付く", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "ホ�Eム" })).toBeTruthy();
    await waitFor(() => expect(workTitles()).toContain("サンプル漫画"));
    const links = within(nav()).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["ホ�Eム", "フォロー", "漫画", "小説", "著老E, "タグ", "出版社", "ブックマ�Eク", "管琁E]);
    expect(within(nav()).getByRole("link", { name: "ホ�Eム" }).getAttribute("aria-current")).toBe("page");
    // 既存�E画面は近いナビの下に収まめE
    await navigate("#/duplicates");
    expect(within(nav()).getByRole("link", { name: "管琁E }).getAttribute("aria-current")).toBe("page");
    await navigate("#/works/1");
    expect(within(nav()).getByRole("link", { name: "ホ�Eム" }).getAttribute("aria-current")).toBe("page");
  });

  it("リーダーはサイドバーを�Eさず全画面、直リンクも開ける", async () => {
    window.location.hash = "#/read/2";
    render(<App />);
    await waitFor(() => expect(viewers).toHaveLength(1));
    expect(screen.queryByRole("complementary", { name: "サイドバー" })).toBeNull();
    await navigate("#/");
    expect(sidebar()).toBeTruthy();
  });

  it("横長では開閉ボタンで細くでき、記�Eする", async () => {
    const { container } = render(<App />);
    const shell = container.querySelector(".app-shell")!;
    expect(shell.classList.contains("collapsed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "サイドバーを細くすめE }));
    expect(shell.classList.contains("collapsed")).toBe(true);
    expect(localStorage.getItem("spinebox.sidebar")).toBe("collapsed");
    cleanup();
    const again = render(<App />);
    expect(again.container.querySelector(".app-shell")!.classList.contains("collapsed")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "サイドバーを庁E��めE }));
    expect(localStorage.getItem("spinebox.sidebar")).toBeNull();
  });

  it("スマ�E幁E�Eハンバ�Eガーで引き出しを開き、ナビを選ぶ・Esc・背景で閉じめE, async () => {
    const { container } = render(<App />);
    const shell = container.querySelector(".app-shell")!;
    const menu = screen.getByRole("button", { name: "メニューを開ぁE });
    fireEvent.click(menu);
    expect(shell.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("button", { name: "メニューを閉じる" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(within(nav()).getByRole("link", { name: "小説" }));
    expect(shell.classList.contains("drawer-open")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "メニューを開ぁE }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(shell.classList.contains("drawer-open")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "メニューを開ぁE }));
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
    expect(await screen.findByText("こ�E条件の作品はありません、E)).toBeTruthy(); // モチE��に種別が漫画の作品は無ぁE
  });
});

describe("蔵書の刁E��替ぁE, () => {
  it("選んだ蔵書で作品・巻の一覧が絞られ、E��んだも�Eを記�Eする", async () => {
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
    expect(await screen.findByText("外付けドライブ�E巻")).toBeTruthy();
    expect(screen.queryByText("サンプル漫画 第02巻")).toBeNull();
    // 開き直しても覚えてぁE��
    cleanup();
    window.location.hash = "#/";
    render(<App />);
    await waitFor(() => expect(workTitles()).toEqual(["外付けの作品"]));
    fireEvent.change(within(sidebar()).getByLabelText("蔵書"), { target: { value: "" } });
    await waitFor(() => expect(workTitles()).toHaveLength(3));
    expect(localStorage.getItem("spinebox.library")).toBeNull();
  });

  it("設定から外した蔵書を覚えてぁE��ら、すべての蔵書に戻ぁE, async () => {
    localStorage.setItem("spinebox.library", "99");
    render(<App />);
    await waitFor(() => expect(workTitles()).toHaveLength(3));
    expect(localStorage.getItem("spinebox.library")).toBeNull();
    expect((within(sidebar()).getByLabelText("蔵書") as HTMLSelectElement).value).toBe("");
  });
});

describe("ライチEダーク", () => {
  it("既定�E OS に従い、�Eり替えたも�Eを記�Eする", async () => {
    osDark = true;
    render(<App />);
    expect(document.documentElement.dataset.theme).toBeUndefined(); // OS の設定に任せる
    // OS がダークなので、�Eり替え�Eライトへ
    fireEvent.click(within(sidebar()).getByRole("button", { name: "ライトにする" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(loadTheme()).toBe("light");
    fireEvent.click(within(sidebar()).getByRole("button", { name: "ダークにする" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("spinebox.theme")).toBe("dark");
  });

  it("OS に従う間�E、OS の設定�E変化に追従すめE, async () => {
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
    // サイドバーの刁E��替えも追従してぁE��(OS はライチE
    expect(within(sidebar()).getByRole("button", { name: "ダークにする" })).toBeTruthy();
  });
});

describe("著老E�Eタグ・出版社の一覧", () => {
  it("値と作品数を�Eし、E��ぶとそ�E値で絞った作品一覧になめE, async () => {
    window.location.hash = "#/authors";
    render(<App />);
    const link = (await screen.findByText("山田太郁E)).closest("a")!;
    expect(link.textContent).toContain("1 作品");
    expect(link.getAttribute("href")).toBe(`#/authors/${encodeURIComponent("山田太郁E)}`);
    fireEvent.click(link);
    await navigate(link.getAttribute("href")!);
    expect(await screen.findByRole("heading", { name: "著老E 山田太郁E })).toBeTruthy();
    await waitFor(() => expect(workTitles()).toEqual(["サンプル漫画"]));
    expect(backend.log.some((l) => l.path === "/api/works")).toBe(true);
    // 固定した頁E��は絞り込みの選択に出さなぁE
    fireEvent.click(screen.getByRole("button", { name: /絞り込み/ }));
    expect(screen.queryByLabelText("著老E)).toBeNull();
    expect(screen.getByRole("link", { name: /著老E�E一覧へ/ }).getAttribute("href")).toBe("#/authors");
  });

  it("値の名前で絞り込め、無ければ案�Eを�EぁE, async () => {
    window.location.hash = "#/tags";
    render(<App />);
    expect(await screen.findByText(/タグの付いた作品がありません/)).toBeTruthy();
    await navigate("#/authors");
    await screen.findByText("山田太郁E);
    fireEvent.change(screen.getByLabelText("著老E��絞り込む"), { target: { value: "�E�ﾏﾀ�E�E } }); // 正規化しなぁE��合わなぁE��E
    expect(screen.queryByText("山田太郁E)).toBeNull();
    fireEvent.change(screen.getByLabelText("著老E��絞り込む"), { target: { value: "山田" } });
    expect(screen.getByText("山田太郁E)).toBeTruthy();
  });

  it("タグ・著老E�E出版社の頁E��ごとにスターを付け外しでき、スター付きを上部に優先表示する", async () => {
    // 作品にタグと出版社を付けておく
    backend.handle("PATCH", "/api/works/1", JSON.stringify({ tags: ["ファンタジー", "バトル"], publisher: "雁E��社" }));
    backend.handle("PATCH", "/api/works/2", JSON.stringify({ tags: ["ファンタジー", "学圁E], publisher: "講諁E��" }));

    window.location.hash = "#/tags";
    render(<App />);
    await screen.findByText("ファンタジー");
    const listedValues = () => Array.from(document.querySelectorAll(".facet-list .facet-value")).map((e) => e.textContent);
    // 初期状態�E名前頁E バトル(1), ファンタジー(2), 学圁E1)
    expect(listedValues()).toEqual(["バトル", "ファンタジー", "学圁E]);

    // 「学園」にスターを付けると最上部に移動すめE
    const starGakuen = screen.getByRole("button", { name: "学圁Eにスターを付けめE });
    expect(starGakuen.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(starGakuen);
    await waitFor(() => expect(starGakuen.getAttribute("aria-pressed")).toBe("true"));
    expect(listedValues()).toEqual(["学圁E, "バトル", "ファンタジー"]);

    // 作品の多い頁E��変えてもスター付きの「学園、E1 作品)が「ファンタジー、E2 作品)より上に来めE
    fireEvent.change(screen.getByLabelText("並び頁E), { target: { value: "count" } });
    expect(listedValues()).toEqual(["学圁E, "ファンタジー", "バトル"]);

    // スターを外すと允E�E頁E��に戻めE
    fireEvent.click(starGakuen);
    await waitFor(() => expect(starGakuen.getAttribute("aria-pressed")).toBe("false"));
    expect(listedValues()).toEqual(["ファンタジー", "バトル", "学圁E]);

    // 出版社ペ�Eジでもスターを付けられ、別画面から戻っても保持されてぁE��
    await navigate("#/publishers");
    await screen.findByText("講諁E��");
    expect(listedValues()).toEqual(["講諁E��", "雁E��社"]);
    fireEvent.click(screen.getByRole("button", { name: "雁E��社 にスターを付けめE }));
    await waitFor(() => expect(listedValues()).toEqual(["雁E��社", "講諁E��"]));
    await navigate("#/authors");
    await screen.findByText("山田太郁E);
    await navigate("#/publishers");
    await screen.findByText("雁E��社");
    expect(listedValues()).toEqual(["雁E��社", "講諁E��"]);

    // API が失敗したら允E�E状態に戻してエラーを�EぁE
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.endsWith("/star")) return new Response(JSON.stringify({ detail: "失敁E }), { status: 500 });
      const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
      return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
    });
    fireEvent.click(screen.getByRole("button", { name: "講諁E�� にスターを付けめE }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "講諁E�� にスターを付けめE }).getAttribute("aria-pressed")).toBe("false");
  });
});

describe("管琁E, () => {
  it("タブが統計�E好み・チE�Eタ管琁E�Eフォロー管琁E�Eタスクセンターの頁E��並ぶ", async () => {
    window.location.hash = "#/admin";
    render(<App />);
    const tabs = within(screen.getByRole("navigation", { name: "管琁E�EタチE })).getAllByRole("link");
    expect(tabs.map((t) => t.textContent)).toEqual(["統訁E, "好み", "チE�Eタ管琁E, "フォロー管琁E, "タスクセンター"]);
    expect(tabs.map((t) => t.getAttribute("href"))).toEqual([
      "#/admin",
      "#/admin/preferences",
      "#/admin/data",
      "#/admin/follows",
      "#/admin/tasks",
    ]);
    expect(tabs[0].getAttribute("aria-current")).toBe("page");
    await navigate("#/admin/data");
    expect(screen.getByRole("link", { name: "重褁E�E候裁E }).getAttribute("href")).toBe("#/duplicates");
    expect(screen.getByRole("link", { name: "受け入めE }).getAttribute("href")).toBe("#/inbox");
    expect(await screen.findByRole("heading", { name: "整琁E })).toBeTruthy(); // #84 の整琁E
    await navigate("#/admin/tasks");
    expect(await screen.findByRole("heading", { name: "履歴" })).toBeTruthy(); // #83 のタスクセンター
    await navigate("#/admin/follows");
    expect(await screen.findByRole("button", { name: "続�Eを調べめE })).toBeTruthy(); // #87 のフォロー管琁E
  });

  it("統計�E API の数を�Eし、蔵書の刁E��替えに従う", async () => {
    window.location.hash = "#/admin";
    render(<App />);
    const tile = async (label: string) => (await screen.findByText(label)).closest<HTMLElement>(".stat-tile")!;
    expect(within(await tile("作品")).getByText("3")).toBeTruthy();
    expect(within(await tile("巻")).getByText("8")).toBeTruthy();
    expect(within(await tile("オフラインの巻")).getByText("1")).toBeTruthy();
    expect(within(await tile("重褁E�E候裁E)).getByText("1")).toBeTruthy();
    expect(within(await tile("読めなかったもの")).getByText("2")).toBeTruthy();
    expect(within(await tile("展開キャチE��ュ(チE��スク)")).getByText("734 MB")).toBeTruthy();
    expect(screen.getByText("対象: すべての蔵書")).toBeTruthy();
    const select = within(sidebar()).getByLabelText("蔵書") as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    fireEvent.change(select, { target: { value: "2" } });
    expect(await screen.findByText("対象: 漫画")).toBeTruthy();
    expect(within(await tile("作品")).getByText("1")).toBeTruthy();
    expect(within(await tile("巻")).getByText("1")).toBeTruthy();
    expect(backend.log.filter((l) => l.path === "/api/stats")).toHaveLength(2);
  });

  it("統計が取れなければ琁E��を�EぁE, async () => {
    backend = createMockBackend(undefined, { statsFailure: true });
    window.location.hash = "#/admin";
    render(<App />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("好みのリーダーの既定�E端末に記�Eし、サーバ�Eの設定�E読み取りだけで出ぁE, async () => {
    window.location.hash = "#/admin/preferences";
    render(<App />);
    fireEvent.click(screen.getByLabelText(/表紙を単独で表示する/));
    expect(loadSettings()).toEqual({ coverAlone: false });
    fireEvent.change(screen.getByLabelText("小説の斁E��サイズ"), { target: { value: "24" } });
    expect(JSON.parse(localStorage.getItem("spinebox.novel")!)).toEqual({ fontSize: 24 });
    expect(await screen.findByText("出さなぁEhide)")).toBeTruthy();
    expect(screen.getByText("2.0 GB")).toBeTruthy();
    expect(backend.log.filter((l) => l.method !== "GET")).toEqual([]); // サーバ�Eの設定�E変えなぁE
  });

  it("スタイル定義で画面全体�E .page に pointer-events: none が適用されてぁE��ぁE#99)", async () => {
    const fs = await import("fs");
    const path = await import("path");
    const css = fs.readFileSync(path.resolve(__dirname, "../styles.css"), "utf-8");
    const matches = Array.from(css.matchAll(/(^|\n)([^\n{]+)\{[^}]*pointer-events:\s*none[^}]*\}/g));
    for (const [, , selector] of matches) {
      const parts = selector.split(",").map((s) => s.trim());
      for (const part of parts) {
        if (/(^|[\s>+~])\.page($|[\s>+~])/.test(part)) {
          expect(part).toContain(".stage");
        }
      }
    }
  });
});
