// 作品(#43)のモック API。mockBackend.ts から使う。アプリ本体からは import しない。

import type { FieldInfo, LinkCandidate, SourceName, VolumeMetaField, VolumeNumber, VolumeSummary, WorkBrief, WorkDetail, WorkLink, WorkProposal, WorkSlot, WorkSummary, WorkVolume } from "../api";

interface MockWork {
  id: number;
  title: string;
  title_manual: boolean;
  author: string | null;
  author_manual: boolean;
  aliases: { id: number; alias: string; manual: boolean; source: "filename" | "merge" | "manual" | "container"; confidence: number }[];
  volumeIds: number[];
  merges: WorkDetail["merges"];
  /** 作品名・著者以外の項目(種別・出版社・ジャンルなど)と、手で直す前の値 */
  extra: Record<string, FieldInfo<any>>;
  previous: Record<string, FieldInfo<any> | null>;
  links: WorkLink[];
}

const SOURCES: SourceName[] = ["ndl", "anilist", "mangaupdates"];
const LABELS: Record<SourceName, string> = { ndl: "国立国会図書館サーチ", anilist: "AniList", mangaupdates: "MangaUpdates" };

const cand = (source_id: string, over: Partial<LinkCandidate>): LinkCandidate => ({
  source_id, title: "サンプル漫画", subtitle: null, romaji: null, authors: ["山田太郎"], kind: "manga", publisher: "集英社",
  released: "2013-05", total_volumes: null, genres: [], url: `https://ndlsearch.ndl.go.jp/books/${source_id}`, score: 0.78, reasons: ["title:exact"], ...over,
});

const emptyLink = (source: SourceName): WorkLink => ({
  source, label: LABELS[source], status: null, manual: false, confidence: null, source_id: null, attempted_at: null, checked_at: null,
  failure: null, linked: null, decision: null, margin: null, hold: null, responses: [], candidates: [],
});

const ANILIST = cand("anilist:101", {
  title: "サンプル漫画", romaji: "Sample Manga", publisher: null, released: null, total_volumes: 4, genres: ["Action", "Comedy"],
  url: "https://anilist.co/manga/101", score: 0.95,
});

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
      volumeIds: [1, 6, 2, 3],
      extra: {}, previous: {},
      links: [
        {
          ...emptyLink("ndl"), status: "pending", attempted_at: "2026-11-02T01:00:00+00:00", decision: "ambiguous", margin: 0.05,
          responses: [{ label: "search", fetched_at: "2026-11-02T01:00:00+00:00", from_cache: false, stale: false }],
          candidates: [
            cand("ndl:a1", { score: 0.82, record_count: 4, volumes: [1, 2, 3, 4] }),
            cand("ndl:n1", { kind: "novel", publisher: "集英社オレンジ文庫", score: 0.8, record_count: 2, volumes: [1, 2] }),
          ],
        },
        {
          ...emptyLink("anilist"), status: "pending", hold: "kind_unresolved", attempted_at: "2026-11-02T01:00:01+00:00",
          candidates: [ANILIST],
        },
        {
          ...emptyLink("mangaupdates"), attempted_at: "2026-11-02T01:00:02+00:00",
          failure: { kind: "timeout", message: "timeout", status: null, retry_after: null, at: "2026-11-02T01:00:02+00:00" },
        },
      ],
      merges: [{
        id: 1, rule: "duplicate", confidence: 0.99, status: "auto", titles: ["Sample manga", "サンプル漫画"],
        evidence: { duplicate: [{ matched_pages: 186, compared_pages: 188, ratio: 0.989, rule: "same_number",
          volume_a: { id: 1, title: "サンプル漫画 第01巻" }, volume_b: { id: 6, title: "Sample manga v01" } }] },
      }],
    },
    { id: 2, title: "外付けの作品", title_manual: false, author: null, author_manual: false, aliases: [], volumeIds: [4], merges: [], extra: {}, previous: {}, links: SOURCES.map(emptyLink) },
    { id: 3, title: "別の読み物", title_manual: false, author: null, author_manual: false, aliases: [], volumeIds: [5], merges: [], extra: {}, previous: {}, links: SOURCES.map(emptyLink) },
  ];
  const proposals: (WorkProposal & { dismissed: boolean })[] = [{
    id: 1, rule: "duplicate", confidence: 0.6, status: "open", dismissed: false,
    evidence: { duplicate: [{ matched_pages: 60, compared_pages: 100, ratio: 0.6, rule: "same_number",
      volume_a: { id: 4, title: "外付けドライブの巻" }, volume_b: { id: 5, title: "v01-03" } }] },
    a: { id: 2, title: "外付けの作品", author: null, aliases: [], volume_count: 1, volumes: [{ id: 4, title: "外付けドライブの巻", volume_number: null }] },
    b: { id: 3, title: "別の読み物", author: null, aliases: [], volume_count: 1, volumes: [{ id: 5, title: "v01-03", volume_number: null }] },
  }];

  const volMeta: Record<number, Partial<Record<VolumeMetaField, FieldInfo<string | null>>>> = {};
  const volPrev: Record<string, FieldInfo<string | null> | null> = {};
  const fillVolumes = (w: MockWork) => {
    for (const id of w.volumeIds) {
      const n = NUMBERS[id];
      if (n && !volMeta[id]) {
        volMeta[id] = {
          publisher: { value: "集英社", source: "public_db", manual: false, confidence: 0.82, from: "ndl", url: `https://ndlsearch.ndl.go.jp/books/v${n}` },
          isbn: { value: `97840887${n}0000`, source: "public_db", manual: false, confidence: 0.82, from: "ndl", url: `https://ndlsearch.ndl.go.jp/books/v${n}` },
        };
      }
    }
  };

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
      metadata: volMeta[id] ?? {},
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

  const val = (w: MockWork, name: string) => w.extra[name]?.value;

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
      kind: val(w, "kind") ?? null, publisher: val(w, "publisher") ?? null, genres: val(w, "genres") ?? [], tags: val(w, "tags") ?? [],
      link_pending: w.links.filter((l) => l.status === "pending").length,
    };
  }

  function detail(w: MockWork): WorkDetail {
    const vols = volumesOf(w);
    const s = slots(vols);
    const m = missingOf(vols);
    return {
      ...summary(w), title_confidence: 0.9, links: w.links,
      fields: {
        title: { value: w.title, source: w.title_manual ? "manual" : "filename", manual: w.title_manual, confidence: 0.9 },
        ...(w.author ? { author: { value: w.author, source: w.author_manual ? "manual" : "filename", manual: w.author_manual, confidence: 0.9 } } : {}),
        ...w.extra,
      },
      slots: s.slots, unnumbered: s.unnumbered,
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

  let fetchState = { running: false, started_at: "2026-11-02T01:04:00+00:00", finished_at: null as string | null, done: 0, total: 0, current: null, summary: null as unknown, error: null };

  function resetField(w: MockWork, field: string): Res {
    const cur = w.extra[field];
    if (!cur?.manual) return res(409, { detail: "この項目は手動で編集されていません" });
    const prev = w.previous[field];
    if (prev) w.extra[field] = prev;
    else delete w.extra[field];
    delete w.previous[field];
    return res(200, detail(w));
  }

  /** 候補を選ぶ。NDL で種別が決まれば、保留だった取得元も自動で連携する */
  function choose(w: MockWork, link: WorkLink, sourceId: string | null): Res {
    if (sourceId === null) {
      Object.assign(link, { status: "none", manual: true, source_id: null, linked: null, hold: null });
      return res(200, detail(w));
    }
    const picked = link.candidates.find((c) => c.source_id === sourceId);
    if (!picked) return res(422, { detail: "その候補は見つかりません" });
    Object.assign(link, { status: "linked", manual: true, source_id: sourceId, linked: picked, confidence: 1, hold: null, decision: "manual" });
    const setAuto = (name: string, value: unknown, sources: string[]) => {
      if (!w.extra[name]?.manual) w.extra[name] = { value, source: "public_db", manual: false, confidence: 1, sources };
    };
    if (link.source === "ndl") {
      if (picked.kind) setAuto("kind", picked.kind, ["ndl"]);
      if (picked.publisher) setAuto("publisher", picked.publisher, ["ndl"]);
      fillVolumes(w);
      for (const other of w.links.filter((l) => l.hold === "kind_unresolved")) {
        const c = other.candidates[0];
        Object.assign(other, { status: "linked", manual: false, source_id: c.source_id, linked: c, confidence: c.score, hold: null, decision: "auto" });
        setAuto("genres", c.genres, [other.source]);
      }
    } else if (picked.genres.length) setAuto("genres", picked.genres, [link.source]);
    return res(200, detail(w));
  }

  function unlink(w: MockWork, link: WorkLink): Res {
    Object.assign(link, { status: link.candidates.length ? "pending" : null, manual: false, source_id: null, linked: null, confidence: null });
    return res(200, detail(w));
  }

  function volumeMeta(method: string, id: number, reset: string | undefined, body: any): Res {
    if (!vol(id)) return res(404, { detail: "巻が見つかりません" });
    const meta = (volMeta[id] ??= {});
    if (method === "PATCH") {
      for (const [name, raw] of Object.entries(body ?? {}) as [VolumeMetaField, string | null][]) {
        if (name === "isbn" && raw && !/^\d{10}$|^\d{13}$/.test(raw.replace(/-/g, ""))) return res(422, { detail: "ISBN は 10 桁か 13 桁で入力してください" });
        volPrev[`${id}:${name}`] = meta[name]?.manual ? (volPrev[`${id}:${name}`] ?? null) : (meta[name] ?? null);
        meta[name] = { value: raw || null, source: "manual", manual: true, confidence: 1 };
      }
      return res(200, { volume_id: id, metadata: meta });
    }
    if (reset) {
      const name = reset as VolumeMetaField;
      if (!meta[name]?.manual) return res(409, { detail: "この項目は手動で編集されていません" });
      const prev = volPrev[`${id}:${name}`];
      if (prev) meta[name] = prev;
      else delete meta[name];
      return res(200, { volume_id: id, metadata: meta });
    }
    return res(405, { detail: "未対応" });
  }

  /** 作品の API を処理する。対象外のパスは null */
  function handle(method: string, path: string, query: string, body: any): Res | null {
    if (!path.startsWith("/api/works") && !/^\/api\/volumes\/\d+\/metadata/.test(path)) return null;
    const live = () => works.filter((w) => w.volumeIds.some((id) => vol(id)));
    if (method === "GET" && path === "/api/works") return res(200, { works: live().map(summary), unassigned: 0 });
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
        target = { id: ++workSeq, title: body.title || first?.title || "新しい作品", title_manual: !!body.title, author: null, author_manual: false, aliases: [], volumeIds: [], merges: [], extra: {}, previous: {}, links: SOURCES.map(emptyLink) };
        works.push(target);
      }
      if (!target) return res(404, { detail: "作品が見つかりません" });
      target.volumeIds.push(...ids);
      return res(200, { work_id: target.id });
    }
    if (method === "GET" && path === "/api/works/metadata") {
      return res(200, { state: fetchState, sources: SOURCES, background: false });
    }
    if (method === "POST" && path === "/api/works/metadata/fetch") {
      fetchState = { ...fetchState, finished_at: "2026-11-02T01:05:00+00:00", done: works.length, total: works.length, summary: { works: works.length, sources: { ndl: works.length } } };
      return res(202, fetchState);
    }
    const ex = path.match(/^\/api\/works\/(\d+)\/(fields\/(\w+)\/reset|metadata\/fetch|links\/(\w+)\/(choose|reset))$/);
    if (ex && method === "POST") {
      const w = find(Number(ex[1]));
      if (!w) return res(404, { detail: "作品が見つかりません" });
      if (ex[3]) return resetField(w, ex[3]);
      if (ex[2] === "metadata/fetch") {
        for (const l of w.links) l.attempted_at = "2026-11-02T02:00:00+00:00";
        return res(200, detail(w));
      }
      const link = w.links.find((l) => l.source === ex[4]);
      if (!link) return res(404, { detail: "取得元が見つかりません" });
      return ex[5] === "choose" ? choose(w, link, body?.source_id ?? null) : unlink(w, link);
    }
    const vm = path.match(/^\/api\/volumes\/(\d+)\/metadata(?:\/(\w+)\/reset)?$/);
    if (vm) return volumeMeta(method, Number(vm[1]), vm[2], body);
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
      for (const name of ["kind", "publisher", "synopsis", "genres", "tags"]) {
        if (!(name in body)) continue;
        if (name === "kind" && body.kind !== null && !["manga", "novel"].includes(body.kind)) return res(422, { detail: "種別は漫画か小説です" });
        if (!(name in w.previous)) w.previous[name] = w.extra[name]?.manual ? (w.previous[name] ?? null) : (w.extra[name] ?? null);
        const empty = body[name] === null || body[name] === "" || (Array.isArray(body[name]) && body[name].length === 0);
        w.extra[name] = { value: empty ? (Array.isArray(body[name]) ? [] : null) : body[name], source: "manual", manual: true, confidence: 1 };
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
