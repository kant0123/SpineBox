// モック API を相手に、作品一覧・作品ページ(欠け・重複のまとめ・手動編集・付け替え)・統合の提案の承認/却下を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { confidenceText, evidenceLines, missingText } from "./labels";

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

describe("ルートと文言", () => {
  it("作品・作品ページ・提案のルートを読む", () => {
    expect(parseRoute("#/works")).toEqual({ name: "works" });
    expect(parseRoute("#/works/12")).toEqual({ name: "work", id: "12" });
    expect(parseRoute("#/works/proposals")).toEqual({ name: "work-proposals" });
    expect(parseRoute("#/works/abc")).toEqual({ name: "library" });
  });

  it("確信度・欠け・根拠の文面", () => {
    expect(confidenceText(0.989)).toBe("99%");
    expect(missingText({ missing_volumes: [3, 5], missing_episodes: [2] })).toBe("3・5 巻、2 話");
    expect(
      evidenceLines({ duplicate: [{ matched_pages: 186, compared_pages: 188, ratio: 0.989, rule: "same_number", volume_a: { id: 1, title: "A" }, volume_b: { id: 2, title: "B" } }] }),
    ).toEqual(["「A」と「B」は、巻番号の候補が同じで見た目の近いページが 186 / 188 ページ(99%)"]);
  });
});

describe("作品の一覧", () => {
  it("作品カードに欠け・重複・統合済み・提案が出て、作品ページへのリンクになる", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(card).getByText("欠け 3 巻")).toBeTruthy();
    expect(within(card).getByText("重複 1")).toBeTruthy();
    expect(within(card).getByText("統合済み")).toBeTruthy();
    expect(within(card).getByText(/別名: Sample manga/)).toBeTruthy();
    expect(card.querySelector("a")!.getAttribute("href")).toBe("#/works/1");
    expect(screen.getByText(/統合の提案が 1 件/).closest("a")!.getAttribute("href")).toBe("#/works/proposals");
    // 巻の一覧と行き来できる
    expect(screen.getByRole("link", { name: "巻" }).getAttribute("href")).toBe("#/");
  });
});

describe("作品ページ", () => {
  it("巻を番号順に並べ、重複はまとめ、欠けを示す", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    expect(await screen.findByText("欠けている巻: 3 巻")).toBeTruthy();
    const slots = screen.getAllByRole("listitem").filter((li) => li.classList.contains("slot"));
    expect(slots.map((s) => s.querySelector("strong")!.textContent)).toEqual(["1巻", "2巻", "4巻"]);
    expect(within(slots[0]).getByText("重複 2 冊")).toBeTruthy();
    expect(within(slots[0]).getByText("サンプル漫画 第01巻")).toBeTruthy();
    expect(within(slots[0]).getByText("Sample manga v01")).toBeTruthy();
    expect(screen.getByText(/を自動で統合/)).toBeTruthy();
    expect(screen.getByText(/186 \/ 188 ページ/)).toBeTruthy();
    expect(screen.getAllByText(/確信度/).length).toBeGreaterThan(0);
  });

  it("作品名・著者を手で直し、別名を足して外せる", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    await screen.findByText("欠けている巻: 3 巻");
    fireEvent.change(screen.getByLabelText("作品名"), { target: { value: "サンプル漫画 改" } });
    fireEvent.change(screen.getByLabelText("著者"), { target: { value: "花子" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getAllByText("手動で編集").length).toBe(2));
    expect(backend.log.find((l) => l.method === "PATCH")!.body).toEqual({ title: "サンプル漫画 改", author: "花子" });
    // 直す前の作品名は別名に残る
    expect(within(screen.getByLabelText("別名を足す").closest("section")!).getByText("サンプル漫画", { selector: "span" })).toBeTruthy();

    fireEvent.change(screen.getByLabelText("別名を足す"), { target: { value: "Sample" } });
    fireEvent.click(screen.getByRole("button", { name: "足す" }));
    await waitFor(() => expect(screen.getByText("Sample")).toBeTruthy());
    fireEvent.click(within(screen.getByText("Sample").closest("li")!).getByRole("button", { name: "外す" }));
    await waitFor(() => expect(screen.queryByText("Sample")).toBeNull());
  });

  it("別名の一致で別の作品に自動統合されたら、吸収先の作品ページへ移る", async () => {
    window.location.hash = "#/works/2";
    render(<App />);
    await screen.findByLabelText("別名を足す");
    fireEvent.change(screen.getByLabelText("別名を足す"), { target: { value: "別の読み物" } });
    fireEvent.click(screen.getByRole("button", { name: "足す" }));
    await waitFor(() => expect(window.location.hash).toBe("#/works/3"));
    expect(screen.queryByText(/この作品は見つかりません/)).toBeNull();
    expect(await screen.findByRole("heading", { name: "別の読み物" })).toBeTruthy();
  });

  it("選んだ巻を新しい作品に分けられる", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    await screen.findByText("欠けている巻: 3 巻");
    fireEvent.click(screen.getByLabelText("Sample manga v01 を選ぶ"));
    fireEvent.change(screen.getByLabelText("新しい作品名"), { target: { value: "別スキャン" } });
    fireEvent.click(screen.getByRole("button", { name: "新しい作品に分ける" }));
    await waitFor(() => expect(window.location.hash).toMatch(/^#\/works\/\d+$/));
    expect(backend.log.find((l) => l.path === "/api/works/assign")!.body).toEqual({ volume_ids: [6], work_id: null, title: "別スキャン" });
  });

  it("存在しない(統合された)作品は、見つからないと言う", async () => {
    window.location.hash = "#/works/99";
    render(<App />);
    expect(await screen.findByText(/この作品は見つかりません/)).toBeTruthy();
  });
});

describe("統合の提案", () => {
  it("確信度と根拠を見て、却下すると消えて、戻せる", async () => {
    window.location.hash = "#/works/proposals";
    render(<App />);
    const card = (await screen.findByText("外付けの作品")).closest("li")!;
    expect(within(card).getByText("60%")).toBeTruthy();
    expect(within(card).getByText(/見た目の近いページが 60 \/ 100 ページ/)).toBeTruthy();
    expect(screen.getByText(/承認するまで統合しません/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "別の作品" }));
    await waitFor(() => expect(screen.getByText("確認待ちの提案はありません。")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("却下・分けたものを表示"));
    fireEvent.click(await screen.findByRole("button", { name: "提案に戻す" }));
    await waitFor(() => expect(screen.getByText("却下した提案はありません。")).toBeTruthy());
  });

  it("承認すると 2 つの作品が 1 つになる", async () => {
    window.location.hash = "#/works/proposals";
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "同じ作品として統合" }));
    await waitFor(() => expect(screen.getByText("確認待ちの提案はありません。")).toBeTruthy());
    expect(backend.log.some((l) => l.path === "/api/works/proposals/1/approve")).toBe(true);
    window.location.hash = "#/works";
    await waitFor(() => expect(screen.getByText("外付けの作品")).toBeTruthy());
    expect(screen.queryByText("別の読み物")).toBeNull();
  });
});

describe("公開データベースの情報(#50)", () => {
  it("作品の一覧に確認待ちが出て、情報の取得ボタンと利用元の表示がある", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(card).getByText("候補の確認待ち")).toBeTruthy();
    const credits = await screen.findByRole("contentinfo", { name: "利用している公開データベース" });
    expect(within(credits).getByRole("link", { name: "国立国会図書館サーチ" }).getAttribute("href")).toBe("https://ndlsearch.ndl.go.jp/");
    expect(credits.textContent).toContain("NDL サーチ API");
    expect(credits.textContent).toContain("AniList API");
    expect(credits.textContent).toContain("MangaUpdates API");
    fireEvent.click(await screen.findByRole("button", { name: "情報を補完" }));
    await waitFor(() => expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/works/metadata/fetch")).toBe(true));
  });

  it("確認待ちの候補を選ぶと反映され、出所が公開データベースになる。失敗した取得元はその理由が出る", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    const ndl = (await screen.findByLabelText("国立国会図書館サーチとの連携")) as HTMLElement;
    expect(within(ndl).getByText("確認待ち")).toBeTruthy();
    expect(screen.getByLabelText("AniListとの連携").textContent).toContain("漫画か小説かが決まるまで保留");
    expect(screen.getByLabelText("MangaUpdatesとの連携").textContent).toContain("応答が時間内に返りませんでした");
    // 種別はまだ入っていない
    expect((screen.getByLabelText("種別") as HTMLSelectElement).value).toBe("");

    fireEvent.click(within(ndl).getByRole("button", { name: "サンプル漫画(漫画・集英社) を採用" }));
    await waitFor(() => expect(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getByText("選んだ候補と連携中")).toBeTruthy());
    expect(backend.log.find((l) => l.path === "/api/works/1/links/ndl/choose")!.body).toEqual({ source_id: "ndl:a1" });
    expect((screen.getByLabelText("種別") as HTMLSelectElement).value).toBe("manga");
    expect((screen.getByLabelText("出版社") as HTMLInputElement).value).toBe("集英社");
    expect(screen.getAllByText(/公開データベース: 国立国会図書館サーチ/).length).toBeGreaterThan(0);
    // 保留だった AniList は種別が決まって自動で連携する
    expect(within(screen.getByLabelText("AniListとの連携")).getByText(/自動で連携/)).toBeTruthy();
    expect((screen.getByLabelText("ジャンル(「、」区切り)") as HTMLInputElement).value).toBe("Action、Comedy");
  });

  it("手で直した項目は手動と示され、自動に戻せる。どれでもないも選べる", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    await screen.findByLabelText("国立国会図書館サーチとの連携");
    fireEvent.change(screen.getByLabelText("出版社"), { target: { value: "手で入れた出版社" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "出版社を自動に戻す" })).toBeTruthy());
    expect(backend.log.find((l) => l.method === "PATCH")!.body).toEqual({ publisher: "手で入れた出版社" });
    fireEvent.click(screen.getByRole("button", { name: "出版社を自動に戻す" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "出版社を自動に戻す" })).toBeNull());
    expect((screen.getByLabelText("出版社") as HTMLInputElement).value).toBe("");

    fireEvent.click(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getAllByRole("button", { name: "どれでもない" })[0]);
    await waitFor(() => expect(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getByText("該当なし(手動で選択)")).toBeTruthy());
    fireEvent.click(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getByRole("button", { name: "手動の選択を解除" }));
    await waitFor(() => expect(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getByText("確認待ち")).toBeTruthy());
  });

  it("作品ページに利用元の表示がある", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    const credits = await screen.findByRole("contentinfo", { name: "利用している公開データベース" });
    expect(within(credits).getByRole("link", { name: "AniList" }).getAttribute("href")).toBe("https://anilist.co");
    expect(within(credits).getByRole("link", { name: "MangaUpdates" }).getAttribute("href")).toBe("https://www.mangaupdates.com");
  });

  it("巻の書誌に出所と出典が出て、手で直して自動に戻せる", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    await screen.findByLabelText("国立国会図書館サーチとの連携");
    fireEvent.click(within(screen.getByLabelText("国立国会図書館サーチとの連携")).getByRole("button", { name: "サンプル漫画(漫画・集英社) を採用" }));
    await waitFor(() => expect(screen.getAllByText(/書誌: 集英社/).length).toBeGreaterThan(0));
    const row = screen.getByText("サンプル漫画 第01巻").closest("li")!;
    const details = row.querySelector("details")!;
    expect(details.textContent).toContain("公開データベース: 国立国会図書館サーチ");
    expect(within(details as HTMLElement).getAllByRole("link", { name: "出典" })[0].getAttribute("href")).toContain("ndlsearch.ndl.go.jp");
    fireEvent.change(within(details as HTMLElement).getByLabelText("この巻のISBN"), { target: { value: "9784088700000" } });
    fireEvent.click(within(details as HTMLElement).getByRole("button", { name: "書誌を保存" }));
    await waitFor(() => expect(within(row).getByRole("button", { name: "ISBNを自動に戻す" })).toBeTruthy());
    expect(backend.log.find((l) => l.method === "PATCH" && l.path === "/api/volumes/1/metadata")!.body).toEqual({ isbn: "9784088700000" });
    fireEvent.click(within(row).getByRole("button", { name: "ISBNを自動に戻す" }));
    await waitFor(() => expect(within(row).queryByRole("button", { name: "ISBNを自動に戻す" })).toBeNull());
  });
});
