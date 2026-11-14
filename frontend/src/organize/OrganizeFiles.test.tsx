// 範囲指定の整理(#84)。プレビュー → 実行 → まとめて元に戻す、パラメーターの挿入、プリセット、外す、フォルダを選ぶ
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { OrganizeItem, OrganizeOp } from "../api";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { organizeFilesHref, parseRoute } from "../route";
import { ClashGroups, RangeItems, summarizeResult } from "./OrganizeFiles";

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
  it("管理の整理タブ(#207)で開ける", async () => {
    expect(parseRoute(organizeFilesHref)).toEqual({ name: "admin", tab: "organize" });
    window.location.hash = organizeFilesHref;
    render(<App />);
    await screen.findByLabelText("ファイル名テンプレート");
    expect(screen.getByRole("link", { name: "整理" }).getAttribute("aria-current")).toBe("page");
  });

  it("ファイル名の整理だけをプレビューし、実行してからまとめて元に戻せる", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const heading = await screen.findByRole("heading", { name: "プレビュー結果" });
    // 結果はボタンの下に出るだけにせず、見出しへフォーカスとスクロールを移す
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getByText("1 巻を整理します。実行するまで蔵書は変わりません。")).toBeTruthy();
    const facts = screen.getByText("実行する").closest("dl")!;
    expect(within(facts).getByText("改名・移動 1 巻")).toBeTruthy();
    expect(within(facts).getByText("対象外")).toBeTruthy();
    expect(within(facts).getByText("そのまま")).toBeTruthy();
    expect(await screen.findByText("半角カナを全角にしました・空白を整えました(全角の空白・続いた空白)")).toBeTruthy();
    expect(screen.getByText("作者A/サンプル漫画 第1巻.cbz")).toBeTruthy();
    // CBZ 化が要る巻は対象外にして、CBZ 化の画面へ案内する
    expect(screen.getByRole("link", { name: "テンプレートの名前で CBZ に整理" })).toBeTruthy();
    expect(posts()).toEqual(["/api/organize/range-plan"]); // プレビューだけでは実行しない
    expect(lastPlan()).toEqual({ root_id: 1, path: "", file_template: "", folder_template: "", exclude_volume_ids: [] });

    // 条件を変えると先頭の欄へフォーカスが戻る
    fireEvent.click(screen.getByRole("button", { name: "条件を変える" }));
    expect(document.activeElement).toBe(screen.getByLabelText("蔵書フォルダ"));

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
    // CBZ 化は元ごとにまとめ、元のパスは見出しに 1 回だけ出す
    const source = (await screen.findByText("作者A/まとめ.zip")).closest(".organize-source") as HTMLElement;
    const row = within(source).getByText("作者A/まとめ.cbz").closest("li")!;
    expect(within(row).queryByText("作者A/まとめ.zip")).toBeNull();
    expect(within(row).getByRole("button", { name: "この本を整理しない" })).toBeTruthy();
    expect(screen.getByText(/全巻を作って確かめた後で退避フォルダへ移します/)).toBeTruthy();
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

  it("競合は既定でファイル数の多い方を残し、行のチェックで少ない方を残す方に変えられる", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.change(fileInput(), { target: { value: "{title} 第{number:2}{unit}" } });
    fireEvent.change(folderInput(), { target: { value: "{title}" } });
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const heading = await screen.findByRole("heading", { name: "競合（1 組）" });
    const group = within(heading.closest("section")!).getByRole("group", { name: /サンプル漫画\/サンプル漫画 第02巻/ });
    const large = within(group).getByRole("radio", { name: /スキャン\/サンプル漫画 第2巻\.cbz/ }) as HTMLInputElement;
    const small = within(group).getByRole("radio", { name: /作者A\/サンプル漫画 2巻\.cbz/ }) as HTMLInputElement;
    expect(large.checked).toBe(true);
    expect(small.checked).toBe(false);
    expect(within(group).getByText("残す")).toBeTruthy();
    expect(within(group).getByText("整理しない")).toBeTruthy();
    expect(within(group).getByText(/ファイル数が多いため、整理しません/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "実行(2 巻)" })).toBeTruthy(); // 競合を残さず、動く巻が決まっている
    const initialFacts = screen.getByText("実行する").closest("dl")!;
    expect(within(initialFacts).getByText("競合")).toBeTruthy();
    expect(within(initialFacts).queryByText("整理しない")).toBeNull(); // 競合の中の「整理しない」は二重に数えない

    // 少ない方を残す方に変える: 多い方(13)を外して作り直す。組は残り、選んだ行にチェックが入る
    fireEvent.click(small);
    await waitFor(() => expect(lastPlan()).toMatchObject({ exclude_volume_ids: [13], folder_template: "{title}" }));
    await waitFor(() => expect(document.activeElement?.getAttribute("data-keep-volume")).toBe("12"));
    const regroup = () => screen.getByRole("group", { name: /サンプル漫画\/サンプル漫画 第02巻/ });
    expect((within(regroup()).getByRole("radio", { name: /作者A\/サンプル漫画 2巻\.cbz/ }) as HTMLInputElement).checked).toBe(true);
    expect(within(regroup()).getByText(/利用者の選択/)).toBeTruthy();
    const facts = screen.getByText("実行する").closest("dl")!;
    expect(within(facts).getByText("改名・移動 2 巻")).toBeTruthy();
    expect(within(facts).queryByText("整理しない")).toBeNull();

    // 前に外した方を選び直すと、外した記録から抜いて送る(両方とも整理しないにならない。#153)
    fireEvent.click(within(regroup()).getByRole("radio", { name: /スキャン\/サンプル漫画 第2巻\.cbz/ }));
    await waitFor(() => expect(lastPlan()).toMatchObject({ exclude_volume_ids: [12] }));
    await waitFor(() =>
      expect((within(regroup()).getByRole("radio", { name: /スキャン\/サンプル漫画 第2巻\.cbz/ }) as HTMLInputElement).checked).toBe(true),
    );
    expect(screen.getByRole("button", { name: "実行(2 巻)" })).toBeTruthy();

    // 入力を変えると前のプレビューは消え、そのことを知らせる
    fireEvent.change(fileInput(), { target: { value: "{title}" } });
    await waitFor(() => expect(screen.queryByText(/競合/)).toBeNull());
    expect(screen.getByText(/前のプレビューを取り消しました/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^実行/ })).toBeNull();
  });

  it("CBZ 化は元と先のパスをリストで出す", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    fireEvent.click(screen.getByLabelText("CBZ 化もまとめて行う"));
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    const source = (await screen.findByText("作者A/まとめ.zip")).closest(".organize-source") as HTMLElement;
    expect(within(source).getByText("作者A/まとめ.cbz")).toBeTruthy();
  });

  it("元を削除するオプションは CBZ 化を選んだときだけ出し、実行には確認のチェックが要る", async () => {
    render(<App />);
    await screen.findByRole("option", { name: "E:/漫画" });
    expect(screen.queryByLabelText("CBZ 化した元を削除する")).toBeNull();
    fireEvent.click(screen.getByLabelText("CBZ 化もまとめて行う"));
    const del = screen.getByLabelText("CBZ 化した元を削除する") as HTMLInputElement;
    expect(del.checked).toBe(false); // 既定は退避
    fireEvent.click(del);
    fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    await screen.findByRole("heading", { name: "プレビュー結果" });
    expect(lastPlan()).toMatchObject({ include_cbz: true, delete_originals: true });
    const commit = await screen.findByRole("button", { name: "実行して元を削除(2 巻)" });
    // 確認のチェックが無ければ実行せず、確認欄へ案内する(ボタンは塞がない)
    fireEvent.click(commit);
    const confirm = screen.getByLabelText(/この整理を元に戻せないことを確認しました/) as HTMLInputElement;
    expect(document.activeElement).toBe(confirm);
    expect(confirm.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText(/確認のチェックを入れてください/)).toBeTruthy();
    expect(posts().filter((x) => x.endsWith("/execute"))).toHaveLength(0);
    fireEvent.click(confirm);
    await act(async () => {
      fireEvent.click(commit);
    });
    await waitFor(() => expect(posts().some((x) => x.endsWith("/execute"))).toBe(true));
    expect(await screen.findByText("元を削除したので、元に戻せません。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "まとめて元に戻す" })).toBeNull();
  });

  it("フォルダーを選ぶと範囲のパスに入り、プレビューはその範囲だけ", async () => {
    render(<App />);
    const opener = await screen.findByRole("button", { name: "フォルダーを選ぶ" });
    expect(opener.getAttribute("aria-haspopup")).toBe("dialog");
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole("heading", { name: "フォルダーを選ぶ" })));
    fireEvent.click(await within(dialog).findByRole("button", { name: "スキャン" }));
    await within(dialog).findByText("この中にフォルダーはありません");
    fireEvent.click(within(dialog).getByRole("button", { name: "このフォルダーにする" }));
    expect((screen.getByLabelText("範囲のパス") as HTMLInputElement).value).toBe("スキャン");

    // 開き直すと今のパスから始まり、1 つ上へ戻れる
    fireEvent.click(screen.getByRole("button", { name: "フォルダーを選ぶ" }));
    const again = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    await within(again).findByRole("button", { name: "1 つ上へ" });
    expect(again.querySelector('[aria-current="location"]')?.textContent).toBe("スキャン");
    fireEvent.click(within(again).getByRole("button", { name: "1 つ上へ" }));
    await within(again).findByRole("button", { name: "作者A" });
    expect(again.querySelector('[aria-current="location"]')?.textContent).toBe("蔵書フォルダ全体");
    act(() => (again as HTMLDialogElement).close());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "プレビュー" }));
    });
    await screen.findByRole("heading", { name: "プレビュー結果" });
    expect(lastPlan()).toMatchObject({ path: "スキャン" });
    expect(screen.queryByText("作者A/サンプル漫画 2巻.cbz")).toBeNull();
  });

  it("前回の範囲が読めないときは蔵書フォルダ全体を表示する", async () => {
    render(<App />);
    await screen.findByLabelText("範囲のパス");
    fireEvent.change(screen.getByLabelText("範囲のパス"), { target: { value: "missing" } });
    fireEvent.click(screen.getByRole("button", { name: "フォルダーを選ぶ" }));
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    expect(await within(dialog).findByText(/蔵書フォルダ全体を表示しています/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "作者A" })).toBeTruthy();
    expect(dialog.querySelector('[aria-current="location"]')?.textContent).toBe("蔵書フォルダ全体");
  });

  it("フォルダー選択はネイティブの <dialog> をモーダルで開く", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "フォルダーを選ぶ" }));
    const dialog = (await screen.findByRole("dialog", { name: "フォルダーを選ぶ" })) as HTMLDialogElement;
    expect(dialog.tagName).toBe("DIALOG");
    expect(dialog.open).toBe(true);
  });

  it("フォルダー選択は Esc で閉じ、開いたボタンへフォーカスを戻す", async () => {
    render(<App />);
    const opener = await screen.findByRole("button", { name: "フォルダーを選ぶ" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    await within(dialog).findByRole("button", { name: "スキャン" });
    act(() => (dialog as HTMLDialogElement).close()); // Esc はブラウザが close() する
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(opener);
  });
});

const baseItem: OrganizeItem = {
  volume_id: 1, title: "巻", from_rel_path: "a.cbz", output_rel_path: "出力/X.cbz", status: "conflict",
  reason: null, cbz_volume_id: null, changes: [], page_count: 5, superseded_by: [],
};
const opWith = (items: OrganizeItem[]) => ({ op_id: "op-1", state: "planned", items, volumes: [], warnings: [], problems: [] }) as unknown as OrganizeOp;

describe("競合の表示", () => {
  afterEach(cleanup);

  it("同じ名前になる組は見出しで分け、同数は残す方を選ばせる。1 つだけの競合は整理しないにできる。名前の大文字小文字は同じ組", () => {
    const onExclude = vi.fn();
    const onChoose = vi.fn();
    const op = opWith([
      { ...baseItem, volume_id: 1, from_rel_path: "A/第1巻.cbz", reason: "「B」と同じ名前になります。残す方を選んでください" },
      { ...baseItem, volume_id: 2, from_rel_path: "B/第1巻.cbz", output_rel_path: "出力/x.CBZ" },
      { ...baseItem, volume_id: 3, from_rel_path: "C/単独.cbz", output_rel_path: "出力/Y.cbz", reason: "この場所にあったファイルの記録が残っています" },
    ]);
    render(<RangeItems op={op} onExclude={onExclude} onChoose={onChoose} />);
    expect(screen.getByRole("heading", { name: "競合（1 組）" })).toBeTruthy();
    const group = screen.getByRole("group", { name: /出力\/X\.cbz/i });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(within(group).getAllByText(/残す方を選んでください/).length).toBeGreaterThan(0);
    fireEvent.click(within(group).getByRole("radio", { name: /B\/第1巻\.cbz/ }));
    expect(onChoose).toHaveBeenCalledWith([1], expect.objectContaining({ volume_id: 2 }));
    // 1 つだけの競合は組にせず、「実行できない」として理由つきで出す(最初から開いておく)
    expect(screen.queryByRole("group", { name: /出力\/Y\.cbz/ })).toBeNull();
    const rest = screen.getByText(/整理しない内容/).closest("details")!;
    expect(rest.open).toBe(true);
    expect(within(rest).getByRole("heading", { name: "実行できない（1 巻）" })).toBeTruthy();
    expect(within(rest).getByText("この場所にあったファイルの記録が残っています")).toBeTruthy();
    expect(onExclude).not.toHaveBeenCalled();
  });

  it("同じ元の別の巻の選択待ちでも、ファイル数の多い方にチェックを入れ、選ぶと実行に入ると知らせる", () => {
    const op = opWith([
      { ...baseItem, volume_id: 1, status: "conflict", conversion: true, page_count: 10, waiting_for: [9],
        reason: "同じ元の「第04巻」の競合で残す方を選ぶと CBZ 化します" },
      { ...baseItem, volume_id: 2, status: "skipped", conversion: true, page_count: 7, superseded_by: [1],
        reason: "ファイル数が多いため、整理しません" },
    ]);
    render(<RangeItems op={op} onChoose={() => undefined} />);
    const group = screen.getByRole("group", { name: /出力\/X\.cbz/i });
    const radios = within(group).getAllByRole("radio") as HTMLInputElement[];
    expect(radios[0].checked).toBe(true);
    expect(within(group).getByText(/競合で残す方を選ぶと、実行に入ります/)).toBeTruthy();
    expect(summarizeResult(op)).toMatchObject({ clashes: 1, needsChoice: 0, clashNoAction: 0 });
  });

  it("選び直し中は、通信が返るまで選んだ行のチェックを保つ", () => {
    const op = opWith([
      { ...baseItem, volume_id: 1, page_count: 5 },
      { ...baseItem, volume_id: 2, page_count: 5 },
    ]);
    const { rerender } = render(<ClashGroups op={op} onChoose={() => undefined} />);
    const group = screen.getByRole("group", { name: /出力\/X\.cbz/i });
    const radios = within(group).getAllByRole("radio") as HTMLInputElement[];
    expect(radios.every((r) => !r.checked)).toBe(true);
    rerender(<ClashGroups op={op} onChoose={() => undefined} pendingKeep={2} />);
    expect((within(group).getAllByRole("radio")[1] as HTMLInputElement).checked).toBe(true);
  });

  it("自動で整理しないにした巻は、別の行を選ぶと組のほかの巻(自動で整理しない巻も)を外す。利用者が整理しないにした巻は数えない", () => {
    const onChoose = vi.fn();
    const op = opWith([
      { ...baseItem, volume_id: 1, status: "move", page_count: 10 },
      { ...baseItem, volume_id: 2, status: "skipped", page_count: 8, superseded_by: [1], reason: "ファイル数が多いため、整理しません" },
      { ...baseItem, volume_id: 3, status: "skipped", page_count: 5, superseded_by: [1], reason: "ファイル数が多いため、整理しません" },
      { ...baseItem, volume_id: 4, status: "skipped", page_count: 9, output_rel_path: "出力/x.cbz", superseded_by: [], reason: "利用者が整理しないにしました" },
    ]);
    render(<RangeItems op={op} onChoose={onChoose} />);
    const group = screen.getByRole("group", { name: /出力\/X\.cbz/i });
    // 利用者が整理しないにした巻(9 ファイル)は行だけ残し、選択肢にはしない
    expect(within(group).getAllByRole("radio")).toHaveLength(3);
    const userRow = within(group).getByText(/利用者が整理しないにしました/).closest("li")!;
    expect(within(userRow).queryByRole("radio")).toBeNull();
    const row = within(group).getByText("5 ファイル").closest("li")!;
    fireEvent.click(within(row).getByRole("radio"));
    expect(onChoose).toHaveBeenCalledWith([1, 2], expect.objectContaining({ volume_id: 3 }));
  });

  it("CBZ 化する元が譲って整理しないになったときも、行を選んで残す方を変えられる。操作なしの履歴にはチェックを出さない", () => {
    const onChoose = vi.fn();
    const items: OrganizeItem[] = [
      { ...baseItem, volume_id: 1, status: "cbz", conversion: true, page_count: 8 },
      { ...baseItem, volume_id: 2, status: "skipped", conversion: true, page_count: 2, superseded_by: [1], reason: "ファイル数が多いため、整理しません" },
    ];
    const { unmount } = render(<RangeItems op={opWith(items)} onChoose={onChoose} />);
    const group = screen.getByRole("group", { name: /出力\/X\.cbz/i });
    const radios = within(group).getAllByRole("radio") as HTMLInputElement[];
    expect(radios[0].checked).toBe(true); // 8 ファイルの方を既定で残す
    const small = within(group).getByText("2 ファイル").closest("li")!;
    fireEvent.click(within(small).getByRole("radio"));
    expect(onChoose).toHaveBeenCalledWith([1], expect.objectContaining({ volume_id: 2 }));
    unmount();
    render(<RangeItems op={opWith(items)} />);
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
