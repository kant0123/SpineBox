// 管理の「読めなかったもの」タブ(#364)。全区分の表示・範囲絞り込み・キーワード検索・ページ送り・空状態/エラーを確かめる。
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ScanIssueRecord } from "../api";
import { createMockBackend, type MockOptions } from "../mock/mockBackend";

let backend: ReturnType<typeof createMockBackend>;

function setup(options?: MockOptions) {
  backend = createMockBackend(undefined, options);
  localStorage.clear();
  window.location.hash = "#/admin/issues";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.status === 204 ? null : r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
}

beforeEach(() => {
  setup();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
});

describe("管理の読めなかったものタブ", () => {
  it("前回スキャンだけでなく解析エラー・オフライン・削除済み・受け入れまで全件を表示する", async () => {
    render(<App />);
    const table = await screen.findByRole("table", { name: "読めなかったものの一覧" });
    expect(within(table).getByText("E:/本/作者A/壊れた巻.zip")).toBeTruthy();
    expect(within(table).getByText("E:/本/作者B/まとめ.zip › ../evil.jpg")).toBeTruthy();
    expect(within(table).getByText("E:/本/作者C/まとめ.7z › v01.rar")).toBeTruthy();
    expect(within(table).getByText("F:/外付け本棚/旧データ.rar")).toBeTruthy();
    expect(within(table).getByText("E:/本/削除済み.cbz")).toBeTruthy();
    expect(within(table).getByText("E:/受け入れ/未整理.zip")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("6 件");
  });

  it("範囲のラジオボタンで絞り込みと件数表示ができる", async () => {
    render(<App />);
    await screen.findByRole("table", { name: "読めなかったものの一覧" });
    const group = screen.getByRole("group", { name: "表示する範囲" });
    expect(within(group).getByLabelText("すべて (6)")).toBeTruthy();
    expect(within(group).getByLabelText("現在の蔵書 (2)")).toBeTruthy();
    expect(within(group).getByLabelText("中の巻の解析 (1)")).toBeTruthy();
    expect(within(group).getByLabelText("オフライン・削除済み等 (2)")).toBeTruthy();
    expect(within(group).getByLabelText("受け入れ (1)")).toBeTruthy();

    fireEvent.click(within(group).getByLabelText("中の巻の解析 (1)"));
    await waitFor(() => {
      const table = screen.getByRole("table", { name: "読めなかったものの一覧" });
      expect(within(table).getAllByRole("row")).toHaveLength(2); // header + 1 data row
      expect(within(table).getByText("E:/本/作者C/まとめ.7z › v01.rar")).toBeTruthy();
    });
    expect(screen.getByRole("status").textContent).toBe("1 件(全体 6 件)");
  });

  it("キーワード検索とクリアボタンが動く", async () => {
    render(<App />);
    await screen.findByRole("table", { name: "読めなかったものの一覧" });

    vi.useFakeTimers();
    const input = screen.getByLabelText("パス・理由で探す") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "外付け 7-Zip" } });
    await act(async () => {
      vi.advanceTimersByTime(350);
    });
    vi.useRealTimers();

    await waitFor(() => {
      const rows = within(screen.getByRole("table", { name: "読めなかったものの一覧" })).getAllByRole("row");
      expect(rows).toHaveLength(2);
      expect(screen.getByText("F:/外付け本棚/旧データ.rar")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "クリア" }));
    expect(input.value).toBe("");
    await waitFor(() => {
      const rows = within(screen.getByRole("table", { name: "読めなかったものの一覧" })).getAllByRole("row");
      expect(rows).toHaveLength(7);
    });
  });

  it("100 件を超える記録をページ送りで確認できる", async () => {
    const many: ScanIssueRecord[] = Array.from({ length: 105 }, (_, i) => ({
      id: `scan:${i + 1}`,
      kind: "scan",
      status: "active",
      path: `E:/本/book_${String(i + 1).padStart(3, "0")}.zip`,
      container_path: null,
      entry_name: null,
      reason: "壊れたアーカイブ",
      recorded_at: `2026-11-20T00:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}Z`,
    }));
    setup({ scanIssueRecords: many });
    render(<App />);

    await screen.findByRole("table", { name: "読めなかったものの一覧" });
    expect(screen.getByRole("status").textContent).toBe("105 件中 1〜100 件を表示");
    const nav = screen.getByRole("navigation", { name: "ページ移動" });
    expect(within(nav).getByText("ページ 1 / 2")).toBeTruthy();
    const prev = within(nav).getByRole("button", { name: "前のページ" }) as HTMLButtonElement;
    const next = within(nav).getByRole("button", { name: "次のページ" }) as HTMLButtonElement;
    expect(prev.disabled).toBe(true);
    expect(next.disabled).toBe(false);

    fireEvent.click(next);
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toBe("105 件中 101〜105 件を表示");
      expect(within(nav).getByText("ページ 2 / 2")).toBeTruthy();
      expect(within(screen.getByRole("table", { name: "読めなかったものの一覧" })).getAllByRole("row")).toHaveLength(6);
    });
    expect(prev.disabled).toBe(false);
    expect(next.disabled).toBe(true);

    fireEvent.click(prev);
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("105 件中 1〜100 件を表示"));
  });

  it("0 件時と絞り込み 0 件時・更新ボタン・API 失敗時の表示が正しい", async () => {
    setup({ scanIssueRecords: [] });
    render(<App />);
    expect(await screen.findByText("読めなかったものはありません。")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();

    cleanup();
    vi.unstubAllGlobals();
    setup({
      scanIssueRecords: [{
        id: "scan:1",
        kind: "scan",
        status: "active",
        path: "E:/本/broken.zip",
        container_path: null,
        entry_name: null,
        reason: "壊れたアーカイブ",
        recorded_at: "2026-11-20T03:00:00Z",
      }],
    });
    render(<App />);
    await screen.findByRole("table", { name: "読めなかったものの一覧" });
    fireEvent.click(screen.getByRole("button", { name: "更新" }));
    await waitFor(() => expect(backend.log.filter((l) => l.path === "/api/scan-issues")).toHaveLength(2));

    fireEvent.click(screen.getByLabelText("受け入れ (0)"));
    expect(await screen.findByText("条件に合う記録はありません。")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("0 件(全 1 件中)");

    cleanup();
    vi.unstubAllGlobals();
    setup({ scanIssuesFailure: true });
    render(<App />);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
