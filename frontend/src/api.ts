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
  pageUrl: (id: VolumeId, index: number) => `/api/volumes/${vid(id)}/pages/${index}`,
  planOrganize: (volumeId: VolumeId) =>
    request<OrganizeOp>("/api/organize/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume_id: Number(volumeId) }),
    }),
  executeOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/execute`, { method: "POST" }),
  undoOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/undo`, { method: "POST" }),
  organizeHistory: () => request<OrganizeOp[]>("/api/organize"),
};
