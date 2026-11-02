// 受け入れ(#66)のモック。本物の整理案(spinebox/organize/intake.py)を、画面を確かめるのに足りる範囲で真似る。
import type { InboxApproveResult, InboxDecision, InboxItem, InboxState, InboxVolume, OrganizeOp } from "../api";

const WORKS: Record<number, string> = { 1: "サンプル漫画", 2: "別の作品" };

const pad = (n: number) => String(n).padStart(2, "0");

const vol = (id: number, title: string, n: number | null, extra: Partial<InboxVolume> = {}): InboxVolume => ({
  volume_id: id,
  title,
  page_count: 8,
  number: n === null ? null : { kind: "volume", start: n, end: n, source: "name" },
  output_rel_path: null,
  duplicate_of: [],
  skipped: false,
  reason: null,
  ...extra,
});

function baseItem(over: Partial<InboxItem> & Pick<InboxItem, "source_id" | "name">): InboxItem {
  return {
    inbox: "E:/受け入れ",
    rel_path: over.name,
    kind: "archive",
    status: "ready",
    category: "new_work",
    mode: "cbz",
    reasons: [],
    warnings: [],
    issues: [],
    work: null,
    volumes: [],
    needs_analysis: [],
    expect_outputs: [],
    ...over,
  };
}

/** 4 通り: 既存作品の新しい巻 / 新しい作品 / 既に持っている巻かもしれない・作品が決まらない / 壊れたアーカイブ */
export function defaultInbox(): InboxItem[] {
  return [
    baseItem({
      source_id: 101, name: "サンプル漫画 第03巻.cbz", mode: "move", category: "existing_work",
      work: { match: "existing", work_id: 1, title: "サンプル漫画", decided: false, confident: true, confidence: 0.9,
              reason: "巻の名前から既存の作品「サンプル漫画」と判断しました", candidates: [] },
      volumes: [vol(1001, "サンプル漫画 第03巻", 3)],
    }),
    baseItem({
      source_id: 102, name: "新作 第01巻.zip", category: "new_work",
      work: { match: "new", work_id: null, title: "新作", decided: false, confident: true, confidence: 0.9,
              reason: "既存の作品に同じ名前がありません。新しい作品「新作」として入れます", candidates: [] },
      volumes: [vol(1002, "新作 第01巻", 1)],
    }),
    baseItem({
      source_id: 103, name: "第2巻.cbz", rel_path: "別名の作品/第2巻.cbz", mode: "move", category: "unknown_work",
      work: { match: "new", work_id: null, title: "別名の作品", decided: false, confident: false, confidence: 0.6,
              reason: "巻自身の名前に作品名が無く、外側の名前「別名の作品」から推測しました",
              candidates: [{ work_id: 1, title: "サンプル漫画" }] },
      volumes: [vol(1003, "第2巻", 2)],
    }),
    baseItem({
      source_id: 104, name: "壊れた巻.zip", status: "blocked", category: "unreadable", mode: null,
      reasons: ["読めないアーカイブ: zip として開けない: File is not a zip file"],
    }),
  ];
}

/** 選んだ内容を整理案に反映する(作品が決まる・番号を直す・取り込まない)。 */
export function applyDecision(base: InboxItem, d: InboxDecision): InboxItem {
  const item: InboxItem = JSON.parse(JSON.stringify(base));
  if (item.status === "blocked" || !item.work) return item;
  const w = item.work;
  if (d.work_id) {
    w.match = "existing";
    w.work_id = d.work_id;
    w.title = WORKS[d.work_id] ?? `作品 ${d.work_id}`;
    w.decided = true;
  } else if (d.new_title) {
    w.match = "new";
    w.work_id = null;
    w.title = d.new_title;
    w.decided = true;
  }
  const known = w.decided || w.confident;
  const unresolved: string[] = [];
  const outputs: string[] = [];
  for (const v of item.volumes) {
    const n = d.numbers?.[String(v.volume_id)];
    if (n !== undefined) v.number = { kind: "volume", start: n, end: n, source: "manual" };
    v.skipped = !!d.skip_volume_ids?.includes(v.volume_id);
    // 作品が「サンプル漫画」で 2 巻なら既に持っている
    v.duplicate_of = w.work_id === 1 && v.number?.start === 2 ? [{ volume_id: 2, title: "サンプル漫画 第02巻", rel_path: "sample_v02.cbz" }] : [];
    v.output_rel_path = null;
    v.reason = v.skipped ? "取り込まない" : null;
    if (v.skipped) continue;
    if (v.duplicate_of.length > 0) {
      v.reason = "書庫の「サンプル漫画 第02巻」と同じ巻かもしれません。取り込まないか、番号を直してください";
      unresolved.push(`「${v.title}」は既に持っている巻かもしれません`);
    } else if (v.number === null) {
      v.reason = "名前から巻・話の番号を読めません。番号を入力してください";
      unresolved.push(`「${v.title}」の番号が読めません`);
    } else if (known) {
      v.output_rel_path = `${w.title}/${w.title} 第${pad(v.number.start)}巻.cbz`;
      outputs.push(v.output_rel_path);
    }
  }
  if (!known) unresolved.unshift(w.reason);
  if (unresolved.length > 0) {
    item.status = "choose";
    item.reasons = unresolved;
  } else if (outputs.length === 0) {
    item.status = "blocked";
    item.reasons = ["取り込む巻がありません(すべて取り込まない)"];
  } else {
    item.status = "ready";
    item.reasons = [];
  }
  item.expect_outputs = outputs.sort();
  return item;
}

export function createMockInbox(ops: OrganizeOp[], nextOpId: () => string) {
  let items = defaultInbox().map((i) => applyDecision(i, { source_id: i.source_id }));
  const accepted = new Map<string, InboxItem>();
  const state = (extra: Partial<InboxState> = {}): InboxState => ({
    inboxes: [{ path: "E:/受け入れ", library: "E:/漫画", problems: [], synced_at: null }],
    items,
    ...extra,
  });

  return {
    handle(method: string, path: string, body: { items?: InboxDecision[] } | undefined): { status: number; body: unknown } | null {
      if (method === "GET" && path === "/api/inbox") return { status: 200, body: state() };
      if (method === "POST" && path === "/api/inbox/sync") return { status: 200, body: state({ volumes: items.length, offline_roots: [] }) };
      if (method === "POST" && path === "/api/inbox/preview") {
        const out = (body?.items ?? []).map((d) => {
          const base = items.find((i) => i.source_id === d.source_id);
          return base ? applyDecision(base, d) : { ...baseItem({ source_id: d.source_id, name: "", volumes: [] }), status: "blocked" as const };
        });
        return { status: 200, body: state({ items: out }) };
      }
      if (method === "POST" && path === "/api/inbox/approve") {
        const results: InboxApproveResult[] = [];
        for (const d of body?.items ?? []) {
          const base = items.find((i) => i.source_id === d.source_id);
          const item = base ? applyDecision(base, d) : null;
          if (!base || !item || item.status !== "ready") {
            results.push({ source_id: d.source_id, ok: false, error: "取り込めません: 確認が要ります", item: item ?? baseItem({ source_id: d.source_id, name: "", volumes: [] }) });
            continue;
          }
          const imported = item.volumes.filter((v) => !v.skipped);
          const op: OrganizeOp = {
            op_id: nextOpId(), kind: "intake", mode: item.mode, naming: "template", template: "<作品名>/<作品名> 第NN巻.cbz",
            state: "done", source_id: item.source_id, source_rel_path: item.rel_path, source_kind: "archive",
            volumes: imported.map((v) => ({ volume_id: v.volume_id, title: v.title, page_count: v.page_count, output_rel_path: v.output_rel_path, new_volume_id: v.volume_id + 5000 })),
            warnings: [], problems: [], needs_analysis: [], created_at: "2026-11-01T00:00:00+00:00",
            executed_at: "2026-11-01T00:00:05+00:00", undone_at: null, error: null,
          };
          ops.unshift(op);
          accepted.set(op.op_id!, base);
          items = items.filter((i) => i.source_id !== d.source_id);
          results.push({ source_id: d.source_id, ok: true, item, op });
        }
        return { status: 200, body: { results } };
      }
      return null;
    },
    /** 元に戻したら受け入れ待ちに戻る */
    undone(opId: string) {
      const base = accepted.get(opId);
      if (base && !items.some((i) => i.source_id === base.source_id)) items = [...items, base].sort((a, b) => a.source_id - b.source_id);
    },
  };
}
