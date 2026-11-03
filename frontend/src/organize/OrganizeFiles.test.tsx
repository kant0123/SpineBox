// 範囲指定の整理(#84)。プレビュー → 実行 → まとめて元に戻す、パラメーターの挿入、プリセット、外す、フォルダを選ぶ
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { organizeFilesHref, parseRoute } from "../route";

let backend: ReturnType<typeof createMockBackend>;
const posts = () => backend.log.filter((l) => l.method === "POST" && l.path !== "/api/organize/range/example").map((l) => l.path);
const lastPlan = () => backend.log.filter((l) => l.path === "/api/organize/range-plan").at(-1)?.body;

beforeEach(() => {
  backend = createMockBackend();
  localStorage.clear();
  window.location.hash = organizeFilesHref;
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const fileInput = () => screen.getByLabelText("ファイル名テンプレート") as HTMLInputElement;
const folderInput = () => screen.getByLabelText("フォルダーテンプレート") as HTMLInputElement;

describe("範囲指定の整理", () => {
  it("単独のルートで開け、管理のデータ管理タブの整理の節に出る", async () => {
    expect(parseRoute(organizeFilesHref)).toEqual({ name: "organize-files" });
    window.location.hash = organizeFilesHref;
    render(<App />);
    await screen.findByLabelText("ファイル名テンプレート");
    expect(screen.getByRole("link", { name: "データ管理" }).getAttribute("aria-current")).toBe("page");
  });

  it("ファイル名の整理だけをプレビューし、実行してからまとめて元に戻せる", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    expect(await screen.findByText("半角カナを全角にしました・空白を整えました(全角の空白・続いた空白)")).toBeTruthy();
    expect(screen.getByText("作者A/サンプル漫画 第1巻.cbz")).toBeTruthy();
    // CBZ 化が要る巻は対象外にして、CBZ 化の画面へ案内する
    expect(screen.getByRole("link", { name: "テンプレートの名前で CBZ に整理" })).toBeTruthy();
    expect(posts()).toEqual(["/api/organize/range-plan"]); // プレビューだけでは実行しない
    expect(lastPlan()).toEqual({ root_id: 1, path: "", file_template: "", folder_template: "", exclude_volume_ids: [] });

    fireEvent.click(screen.getByRole("button", { name: "実行(1 巻)" }));
    fireEvent.click(await screen.findByRole("button", { name: "まとめて元に戻す" }));
    await screen.findByText("元に戻しました");
    expect(posts().slice(1)).toEqual([expect.stringMatching(/^\/api\/organize\/op-\d+\/execute$/), expect.stringMatching(/^\/api\/organize\/op-\d+\/undo$/)]);
  });

  it("パラメーターはカーソル位置に入り、選択した文字は置き換える。プリセットは両方のテンプレートを入れる", async () => {
    render(<App />);
    await screen.findByRole("button", { name: "作品名" });
    const file = fileInput();
    fireEvent.focus(file);
    fireEvent.change(file, { target: { value: "第 巻" } });
    file.setSelectionRange(1, 1);
    fireEvent.click(screen.getByRole("button", { name: "巻番号(2 桁)" }));
    expect(fileInput().value).toBe("第{number:2} 巻");
    fileInput().setSelectionRange(0, 0);
    fireEvent.click(screen.getByRole("button", { name: "作品名" }));
    expect(fileInput().value).toBe("{title}第{number:2} 巻");
    // 選択中の文字(「 巻」)を置き換える
    fileInput().setSelectionRange(18, 20);
    fireEvent.click(screen.getByRole("button", { name: "単位(巻 / 話)" }));
    expect(fileInput().value).toBe("{title}第{number:2}{unit}");
    // フォルダー欄に移るとそちらへ入る
    fireEvent.focus(folderInput());
    fireEvent.click(screen.getByRole("button", { name: "作品名" }));
    expect(folderInput().value).toBe("{title}");

    fireEvent.click(screen.getByRole("button", { name: "出版社別フォルダー" }));
    expect(fileInput().value).toBe("{title} 第{number:2}{unit}");
    expect(folderInput().value).toBe("{publisher}/{title}");
    expect(await screen.findByText("出版社X/サンプル漫画/サンプル漫画 第03巻.cbz")).toBeTruthy(); // パスの例
    fireEvent.click(screen.getByRole("button", { name: "設定のテンプレート" }));
    expect(folderInput().value).toBe("{title}");
  });

  it("テンプレートの誤りはプレビューさせない", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.change(fileInput(), { target: { value: "{title" } });
    expect(await screen.findByText("{ が閉じていません")).toBeTruthy();
    expect(fileInput().getAttribute("aria-invalid")).toBe("true");
    expect(fileInput().getAttribute("aria-describedby")).toBe("organize-template-error organize-file-template-hint");
    for (const [input, hintId] of [
      [screen.getByLabelText("範囲のパス"), "organize-path-hint"],
      [fileInput(), "organize-file-template-hint"],
      [folderInput(), "organize-folder-template-hint"],
    ] as const) {
      const hint = document.getElementById(hintId)!;
      expect(hint.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect((screen.getByRole("button", { name: "プレビュー" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("衝突した巻を外して作り直し、入力を変えると前のプレビューは消える", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.change(fileInput(), { target: { value: "{title} 第{number:2}{unit}" } });
    fireEvent.change(folderInput(), { target: { value: "{title}" } });
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const conflicts = (await screen.findByRole("heading", { name: "衝突(2 巻)" })).closest("section")!;
    expect(within(conflicts).getAllByText(/と同じ名前になります/)).toHaveLength(2);
    fireEvent.click(within(conflicts).getAllByRole("button", { name: "この巻を外す" })[0]);
    await screen.findByRole("heading", { name: "外した(1 巻)" });
    expect(lastPlan()).toMatchObject({ exclude_volume_ids: [12], folder_template: "{title}" });
    expect(screen.queryByRole("heading", { name: /^衝突/ })).toBeNull();

    fireEvent.change(fileInput(), { target: { value: "{title}" } });
    await waitFor(() => expect(screen.queryByRole("heading", { name: /^外した/ })).toBeNull());
    expect(screen.queryByRole("button", { name: /^実行/ })).toBeNull();
  });

  it("フォルダを選ぶと範囲のパスに入り、プレビューはその範囲だけ", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "フォルダを選ぶ" }));
    const dialog = await screen.findByRole("dialog", { name: "フォルダを選ぶ" });
    fireEvent.click(await within(dialog).findByRole("button", { name: "スキャン/" }));
    await within(dialog).findByText("この中にフォルダはありません");
    fireEvent.click(within(dialog).getByRole("button", { name: "このフォルダにする" }));
    expect((screen.getByLabelText("範囲のパス") as HTMLInputElement).value).toBe("スキャン");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    });
    await screen.findByText("状態:", { exact: false });
    expect(lastPlan()).toMatchObject({ path: "スキャン" });
    expect(screen.queryByText("作者A/サンプル漫画 2巻.cbz")).toBeNull();
  });
});
