// モック API を相手に、データ管理の画面(ジョブの一覧・依頼・進み具合・待ち行列・中止・履歴)を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import type { Job } from "../api";
import { DataJobs, countText, stepText, summaryText } from "./DataJobs";

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

describe("ルートと入口", () => {
  it("#/data は管理のデータ管理タブを開き、ジョブはデータ管理(依頼)とタスクセンター(実行中・履歴)に分かれる", async () => {
    expect(parseRoute("#/data")).toEqual({ name: "admin", tab: "data" });
    expect(parseRoute("#/data/")).toEqual({ name: "admin", tab: "data" });
    window.location.hash = "#/data";
    render(<App />);
    await screen.findByText("足りない作品情報を補う");
    const tabs = within(screen.getByRole("navigation", { name: "管理のタブ" }));
    expect(tabs.getByRole("link", { name: "スキャンと補完" }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("heading", { name: "履歴" })).toBeNull(); // 履歴はタスクセンター
    expect(tabs.getByRole("link", { name: "タスクセンター" }).getAttribute("href")).toBe("#/admin/tasks");
    window.location.hash = "#/admin/tasks";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(await screen.findByRole("heading", { name: "履歴" })).toBeTruthy();
    expect(screen.queryByText("足りない作品情報を補う")).toBeNull(); // 依頼の一覧はデータ管理
  });
});

describe("データ管理の画面", () => {
  it("ジョブを見出しごとに説明と対象の件数つきで並べ、履歴は新しい順で失敗の理由を出す", async () => {
    render(<DataJobs pollMs={20} />);
    const daily = (await screen.findByText("足りない作品情報を補う")).closest("li")!;
    expect(within(daily).getByText("登録済みの 3 作品が補完待ち")).toBeTruthy();
    // いつもの更新(3 つ)だけを開いて見せ、ほかは畳む(#207)。daily_scan と同じ処理の scan は出さない
    const daily3 = screen.getByRole("heading", { name: "いつもの更新" }).parentElement!;
    expect(within(daily3).getAllByRole("listitem").map((li) => li.querySelector("strong")!.textContent)).toEqual([
      "新しいファイルを取り込む", "足りない作品情報を補う", "ライブラリ全体を同期"]);
    expect(screen.queryByText("ファイルのスキャンのみ", { selector: ".job-kind strong" })).toBeNull();
    expect(within(screen.getByText("中の巻を調べる").closest("li")!).getByText("未解析 1 巻")).toBeTruthy();
    expect(within(screen.getByText("作品情報のみ補完").closest("li")!).getByLabelText("全作品を取り直す")).toBeTruthy();
    // 作品一覧のヘッダーから移した操作(#102)
    const individual = screen.getByText("個別の処理(9)").closest("details")!;
    expect(individual.open).toBe(false);
    expect(within(individual).getByText("表紙のみ補完")).toBeTruthy();
    expect(within(individual).getByText("重複を調べる")).toBeTruthy();
    expect(within(individual).getByText("作品の名寄せを計算し直す")).toBeTruthy();
    const history = screen.getByRole("heading", { name: "履歴" }).parentElement!;
    const rows = within(history).getAllByRole("listitem");
    expect(rows.map((r) => within(r).getByText(/スキャン|重複/, { selector: "strong" }).textContent)).toEqual(["ファイルのスキャンのみ", "重複を調べる"]);
    expect(within(rows[0]).getByText("成功")).toBeTruthy();
    expect(within(rows[0]).getByText(/各画面のボタンから/)).toBeTruthy();
    expect(within(rows[1]).getByText("理由: RuntimeError: 読めない巻があります")).toBeTruthy();
    expect(screen.getByText(/ファイルを動かしたり消したりしません/)).toBeTruthy();
  });

  it("依頼すると実行中に進み具合が出て、2 本目は待ち行列に入り、終わると履歴に移る", async () => {
    render(<DataJobs pollMs={20} />);
    fireEvent.click(await screen.findByRole("button", { name: "全ファイルを確かめ直すを実行" }));
    const running = screen.getByRole("heading", { name: "実行中" }).parentElement!;
    await within(running).findByText("全ファイルを確かめ直す");
    fireEvent.click(screen.getByRole("button", { name: "重複を調べるを実行" }));
    await within(running).findByText("待ち行列");
    expect(within(running).getByText("重複を調べる")).toBeTruthy();
    expect((screen.getByRole("button", { name: "重複を調べるを実行" }) as HTMLButtonElement).disabled).toBe(true);
    const body = backend.log.filter((l) => l.method === "POST" && l.path === "/api/jobs").map((l) => l.body);
    expect(body).toEqual([{ kind: "maintenance", params: {} }, { kind: "duplicates", params: {} }]);
    await waitFor(() => expect(within(running).getByText("動いているジョブはありません。")).toBeTruthy(), { timeout: 3000 });
    const history = screen.getByRole("heading", { name: "履歴" }).parentElement!;
    expect(within(history).getAllByText("成功").length).toBe(3);
  });

  it("実行中のジョブを中止でき、待ちは取り下げられる", async () => {
    render(<DataJobs pollMs={200} />);
    fireEvent.click(await screen.findByRole("button", { name: "表紙のみ補完を実行" }));
    fireEvent.click(screen.getByRole("button", { name: "中の巻を調べるを実行" }));
    fireEvent.click(await screen.findByRole("button", { name: "取り下げ" }));
    fireEvent.click(await screen.findByRole("button", { name: "中止" }));
    await waitFor(() => expect(screen.getAllByText("中止").length).toBeGreaterThanOrEqual(2), { timeout: 3000 });
    expect(backend.log.filter((l) => l.path.endsWith("/cancel")).length).toBe(2);
  });

  it("全作品の取り直しを指定して依頼できる", async () => {
    render(<DataJobs pollMs={20} />);
    const card = (await screen.findByText("作品情報のみ補完")).closest("li")!;
    fireEvent.click(within(card).getByLabelText("全作品を取り直す"));
    fireEvent.click(within(card).getByRole("button", { name: "作品情報のみ補完を実行" }));
    await waitFor(() => expect(backend.log.some((l) => l.path === "/api/jobs" && l.method === "POST")).toBe(true));
    expect(backend.log.find((l) => l.path === "/api/jobs" && l.method === "POST")!.body).toEqual({ kind: "metadata", params: { all: true } });
  });
});

describe("整理のタブ", () => {
  it("整理は管理の「整理」タブ。旧 #/data/organize・#/organize も同じタブを開き、スキャンと補完のタブには出さない", async () => {
    for (const hash of ["#/admin/organize", "#/data/organize", "#/organize"]) {
      expect(parseRoute(hash)).toEqual({ name: "admin", tab: "organize" });
    }
    window.location.hash = "#/data/organize";
    render(<App />);
    expect(await screen.findByLabelText("ファイル名テンプレート")).toBeTruthy();
    expect(screen.getByRole("link", { name: "整理" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: "整理の履歴" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "受け入れ" }).getAttribute("href")).toBe("#/inbox");
    expect(screen.queryByText("足りない作品情報を補う")).toBeNull();
    cleanup();
    window.location.hash = "#/admin/data";
    render(<App />);
    await screen.findByText("足りない作品情報を補う");
    expect(screen.queryByLabelText("ファイル名テンプレート")).toBeNull();
    expect(screen.queryByRole("heading", { name: "ほかの画面" })).toBeNull(); // #207 で廃止
  });

  it("整理を実行・元に戻すとタスクセンターの履歴にも残る", async () => {
    window.location.hash = "#/admin/organize";
    render(<App />);
    const section = (await screen.findByRole("heading", { name: "ファイル名・フォルダーの整理" })).closest("section")!;
    await within(section).findByRole("option", { name: "E:/漫画" });
    fireEvent.click(within(section).getByRole("button", { name: "プレビュー" }));
    fireEvent.click(await within(section).findByRole("button", { name: /^実行\(/ }));
    fireEvent.click(await within(section).findByRole("button", { name: "まとめて元に戻す" }));
    await within(section).findByText("元に戻しました");
    window.location.hash = "#/admin/tasks";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    const history = (await screen.findByRole("heading", { name: "履歴" })).parentElement!;
    expect(within(history).getByText("整理を元に戻す(範囲の整理)")).toBeTruthy();
    expect(within(history).getByText("整理を実行(範囲の整理)")).toBeTruthy();
  });
});

describe("summaryText", () => {
  it("知っている数だけを並べ、段ごとの要約は段の名前つきで出す", () => {
    expect(summaryText({ volumes: 12, offline_roots: ["E:/"], issues_total: 0 })).toBe("12 巻・オフライン 1 か所");
    expect(summaryText({ scan: { volumes: 3 }, analyze: { analyzed: 2, errors: 1 }, thumbnails: { made: 0, existing: 2 } }))
      .toBe("スキャン: 3 巻・中の巻: 解析 2 巻・読めない 1 件・表紙: 作成済み 2");
    expect(summaryText(null)).toBe("");
  });
});

describe("進み具合の文言(#207)", () => {
  const base = { step: "中の巻を調べる", step_index: 2, step_count: 5, done: 45, total: 120 } as unknown as Job;
  it("複数の段は「段 i/n」を付け、件数は総数があれば「n / 総数」、無ければ「n 件目」", () => {
    expect(stepText(base)).toBe("段 2/5: 中の巻を調べる");
    expect(stepText({ ...base, step_count: null, step_index: null })).toBe("中の巻を調べる");
    expect(stepText({ ...base, step_count: 1, step_index: 1 })).toBe("中の巻を調べる");
    expect(stepText({ ...base, step: null })).toBe("");
    expect(countText(base)).toBe("45 / 120");
    expect(countText({ ...base, total: null })).toBe("45 件目");
    expect(countText({ ...base, total: null, done: null })).toBe("");
  });

  it("スキャンと補完では実行中を 1 行で出し、詳しい進み具合はタスクセンター", async () => {
    render(<DataJobs pollMs={60} view="data" />);
    fireEvent.click(await screen.findByRole("button", { name: "ライブラリ全体を同期を実行" }));
    const now = await screen.findByText(/段 \d\/5/);
    expect(now.closest(".job-now")!.querySelector("a")!.getAttribute("href")).toBe("#/admin/tasks");
    expect(screen.queryByRole("heading", { name: "実行中" })).toBeNull();
    expect(screen.queryByRole("button", { name: "中止" })).toBeNull(); // 中止はタスクセンター
  });

  it("タスクセンターは段と件数を分けて出し、総数の無い段は値の無い progress にする", async () => {
    render(<DataJobs pollMs={60} view="all" />);
    fireEvent.click(await screen.findByRole("button", { name: "ライブラリ全体を同期を実行" }));
    const bar = await screen.findByRole("progressbar");
    await waitFor(() => expect(bar.hasAttribute("value") || bar.textContent).toBeTruthy());
    await screen.findByText(/^段 [2-5]\/5: /, {}, { timeout: 3000 });
    await waitFor(() => expect((bar as HTMLProgressElement).value).toBe(33), { timeout: 3000 });
  });
});
