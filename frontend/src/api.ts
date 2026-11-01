// バックエンド(spinebox/)との API 契約。Issue #14・#15 で共有している形に合わせる。
// ページ番号は 0 始まり(pages[].index と同じ)。

export type VolumeId = number | string;

export interface Progress {
  page: number;
  finished: boolean;
}

export interface VolumeSummary {
  id: VolumeId;
  title: string;
  source_name: string;
  page_count: number | null;
  offline: boolean;
  progress: Progress | null;
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
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** サーバーが返した {"detail": ...}(あれば) */
    readonly detail?: string,
  ) {
    super(message);
  }
}

const vid = (id: VolumeId) => encodeURIComponent(String(id));

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  const text = await res.text();
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const d = JSON.parse(text)?.detail;
      if (typeof d === "string") detail = d;
    } catch {
      // 本文が JSON でない
    }
    throw new ApiError(`${init?.method ?? "GET"} ${path} が ${res.status} で失敗しました`, res.status, detail);
  }
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  listVolumes: () => request<VolumeSummary[]>("/api/volumes"),
  getVolume: (id: VolumeId) => request<VolumeDetail>(`/api/volumes/${vid(id)}`),
  scan: () => request<ScanResult>("/api/scan", { method: "POST" }),
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
