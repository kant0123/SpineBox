// 範囲指定の整理(#84)のモック。テンプレートの展開は本物(spinebox/organize/template.py)の一部だけをまねる。
import type { OrganizeItem, OrganizeOp, RangeOptions } from "../api";

const TITLE = "サンプル漫画";

const FOLDERS: Record<string, string[]> = { "": ["作者A", "スキャン"], 作者A: [], スキャン: [] };

function render(template: string, number: number): string {
  return template
    .replace(/\[([^\]]*)\]/g, (_, inner: string) => (inner.includes("{author}") || inner.includes("{label}") ? "" : inner))
    .replace(/\{title\}/g, TITLE)
    .replace(/\{number:2\}/g, String(number).padStart(2, "0"))
    .replace(/\{number(:\d)?\}/g, String(number))
    .replace(/\{unit\}/g, "巻")
    .replace(/\{(publisher|kind|year)\}/g, (_, k: string) => ({ publisher: "出版社X", kind: "漫画", year: "2020" })[k] ?? "");
}

function templateError(...templates: string[]): string | null {
  for (const t of templates) {
    if ((t.match(/\{/g) ?? []).length !== (t.match(/\}/g) ?? []).length) return "{ が閉じていません";
    const bad = t.match(/\{(?!title|author|publisher|label|number|unit|kind|year)([^}]*)\}/);
    if (bad) return `変数 ${bad[1]} は使えません`;
  }
  return null;
}

export function createMockRange(ops: OrganizeOp[], nextId: () => string) {
  const options: RangeOptions = {
    roots: [{ id: 1, path: "E:/漫画", online: true }],
    defaults: { file_template: "{title} 第{number:2}巻", folder_template: "{title}" },
    presets: [
      { label: "作品名と巻数", file_template: "{title} 第{number:2}{unit}", folder_template: "" },
      { label: "作品別フォルダー", file_template: "{title} 第{number:2}{unit}", folder_template: "{title}" },
      { label: "出版社別フォルダー", file_template: "{title} 第{number:2}{unit}", folder_template: "{publisher}/{title}" },
    ],
    parameters: [
      { token: "{title}", label: "作品名" },
      { token: "{author}", label: "著者" },
      { token: "{number:2}", label: "巻番号(2 桁)" },
      { token: "{unit}", label: "単位(巻 / 話)" },
    ],
  };

  function plan(body: { root_id?: number; path?: string; file_template?: string; folder_template?: string; exclude_volume_ids?: number[]; include_cbz?: boolean; delete_originals?: boolean }): OrganizeOp | { templateError: string } {
    const fileT = (body.file_template ?? "").trim();
    const folderT = (body.folder_template ?? "").trim();
    const err = templateError(fileT, folderT);
    if (err) return { templateError: err };
    const sub = (body.path ?? "").replace(/^\/+|\/+$/g, "");
    const exclude = body.exclude_volume_ids ?? [];
    const includeCbz = Boolean(body.include_cbz);
    const target = (from: string, cleaned: string, n: number) => {
      const dir = folderT ? [sub, render(folderT, n)].filter(Boolean).join("/") : from.split("/").slice(0, -1).join("/");
      return [dir, `${fileT ? render(fileT, n) : cleaned}.cbz`].filter(Boolean).join("/");
    };
    const items: OrganizeItem[] = [
      { volume_id: 11, title: "ｻﾝﾌﾟﾙ漫画　第1巻", from_rel_path: "作者A/ｻﾝﾌﾟﾙ漫画　第1巻.cbz", output_rel_path: null, status: "move", reason: null, cbz_volume_id: null,
        changes: [], page_count: 8, superseded_by: [] },
      { volume_id: 12, title: "サンプル漫画 2巻", from_rel_path: "作者A/サンプル漫画 2巻.cbz", output_rel_path: null, status: "move", reason: null, cbz_volume_id: null, changes: [],
        page_count: 6, superseded_by: [] },
      { volume_id: 13, title: "サンプル漫画 第2巻", from_rel_path: "スキャン/サンプル漫画 第2巻.cbz", output_rel_path: null, status: "move", reason: null, cbz_volume_id: null, changes: [],
        page_count: 8, superseded_by: [] },
      { volume_id: 14, title: "まとめ", from_rel_path: "作者A/まとめ.zip", output_rel_path: null, status: "excluded", cbz_volume_id: 14, changes: [], page_count: 8, superseded_by: [],
        reason: "1 つのファイルに複数の巻・入れ子の巻があります(この画面では動かしません。先に「CBZ に整理」してください)" },
    ];
    const all = items.filter((it) => !sub || it.from_rel_path.startsWith(`${sub}/`));
    const numbers: Record<number, number> = { 11: 1, 12: 2, 13: 2 };
    const cleaned: Record<number, [string, string[]]> = {
      11: ["サンプル漫画 第1巻", ["半角カナを全角にしました", "空白を整えました(全角の空白・続いた空白)"]],
      12: ["サンプル漫画 2巻", []],
      13: ["サンプル漫画 第2巻", []],
    };
    for (const it of all) {
      if (exclude.includes(it.volume_id)) {
        // 名前は出しておく(競合で別の行を選んだ結果なら、選んだ巻に譲ったと分かるように)
        if (it.status !== "excluded" && it.volume_id !== 14) {
          const [name] = cleaned[it.volume_id] ?? ["まとめ", []];
          it.output_rel_path = target(it.from_rel_path, name, numbers[it.volume_id] ?? 1);
        }
        Object.assign(it, { status: "skipped", reason: "利用者が整理しない巻にしました" });
        continue;
      }
      if (it.volume_id === 14) {
        if (!includeCbz) continue;
        Object.assign(it, { status: "cbz", reason: null, conversion: true, cbz_volume_id: null });
      }
      if (it.status === "excluded") continue;
      const [name, changes] = cleaned[it.volume_id] ?? ["まとめ", []];
      it.output_rel_path = target(it.from_rel_path, name, numbers[it.volume_id] ?? 1);
      it.changes = fileT ? [] : changes;
      if (it.output_rel_path === it.from_rel_path) Object.assign(it, { status: "unchanged", reason: "既にテンプレートどおりの名前です" });
    }
    const isMoving = (it: OrganizeItem) => it.status === "move" || it.status === "cbz";
    // 同じ名前になる巻は、ファイル数の多い方を残す(最多が同数なら衝突のまま)
    for (const it of all) {
      if (!isMoving(it)) continue;
      const group = all.filter((o) => isMoving(o) && o.output_rel_path === it.output_rel_path);
      const most = Math.max(...group.map((o) => o.page_count ?? 0));
      if (group.length > 1 && (it.page_count ?? 0) < most) {
        const top = group.filter((o) => o.page_count === most);
        Object.assign(it, {
          status: "skipped",
          superseded_by: top.map((o) => o.volume_id),
          reason: `同じ名前の${top.map((o) => `「${o.title}」`).join("・")}(${most} ファイル)のほうがファイル数が多いため、整理しません(この巻は ${it.page_count} ファイル)`,
        });
      }
    }
    for (const it of all) {
      const same = all.filter((o) => o !== it && isMoving(o) && isMoving(it) && o.output_rel_path === it.output_rel_path);
      if (same.length) it.reason = `${same.map((o) => `「${o.title}」`).join("・")}と同じ名前になります。残す方を選んでください`;
    }
    for (const it of all) if (it.reason && isMoving(it)) it.status = "conflict";
    // 利用者が整理しないにした巻のうち、同じ名前で残る巻があるものは、その巻に譲ったことにする(本物の _mark_kept_peers)
    for (const it of all) {
      if (!exclude.includes(it.volume_id) || !it.output_rel_path) continue;
      const kept = all.filter((o) => (isMoving(o) || o.status === "unchanged") && o.output_rel_path === it.output_rel_path);
      if (kept.length) {
        it.superseded_by = kept.map((o) => o.volume_id);
        it.reason = `同じ名前の${kept.map((o) => `「${o.title}」`).join("・")}を残すため、整理しません(利用者の選択)`;
      }
    }
    const moving = all.filter(isMoving);
    return {
      op_id: moving.length ? nextId() : null,
      state: moving.length ? "planned" : "blocked",
      kind: "range", naming: "template", template: null, work_id: null, items: all,
      scope: { root_id: 1, root_path: "E:/漫画", path: sub, file_template: fileT, folder_template: folderT },
      include_cbz: includeCbz,
      delete_originals: includeCbz && Boolean(body.delete_originals),
      cbz_sources: includeCbz && all.some((it) => it.status === "cbz" && it.volume_id === 14)
        ? [{ source_id: 0, rel_path: "作者A/まとめ.zip", volume_ids: [14], source_kind: "archive" as const }]
        : [],
      source_id: 0, source_rel_path: "", source_kind: "archive",
      volumes: moving.map((it) => ({ volume_id: it.volume_id, title: it.title, page_count: 8, output_rel_path: it.output_rel_path })),
      warnings: all.some((it) => it.status === "conflict")
        ? ["競合している巻は改名・CBZ 化しません(残す方を選ぶか、作品・巻の情報を直して計画を作り直してください)"]
        : includeCbz && moving.some((it) => it.status === "cbz")
          ? ["CBZ 化する元は、全巻を作って検証してから確定します。元は退避フォルダへ移します(削除しません)"]
          : [],
      problems: moving.length ? [] : ["改名・移動・CBZ 化する巻がありません"],
      needs_analysis: [], created_at: "2026-11-02T00:00:00+00:00", executed_at: null, undone_at: null, error: null,
    };
  }

  function handle(method: string, path: string, query: string, parsed: Record<string, unknown> | undefined): { status: number; body: unknown } | null {
    if (method === "GET" && path === "/api/organize/range/options") return { status: 200, body: options };
    if (method === "POST" && path === "/api/organize/range/example") {
      const fileT = String(parsed?.file_template ?? "");
      const folderT = String(parsed?.folder_template ?? "");
      const err = templateError(fileT, folderT);
      if (err) return { status: 200, body: { error: err, examples: [] } };
      const sub = String(parsed?.path ?? "");
      const dir = folderT ? [sub, render(folderT, 3)].filter(Boolean).join("/") : "(元のフォルダ)";
      return { status: 200, body: { error: null, examples: [{ label: "値がそろった巻", path: `${dir}/${fileT ? render(fileT, 3) : "(元の名前を整理したもの)"}.cbz`, reason: null }] } };
    }
    if (method === "GET" && path === "/api/organize/range/folders") {
      const p = new URLSearchParams(query).get("path") ?? "";
      if (!(p in FOLDERS)) return { status: 422, body: { detail: `範囲のフォルダが見つかりません: ${p}` } };
      return { status: 200, body: { root_id: 1, path: p, parent: p ? p.split("/").slice(0, -1).join("/") : null, folders: FOLDERS[p] } };
    }
    if (method === "POST" && path === "/api/organize/range-plan") {
      const r = plan(parsed ?? {});
      if ("templateError" in r) return { status: 422, body: { detail: r.templateError } };
      if (r.op_id) ops.unshift(r);
      return { status: 200, body: r };
    }
    return null;
  }

  return { handle };
}
