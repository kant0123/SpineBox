// 管理の「蔵書」タブ(#313)。モック API を相手に、蔵書(グループ)とフォルダの追加・名前変更・移動・外す・削除と、
// サイドバーの切り替えへの即時反映を確かめる。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { createMockBackend } from "../mock/mockBackend";
import { serverCrumbs } from "../shell/FolderPicker";

let backend: ReturnType<typeof createMockBackend>;

beforeEach(() => {
  backend = createMockBackend();
  localStorage.clear();
  window.location.hash = "#/admin/libraries";
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.status === 204 ? null : r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const sidebarOptions = () =>
  Array.from((screen.getByLabelText("蔵書", { selector: "select" }) as HTMLSelectElement).options).map((o) => o.textContent);
const group = async (name: RegExp) => within(await screen.findByRole("region", { name }));
const call = (method: string, path: string, body?: unknown) =>
  backend.handle(method, path, body === undefined ? undefined : JSON.stringify(body));

describe("蔵書の管理", () => {
  it("蔵書ごとにフォルダ(パス・巻数・オフライン)を並べ、サイドバーと同じ蔵書を出す", async () => {
    render(<App />);
    const hon = await group(/^本/);
    expect(hon.getByText("D:/本")).toBeTruthy();
    const manga = await group(/^漫画/);
    expect(manga.getByText(/オフライン/)).toBeTruthy();
    await waitFor(() => expect(sidebarOptions()).toEqual(["すべての蔵書", "本(7 巻)", "漫画(オフライン)(1 巻)"]));
  });

  it("蔵書を足すと、サイドバーにも出る。空の名前と同じ名前は理由を出す", async () => {
    render(<App />);
    await group(/^本/);
    fireEvent.click(screen.getByRole("button", { name: "蔵書を追加" }));
    const name = screen.getByLabelText("蔵書の名前");
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect((await screen.findByText("蔵書の名前を入力してください。")).id).toBe("libraries-new-name-error");
    expect(name.getAttribute("aria-describedby")).toContain("libraries-new-name-error");
    expect(document.activeElement).toBe(name);

    fireEvent.change(name, { target: { value: "本" } });
    fireEvent.click(screen.getByRole("button", { name: "蔵書を追加" }));
    expect((await screen.findByRole("alert")).textContent).toContain("同じ名前の蔵書があります");

    fireEvent.change(name, { target: { value: " 同人 " } });
    fireEvent.click(screen.getByRole("button", { name: "蔵書を追加" }));
    expect((await screen.findByRole("status")).textContent).toContain("蔵書「同人」を追加しました");
    await group(/^同人/);
    await waitFor(() => expect(sidebarOptions()).toContain("同人(0 巻)"));
    expect((name as HTMLInputElement).value).toBe("");
  });

  it("蔵書へフォルダを足し(手入力)、スキャンを依頼できる。足した蔵書が選ばれたまま巻数が出る", async () => {
    render(<App />);
    const hon = await group(/^本/);
    fireEvent.click(hon.getByRole("button", { name: /フォルダを追加/ }));
    const path = screen.getByLabelText(/追加するフォルダ/);
    expect(document.activeElement).toBe(path);
    expect(path.getAttribute("aria-describedby")).toBe("library-1-path-hint");
    fireEvent.change(path, { target: { value: "D:/同人" } });
    fireEvent.click(screen.getByRole("button", { name: "追加する" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("「本」にフォルダを追加しました"));
    expect(within(await screen.findByRole("region", { name: /^本/ })).getByText("D:/同人")).toBeTruthy();
    expect(screen.queryByLabelText(/追加するフォルダ/)).toBeNull();
    expect(backend.log.find((l) => l.path === "/api/library-folders")?.body).toEqual({ path: "D:/同人", group_id: 1 });
    // フォームを閉じたら、開いたボタンへフォーカスが戻る
    await waitFor(() => expect(document.activeElement?.id).toBe("library-1-add"));

    fireEvent.click(screen.getByRole("button", { name: "スキャンする" }));
    await waitFor(() => expect(backend.log.some((l) => l.method === "POST" && l.path === "/api/jobs")).toBe(true));
    expect((await screen.findByText(/スキャンを依頼しました/)).textContent).toBeTruthy();
  });

  it("フォルダが無い・読めない・登録済みなら、サーバーの理由を出して入力を残す", async () => {
    render(<App />);
    const hon = await group(/^本/);
    fireEvent.click(hon.getByRole("button", { name: /フォルダを追加/ }));
    fireEvent.click(screen.getByRole("button", { name: "追加する" }));
    expect(await screen.findByText(/フォルダのパスを入力するか/)).toBeTruthy();
    expect(screen.getByLabelText(/追加するフォルダ/).getAttribute("aria-invalid")).toBe("true");

    const path = screen.getByLabelText(/追加するフォルダ/);
    fireEvent.change(path, { target: { value: "Z:/無い" } });
    fireEvent.click(screen.getByRole("button", { name: "追加する" }));
    expect((await screen.findByRole("alert")).textContent).toContain("フォルダが見つからないか、読めません");
    expect((path as HTMLInputElement).value).toBe("Z:/無い");

    fireEvent.change(path, { target: { value: "D:/本" } });
    fireEvent.click(screen.getByRole("button", { name: "追加する" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("登録済み"));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(screen.queryByLabelText(/追加するフォルダ/)).toBeNull();
  });

  it("「フォルダーを選ぶ」でサーバー機のフォルダを辿って選ぶ", async () => {
    render(<App />);
    const hon = await group(/^本/);
    fireEvent.click(hon.getByRole("button", { name: /フォルダを追加/ }));
    fireEvent.click(screen.getByRole("button", { name: "フォルダーを選ぶ" }));
    const dialog = await screen.findByRole("dialog", { name: "フォルダーを選ぶ" });
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole("heading", { name: "フォルダーを選ぶ" })));
    // ドライブの一覧は「このフォルダーにする」を出さない(選べない)
    expect(within(dialog).queryByRole("button", { name: "このフォルダーにする" })).toBeNull();
    fireEvent.click(await within(dialog).findByRole("button", { name: /D:/ }));
    fireEvent.click(await within(dialog).findByRole("button", { name: /同人/ }));
    await within(dialog).findByText("この中にフォルダーはありません");
    expect(dialog.querySelector('[aria-current="location"]')?.textContent).toBe("同人");
    fireEvent.click(within(dialog).getByRole("button", { name: "このフォルダーにする" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((screen.getByLabelText(/追加するフォルダ/) as HTMLInputElement).value).toBe("D:/同人");
    expect(backend.log.some((l) => l.path === "/api/fs/folders")).toBe(true);
  });

  it("フォルダを別の蔵書へ移し、外す(外しても本の記録は消えない旨を出す)", async () => {
    render(<App />);
    const hon = await group(/^本/);
    // 移す先を選ばずに押すと、選択肢へフォーカスして理由を出す
    fireEvent.click(hon.getByRole("button", { name: /^移す/ }));
    const target = hon.getByLabelText(/移す先/);
    expect(await hon.findByText("移す先の蔵書を選んでください。")).toBeTruthy();
    expect(target.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(target);
    fireEvent.change(target, { target: { value: "2" } });
    fireEvent.click(hon.getByRole("button", { name: /^移す/ }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("「漫画」へ移しました"));
    expect(within(await screen.findByRole("region", { name: /^漫画/ })).getByText("D:/本")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: /^本/ })).getByText("フォルダがありません。")).toBeTruthy();

    const manga = await group(/^漫画/);
    fireEvent.click(manga.getAllByRole("button", { name: /^外す/ })[0]);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("本と読書の記録は残っています"));
  });

  it("蔵書の名前を変え、フォルダの無い蔵書だけ削除できる", async () => {
    render(<App />);
    const hon = await group(/^本/);
    expect(hon.queryByRole("button", { name: /蔵書を削除/ })).toBeNull(); // フォルダが残っている間は出さない
    fireEvent.click(hon.getByRole("button", { name: /名前を変える/ }));
    const input = screen.getByLabelText("蔵書の名前", { selector: "#library-1-name" });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "名前を変える" }));
    expect(await screen.findByText("蔵書の名前を入力してください。")).toBeTruthy();
    fireEvent.change(input, { target: { value: "紙の本" } });
    fireEvent.click(screen.getByRole("button", { name: "名前を変える" }));
    await group(/^紙の本/);
    await waitFor(() => expect(sidebarOptions()[1]).toMatch(/^紙の本/));

  });

  it("蔵書が 1 つも無いとき、フォルダを足すと「デフォルト」ができる", async () => {
    call("DELETE", "/api/library-folders/1");
    call("DELETE", "/api/library-folders/2");
    call("DELETE", "/api/library-groups/1");
    call("DELETE", "/api/library-groups/2");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "蔵書がまだありません" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "フォルダを追加" }));
    fireEvent.change(screen.getByLabelText(/追加するフォルダ/), { target: { value: "E:/漫画" } });
    fireEvent.click(screen.getByRole("button", { name: "追加する" }));
    await group(/^デフォルト/);
    expect(backend.log.find((l) => l.path === "/api/library-folders" && l.method === "POST")?.body).toEqual({ path: "E:/漫画" });
    await waitFor(() => expect(sidebarOptions()).toEqual(["すべての蔵書", "デフォルト(0 巻)"]));
    expect(screen.queryByRole("heading", { name: "蔵書がまだありません" })).toBeNull();
  });

  it("フォルダの無い蔵書は削除でき、サイドバーで選んでいた蔵書が消えたら「すべての蔵書」に戻る", async () => {
    render(<App />);
    const manga = await group(/^漫画/);
    const select = screen.getByLabelText("蔵書", { selector: "select" }) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(3));
    fireEvent.change(select, { target: { value: "2" } });
    expect(localStorage.getItem("spinebox.libraryGroup")).toBe("2");
    fireEvent.click(manga.getAllByRole("button", { name: /^外す/ })[0]);
    fireEvent.click(await manga.findByRole("button", { name: /蔵書を削除/ }));
    await waitFor(() => expect(screen.queryByRole("region", { name: /^漫画/ })).toBeNull());
    await waitFor(() => expect(localStorage.getItem("spinebox.libraryGroup")).toBeNull());
    expect(select.value).toBe("");
  });

  it("蔵書を読み込めなければ理由を出し、もう一度読み込める", async () => {
    let fail = true;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      if (fail && url === "/api/libraries") return new Response("{}", { status: 500 });
      const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
      return new Response(r.status === 204 ? null : r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
    });
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain("蔵書を読み込めませんでした");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "もう一度読み込む" }));
    await group(/^本/);
  });
});

describe("サーバー機のパスのパンくず", () => {
  it("Windows のドライブ・UNC・POSIX のパスを階層に分ける", () => {
    expect(serverCrumbs("")).toEqual([]);
    expect(serverCrumbs("D:/本/漫画")).toEqual([
      { label: "D:\\", path: "D:\\" },
      { label: "本", path: "D:\\本" },
      { label: "漫画", path: "D:\\本\\漫画" },
    ]);
    expect(serverCrumbs("\\\\nas\\share\\本")).toEqual([
      { label: "\\\\nas\\share", path: "\\\\nas\\share" },
      { label: "本", path: "\\\\nas\\share\\本" },
    ]);
    expect(serverCrumbs("/srv/lib")).toEqual([
      { label: "/", path: "/" },
      { label: "srv", path: "/srv" },
      { label: "lib", path: "/srv/lib" },
    ]);
  });
});

describe("蔵書ごとの公開データベースの取得元(#316)", () => {
  const sourcesOf = (name: string) =>
    (JSON.parse(call("GET", "/api/libraries").body) as { name: string; metadata_sources: string[] | null }[])
      .find((g) => g.name === name)!.metadata_sources;

  it("指定の無い蔵書は既定(設定の取得元)を出し、フォームで選んで保存できる", async () => {
    render(<App />);
    const hon = await group(/^本/);
    expect((await hon.findByText(/^既定\(/)).textContent).toBe("既定(国立国会図書館サーチ、メディア芸術データベース、Bangumi)");

    const opener = hon.getByRole("button", { name: /取得元を変える/ });
    fireEvent.click(opener);
    const mode = screen.getByRole("group", { name: /公開データベースの取得元/ });
    const [follow, choose] = within(mode).getAllByRole("radio") as HTMLInputElement[];
    expect(follow.checked).toBe(true);
    expect(document.activeElement).toBe(follow);
    expect(mode.getAttribute("aria-describedby")).toBe("library-1-sources-form-hint");
    expect(screen.queryByRole("group", { name: "使う取得元" })).toBeNull();  // 既定に従う間は、取得元の一覧を出さない
    // 開いているフォームは 1 つだけ(ほかの操作のボタンは隠す)
    expect(hon.queryByRole("button", { name: /名前を変える/ })).toBeNull();

    fireEvent.click(choose);
    const list = within(screen.getByRole("group", { name: "使う取得元" }));
    expect(list.getAllByRole("checkbox").map((c) => c.parentElement!.textContent)).toEqual(
      ["国立国会図書館サーチ", "メディア芸術データベース", "Bangumi", "E-Hentai", "nHentai"]);
    // 指定の無い蔵書は、既定の取得元にチェックが入った状態から選び始める
    expect((list.getAllByRole("checkbox") as HTMLInputElement[]).map((c) => c.checked)).toEqual([true, true, true, false, false]);
    fireEvent.click(list.getByLabelText("nHentai"));
    fireEvent.click(list.getByLabelText("E-Hentai"));
    fireEvent.click(list.getByLabelText("国立国会図書館サーチ"));
    fireEvent.click(list.getByLabelText("メディア芸術データベース"));
    fireEvent.click(list.getByLabelText("Bangumi"));
    fireEvent.click(screen.getByRole("button", { name: "取得元を保存する" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("「本」の取得元を変えました"));
    expect(screen.getByRole("status").textContent).toContain("取得済みの情報は残ります");
    expect(sourcesOf("本")).toEqual(["ehentai", "nhentai"]);  // 引く順で保存される
    expect(sourcesOf("漫画")).toBeNull();
    const after = await group(/^本/);
    expect(after.getByText("E-Hentai、nHentai")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "使う取得元" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(after.getByRole("button", { name: /取得元を変える/ })));
  });

  it("全部外すと「取得しない」になり、既定に戻せる。やめると何も変えない", async () => {
    call("PUT", "/api/library-groups/1/metadata-sources", { sources: ["ndl"] });
    render(<App />);
    const hon = await group(/^本/);
    expect(await hon.findByText("国立国会図書館サーチ")).toBeTruthy();

    fireEvent.click(hon.getByRole("button", { name: /取得元を変える/ }));
    const ndl = within(screen.getByRole("group", { name: "使う取得元" })).getByLabelText("国立国会図書館サーチ") as HTMLInputElement;
    expect(ndl.checked).toBe(true);  // 今の指定が入った状態で開く
    fireEvent.click(ndl);
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(sourcesOf("本")).toEqual(["ndl"]);

    fireEvent.click((await group(/^本/)).getByRole("button", { name: /取得元を変える/ }));
    fireEvent.click(within(screen.getByRole("group", { name: "使う取得元" })).getByLabelText("国立国会図書館サーチ"));
    fireEvent.click(screen.getByRole("button", { name: "取得元を保存する" }));
    await waitFor(() => expect(sourcesOf("本")).toEqual([]));
    expect(await (await group(/^本/)).findByText("取得しない")).toBeTruthy();

    fireEvent.click((await group(/^本/)).getByRole("button", { name: /取得元を変える/ }));
    fireEvent.click(within(screen.getByRole("group", { name: /公開データベースの取得元/ })).getAllByRole("radio")[0]);
    fireEvent.click(screen.getByRole("button", { name: "取得元を保存する" }));
    await waitFor(() => expect(sourcesOf("本")).toBeNull());
    expect(await (await group(/^本/)).findByText(/^既定\(/)).toBeTruthy();
  });

  it("保存に失敗したら理由を出し、フォームは開いたまま", async () => {
    render(<App />);
    const hon = await group(/^本/);
    fireEvent.click(await hon.findByRole("button", { name: /取得元を変える/ }));
    call("DELETE", "/api/library-folders/1");
    call("DELETE", "/api/library-groups/1");  // 画面を開いている間に、蔵書が消えた
    fireEvent.click(screen.getByRole("button", { name: "取得元を保存する" }));
    expect((await screen.findByRole("alert")).textContent).toContain("蔵書が見つかりません");
  });

  it("サーバーの設定を読めないときは、取得元の表示と操作を出さない(蔵書の管理は続けられる)", async () => {
    backend = createMockBackend(undefined, { settingsFailure: true });
    render(<App />);
    const hon = await group(/^本/);
    expect(hon.getByRole("button", { name: /名前を変える/ })).toBeTruthy();
    expect(hon.queryByRole("button", { name: /取得元を変える/ })).toBeNull();
    expect(hon.queryByText("公開データベースの取得元")).toBeNull();
  });

  it("モック API は知らない取得元・配列でない値を弾く", () => {
    expect(call("PUT", "/api/library-groups/1/metadata-sources", { sources: ["anilist"] }).status).toBe(422);
    expect(call("PUT", "/api/library-groups/1/metadata-sources", { sources: "ndl" }).status).toBe(422);
    expect(call("PUT", "/api/library-groups/99/metadata-sources", { sources: null }).status).toBe(404);
  });
});
