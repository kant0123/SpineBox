// モック API を相手に、作品一覧・作品ページ(欠け・重複のまとめ・手動編集・付け替え)・統合の提案の承認/却下を確かめる
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { parseRoute } from "../route";
import { confidenceText, evidenceLines, missingLabel, missingText } from "./labels";
import { workQueryString } from "../api";
import { normalizeSearch } from "../mock/mockWorks";

let backend: ReturnType<typeof createMockBackend>;

beforeEach(() => {
  backend = createMockBackend();
  localStorage.clear();
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
    expect(parseRoute("#/works")).toEqual({ name: "works", scope: { by: "all" } });
    expect(parseRoute("#/works/12")).toEqual({ name: "work", id: "12" });
    expect(parseRoute("#/works/proposals")).toEqual({ name: "review", tab: "proposals" }); // 確認へ移した(#207)
    expect(parseRoute("#/works/abc")).toEqual({ name: "works", scope: { by: "all" } });
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
  it("作品カードに巻数タグ・欠け・提案が出て、重複・統合済み・種別・ジャンルは出ない", async () => {
    backend.handle("PATCH", "/api/works/1", JSON.stringify({ publisher: "集英社" }));
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(card).getByText("既刊4巻(〜4巻)")).toBeTruthy();
    expect(within(card).getByText("欠け 3 巻")).toBeTruthy();
    expect(within(card).queryByText(/重複/)).toBeNull();
    expect(within(card).queryByText("統合済み")).toBeNull();
    // 種別・ジャンルはカードに出さない(出版社だけ残す)
    expect(within(card).getByText("集英社")).toBeTruthy();
    expect(within(card).queryByText("漫画")).toBeNull();
    const other = (await screen.findByText("外付けの作品")).closest("li")!;
    expect(within(other).getByText("既刊1巻")).toBeTruthy();
    expect(within(other).queryByText(/ファンタジー/)).toBeNull();
    expect(within((await screen.findByText("別の読み物")).closest("li")!).queryByText("小説")).toBeNull();
    // 別名は作品ページで見る(一覧のカードには出さない)
    expect(within(card).queryByText(/別名/)).toBeNull();
    expect(card.querySelector("a")!.getAttribute("href")).toBe("#/works/1/titles");
    expect((await screen.findByText(/確認待ちが \d+ 件/)).closest("a")!.getAttribute("href")).toBe("#/review");
    // 巻の一覧は廃止した
  });

  it("ヘッダーに重複の候補・情報を補完・表紙を作る・名寄せを取り直すを置かない(管理のデータ管理タブに集約)", async () => {
    window.location.hash = "#/works";
    render(<App />);
    await screen.findByText("サンプル漫画");
    expect(screen.queryByRole("link", { name: "重複の候補" })).toBeNull();
    expect(screen.queryByRole("button", { name: "情報を補完" })).toBeNull();
    expect(screen.queryByRole("button", { name: "表紙を作る" })).toBeNull();
    expect(screen.queryByRole("button", { name: "名寄せを取り直す" })).toBeNull();
  });

  it("表紙の右下に後で読むを重ね、付けたものだけに絞り込める(#173)", async () => {
    expect(workQueryString({ read_later: true })).toBe("?read_later=1");
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    const button = within(card).getByRole("button", { name: "サンプル漫画 を後で読む" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    // ボタンは表紙のリンクの外(兄弟要素)で、表紙の重ね先にある
    const wrap = card.querySelector(".work-cover-wrap")!;
    expect(within(wrap as HTMLElement).queryByRole("button", { name: "サンプル漫画 を後で読む" })).toBeTruthy();
    expect(wrap.querySelector(".work-cover-link")!.contains(button)).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(within(card).getByRole("button", { name: "サンプル漫画 の後で読むを外す" }).getAttribute("aria-pressed")).toBe("true"));
    expect(backend.log.some((l) => l.method === "PUT" && l.path === "/api/read-later/works/1")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /絞り込み/ }));
    fireEvent.click(screen.getByLabelText("後で読むだけ"));
    await waitFor(() => expect(screen.queryByText("別の読み物")).toBeNull());
    expect(screen.getByText("サンプル漫画")).toBeTruthy();

    fireEvent.click(within(card).getByRole("button", { name: "サンプル漫画 の後で読むを外す" }));
    await waitFor(() => expect(screen.queryByText("サンプル漫画")).toBeNull());
  });
});

describe("作品のタイトル一覧(#105 / #115)", () => {
  it("巻を番号順に並べ、名前を「作品名 第N巻」にそろえ、表紙・読書状態・全体の進捗を出す", async () => {
    window.location.hash = "#/works/1/titles";
    render(<App />);
    expect(await screen.findByRole("heading", { name: "タイトル一覧(5)" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "サンプル漫画" })).toBeTruthy();
    const items = [...document.querySelectorAll<HTMLElement>(".title-list .title-item")];
    // 1巻(重複 2 冊)・2巻・欠けの 3巻・4巻・最新巻の 5巻。巻番号の列は出さない(巻名に番号が含まれるため)
    expect(items.every((li) => li.querySelector(".title-number") === null)).toBe(true);
    expect(items.map((li) => li.querySelector(".volume-title")!.textContent)).toEqual([
      "サンプル漫画 第1巻", "サンプル漫画 第2巻", "サンプル漫画 第3巻", "サンプル漫画 第4巻", "サンプル漫画 第5巻",
    ]);
    // 表紙は行の一番左(読み込めない・まだ作っていないときは枠だけ残る)
    expect(items[0].firstElementChild!.classList.contains("title-cover")).toBe(true);
    expect(items[0].querySelector("img.work-cover")!.getAttribute("src")).toBe("/api/volumes/1/thumbnail");
    // 進み具合は「N% (読んだ / 全体)」。未読の文字は行に出さない
    expect(items[1].querySelector(".volume-meta")!.textContent).toContain("50% (4/8)");
    expect(within(items[1]).queryByText("未読")).toBeNull();
    // 行の色と凡例
    expect(items[0].classList.contains("status-unread")).toBe(true);
    expect(items[1].classList.contains("status-reading")).toBe(true);
    expect(items[3].classList.contains("status-finished")).toBe(true);
    const legend = screen.getByRole("list", { name: "読書状態の凡例" });
    expect(legend.textContent).toContain("未読");
    expect(legend.textContent).toContain("読書中");
    expect(legend.textContent).toContain("読了");
    // 作品全体の進み具合(9 / 22 ページ・読了 1 / 3 巻)
    expect(screen.getByText(/全体 41%\(9 \/ 22\)・読了 1 \/ 3 巻/)).toBeTruthy();
    // 欠け・最新巻はフォローの続刊の確認から出し、リーダーへは行かせない
    expect(within(items[2]).getByText("欠け")).toBeTruthy();
    expect(within(items[2]).getByText(/2014年1月 発売/)).toBeTruthy();
    expect(items[2].querySelector("a")).toBeNull();
    expect(within(items[4]).getByText("最新巻")).toBeTruthy();
    expect(within(items[4]).getByText(/2015年3月 発売/)).toBeTruthy();
    // ボタンは今まで作品カードから開いていた作品の管理ページ(#/works/<id>)へ
    expect(screen.getByRole("link", { name: "作品を管理" }).getAttribute("href")).toBe("#/works/1");
    expect(screen.getByRole("link", { name: "‹ 作品の一覧へ" }).getAttribute("href")).toBe("#/works");
  });

  it("重複は 1 行にまとめ、押すと重複の一覧から読む巻を選べる", async () => {
    window.location.hash = "#/works/1/titles";
    render(<App />);
    await screen.findByRole("heading", { name: "タイトル一覧(5)" });
    const items = [...document.querySelectorAll<HTMLElement>(".title-list .title-item")];
    const dup = items[0];
    expect(dup.querySelector(".dup-badge")!.textContent).toBe("重複");
    expect(dup.tagName).toBe("BUTTON");
    expect(items[1].tagName).toBe("A");
    expect(items[1].getAttribute("href")).toBe("#/read/2");
    fireEvent.click(dup);
    const dialog = await screen.findByRole("dialog", { name: "1巻 の重複(2 冊)" });
    const choices = [...dialog.querySelectorAll("a")];
    expect(choices.map((a) => a.getAttribute("href"))).toEqual(["#/read/1", "#/read/6"]);
    expect(choices[0].textContent).toContain("サンプル漫画 第01巻");
    expect(choices[1].textContent).toContain("Sample manga v01");
    // Tab はモーダルの中を回る(最後の「閉じる」から先頭の巻へ)
    const close = within(dialog).getByRole("button", { name: "閉じる" });
    close.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(choices[0]);
    // Escape で閉じ、開いた行へフォーカスが戻る
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(dup);
  });

  it("オフラインの巻はタイトルだけ出し、リーダーへはリンクしない", async () => {
    window.location.hash = "#/works/2/titles";
    render(<App />);
    const item = (await screen.findByText("外付けドライブの巻")).closest("li")!;
    expect(item.querySelector("a")).toBeNull();
    expect(within(item).getByText(/オフライン/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "作品を管理" }).getAttribute("href")).toBe("#/works/2");
  });

  it("存在しない(統合された)作品は、見つからないと言う", async () => {
    window.location.hash = "#/works/99/titles";
    render(<App />);
    expect(await screen.findByText(/この作品は見つかりません/)).toBeTruthy();
  });
});

describe("作品カードの表紙(#75)", () => {
  it("表紙の巻のサムネイルを出し、読み込めたら見せ、404 なら表紙なしの枠を残す", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    const img = card.querySelector("img.work-cover") as HTMLImageElement;
    expect(img.getAttribute("src")).toMatch(/^\/api\/volumes\/\d+\/thumbnail$/);
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.classList.contains("loaded")).toBe(false);
    fireEvent.load(img);
    expect(img.classList.contains("loaded")).toBe(true);
    fireEvent.error(img);
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector(".work-cover-placeholder")!.textContent).toBe("表紙なし");
    expect(within(card).getByText("サンプル漫画")).toBeTruthy();
  });

  it("表紙を読み込めない作品も表紙の枠を出し、タイトルを表示する", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("別の読み物")).closest("li")!;
    const img = card.querySelector("img.work-cover") as HTMLImageElement;
    fireEvent.error(img);
    expect(card.querySelector("img.work-cover")).toBeNull();
    expect(card.querySelector(".work-cover-placeholder")!.textContent).toBe("表紙なし");
    expect(within(card).getByText("別の読み物")).toBeTruthy();
    // すべてのカードが表紙か表紙なしの枠を持ち、タイトルの開始位置がずれない
    for (const item of document.querySelectorAll(".work-card")) {
      expect(item.querySelector("img.work-cover, .work-cover-placeholder")).toBeTruthy();
    }
  });

  it("モック: 入れ子の巻の表紙は、開くか作るまで 404 pending", () => {
    const nested = backend.volumes.find((v) => v.page_count === null && !v.format)!;
    const get = () => backend.handle("GET", `/api/volumes/${nested.id}/thumbnail`);
    expect(get().status).toBe(404);
    expect(JSON.parse(get().body as string).pending).toBe(true);
    backend.handle("GET", `/api/volumes/${nested.id}`);
    expect(get().status).toBe(200);
  });
});

describe("作品の検索と絞り込み(#56)", () => {
  const cardTitles = () => Array.from(document.querySelectorAll(".work-card .volume-title")).map((e) => e.textContent);

  it("クエリ文字列は空の値を付けない", () => {
    expect(workQueryString({})).toBe("");
    expect(workQueryString({ q: " お気楽 ", author: "", status: "reading" })).toBe("?q=%E3%81%8A%E6%B0%97%E6%A5%BD&status=reading");
    expect(workQueryString({ read_later: true })).toBe("?read_later=1");
    expect(normalizeSearch("ＯＫＩ カクｶｸ・x")).toBe("okiかくかくx");
  });

  it("検索語で作品を絞り、0 件なら条件に合わないと言って、条件を消せる", async () => {
    window.location.hash = "#/works";
    render(<App />);
    await screen.findByText("サンプル漫画");
    expect(cardTitles()).toHaveLength(3);
    fireEvent.change(screen.getByLabelText("作品を検索"), { target: { value: "sample" } }); // 別名で当たる
    await waitFor(() => expect(cardTitles()).toEqual(["サンプル漫画"]));
    expect(screen.getByText(/1 件 \/ 全 3 作品/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("作品を検索"), { target: { value: "存在しない" } });
    expect(await screen.findByText(/条件に合う作品がありません/)).toBeTruthy();
    expect(screen.queryByText(/作品がありません。蔵書フォルダ/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "条件を消す" }));
    await waitFor(() => expect(cardTitles()).toHaveLength(3));
    expect((screen.getByLabelText("作品を検索") as HTMLInputElement).value).toBe("");
  });

  it("絞り込みは開いたときだけ出て、読書状態と項目で絞れる", async () => {
    window.location.hash = "#/works";
    render(<App />);
    await screen.findByText("サンプル漫画");
    expect(screen.queryByLabelText("読書状態")).toBeNull(); // スマホ幅で邪魔にならないよう、初めは閉じている
    fireEvent.click(screen.getByRole("button", { name: "絞り込み" }));
    fireEvent.change(screen.getByLabelText("読書状態"), { target: { value: "unread" } });
    await waitFor(() => expect(cardTitles()).toEqual(["外付けの作品", "別の読み物"]));
    expect(screen.getByRole("button", { name: "絞り込み(1)" })).toBeTruthy();

    // 候補のある項目だけ出る(モックでは種別とジャンルと著者。タグは無い)
    expect(screen.getByLabelText("著者")).toBeTruthy();
    expect(screen.queryByLabelText("タグ")).toBeNull();
    fireEvent.change(screen.getByLabelText("種別"), { target: { value: "novel" } });
    await waitFor(() => expect(cardTitles()).toEqual(["別の読み物"]));
    fireEvent.change(screen.getByLabelText("読書状態"), { target: { value: "reading" } });
    expect(await screen.findByText(/条件に合う作品がありません/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("種別"), { target: { value: "" } });
    await waitFor(() => expect(cardTitles()).toEqual(["サンプル漫画"]));
    expect(within(document.querySelector(".work-card")!).getByText("読書中")).toBeTruthy();
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
  it("作品の一覧に確認待ちが出て、利用元の表示がある", async () => {
    window.location.hash = "#/works";
    render(<App />);
    const card = (await screen.findByText("サンプル漫画")).closest("li")!;
    expect(within(card).getByText("候補の確認待ち")).toBeTruthy();
    const credits = await screen.findByRole("contentinfo", { name: "利用している公開データベース" });
    expect(within(credits).getByRole("link", { name: "国立国会図書館サーチ" }).getAttribute("href")).toBe("https://ndlsearch.ndl.go.jp/");
    expect(credits.textContent).toContain("NDL サーチ API");
    expect(credits.textContent).toContain("AniList API");
    expect(credits.textContent).toContain("MangaUpdates API");
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
    expect((screen.getByLabelText("ジャンル(「、」区切り)") as HTMLInputElement).value).toBe("アクション、コメディ");
  });

  it("あらすじは表示も編集もしない(#82。公開データベースと連携しても)", async () => {
    window.location.hash = "#/works/1";
    render(<App />);
    const ndl = (await screen.findByLabelText("国立国会図書館サーチとの連携")) as HTMLElement;
    fireEvent.click(within(ndl).getByRole("button", { name: "サンプル漫画(漫画・集英社) を採用" }));
    await waitFor(() => expect(within(screen.getByLabelText("AniListとの連携")).getByText(/自動で連携/)).toBeTruthy());
    expect(screen.queryByLabelText("あらすじ")).toBeNull();
    expect(screen.queryByText(/あらすじ/)).toBeNull();
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

describe("欠けの表示", () => {
  it("番号が少なければ並べ、多すぎるときは件数だけにして内訳をツールチップに回す", () => {
    expect(missingLabel({ missing_volumes: [3, 5], missing_episodes: [2] })).toEqual({ text: "3・5 巻、2 話" });
    // 境界: 5 個までは番号を並べる
    expect(missingLabel({ missing_volumes: [1, 2, 3, 4, 5], missing_episodes: [] })).toEqual({ text: "1・2・3・4・5 巻" });
    // 6 個からは件数だけ(巻と話を合わせて数える)
    expect(missingLabel({ missing_volumes: [1, 2, 3, 4, 5, 6], missing_episodes: [] })).toEqual({
      text: "6 巻",
      title: "1・2・3・4・5・6 巻",
    });
    expect(missingLabel({ missing_volumes: [1, 2, 3, 4], missing_episodes: [1, 2] })).toEqual({
      text: "4 巻、2 話",
      title: "1・2・3・4 巻、1・2 話",
    });
    expect(missingLabel({ missing_volumes: [], missing_episodes: [] })).toEqual({ text: "" });
  });

  it("作品カードは多すぎるとき件数だけを出し、内訳をツールチップに入れる", async () => {
    const list = JSON.parse(backend.handle("GET", "/api/works").body);
    list.works.find((w: { title: string }) => w.title === "サンプル漫画").missing_volumes = [1, 2, 3, 4, 5, 6];
    const stub = (globalThis as any).fetch as (url: string, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/works") return new Response(JSON.stringify(list), { status: 200, headers: { "Content-Type": "application/json" } });
      return stub(url, init);
    });
    window.location.hash = "#/works";
    render(<App />);
    const badge = await screen.findByText("欠け 6 巻");
    expect(badge.getAttribute("title")).toBe("1・2・3・4・5・6 巻");
  });

  it("作品ページの「欠けている巻」も同じ規則にする", async () => {
    const detail = JSON.parse(backend.handle("GET", "/api/works/1").body);
    detail.numbers.volume = { max: 6, missing: [1, 2, 3, 4, 5, 6], limited: false };
    const stub = (globalThis as any).fetch as (url: string, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url === "/api/works/1") return new Response(JSON.stringify(detail), { status: 200, headers: { "Content-Type": "application/json" } });
      return stub(url, init);
    });
    window.location.hash = "#/works/1";
    render(<App />);
    const notice = await screen.findByText("欠けている巻: 6 巻");
    expect(notice.getAttribute("title")).toBe("1・2・3・4・5・6 巻");
  });
});

describe("統合された作品のページ", () => {
  it("情報の取得で別の作品に自動統合されたら、吸収先の作品ページへ移る", async () => {
    window.location.hash = "#/works/2";
    // 取得の結果、作品 2 が作品 3 に吸収された(サーバーは吸収先の詳細を返す)
    const stub = (globalThis as any).fetch as (url: string, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/works/2/metadata/fetch")) {
        const r = backend.handle("GET", "/api/works/3");
        return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
      }
      return stub(url, init);
    });
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "情報を取得" }));
    await waitFor(() => expect(window.location.hash).toBe("#/works/3"));
  });
});
