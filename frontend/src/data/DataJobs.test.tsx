// モック API を相手に、データ管理の画面(ジョブの一覧・依頼・進み具合・待ち行列・中止・履歴)を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { DataJobs, summaryText } from "./DataJobs";

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
  it("#/data をデータ管理の画面として読み、作品・巻の一覧のタブから辿れる", async () => {
    expect(parseRoute("#/data")).toEqual({ name: "data" });
    expect(parseRoute("#/data/")).toEqual({ name: "data" });
    render(<App />);
    await screen.findByText("サンプル漫画 第01巻");
    const tab = screen.getByRole("link", { name: "データ管理" });
    expect(tab.getAttribute("href")).toBe("#/data");
  });
});

describe("データ管理の画面", () => {
  it("ジョブを見出しごとに説明と対象の件数つきで並べ、履歴は新しい順で失敗の理由を出す", async () => {
    render(<DataJobs pollMs={20} />);
    const daily = (await screen.findByText("補完待ちの作品を補完")).closest("li")!;
    expect(within(daily).getByText("登録済みの 3 作品が補完待ち")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "日常の同期" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "ライブラリ全体" })).toBeTruthy();
    expect(within(screen.getByText("中の巻を調べる").closest("li")!).getByText("未解析 1 巻")).toBeTruthy();
    expect(within(screen.getByText("作品情報のみ補完").closest("li")!).getByLabelText("全作品を取り直す")).toBeTruthy();
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
    fireEvent.click(await screen.findByRole("button", { name: "ライブラリ全体のメンテナンスを実行" }));
    const running = screen.getByRole("heading", { name: "実行中" }).parentElement!;
    await within(running).findByText("ライブラリ全体のメンテナンス");
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

describe("整理の節", () => {
  it("データ管理の下に「整理」節があり、単独の入口 #/data/organize も同じページの整理へ移る", async () => {
    window.location.hash = "#/data";
    render(<App />);
    const section = (await screen.findByRole("heading", { name: "整理" })).closest("section")!;
    expect(within(section).getByLabelText("ファイル名テンプレート")).toBeTruthy();
    expect(screen.getByText("補完待ちの作品を補完")).toBeTruthy(); // 同じページにジョブの一覧もある
    cleanup();
    window.location.hash = "#/data/organize";
    render(<App />);
    await screen.findByLabelText("ファイル名テンプレート");
    expect(screen.getByRole("heading", { name: "実行中" })).toBeTruthy();
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
