// モック API を相手に、一覧 → リーダー → ページ送り → 位置保存 → 再開の流れを通す
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { createMockBackend } from "./mock/mockBackend";
import { organizeHistoryHref, organizeHref, organizeTemplateHref, readHref, volumesHref, workHref, workOrganizeHref } from "./route";

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

// manga-viewer は実ブラウザで確かめる外部ライブラリ。ここでは渡した設定と destroy だけを記録する偽物にする
const viewers = vi.hoisted(() => [] as { opts: any; destroy: () => void }[]);
vi.mock("@tokagemushi/manga-viewer", () => ({
  default: class {
    destroy = vi.fn();
    goToPage = vi.fn();
    constructor(public opts: any) {
      viewers.push(this);
    }
  },
}));
const puts = () => backend.log.filter((l) => l.method === "PUT").map((l) => ({ path: l.path, body: l.body }));

beforeEach(() => {
  backend = createMockBackend();
  window.location.hash = volumesHref; // 巻の一覧(ホームは作品一覧 #85)
  localStorage.clear();
  viewers.length = 0;
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
  it.skip("巻・ページ数・読書状態・オフラインを出し、スキャンできる", async () => {
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
    expect(await screen.findByText(/8 巻を検出しました/)).toBeTruthy();
    expect(screen.getByText(/E:\/漫画/)).toBeTruthy();
    // 読めなかったものは件数と理由の一覧を出す
    const issues = screen.getByText("読めなかったもの 2 件").closest("details")!;
    expect(within(issues).getByText("E:/本/作者A/壊れた巻.zip")).toBeTruthy();
    expect(within(issues).getByText(/zip として開けない/)).toBeTruthy();
    expect(within(issues).getByText("E:/本/作者B/まとめ.zip › ../evil.jpg")).toBeTruthy();
    expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/scan")).toBe(true);
  });

  it.skip("未解析の内側アーカイブを調べると子の巻に分かれ、食い違いのバッジと説明が出る", async () => {
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

  it.skip("読めなかったものが無ければ件数を出さず、多いときは省略を知らせる", async () => {
    const stub = (backendOptions: Parameters<typeof createMockBackend>[1]) => {
      backend = createMockBackend(undefined, backendOptions);
      vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
        const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
        return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
      });
    };
    stub({ scanIssues: [] });
    render(<App />);
    await screen.findByText("サンプル漫画 第02巻");
    fireEvent.click(screen.getByRole("button", { name: "スキャン" }));
    expect(await screen.findByText(/8 巻を検出しました/)).toBeTruthy();
    expect(screen.queryByText(/読めなかったもの/)).toBeNull();
    cleanup();

    // 上限で切った分(issues_total > issues.length)は省略を知らせる
    backend = createMockBackend();
    const handle = backend.handle;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const r = handle(init?.method ?? "GET", url, init?.body as string | undefined);
      if (init?.method === "POST" && url === "/api/scan") {
        return new Response(JSON.stringify({ ...JSON.parse(r.body), issues_total: 205 }), {
          status: 200, headers: { "Content-Type": r.contentType } });
      }
      return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
    });
    render(<App />);
    await screen.findByText("サンプル漫画 第02巻");
    fireEvent.click(screen.getByRole("button", { name: "スキャン" }));
    expect(await screen.findByText("読めなかったもの 205 件")).toBeTruthy();
    expect(screen.getByText("ほか 203 件は省略しました。")).toBeTruthy();
  });

  it.skip("スキャンが失敗したら、サーバーが返した原因を出す", async () => {
    backend = createMockBackend(undefined, {
      scanFailure: { status: 500, detail: "スキャンに失敗しました: ValueError: 想定外" },
    });
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
      return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
    });
    render(<App />);
    await screen.findByText("サンプル漫画 第02巻");
    fireEvent.click(screen.getByRole("button", { name: "スキャン" }));
    expect((await screen.findByRole("alert")).textContent).toContain("ValueError: 想定外");
    expect(screen.getByRole("button", { name: "スキャン" })).toBeTruthy(); // 押し直せる
  });

  it("API が落ちていてもエラー表示で止まる", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 500 }));
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("読み込めませんでした");
  });
});

describe("リーダー(漫画)", () => {
  beforeEach(() => setViewport(375, 812));

  it.skip("一覧から巻を開くと manga-viewer に保存位置とページを渡し、位置の保存が一覧に反映される", async () => {
    render(<App />);
    await screen.findByText("サンプル漫画 第02巻");
    expect(screen.getByText("サンプル漫画 第02巻").closest("a")!.getAttribute("href")).toBe(readHref(2));
    await navigate(readHref(2));

    await waitFor(() => expect(viewers).toHaveLength(1));
    const opts = viewers[0].opts;
    expect(opts.pages).toHaveLength(8);
    expect(JSON.parse(localStorage.getItem("spinebox.progress.2")!)).toMatchObject({ pageIndex: 3 }); // 保存位置から再開

    act(() => {
      opts.onPageChange(8, 8);
      opts.onComplete();
    });
    await navigate(volumesHref); // 離れるときに待たず送る
    expect(viewers[0].destroy).toHaveBeenCalled();
    expect(puts()).toEqual([{ path: "/api/volumes/2/progress", body: { page: 7, finished: true } }]);
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    await waitFor(() => expect(within(card).getByText("既読")).toBeTruthy());
  });

  it("開けない巻はメッセージを出す", async () => {
    window.location.hash = readHref(4);
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("開けませんでした");
  });
});

describe("整理(CBZ 化)", () => {
  beforeEach(() => setViewport(375, 812));
  const posts = () => backend.log.filter((l) => l.method === "POST").map((l) => l.path);

  it.skip("一覧 → プレビュー → 実行 → 履歴から元に戻す", async () => {
    render(<App />);
    const card = (await screen.findByText("サンプル漫画 第02巻")).closest("li")!;
    expect(within(card).getByRole("link", { name: "CBZ に整理" }).getAttribute("href")).toBe(organizeHref(2));
    // オフラインの巻には出さない
    expect(within(screen.getByText("外付けドライブの巻").closest("li")!).queryByText("CBZ に整理")).toBeNull();

    await navigate(organizeHref(2));
    expect(await screen.findByText("サンプル漫画 第02巻.cbz")).toBeTruthy();
    expect(screen.getByText("未実行")).toBeTruthy();
    expect(posts()).toEqual(["/api/organize/plan"]); // プレビューだけでは実行しない

    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    expect(await screen.findByText("整理済み")).toBeTruthy();
    expect(posts()).toEqual(["/api/organize/plan", "/api/organize/op-1/execute"]);

    await navigate(organizeHistoryHref);
    fireEvent.click(await screen.findByRole("button", { name: "元に戻す" }));
    expect(await screen.findByText("元に戻しました")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "元に戻す" })).toBeNull();
    expect(posts().at(-1)).toBe("/api/organize/op-1/undo");
  });

  it("出力名をテンプレートに切り替えてプレビューできる", async () => {
    window.location.hash = organizeHref(2);
    render(<App />);
    expect(await screen.findByText("サンプル漫画 第02巻.cbz")).toBeTruthy();
    expect(screen.getByRole("link", { name: "テンプレートの名前にする" }).getAttribute("href")).toBe(organizeTemplateHref(2));
    await navigate(organizeTemplateHref(2));
    expect(await screen.findByText("サンプル漫画/サンプル漫画 第02巻.cbz")).toBeTruthy();
    expect(screen.getByText("<作品名>/<作品名> 第NN巻.cbz")).toBeTruthy();
    expect(backend.log.filter((l) => l.method === "POST").map((l) => l.body)).toEqual([
      { volume_id: 2, naming: "title" }, { volume_id: 2, naming: "template" }]);
  });

  it("作品ページ → テンプレートどおりに整理: 競合を外して実行 → 履歴から元に戻す", async () => {
    window.location.hash = workHref(1);
    render(<App />);
    const link = await screen.findByRole("link", { name: "この作品をテンプレートどおりに整理" });
    expect(link.getAttribute("href")).toBe(workOrganizeHref(1));
    await navigate(workOrganizeHref(1));
    // 変更前 → 変更後・競合・対象外を理由つきで出す
    expect(await screen.findByText("sample_v02.cbz")).toBeTruthy();
    expect(screen.getAllByText("競合")).toHaveLength(2);
    expect(screen.getByText(/1 巻 = 1 CBZ ではありません/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "テンプレートの名前で CBZ に整理" }).getAttribute("href")).toBe(organizeTemplateHref(3));
    expect(posts()).toEqual(["/api/organize/rename-plan"]); // プレビューだけでは実行しない

    fireEvent.click(screen.getAllByRole("button", { name: "この巻を整理しない" })[1]);
    await waitFor(() => expect(screen.queryAllByText("競合")).toHaveLength(0));
    expect(screen.getByText("整理しない")).toBeTruthy();
    expect(backend.log.filter((l) => l.path === "/api/organize/rename-plan").at(-1)?.body).toEqual({ work_id: 1, exclude_volume_ids: [6] });

    fireEvent.click(screen.getByRole("button", { name: "実行" }));
    expect(await screen.findByText("整理済み")).toBeTruthy();
    await navigate(organizeHistoryHref);
    fireEvent.click(await screen.findByRole("button", { name: "元に戻す" }));
    expect(await screen.findByText("元に戻しました")).toBeTruthy();
    expect(posts().at(-1)).toMatch(/^\/api\/organize\/op-\d+\/undo$/);
  });

  it("中の巻を調べていない内側アーカイブは、理由を出して調べるボタンだけを出す", async () => {
    window.location.hash = organizeHref(5);
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("中の巻をまだ調べていません");
    expect(screen.getByText("実行できません")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "実行" })).toBeNull();
    expect(posts()).toEqual(["/api/organize/plan"]); // 調べるのは明示操作。プレビューでは解析しない

    fireEvent.click(screen.getByRole("button", { name: "中の巻を調べて計画を作り直す" }));
    await waitFor(() => expect(posts()).toEqual(["/api/organize/plan", "/api/volumes/5/analyze", "/api/organize/plan"]));
    // 調べた後の計画は別の理由(読めない内側)で実行できない
    expect((await screen.findByRole("alert")).textContent).toContain("inner.rar");
    expect(screen.queryByRole("button", { name: "実行" })).toBeNull();
    expect(screen.queryByRole("button", { name: "中の巻を調べて計画を作り直す" })).toBeNull();
  });

  it("サーバーの理由(detail)をそのまま出す", async () => {
    window.location.hash = organizeHref(999);
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("巻が見つかりません");
  });

  it("履歴が空なら案内を出す", async () => {
    window.location.hash = organizeHistoryHref;
    render(<App />);
    expect(await screen.findByText(/まだ整理していません/)).toBeTruthy();
  });
});
