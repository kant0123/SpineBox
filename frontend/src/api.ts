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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const vid = (id: VolumeId) => encodeURIComponent(String(id));

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new ApiError(`${init?.method ?? "GET"} ${path} が ${res.status} で失敗しました`, res.status);
  const text = await res.text();
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
};
