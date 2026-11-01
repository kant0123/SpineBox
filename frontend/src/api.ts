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

export interface ScanResult {
  volumes: number;
  offline_roots: string[];
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
  summary: { fingerprinted: number; skipped: number; errors: number; candidates: number } | null;
  error: string | null;
}

export interface DuplicateList {
  scan: DuplicateScanState;
  candidates: DuplicateCandidate[];
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
};
