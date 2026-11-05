// 範囲指定の整理(#84)。プレビュー → 実行 → まとめて元に戻す、パラメーターの挿入、プリセット、外す、フォルダを選ぶ
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrganizeItem, OrganizeOp } from "../api";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { organizeFilesHref, parseRoute } from "../route";
import { RangeItems } from "./OrganizeFiles";

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
/** 入力欄ごとの「項目を差し込む」(フォーカス先に依存しない) */
const fileChips = () => within(screen.getByRole("group", { name: "ファイル名テンプレートに項目を差し込む" }));
const folderChips = () => within(screen.getByRole("group", { name: "フォルダーテンプレートに項目を差し込む" }));

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
    const heading = await screen.findByRole("heading", { name: "プレビュー結果" });
    // 結果はボタンの下に出るだけにせず、見出しへフォーカスとスクロールを移す
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getByText("1 巻を改名・移動、1 巻は対象外、2 巻はそのまま。")).toBeTruthy();
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

  it("CBZ 化もまとめて行うにチェックすると、対象外の元も CBZ 化の計画に入る", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    const check = screen.getByLabelText("CBZ 化もまとめて行う") as HTMLInputElement;
    expect(check.checked).toBe(false);
    expect(check.getAttribute("aria-describedby")).toBe("organize-include-cbz-hint");
    fireEvent.click(check);
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const section = (await screen.findByRole("heading", { name: "CBZ 化(1 巻)" })).closest("section")!;
    expect(within(section).getByText("作者A/まとめ.cbz")).toBeTruthy();
    expect(within(section).getByRole("button", { name: "この元を外す" })).toBeTruthy();
    expect(lastPlan()).toMatchObject({ include_cbz: true });
    expect(screen.getByRole("button", { name: "実行(2 巻)" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "テンプレートの名前で CBZ に整理" })).toBeNull();
    // チェックを外すと前のプレビューは使えない
    fireEvent.click(check);
    await waitFor(() => expect(screen.queryByRole("button", { name: /^実行/ })).toBeNull());
  });

  it("パラメーターは入力欄ごとに差し込め、カーソル位置に入り、選択した文字は置き換える。プリセットは両方のテンプレートを入れる", async () => {
    render(<App />);
    await screen.findByRole("group", { name: "ファイル名テンプレートに項目を差し込む" });
    const file = fileInput();
    fireEvent.focus(file);
    fireEvent.change(file, { target: { value: "第 巻" } });
    file.setSelectionRange(1, 1);
    fireEvent.click(fileChips().getByRole("button", { name: "巻番号(2 桁)" }));
    expect(fileInput().value).toBe("第{number:2} 巻");
    fileInput().setSelectionRange(0, 0);
    fireEvent.click(fileChips().getByRole("button", { name: "作品名" }));
    expect(fileInput().value).toBe("{title}第{number:2} 巻");
    // 選択中の文字(「 巻」)を置き換える
    fileInput().setSelectionRange(18, 20);
    fireEvent.click(fileChips().getByRole("button", { name: "単位(巻 / 話)" }));
    expect(fileInput().value).toBe("{title}第{number:2}{unit}");
    // フォルダーの欄のチップは、フォーカスしていなくてもその欄へ入る
    fireEvent.click(screen.getByText(/フォルダーへ振り分ける/));
    fireEvent.click(folderChips().getByRole("button", { name: "作品名" }));
    expect(folderInput().value).toBe("{title}");

    fireEvent.click(screen.getByRole("button", { name: "出版社別フォルダー" }));
    expect(fileInput().value).toBe("{title} 第{number:2}{unit}");
    expect(folderInput().value).toBe("{publisher}/{title}");
    expect(await screen.findByText("出版社X/サンプル漫画/サンプル漫画 第03巻.cbz")).toBeTruthy(); // パスの例
    fireEvent.click(screen.getByRole("button", { name: "設定のテンプレート" }));
    expect(folderInput().value).toBe("{title}");
  });

  it("テンプレートの誤りがあるときは送信せず、直す欄へフォーカスする(ボタンは塞がない)", async () => {
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
    const preview = screen.getByRole("button", { name: "プレビュー" }) as HTMLButtonElement;
    expect(preview.disabled).toBe(false);
    fireEvent.click(preview);
    expect(document.activeElement).toBe(fileInput());
    expect(posts()).not.toContain("/api/organize/range-plan");
  });

  it("同じ名前の巻はファイル数の多い方を残し、整理後の名前ごとのツリーで出す。「こちらを残す」で逆にできる", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.change(fileInput(), { target: { value: "{title} 第{number:2}{unit}" } });
    fireEvent.change(folderInput(), { target: { value: "{title}" } });
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const clash = (await screen.findByRole("heading", { name: "衝突(1 組)" })).closest("section")!;
    // 見出しは整理後の名前。その下に、同じ名前になる元のファイル名が並ぶ
    const group = within(clash).getByText("サンプル漫画/サンプル漫画 第02巻.cbz").closest("li")!;
    const rows = within(group).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    // ファイル数の少ない方(6)は既定で外れ、多い方(8)が動く
    const small = rows.find((r) => r.textContent?.includes("作者A/サンプル漫画 2巻.cbz"))!;
    const large = rows.find((r) => r.textContent?.includes("スキャン/サンプル漫画 第2巻.cbz"))!;
    expect(within(small).getByText("外した")).toBeTruthy();
    expect(within(small).getByText(/ファイル数が多いため外しました/)).toBeTruthy();
    expect(within(small).getByText("(6 ファイル)")).toBeTruthy();
    expect(within(large).getByText("改名・移動")).toBeTruthy();
    expect(screen.getByRole("button", { name: "実行(2 巻)" })).toBeTruthy(); // 衝突を残さず、動く巻が決まっている

    // 「こちらを残す」: ファイル数の多い方を外して作り直すと、少ない方が動く
    fireEvent.click(within(small).getByRole("button", { name: "こちらを残す" }));
    await screen.findByRole("heading", { name: "外した(1 巻)" });
    expect(lastPlan()).toMatchObject({ exclude_volume_ids: [13], folder_template: "{title}" });
    expect(screen.queryByRole("heading", { name: /^衝突/ })).toBeNull();

    // 入力を変えると前のプレビューは消える
    fireEvent.change(fileInput(), { target: { value: "{title}" } });
    await waitFor(() => expect(screen.queryByRole("heading", { name: /^外した/ })).toBeNull());
    expect(screen.queryByRole("button", { name: /^実行/ })).toBeNull();
  });

  it("CBZ 化は整理後の名前の下に元のファイル名を出す", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.click(screen.getByLabelText("CBZ 化もまとめて行う"));
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const section = (await screen.findByRole("heading", { name: "CBZ 化(1 巻)" })).closest("section")!;
    const group = within(section).getByText("作者A/まとめ.cbz").closest("li")!;
    expect(within(group).getByText("作者A/まとめ.zip")).toBeTruthy();
  });

  it("フォルダーを選ぶと範囲のパスに入り、プレビューはその範囲だけ", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "フォルダーを選ぶ" }));
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    fireEvent.click(await within(dialog).findByRole("button", { name: "スキャン" }));
    await within(dialog).findByText("この中にフォルダーはありません");
    fireEvent.click(within(dialog).getByRole("button", { name: "このフォルダーにする" }));
    expect((screen.getByLabelText("範囲のパス") as HTMLInputElement).value).toBe("スキャン");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    });
    await screen.findByRole("heading", { name: "プレビュー結果" });
    expect(lastPlan()).toMatchObject({ path: "スキャン" });
    expect(screen.queryByText("作者A/サンプル漫画 2巻.cbz")).toBeNull();
  });

  it("フォルダー選択は Esc で閉じ、開いたボタンへフォーカスを戻す", async () => {
    render(<App />);
    const opener = await screen.findByRole("button", { name: "フォルダーを選ぶ" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    await within(dialog).findByRole("button", { name: "スキャン" });
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(opener);
  });
});

const baseItem: OrganizeItem = {
  volume_id: 1, title: "巻", from_rel_path: "a.cbz", output_rel_path: "出力/X.cbz", status: "conflict",
  reason: null, cbz_volume_id: null, changes: [], page_count: 5, superseded_by: [],
};
const opWith = (items: OrganizeItem[]) => ({ op_id: "op-1", state: "planned", items, volumes: [], warnings: [], problems: [] }) as unknown as OrganizeOp;

describe("衝突の表示", () => {
  afterEach(cleanup);

  it("ファイル数が同じ巻は衝突のまま整理後の名前の下にまとめ、どちらかを外させる。名前の大文字小文字は同じ組", () => {
    const onExclude = vi.fn();
    const op = opWith([
      { ...baseItem, volume_id: 1, from_rel_path: "A/第1巻.cbz", reason: "「B」と同じ名前になります。どれかを外してください" },
      { ...baseItem, volume_id: 2, from_rel_path: "B/第1巻.cbz", output_rel_path: "出力/x.CBZ" },
      { ...baseItem, volume_id: 3, from_rel_path: "C/単独.cbz", output_rel_path: "出力/Y.cbz", reason: "この場所にあったファイルの記録が残っています" },
    ]);
    render(<RangeItems op={op} onExclude={onExclude} />);
    expect(screen.getByRole("heading", { name: "衝突(2 組)" })).toBeTruthy();
    const group = screen.getByText("出力/X.cbz").closest("li")!;
    expect(within(group).getAllByRole("listitem")).toHaveLength(2);
    // 1 つで衝突している巻も、同じ形(名前の下に元のファイル名)で出す
    expect(within(screen.getByText("出力/Y.cbz").closest("li")!).getByText("C/単独.cbz")).toBeTruthy();
    fireEvent.click(within(group).getAllByRole("button", { name: "この巻を外す" })[1]);
    expect(onExclude).toHaveBeenCalledWith(2);
    expect(screen.queryByRole("button", { name: "こちらを残す" })).toBeNull();
  });

  it("自動で外した巻の「こちらを残す」は、組のほかの巻(自動で外した巻も)をすべて外す。利用者が外した巻は数えない", () => {
    const onKeep = vi.fn();
    const op = opWith([
      { ...baseItem, volume_id: 1, status: "move", page_count: 10 },
      { ...baseItem, volume_id: 2, status: "skipped", page_count: 8, superseded_by: [1], reason: "ファイル数が多いため外しました" },
      { ...baseItem, volume_id: 3, status: "skipped", page_count: 5, superseded_by: [1], reason: "ファイル数が多いため外しました" },
      { ...baseItem, volume_id: 4, status: "skipped", page_count: 9, output_rel_path: "出力/x.cbz", superseded_by: [], reason: "外しました" },
    ]);
    render(<RangeItems op={op} onExclude={() => undefined} onKeep={onKeep} />);
    const row = screen.getByText("(5 ファイル)").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "こちらを残す" }));
    expect(onKeep).toHaveBeenCalledWith([1, 2]);
  });

  it("CBZ 化する元が譲って外れたときは「この元を残す」と出す。履歴(操作なし)にはボタンを出さない", () => {
    const items: OrganizeItem[] = [
      { ...baseItem, volume_id: 1, status: "cbz", conversion: true },
      { ...baseItem, volume_id: 2, status: "skipped", conversion: true, page_count: 2, superseded_by: [1], reason: "ファイル数が多いため外しました" },
    ];
    const { unmount } = render(<RangeItems op={opWith(items)} onExclude={() => undefined} onKeep={() => undefined} />);
    expect(screen.getByRole("button", { name: "この元を残す" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "この元を外す" })).toBeTruthy();
    unmount();
    render(<RangeItems op={opWith(items)} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
