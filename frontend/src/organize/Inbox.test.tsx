// 受け入れの画面。モック API を相手に、同期 → 整理案(選び直し)→ 承認 → 履歴から元に戻す、を通す
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { inboxHref } from "../route";

let backend: ReturnType<typeof createMockBackend>;

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 375 });
  backend = createMockBackend();
  window.location.hash = inboxHref;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const posts = () => backend.log.filter((l) => l.method === "POST").map((l) => l.path);
const card = (name: string) => screen.getByText(name, { selector: "strong" }).closest("li")!;

describe("受け入れ", () => {
  it("4 通りを出し、承認できるものだけ選ばれている。同期では何も動かさない", async () => {
    render(<App />);
    expect(await screen.findByText("サンプル漫画 第03巻.cbz", { selector: "strong" })).toBeTruthy();
    expect(within(card("サンプル漫画 第03巻.cbz")).getByText("既存の作品の巻")).toBeTruthy();
    expect(within(card("サンプル漫画 第03巻.cbz")).getByText("中身を変えずに書庫へ移動")).toBeTruthy();
    expect(within(card("新作 第01巻.zip")).getByText("新しい作品")).toBeTruthy();
    expect(within(card("新作 第01巻.zip")).getByText("巻ごとに CBZ にして書庫へ")).toBeTruthy();
    expect(within(card("第2巻.cbz")).getByText("確認が要ります")).toBeTruthy();
    expect(within(card("壊れた巻.zip")).getByText(/読めないアーカイブ/)).toBeTruthy();
    // 承認できる 2 件だけが選ばれ、確認が要るもの・取り込めないものは選べない
    expect(screen.getByRole("button", { name: "承認して書庫へ(2 件)" })).toBeTruthy();
    expect((screen.getByLabelText("第2巻.cbz を承認する") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("壊れた巻.zip を承認する") as HTMLInputElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "同期" }));
    expect(await screen.findByText(/受け入れフォルダで 4 巻を検出しました/)).toBeTruthy();
    expect(posts()).toEqual(["/api/inbox/sync"]); // 承認するまで動かさない
  });

  it("確認が要る本は、作品を選んで番号を直すまで承認できない", async () => {
    render(<App />);
    await screen.findByText("第2巻.cbz", { selector: "strong" });
    const c = card("第2巻.cbz");
    expect(within(c).getByText(/外側の名前「別名の作品」から推測しました/, { selector: ".muted" })).toBeTruthy();
    // 近い名前の作品を選ぶと、同じ番号の巻が既にあると分かる
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の作品"), { target: { value: "1" } });
    expect(await within(c).findByText(/書庫の「サンプル漫画 第02巻」と同じ巻かもしれません/)).toBeTruthy();
    expect((screen.getByLabelText("第2巻.cbz を承認する") as HTMLInputElement).disabled).toBe(true);
    const sent = backend.log.filter((l) => l.path === "/api/inbox/preview").at(-1)?.body;
    expect(sent).toEqual({ items: [{ source_id: 103, work_id: 1 }] });

    // 番号を直すと別の巻として取り込める
    fireEvent.click(within(c).getByRole("button", { name: "番号を直す" }));
    fireEvent.change(within(c).getByLabelText("第2巻 の番号"), { target: { value: "4" } });
    expect(await within(c).findByText("サンプル漫画/サンプル漫画 第04巻.cbz")).toBeTruthy();
    await waitFor(() => expect((screen.getByLabelText("第2巻.cbz を承認する") as HTMLInputElement).disabled).toBe(false));
    expect(screen.getByRole("button", { name: "承認して書庫へ(3 件)" })).toBeTruthy();
    expect(posts().filter((p) => p !== "/api/inbox/preview")).toEqual([]); // 整理案を作り直しただけで、何も動かしていない
  });

  it("取り込まないと決めると取り込む巻が無くなる。新しい作品名も決められる", async () => {
    render(<App />);
    await screen.findByText("第2巻.cbz", { selector: "strong" });
    const c = card("第2巻.cbz");
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の作品"), { target: { value: "1" } });
    await within(c).findByText(/同じ巻かもしれません/);
    fireEvent.click(within(c).getByLabelText("取り込まない"));
    expect(await within(c).findByText("取り込む巻がありません(すべて取り込まない)")).toBeTruthy();
    fireEvent.click(within(c).getByLabelText("取り込まない")); // 戻す
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の作品"), { target: { value: "new" } });
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の新しい作品名"), { target: { value: "あたらしい作品" } });
    expect(await within(c).findByText("あたらしい作品/あたらしい作品 第02巻.cbz")).toBeTruthy();
    expect(backend.log.filter((l) => l.path === "/api/inbox/preview").at(-1)?.body).toEqual({
      items: [{ source_id: 103, new_title: "あたらしい作品" }],
    });
  });

  it("承認すると選んだものだけ書庫に入り、残りは受け入れ待ちのまま。履歴から元に戻せる", async () => {
    render(<App />);
    await screen.findByText("新作 第01巻.zip", { selector: "strong" });
    fireEvent.click(screen.getByLabelText("サンプル漫画 第03巻.cbz を承認する")); // 1 件は外す
    fireEvent.click(screen.getByRole("button", { name: "承認して書庫へ(1 件)" }));
    expect(await screen.findByText(/書庫に入れました\(新作\/新作 第01巻\.cbz\)/)).toBeTruthy();
    const approve = backend.log.find((l) => l.path === "/api/inbox/approve")!;
    expect(approve.body).toEqual({ items: [{ source_id: 102, expect_outputs: ["新作/新作 第01巻.cbz"] }] });
    // 承認した本は受け入れ待ちから消え、承認しなかった本は残る
    await waitFor(() => expect(screen.queryByLabelText("新作 第01巻.zip を承認する")).toBeNull());
    expect(screen.getByLabelText("サンプル漫画 第03巻.cbz を承認する")).toBeTruthy();
    const hist = (await screen.findByText("整理済み")).closest("li")!;
    expect(within(hist).getByText("新作 第01巻.zip")).toBeTruthy();

    fireEvent.click(within(hist).getByRole("button", { name: "元に戻す" }));
    expect(await screen.findByText("元に戻しました")).toBeTruthy();
    // 元に戻すと、また受け入れ待ちに出る
    expect(await screen.findByLabelText("新作 第01巻.zip を承認する")).toBeTruthy();
    expect(posts().at(-1)).toMatch(/^\/api\/organize\/op-\d+\/undo$/);
  });

  it("別の本を承認しても、まだ承認していない本の選び直しと選択は消えない", async () => {
    render(<App />);
    await screen.findByText("第2巻.cbz", { selector: "strong" });
    const c = card("第2巻.cbz");
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の作品"), { target: { value: "new" } });
    fireEvent.change(within(c).getByLabelText("第2巻.cbz の新しい作品名"), { target: { value: "あたらしい作品" } });
    await within(c).findByText("あたらしい作品/あたらしい作品 第02巻.cbz");
    fireEvent.click(screen.getByLabelText("サンプル漫画 第03巻.cbz を承認する")); // 1 件は選ばない
    await waitFor(() => expect(screen.getByRole("button", { name: "承認して書庫へ(2 件)" })).toBeTruthy());
    fireEvent.click(screen.getByLabelText("第2巻.cbz を承認する")); // 選び直した本も今回は承認しない
    fireEvent.click(screen.getByRole("button", { name: "承認して書庫へ(1 件)" }));
    expect(await screen.findByText(/書庫に入れました\(新作\/新作 第01巻\.cbz\)/)).toBeTruthy();
    await waitFor(() => expect(screen.queryByLabelText("新作 第01巻.zip を承認する")).toBeNull());
    // 残った本は、選んだ作品名で整理案が出たまま、選択も承認前のまま
    const rest = card("第2巻.cbz");
    expect(await within(rest).findByText("あたらしい作品/あたらしい作品 第02巻.cbz")).toBeTruthy();
    expect((screen.getByLabelText("第2巻.cbz を承認する") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByLabelText("サンプル漫画 第03巻.cbz を承認する") as HTMLInputElement).checked).toBe(false);
  });

  it("受け入れフォルダが未設定ならそう伝える", async () => {
    vi.stubGlobal("fetch", async (url: string) => {
      const body = url.startsWith("/api/inbox") ? { inboxes: [], items: [] } : url.startsWith("/api/works") ? { works: [], total: 0, facets: {}, unassigned: 0 } : [];
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    render(<App />);
    expect(await screen.findByText(/受け入れフォルダが設定されていません/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "同期" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
