// リーダーの読み込み待ち・取得失敗の表示(#26)と、漫画リーダー(manga-viewer)への結線。結線の通しは App.test.tsx
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HeaderButton, MangaViewerOptions } from "@tokagemushi/manga-viewer";
import { ApiError } from "../api";
import { createMockBackend, defaultVolumes, type MockOptions } from "../mock/mockBackend";
import { saveSettings } from "../settings";
import { volumeErrorMessage } from "./loadError";
import { progressKey } from "./MangaReader";
import { Reader, SLOW_HINT_MS } from "./Reader";

// manga-viewer は Shadow DOM に実描画する外部ライブラリ。ここでは渡した設定の記録と、
// ヘッダーのボタン・destroy・goToPage だけを持つ偽物に差し替える(実物の挙動は実ブラウザで確かめる)
const fake = vi.hoisted(() => ({
  instances: [] as any[],
  /** 構築中に呼ぶ処理(実物が構築中に onPageChange を呼ぶ・確認を出す、の再現用) */
  duringConstruct: null as null | ((opts: any, root: ShadowRoot) => void),
}));
vi.mock("@tokagemushi/manga-viewer", () => ({
  default: class {
    opts: any;
    destroy = vi.fn();
    goToPage = vi.fn();
    // 最後の画面にいる 1 画面の巻として振る舞う(最後からさらに進む操作の結線 #324 用)
    _slots = [0];
    _currentSlotIndex = 0;
    _goNext() {}
    _navigateTo(_idx: number) {}
    constructor(opts: any) {
      this.opts = opts;
      const root = (opts.container as HTMLElement).attachShadow({ mode: "open" });
      for (const b of opts.headerButtons as (string | HeaderButton)[]) {
        if (typeof b === "string") continue;
        const btn = document.createElement("button");
        btn.className = `mv-header-btn ${b.className ?? ""}`;
        btn.title = b.label;
        btn.addEventListener("click", (e) => b.onClick(e, this as never));
        root.appendChild(btn);
      }
      fake.duringConstruct?.(opts, root);
      fake.instances.push(this);
    }
  },
}));

let backend: ReturnType<typeof createMockBackend>;

function useBackend(options: MockOptions = {}, volumes?: ReturnType<typeof defaultVolumes>) {
  backend = createMockBackend(volumes, options);
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
  window.location.hash = "";
  localStorage.clear();
  fake.instances.length = 0;
  fake.duringConstruct = null;
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

    expect(screen.getByText("読み込み中…")).toBeTruthy();
    expect(backLink()).toBe("#/");
    expect(screen.queryByText(/展開に数秒/)).toBeNull();

    await act(async () => vi.advanceTimersByTime(SLOW_HINT_MS));
    expect(screen.getByText(/展開に数秒/)).toBeTruthy();

    await act(async () => vi.advanceTimersByTime(5000));
    expect(fake.instances).toHaveLength(1);
    expect(screen.queryByText(/展開に数秒/)).toBeNull();
  });

  it("2 回目以降(遅延なし)はそのまま開く", async () => {
    useBackend({ firstOpenDelayMs: 5000 });
    backend.handle("GET", "/api/volumes/5"); // 1 回目は済んだことにする
    render(<Reader id="5" />);
    await waitFor(() => expect(fake.instances).toHaveLength(1));
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
    expect(fake.instances).toHaveLength(0);
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

// ---- 漫画リーダー(manga-viewer)への結線 ----

const opened = async (id: string, options?: MockOptions, volumes?: ReturnType<typeof defaultVolumes>) => {
  useBackend(options, volumes);
  const r = render(<Reader id={id} />);
  await waitFor(() => expect(fake.instances).toHaveLength(1));
  const viewer = fake.instances[0];
  const opts = viewer.opts as MangaViewerOptions;
  const shadow = (opts.container as HTMLElement).shadowRoot!;
  return { viewer, opts, shadow, ...r };
};
const custom = (opts: MangaViewerOptions) => (opts.headerButtons ?? []).filter((b): b is HeaderButton => typeof b !== "string");
const puts = () => backend.log.filter((l) => l.method === "PUT" && l.path.endsWith("/progress")).map((l) => ({ path: l.path, body: l.body }));
const nameOf = (b: string | HeaderButton) => (typeof b === "string" ? b : b.className);

describe("manga-viewer に渡す設定", () => {
  it("ページ画像・右綴じ・タイトル・しおり API・テーマを渡す", async () => {
    const { opts } = await opened("2");
    expect(opts.pages).toEqual(Array.from({ length: 8 }, (_, i) => `/api/volumes/2/pages/${i}`));
    expect(opts.direction).toBe("rtl");
    expect(opts.title).toBe("サンプル漫画 第02巻");
    expect(opts.bookmarks).toBe(true);
    expect(opts.bookmarkId).toBe("2");
    expect(opts.bookmarkApi).toBe("/api/page-marks");
    expect(opts.messages?.bookmarkAdd).toContain("しおり");
    expect(["light", "dark"]).toContain(opts.theme);
  });

  it.each([true, false])("表紙を単独で表示する設定(%s)が firstPageSingle になる", async (coverAlone) => {
    saveSettings({ coverAlone });
    const { opts } = await opened("1");
    expect(opts.firstPageSingle).toBe(coverAlone);
  });

  it("以前の版が保存した spreadMode が残っていても読み飛ばす", async () => {
    localStorage.setItem("spinebox.reader", JSON.stringify({ coverAlone: false, spreadMode: "single" }));
    const { opts } = await opened("1");
    expect(opts.firstPageSingle).toBe(false);
  });

  it("ヘッダーのボタンは共有・リンクコピーを含めない", async () => {
    const { opts } = await opened("3"); // 次巻なし
    expect(opts.headerButtons!.map(nameOf)).toEqual(["back", "bookmark", "spinebox-volume-bookmark", "fullscreen", "help"]);
  });

  it("戻るは巻の作品のタイトル一覧へ移る(#324)", async () => {
    const { opts, viewer } = await opened("1");
    const workId = JSON.parse(backend.handle("GET", "/api/volumes/1").body).work_id;
    expect(workId).not.toBeNull();
    window.location.hash = "#/read/1";
    opts.onBack!(new Event("click"), viewer);
    expect(window.location.hash).toBe(`#/works/${workId}/titles`);
  });

  it("作品に割り当てられていない巻の戻るは一覧へ移る", async () => {
    useBackend();
    const detail = JSON.parse(backend.handle("GET", "/api/volumes/1").body);
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ ...detail, work_id: null, next_volume: null })));
    render(<Reader id="1" />);
    await waitFor(() => expect(fake.instances).toHaveLength(1));
    window.location.hash = "#/read/1";
    fake.instances[0].opts.onBack(new Event("click"), fake.instances[0]);
    expect(window.location.hash).toBe("#/");
  });

  it("ページスライダーを大きくするスタイルを Shadow DOM に足す(#324)", async () => {
    const { shadow } = await opened("1");
    expect(shadow.querySelector("style[data-spinebox-slider]")).not.toBeNull();
  });

  it("アンマウントで destroy する", async () => {
    const { viewer, unmount } = await opened("1");
    expect(viewer.destroy).not.toHaveBeenCalled();
    unmount();
    expect(viewer.destroy).toHaveBeenCalledTimes(1);
  });
});

describe("最後の画面からさらに進む(#324)", () => {
  const settle = () => vi.spyOn(Date, "now").mockReturnValue(Date.now() + 1000);
  afterEach(() => vi.restoreAllMocks());

  it("次巻があれば次巻のリーダーへ移る(キー・スワイプとタップの両方の経路)", async () => {
    const { viewer } = await opened("2");
    settle();
    viewer._goNext();
    expect(window.location.hash).toBe("#/read/3");
    window.location.hash = "";
    viewer._navigateTo(1);
    expect(window.location.hash).toBe("#/read/3");
  });

  it("次巻が無ければ何もしない", async () => {
    const { viewer } = await opened("3");
    settle();
    viewer._goNext();
    expect(window.location.hash).toBe("");
  });

  it("続きから読むかの確認が出ている間は移らない", async () => {
    fake.duringConstruct = (_o, root) => {
      const d = document.createElement("div");
      d.className = "mv-resume-dialog";
      root.appendChild(d);
    };
    const { viewer } = await opened("2");
    settle();
    viewer._goNext();
    expect(window.location.hash).toBe("");
  });
});

describe("次の巻へのボタン(#113)", () => {
  it("次巻があれば「次の巻へ」で次巻のリーダーへ移る", async () => {
    const { opts, shadow } = await opened("2");
    expect(custom(opts).map((b) => b.label)).toContain("次の巻へ");
    fireEvent.click(shadow.querySelector(".spinebox-next-volume")!);
    expect(window.location.hash).toBe("#/read/3");
  });

  it("次巻が無ければボタンを出さない", async () => {
    const { opts } = await opened("3");
    expect(custom(opts).map((b) => b.label)).not.toContain("次の巻へ");
  });

  it("次巻がオフラインならボタンは出すが、移動せずオフラインであることを出す", async () => {
    const { shadow } = await opened("2", {}, defaultVolumes().map((v) => (v.id === 3 ? { ...v, offline: true } : v)));
    fireEvent.click(shadow.querySelector(".spinebox-next-volume")!);
    expect(await screen.findByText("次の巻「読了済みの巻」はオフラインです")).toBeTruthy();
    expect(window.location.hash).toBe("");
  });
});

describe("再開位置の受け渡し", () => {
  it("サーバーの位置(0 始まり)を manga-viewer の保存形式で置く", async () => {
    await opened("2"); // progress.page = 3
    const saved = JSON.parse(localStorage.getItem(progressKey("2"))!);
    expect(saved).toMatchObject({ slotIndex: 0, pageIndex: 3 });
    expect(Date.now() - saved.timestamp).toBeLessThan(5000);
  });

  it("位置が無い巻では、前に残っていた値を消す", async () => {
    localStorage.setItem(progressKey("1"), JSON.stringify({ slotIndex: 0, pageIndex: 5, timestamp: Date.now() }));
    await opened("1");
    expect(localStorage.getItem(progressKey("1"))).toBeNull();
  });

  it("位置が 0 ページ目でも消す", async () => {
    localStorage.setItem(progressKey("9"), "x");
    await opened("9", {}, [{ ...defaultVolumes()[0], id: 9, progress: { page: 0, finished: false } }]);
    expect(localStorage.getItem(progressKey("9"))).toBeNull();
  });
});

describe("読書位置のサーバー保存", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] }));

  const open = async (id: string) => {
    useBackend();
    const r = render(<Reader id={id} />);
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(fake.instances).toHaveLength(1);
    const opts = fake.instances[0].opts as MangaViewerOptions;
    return { opts, shadow: (opts.container as HTMLElement).shadowRoot!, ...r };
  };
  const wait = (ms: number) => act(async () => vi.advanceTimersByTimeAsync(ms));

  it("構築中の通知では保存しない(サーバーの位置を 1 ページ目で上書きしない)", async () => {
    // 実物は構築中に onPageChange(1) を呼ぶ
    fake.duringConstruct = (o) => {
      o.onPageChange(1, 8);
      o.onComplete();
    };
    await open("2");
    await wait(2000);
    expect(puts()).toEqual([]);
  });

  it("ページが変わると 0 始まりで、まとめて 1 回だけ送る(デバウンス)", async () => {
    const { opts } = await open("2");
    act(() => opts.onPageChange!(5, 8));
    act(() => opts.onPageChange!(6, 8));
    await wait(500);
    expect(puts()).toEqual([]);
    act(() => opts.onPageChange!(7, 8));
    await wait(800);
    expect(puts()).toEqual([{ path: "/api/volumes/2/progress", body: { page: 6, finished: false } }]);
  });

  it("最終ページ(onComplete)で既読になり、前へ戻っても既読のまま", async () => {
    const { opts } = await open("2");
    act(() => {
      opts.onPageChange!(8, 8);
      opts.onComplete!();
    });
    await wait(800);
    expect(puts().at(-1)).toEqual({ path: "/api/volumes/2/progress", body: { page: 7, finished: true } });
    act(() => opts.onPageChange!(7, 8));
    await wait(800);
    expect(puts().at(-1)).toEqual({ path: "/api/volumes/2/progress", body: { page: 6, finished: true } });
  });

  it("続きから読むかの確認が出ている間は保存しない", async () => {
    const { opts, shadow } = await open("2");
    const dialog = document.createElement("div");
    dialog.className = "mv-resume-dialog";
    shadow.appendChild(dialog);
    act(() => opts.onPageChange!(1, 8));
    act(() => opts.onComplete!());
    await wait(2000);
    expect(puts()).toEqual([]);
    dialog.remove(); // 選び終えた後は保存する
    act(() => opts.onPageChange!(6, 8));
    await wait(800);
    expect(puts()).toEqual([{ path: "/api/volumes/2/progress", body: { page: 5, finished: false } }]);
  });

  it("待っている値は、画面を離れる(アンマウント)ときに即座に送る", async () => {
    const { opts, unmount } = await open("2");
    act(() => opts.onPageChange!(6, 8));
    expect(puts()).toEqual([]);
    unmount();
    await wait(0);
    expect(puts()).toEqual([{ path: "/api/volumes/2/progress", body: { page: 5, finished: false } }]);
  });
});

describe("巻のブックマーク(#86)", () => {
  it("押すとサーバーへ付け外しし、ボタンの表示を切り替える", async () => {
    const { shadow } = await opened("1");
    const btn = shadow.querySelector<HTMLElement>(".spinebox-volume-bookmark")!;
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("false"));
    expect(btn.title).toBe("ブックマークに追加");
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("true"));
    expect(btn.title).toBe("ブックマークを外す");
    expect(backend.log.filter((l) => l.method === "PUT" && l.path.includes("bookmark"))).toHaveLength(1);
    fireEvent.click(btn);
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("false"));
    expect(btn.getAttribute("aria-label")).toBe("ブックマークに追加");
  });

  it("失敗したら元に戻して理由を出す", async () => {
    const { shadow } = await opened("1");
    vi.stubGlobal("fetch", async () => new Response("", { status: 500 }));
    const btn = shadow.querySelector<HTMLElement>(".spinebox-volume-bookmark")!;
    fireEvent.click(btn);
    expect(await screen.findByText("ブックマークを変更できませんでした")).toBeTruthy();
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });
});

describe("小説・ページが無い巻", () => {
  it("小説は manga-viewer を使わず NovelReader で開く", async () => {
    useBackend();
    render(<Reader id="7" />);
    expect(await screen.findByTestId("novel-text")).toBeTruthy();
    expect(fake.instances).toHaveLength(0);
  });

  it("ページが 0 件なら理由を出す", async () => {
    useBackend({}, [{ ...defaultVolumes()[0], id: 10, page_count: 0 }]);
    render(<Reader id="10" />);
    expect((await screen.findByRole("alert")).textContent).toContain("ページがありません");
    expect(fake.instances).toHaveLength(0);
  });
});
