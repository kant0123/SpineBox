// 作品(#43)のモック API。mockBackend.ts から使う。アプリ本体からは import しない。

import type { ReadingState, VolumeNumber, VolumeSummary, WorkBrief, WorkDetail, WorkFilterName, WorkProposal, WorkSlot, WorkSummary, WorkVolume } from "../api";

interface MockWork {
  id: number;
  title: string;
  title_manual: boolean;
  author: string | null;
  author_manual: boolean;
  aliases: { id: number; alias: string; manual: boolean; source: "filename" | "merge" | "manual" | "container"; confidence: number }[];
  volumeIds: number[];
  merges: WorkDetail["merges"];
  /** 公開 DB の項目(#50)の代わり。絞り込みの候補を画面で見るため */
  fields?: Partial<Record<"kind" | "genre" | "tag", string>>;
}

/** バックエンド(spinebox/naming.py normalize_title)と同じ正規化: NFKC・小文字・カタカナ → ひらがな・記号と空白を落とす */
export function normalizeSearch(text: string): string {
  return Array.from(text.normalize("NFKC").toLowerCase())
    .map((ch) => (ch >= "ァ" && ch <= "ヶ" ? String.fromCharCode(ch.charCodeAt(0) - 0x60) : ch))
    .filter((ch) => /[\p{L}\p{N}]/u.test(ch))
    .join("");
}

const splitValues = (v: string | null | undefined) => (v ?? "").split(/[、,，;；]/).map((x) => x.trim()).filter(Boolean);

type Res = { status: number; body: unknown };

/** 画面確認用の巻番号(モックの巻の番号とは別に、作品ページの欠け表示を見せるため) */
const NUMBERS: Record<number, number> = { 1: 1, 6: 1, 2: 2, 3: 4 };

export function createMockWorks(getVolumes: () => (VolumeSummary & { hidden?: boolean })[]) {
  let aliasSeq = 10;
  let workSeq = 10;
  const works: MockWork[] = [
    {
      id: 1, title: "サンプル漫画", title_manual: false, author: "山田太郎", author_manual: false,
      aliases: [{ id: 1, alias: "Sample manga", manual: false, source: "merge", confidence: 0.99 }],
      volumeIds: [1, 6, 2, 3], fields: { kind: "漫画", genre: "ファンタジー、コメディ" },
      merges: [{
        id: 1, rule: "duplicate", confidence: 0.99, status: "auto", titles: ["Sample manga", "サンプル漫画"],
        evidence: { duplicate: [{ matched_pages: 186, compared_pages: 188, ratio: 0.989, rule: "same_number",
          volume_a: { id: 1, title: "サンプル漫画 第01巻" }, volume_b: { id: 6, title: "Sample manga v01" } }] },
      }],
    },
    { id: 2, title: "外付けの作品", title_manual: false, author: null, author_manual: false, aliases: [], volumeIds: [4], merges: [] },
    { id: 3, title: "別の読み物", title_manual: false, author: null, author_manual: false, aliases: [], volumeIds: [5], merges: [], fields: { kind: "小説" } },
  ];
  const proposals: (WorkProposal & { dismissed: boolean })[] = [{
    id: 1, rule: "duplicate", confidence: 0.6, status: "open", dismissed: false,
    evidence: { duplicate: [{ matched_pages: 60, compared_pages: 100, ratio: 0.6, rule: "same_number",
      volume_a: { id: 4, title: "外付けドライブの巻" }, volume_b: { id: 5, title: "v01-03" } }] },
    a: { id: 2, title: "外付けの作品", author: null, aliases: [], volume_count: 1, volumes: [{ id: 4, title: "外付けドライブの巻", volume_number: null }] },
    b: { id: 3, title: "別の読み物", author: null, aliases: [], volume_count: 1, volumes: [{ id: 5, title: "v01-03", volume_number: null }] },
  }];

  const find = (id: number) => works.find((w) => w.id === id);
  const vol = (id: number) => getVolumes().find((v) => v.id === id && !v.hidden);
  const number = (id: number): VolumeNumber | null => (NUMBERS[id] ? { kind: "volume", start: NUMBERS[id], end: NUMBERS[id], evidence: `${NUMBERS[id]}` } : null);

  const volJson = (id: number): WorkVolume | null => {
    const v = vol(id);
    if (!v) return null;
    return {
      id: Number(v.id), title: v.title, parent_title: v.parent_title, page_count: v.page_count, offline: v.offline, progress: v.progress,
      volume_number: number(id), duplicate_count: v.duplicate_count,
      assignment: { source: "filename", manual: false, confidence: 0.9, evidence: { level: "volume" } },
    };
  };
  const volumesOf = (w: MockWork) => w.volumeIds.map(volJson).filter((v): v is WorkVolume => v !== null);

  function slots(vols: WorkVolume[]): { slots: WorkSlot[]; unnumbered: WorkVolume[] } {
    const groups = new Map<number, WorkVolume[]>();
    const unnumbered: WorkVolume[] = [];
    for (const v of vols) {
      if (!v.volume_number) unnumbered.push(v);
      else groups.set(v.volume_number.start, [...(groups.get(v.volume_number.start) ?? []), v]);
    }
    const out = [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([n, vs]): WorkSlot => ({
      kind: "volume", start: n, end: n, label: `${n}巻`, duplicate: vs.length > 1, volumes: vs,
    }));
    return { slots: out, unnumbered };
  }

  const missingOf = (vols: WorkVolume[]) => {
    const have = new Set(vols.map((v) => v.volume_number?.start).filter((n): n is number => !!n));
    const max = Math.max(0, ...have);
    return { max: max || null, missing: Array.from({ length: max }, (_, i) => i + 1).filter((n) => !have.has(n)) };
  };

  const openFor = (id: number) => proposals.filter((p) => p.status === "open" && !p.dismissed && (p.a.id === id || p.b.id === id)).length;

  function summary(w: MockWork): WorkSummary {
    const vols = volumesOf(w);
    const m = missingOf(vols);
    return {
      id: w.id, title: w.title, title_source: w.title_manual ? "manual" : "filename", title_manual: w.title_manual,
      author: w.author, author_source: w.author ? (w.author_manual ? "manual" : "filename") : null, author_manual: w.author_manual,
      aliases: w.aliases.map((a) => ({ id: a.id, alias: a.alias, source: a.source, manual: a.manual, confidence: a.confidence })),
      volume_count: vols.length, offline_count: vols.filter((v) => v.offline).length,
      finished_count: vols.filter((v) => v.progress?.finished).length,
      duplicate_slots: slots(vols).slots.filter((s) => s.duplicate).length,
      max_volume: m.max, missing_volumes: m.missing, missing_episodes: [], pending_proposals: openFor(w.id), merged: w.merges.length > 0,
      reading_state: readingState(vols),
    };
  }

  function readingState(vols: WorkVolume[]): ReadingState {
    if (vols.length && vols.every((v) => v.progress?.finished)) return "finished";
    return vols.some((v) => v.progress) ? "reading" : "unread";
  }

  const valuesOf = (w: MockWork): Record<WorkFilterName, string[]> => ({
    author: splitValues(w.author), kind: splitValues(w.fields?.kind), genre: splitValues(w.fields?.genre), tag: splitValues(w.fields?.tag),
  });

  /** GET /api/works の検索・絞り込み(バックエンドと同じ規則の簡易版: 語は AND、項目は値の一致) */
  function listWorks(query: string): Res {
    const params = new URLSearchParams(query);
    const all = works.filter((w) => w.volumeIds.some((id) => vol(id)));
    const facets: Record<WorkFilterName, string[]> = { author: [], kind: [], genre: [], tag: [] };
    for (const w of all) {
      const vals = valuesOf(w);
      for (const k of Object.keys(facets) as WorkFilterName[]) facets[k] = [...new Set([...facets[k], ...vals[k]])].sort();
    }
    const status = params.get("status");
    if (status && !["unread", "reading", "finished"].includes(status)) return res(422, { detail: "status は unread / reading / finished" });
    const terms = (params.get("q") ?? "").split(/\s+/).map(normalizeSearch).filter(Boolean);
    const shown = all.filter((w) => {
      const body = [w.title, ...w.aliases.map((a) => a.alias), w.author ?? "", ...Object.values(w.fields ?? {}), ...volumesOf(w).map((v) => v.title)]
        .map(normalizeSearch).join("\n");
      if (!terms.every((t) => body.includes(t))) return false;
      const vals = valuesOf(w);
      for (const k of Object.keys(facets) as WorkFilterName[]) {
        const wanted = params.get(k);
        if (wanted && !vals[k].some((v) => normalizeSearch(v) === normalizeSearch(wanted))) return false;
      }
      return !status || readingState(volumesOf(w)) === status;
    });
    return res(200, { works: shown.map(summary), total: all.length, facets, unassigned: 0 });
  }

  function detail(w: MockWork): WorkDetail {
    const vols = volumesOf(w);
    const s = slots(vols);
    const m = missingOf(vols);
    return {
      ...summary(w), title_confidence: 0.9, slots: s.slots, unnumbered: s.unnumbered,
      numbers: { volume: { max: m.max, missing: m.missing, limited: false }, episode: { max: null, missing: [], limited: false } },
      proposals: proposals.filter((p) => p.status === "open" && !p.dismissed && (p.a.id === w.id || p.b.id === w.id)),
      merges: w.merges,
      aliases: w.aliases.map((a) => ({ id: a.id, alias: a.alias, source: a.source, manual: a.manual, confidence: a.confidence })),
    };
  }

  const brief = (w: MockWork): WorkBrief => ({
    id: w.id, title: w.title, author: w.author, aliases: w.aliases.map((a) => a.alias), volume_count: w.volumeIds.length,
    volumes: volumesOf(w).map((v) => ({ id: v.id, title: v.title, volume_number: v.volume_number })),
  });

  const res = (status: number, body: unknown): Res => ({ status, body });

  /** 作品の API を処理する。対象外のパスは null */
  function handle(method: string, path: string, query: string, body: any): Res | null {
    if (!path.startsWith("/api/works")) return null;
    if (method === "GET" && path === "/api/works") return listWorks(query);
    if (method === "POST" && path === "/api/works/refresh") return res(200, { works_created: 0, volumes_assigned: 0, merged: 0, proposals_open: proposals.length });
    if (method === "GET" && path === "/api/works/proposals") {
      const status = new URLSearchParams(query).get("status") ?? "open";
      const wantDismissed = status === "dismissed";
      return res(200, proposals.filter((p) => p.dismissed === wantDismissed && (wantDismissed || p.status === "open")).map((p) => ({
        ...p, a: brief(find(p.a.id)!), b: brief(find(p.b.id)!),
      })));
    }
    const dec = path.match(/^\/api\/works\/proposals\/(\d+)\/(approve|dismiss|restore)$/);
    if (dec && method === "POST") {
      const p = proposals.find((x) => x.id === Number(dec[1]));
      if (!p) return res(404, { detail: "提案が見つかりません" });
      if (dec[2] === "approve") {
        const keep = find(p.a.id)!, drop = find(p.b.id)!;
        keep.aliases.push({ id: ++aliasSeq, alias: drop.title, manual: false, source: "merge", confidence: p.confidence });
        keep.volumeIds.push(...drop.volumeIds);
        works.splice(works.indexOf(drop), 1);
        keep.merges.push({ id: 100 + p.id, rule: p.rule, confidence: p.confidence, status: "auto", titles: [keep.title, drop.title], evidence: p.evidence });
        p.status = "open";
        proposals.splice(proposals.indexOf(p), 1);
      } else {
        p.dismissed = dec[2] === "dismiss";
      }
      return res(200, { id: p.id, status: p.dismissed ? "dismissed" : "open" });
    }
    if (method === "POST" && path === "/api/works/assign") {
      const ids: number[] = body.volume_ids;
      for (const w of works) w.volumeIds = w.volumeIds.filter((id) => !ids.includes(id));
      let target = body.work_id == null ? undefined : find(body.work_id);
      if (body.work_id == null) {
        const first = vol(ids[0]);
        target = { id: ++workSeq, title: body.title || first?.title || "新しい作品", title_manual: !!body.title, author: null, author_manual: false, aliases: [], volumeIds: [], merges: [] };
        works.push(target);
      }
      if (!target) return res(404, { detail: "作品が見つかりません" });
      target.volumeIds.push(...ids);
      return res(200, { work_id: target.id });
    }
    const m = path.match(/^\/api\/works\/(\d+)(?:\/(aliases|merge)(?:\/(\d+))?)?$/);
    if (!m) return res(404, { detail: "見つかりません" });
    const w = find(Number(m[1]));
    if (!w) return res(404, { detail: "作品が見つかりません" });
    if (!m[2] && method === "GET") return res(200, detail(w));
    if (!m[2] && method === "PATCH") {
      if (typeof body.title === "string") {
        if (!body.title.trim()) return res(422, { detail: "作品名を入力してください" });
        w.aliases.push({ id: ++aliasSeq, alias: w.title, manual: true, source: "manual", confidence: 1 });
        w.title = body.title.trim();
        w.title_manual = true;
      }
      if (typeof body.author === "string") {
        w.author = body.author.trim() || null;
        w.author_manual = true;
      }
      return res(200, detail(w));
    }
    if (m[2] === "aliases" && method === "POST") {
      if (!body.alias?.trim()) return res(422, { detail: "別名を入力してください" });
      // 別の作品の名前と同じ別名を足すと、その作品へ自動で統合される(吸収されるのはこの作品の側。返すのは吸収先)
      const target = works.find((x) => x !== w && x.title === body.alias.trim());
      if (target) {
        target.aliases.push({ id: ++aliasSeq, alias: w.title, manual: false, source: "merge", confidence: 0.9 });
        target.volumeIds.push(...w.volumeIds);
        works.splice(works.indexOf(w), 1);
        return res(200, detail(target));
      }
      w.aliases.push({ id: ++aliasSeq, alias: body.alias.trim(), manual: true, source: "manual", confidence: 1 });
      return res(200, detail(w));
    }
    if (m[2] === "aliases" && method === "DELETE") {
      w.aliases = w.aliases.filter((a) => a.id !== Number(m[3]));
      return res(200, detail(w));
    }
    if (m[2] === "merge" && method === "POST") {
      const other = find(body.other_id);
      if (!other || other === w) return res(other ? 409 : 404, { detail: other ? "同じ作品です" : "作品が見つかりません" });
      w.aliases.push({ id: ++aliasSeq, alias: other.title, manual: true, source: "merge", confidence: 1 });
      w.volumeIds.push(...other.volumeIds);
      works.splice(works.indexOf(other), 1);
      w.merges.push({ id: 200 + other.id, rule: "manual", confidence: 1, status: "approved", titles: [w.title, other.title], evidence: {} });
      return res(200, detail(w));
    }
    return res(405, { detail: "未対応" });
  }

  return { handle, works, proposals };
}
