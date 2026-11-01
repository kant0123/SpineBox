// バックエンド(spinebox/)との API 契約。Issue #14・#15 で共有している形に合わせる。
// ページ番号は 0 始まり(pages[].index と同じ)。

export type VolumeId = number | string;

export interface Progress {
  page: number;
  finished: boolean;
}

/** 名前から読んだ巻・話の番号の候補(確定ではない)。evidence は名前の中で当たった部分 */
export interface VolumeNumber {
  kind: "volume" | "episode";
  start: number;
  end: number;
  evidence: string;
}

/** 名前の巻範囲と中身の食い違い。scope は名前を比べた相手(volume = 分解した親の巻、source = 外側のアーカイブ) */
export interface Finding {
  scope: "volume" | "source";
  subject: string;
  name_range: VolumeNumber;
  found: number[];
  missing: number[];
  extra: number[];
  duplicates: number[];
  unnumbered: number;
  message: string;
}

export interface VolumeSummary {
  id: VolumeId;
  title: string;
  source_name: string;
  page_count: number | null;
  offline: boolean;
  progress: Progress | null;
  /** 分解で作った子の巻なら親(まとめ)の id。親は一覧に出ない */
  parent_id: VolumeId | null;
  parent_title: string | null;
  volume_number: VolumeNumber | null;
  findings: Finding[];
  /** 未解析の内側アーカイブの巻(「中の巻を調べる」で分解できる) */
  can_analyze: boolean;
  /** この巻を含む、却下されていない重複候補の数(別スキャンの疑い。#38) */
  duplicate_count: number;
  /** 名前から割り当てられた作品(#43)。割り当てが無ければ null */
  work_id?: number | null;
}

export interface PageInfo {
  index: number;
  name: string;
}

export interface VolumeDetail {
  id: VolumeId;
  title: string;
  page_count: number | null;
  pages: PageInfo[];
  progress: Progress | null;
  parent_id?: VolumeId | null;
  volume_number?: VolumeNumber | null;
  findings?: Finding[];
}

export interface AnalyzeResult {
  id: VolumeId;
  analyzed: boolean;
  children: { id: VolumeId; title: string; page_count: number | null; volume_number: VolumeNumber | null }[];
  findings: Finding[];
}

/** スキャンで読めなかった・巻にならなかったもの。container_path / entry_name は入れ子のアーカイブの中の話 */
export interface ScanIssue {
  path: string;
  container_path: string | null;
  entry_name: string | null;
  reason: string;
}

export interface ScanResult {
  volumes: number;
  offline_roots: string[];
  /** 読めなかったもの(上限件数まで)。issues_total は上限で切る前の件数 */
  issues: ScanIssue[];
  issues_total: number;
}

/** 重複候補の片方の巻。ページ画像は `api.pageUrl(id, 0)` が表紙 */
export interface DuplicateVolume {
  id: VolumeId;
  title: string;
  source_name: string;
  parent_title: string | null;
  page_count: number | null;
  offline: boolean;
  volume_number: VolumeNumber | null;
  progress: Progress | null;
}

/** 候補の根拠。pages_a / matched_a は候補の a 側、samples の a_page / b_page はそれぞれ a・b のページ番号(0 始まり) */
export interface DuplicateEvidence {
  matched_pages: number;
  compared_pages: number;
  pages_a: number;
  pages_b: number;
  matched_a: number;
  matched_b: number;
  near_bits: number;
  number_a: string | null;
  number_b: string | null;
  samples: { a_page: number; b_page: number; distance: number }[];
}

export interface DuplicateCandidate {
  id: number;
  /** same_number = 巻番号の候補が同じ、visual_only = どちらかの巻番号が不明で見た目だけで判定 */
  rule: "same_number" | "visual_only";
  matched_pages: number;
  compared_pages: number;
  evidence: DuplicateEvidence;
  dismissed: boolean;
  a: DuplicateVolume;
  b: DuplicateVolume;
}

export interface DuplicateScanState {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  done: number;
  total: number;
  summary: { fingerprinted: number; errors: number; candidates: number } | null;
  error: string | null;
}

export interface DuplicateList {
  scan: DuplicateScanState;
  candidates: DuplicateCandidate[];
}

// 作品の名寄せ(#43)。出所(source)・手動か・確信度は項目ごとに持つ
export type WorkSource = "filename" | "container" | "duplicate" | "alias" | "merge" | "manual" | "previous_title" | "public_db";

export interface WorkAlias {
  id: number;
  alias: string;
  source: WorkSource;
  manual: boolean;
  confidence: number | null;
}

export interface WorkSummary {
  id: number;
  title: string;
  title_source: WorkSource;
  title_manual: boolean;
  author: string | null;
  author_source: WorkSource | null;
  author_manual: boolean;
  aliases: WorkAlias[];
  volume_count: number;
  offline_count: number;
  finished_count: number;
  duplicate_slots: number;
  max_volume: number | null;
  missing_volumes: number[];
  missing_episodes: number[];
  pending_proposals: number;
  /** 統合された作品(根拠は詳細の merges) */
  merged: boolean;
  /** 読書状態。全巻読了 = finished、1 巻も開いていない = unread、それ以外 = reading(#56) */
  reading_state: ReadingState;
}

export type ReadingState = "unread" | "reading" | "finished";

/** 作品一覧の絞り込み(#56)。kind / genre / tag は公開 DB の項目(#50)があるときだけ候補が出る */
export type WorkFilterName = "author" | "kind" | "genre" | "tag";

export interface WorkQuery {
  /** 全文検索の語(空白区切りで AND。作品名・別名・著者・巻の名前・その他の項目) */
  q?: string;
  author?: string;
  kind?: string;
  genre?: string;
  tag?: string;
  status?: ReadingState;
}

export interface WorkList {
  /** 検索語・絞り込みに合う作品 */
  works: WorkSummary[];
  /** 絞る前の作品数 */
  total: number;
  /** 絞り込みの候補(絞る前の作品の値) */
  facets: Record<WorkFilterName, string[]>;
  /** 作品に割り当てられていない巻の数 */
  unassigned: number;
}

/** 検索語・絞り込みをクエリ文字列にする(空の値は付けない) */
export function workQueryString(query: WorkQuery = {}): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (typeof v === "string" && v.trim()) params.set(k, v.trim());
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export interface WorkVolume {
  id: number;
  title: string;
  parent_title: string | null;
  page_count: number | null;
  offline: boolean;
  progress: Progress | null;
  volume_number: VolumeNumber | null;
  duplicate_count: number;
  assignment: { source: WorkSource; manual: boolean; confidence: number | null; evidence: { level?: string; name?: string } | null };
}

export interface WorkSlot {
  kind: "volume" | "episode";
  start: number;
  end: number;
  label: string;
  /** 同じ番号の巻が 2 つ以上(重複としてまとめて表示) */
  duplicate: boolean;
  volumes: WorkVolume[];
}

export interface WorkBrief {
  id: number;
  title: string;
  author: string | null;
  aliases: string[];
  volume_count: number;
  volumes: { id: number; title: string; volume_number: VolumeNumber | null }[];
}

export interface DuplicateItem {
  matched_pages: number;
  compared_pages: number;
  ratio: number;
  rule: string;
  volume_a: { id: number; title: string };
  volume_b: { id: number; title: string };
}

export interface MergeEvidence {
  duplicate?: DuplicateItem[];
  alias?: { norm: string; a: { name: string; source: string; is_title: boolean }; b: { name: string; source: string; is_title: boolean } };
}

export interface WorkProposal {
  id: number;
  rule: "duplicate" | "alias" | "manual";
  confidence: number;
  status: "open";
  evidence: MergeEvidence;
  a: WorkBrief;
  b: WorkBrief;
}

export interface WorkMerge {
  id: number;
  rule: "duplicate" | "alias" | "manual";
  confidence: number;
  status: "auto" | "approved";
  evidence: MergeEvidence;
  titles: string[];
}

export interface WorkDetail extends WorkSummary {
  title_confidence: number | null;
  slots: WorkSlot[];
  unnumbered: WorkVolume[];
  numbers: { volume: { max: number | null; missing: number[]; limited: boolean }; episode: { max: number | null; missing: number[]; limited: boolean } };
  proposals: WorkProposal[];
  merges: WorkMerge[];
}

// 整理処理(CBZ 化)。プレビュー → 実行 → 元に戻す(spinebox/organize/)
export type OrganizeState =
  | "blocked" // 問題があり実行できない計画(保存されない。op_id は null)
  | "planned"
  | "running"
  | "done"
  | "failed"
  | "undoing"
  | "undone"
  | "undo_failed"
  | "rollback_failed";

export interface OrganizeVolume {
  volume_id: number;
  title: string;
  page_count: number | null;
  output_rel_path: string | null;
  new_volume_id?: number | null;
}

export interface OrganizeOp {
  op_id: string | null;
  state: OrganizeState;
  error?: string | null;
  created_at?: string;
  executed_at?: string | null;
  undone_at?: string | null;
  source_id: number;
  source_rel_path: string;
  source_kind: "folder" | "archive";
  volumes: OrganizeVolume[];
  warnings: string[];
  problems: string[];
  /** 中の巻をまだ調べていない内側アーカイブの巻 id(先に api.analyze が要る) */
  needs_analysis?: number[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** バックエンドが返した `detail`(利用者向けの文言)。無ければ undefined */
    readonly detail?: string,
  ) {
    super(message);
  }
}

async function errorDetail(res: Response): Promise<string | undefined> {
  try {
    const d = (await res.json())?.detail;
    return typeof d === "string" && d ? d : undefined;
  } catch {
    return undefined; // 本文が JSON でない(プロキシの 502 など)
  }
}

const vid = (id: VolumeId) => encodeURIComponent(String(id));

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    throw new ApiError(`${init?.method ?? "GET"} ${path} が ${res.status} で失敗しました`, res.status, await errorDetail(res));
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  listVolumes: () => request<VolumeSummary[]>("/api/volumes"),
  getVolume: (id: VolumeId) => request<VolumeDetail>(`/api/volumes/${vid(id)}`),
  scan: () => request<ScanResult>("/api/scan", { method: "POST" }),
  analyze: (id: VolumeId) => request<AnalyzeResult>(`/api/volumes/${vid(id)}/analyze`, { method: "POST" }),
  saveProgress: (id: VolumeId, p: Progress, keepalive = false) =>
    request<unknown>(`/api/volumes/${vid(id)}/progress`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: p.page, finished: p.finished }),
      // ページを閉じる直前の送信でも届くようにする
      keepalive,
    }),
  listDuplicates: (status: "open" | "dismissed" = "open") => request<DuplicateList>(`/api/duplicates?status=${status}`),
  /** 見た目の指紋を作って候補を出す(裏で動く。進み具合は listDuplicates の scan)。蔵書は変えない */
  scanDuplicates: () => request<DuplicateScanState>("/api/duplicates/scan", { method: "POST" }),
  dismissDuplicate: (id: number) => request<unknown>(`/api/duplicates/${id}/dismiss`, { method: "POST" }),
  restoreDuplicate: (id: number) => request<unknown>(`/api/duplicates/${id}/restore`, { method: "POST" }),
  pageUrl: (id: VolumeId, index: number) => `/api/volumes/${vid(id)}/pages/${index}`,
  planOrganize: (volumeId: VolumeId) =>
    request<OrganizeOp>("/api/organize/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume_id: Number(volumeId) }),
    }),
  executeOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/execute`, { method: "POST" }),
  undoOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/undo`, { method: "POST" }),
  refreshWorks: () => request<unknown>("/api/works/refresh", { method: "POST" }),
  listWorks: (query: WorkQuery = {}) => request<WorkList>(`/api/works${workQueryString(query)}`),
  getWork: (id: number | string) => request<WorkDetail>(`/api/works/${vid(id)}`),
  editWork: (id: number | string, body: { title?: string; author?: string }) =>
    request<WorkDetail>(`/api/works/${vid(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  addAlias: (id: number | string, alias: string) =>
    request<WorkDetail>(`/api/works/${vid(id)}/aliases`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alias }) }),
  removeAlias: (id: number | string, aliasId: number) => request<WorkDetail>(`/api/works/${vid(id)}/aliases/${aliasId}`, { method: "DELETE" }),
  mergeWorks: (id: number | string, otherId: number) =>
    request<WorkDetail>(`/api/works/${vid(id)}/merge`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ other_id: otherId }) }),
  /** 巻を別の作品へ付け替える。workId が null なら新しい作品に分ける */
  assignVolumes: (volumeIds: number[], workId: number | null, title?: string) =>
    request<{ work_id: number }>("/api/works/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume_ids: volumeIds, work_id: workId, ...(title ? { title } : {}) }),
    }),
  listProposals: (status: "open" | "dismissed" | "applied" = "open") => request<WorkProposal[]>(`/api/works/proposals?status=${status}`),
  decideProposal: (id: number, action: "approve" | "dismiss" | "restore") =>
    request<{ id: number; status: string }>(`/api/works/proposals/${id}/${action}`, { method: "POST" }),
  organizeHistory: () => request<OrganizeOp[]>("/api/organize"),
};
