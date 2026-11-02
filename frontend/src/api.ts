// バックエンド(spinebox/)との API 契約。Issue #14・#15 で共有している形に合わせる。
// ページ番号は 0 始まり(pages[].index と同じ)。

export type VolumeId = number | string;

export interface Progress {
  /** 画像の巻はページ番号、小説は区切りの番号 */
  page: number;
  finished: boolean;
  /** 小説だけ: 区切りの中の割合(0〜1)。文字サイズ・画面幅が変わっても近い位置で再開するため */
  position?: number;
}

/** 巻の形式。images = ページ画像の巻(漫画など)、epub / text = 小説(縦書きリーダーで読む) */
export type VolumeFormat = "images" | "epub" | "text";

export const isNovel = (format: VolumeFormat | undefined) => format === "epub" || format === "text";

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
  /** 省略時は images。小説の page_count は区切りの数(開くまで null) */
  format?: VolumeFormat;
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

/** 小説の区切り(EPUB の本文 1 つ / テキストの一定文字数)。length は全体の進み具合の重み */
export interface SectionInfo {
  index: number;
  length: number;
}

export interface VolumeDetail {
  id: VolumeId;
  title: string;
  format?: VolumeFormat;
  page_count: number | null;
  pages: PageInfo[];
  /** 小説だけ */
  sections?: SectionInfo[];
  /** テキストの小説で判定した文字コード */
  encoding?: string | null;
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
  /** データ管理のジョブ(#83)。待ちのときも running は true */
  job_id?: number | null;
  status?: JobStatus | null;
}

/** 表紙の作成(明示操作。POST /api/thumbnails/build)の状態 */
export interface ThumbnailBuildState {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  done: number;
  total: number;
  summary: { total: number; made: number; existing: number; skipped: number } | null;
  error: string | null;
  job_id?: number | null;
  status?: JobStatus | null;
}

// データ管理のジョブ(#83)。1 本ずつ動き、依頼は待ち行列に入る。履歴はサーバーの DB に残る
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
/** jobs = データ管理の画面、button = 各画面の既存のボタン、background = 裏の定期処理 */
export type JobOrigin = "jobs" | "button" | "background";

export interface Job {
  id: number | null;
  kind: string;
  label: string;
  origin: JobOrigin;
  params: Record<string, unknown>;
  status: JobStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  done: number | null;
  total: number | null;
  /** 今の対象(ファイル・巻・作品の名前) */
  current: string | null;
  /** 複数の処理を順に行うジョブの、今の段 */
  step: string | null;
  /** 裏の定期処理で、続けて動いた回数(1 行にまとめる) */
  runs: number;
  summary: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
  cancellable: boolean;
}

export interface JobsSnapshot {
  current: Job | null;
  queue: Job[];
  history: Job[];
}

export interface JobKind {
  kind: string;
  group: string;
  label: string;
  description: string;
  count: number | null;
  count_text: string | null;
  available: boolean;
  unavailable_reason: string | null;
  options: { name: string; label: string }[];
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

/** 種別。漫画 / 小説(手で直せる。公開データベースから入ることもある) */
export type WorkKind = "manga" | "novel";

/** 項目ごとの値と出所。manual = 利用者が手で直した(再取得・再計算で変えない) */
export interface FieldInfo<T> {
  value: T;
  source: WorkSource;
  manual: boolean;
  confidence: number | null;
  /** 公開データベースから入った値の取得元(ndl / anilist / mangaupdates) */
  sources?: string[];
  /** 巻の書誌: 取得元と、その書誌の URL */
  from?: string | null;
  url?: string | null;
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
  /** 公開データベース由来(または手で直した)作品の情報(#50) */
  kind: WorkKind | null;
  publisher: string | null;
  genres: string[];
  tags: string[];
  /** 確認待ちの公開データベースの候補がある取得元の数 */
  link_pending: number;
  /** 表紙にする巻(最小の巻番号の画像の巻。無ければ null)。画像は `api.thumbnailUrl()`(#75) */
  cover_volume_id: number | null;
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
  format?: VolumeFormat;
  assignment: { source: WorkSource; manual: boolean; confidence: number | null; evidence: { level?: string; name?: string } | null };
  /** 巻の書誌(出版社・レーベル・発売日・ISBN など)。項目ごとに出所つき */
  metadata?: Partial<Record<VolumeMetaField, FieldInfo<string | null>>>;
}

export type VolumeMetaField = "publisher" | "label" | "released" | "isbn" | "volume_label" | "subtitle";

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

export type SourceName = "ndl" | "anilist" | "mangaupdates";

/** 公開データベースの候補(利用者に見せる概要) */
export interface LinkCandidate {
  source_id: string;
  title: string;
  subtitle: string | null;
  romaji: string | null;
  authors: string[];
  kind: WorkKind | null;
  publisher: string | null;
  label?: string | null;
  released: string | null;
  total_volumes: number | null;
  genres: string[];
  url: string | null;
  score: number;
  reasons: string[];
  /** 国立国会図書館サーチ: 同じ本としてまとめた書誌の件数と、その巻番号 */
  record_count?: number;
  volumes?: number[];
}

export interface LinkFailure {
  kind: "timeout" | "connect" | "http_status" | "rate_limited" | "invalid_response" | "too_large" | "disabled" | "internal";
  message: string;
  status: number | null;
  retry_after: number | null;
  at: string;
}

/** 作品と取得元 1 つの連携。status: linked 連携済み / pending 確認待ち / none 該当なし / null まだ取得できていない */
export interface WorkLink {
  source: SourceName;
  label: string;
  status: "linked" | "pending" | "none" | null;
  manual: boolean;
  confidence: number | null;
  source_id: string | null;
  attempted_at: string | null;
  checked_at: string | null;
  failure: LinkFailure | null;
  linked: LinkCandidate | null;
  decision: string | null;
  margin: number | null;
  /** kind_unresolved = 漫画か小説かが決まるまで自動では連携しない */
  hold: string | null;
  responses: { label: string; fetched_at: string; from_cache: boolean; stale: boolean }[];
  candidates: LinkCandidate[];
}

export interface WorkEditBody {
  title?: string;
  author?: string | null;
  kind?: WorkKind | null;
  publisher?: string | null;
  synopsis?: string | null;
  genres?: string[];
  tags?: string[];
}

export type WorkFieldName = "title" | "author" | "kind" | "publisher" | "genres" | "tags" | "synopsis";

export interface MetadataFetchState {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  done: number;
  total: number;
  /** 今取得している作品の名前 */
  current: string | null;
  summary: { works: number; sources: Record<string, number> } | null;
  error: string | null;
  job_id?: number | null;
  status?: JobStatus | null;
}

export interface MetadataStatus {
  state: MetadataFetchState;
  sources: SourceName[];
  background: boolean;
}

export interface WorkDetail extends WorkSummary {
  title_confidence: number | null;
  fields: Partial<Record<WorkFieldName | "total_volumes", FieldInfo<any>>>;
  links: WorkLink[];
  slots: WorkSlot[];
  unnumbered: WorkVolume[];
  numbers: { volume: { max: number | null; missing: number[]; limited: boolean }; episode: { max: number | null; missing: number[]; limited: boolean } };
  proposals: WorkProposal[];
  merges: WorkMerge[];
}

// 整理処理(CBZ 化・テンプレートでの改名と移動)。プレビュー → 実行 → 元に戻す(spinebox/organize/)
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

/** 改名・移動の計画での各巻の扱い */
export type OrganizeItemStatus = "move" | "unchanged" | "excluded" | "conflict" | "skipped";

export interface OrganizeItem {
  volume_id: number;
  title: string;
  from_rel_path: string;
  output_rel_path: string | null;
  status: OrganizeItemStatus;
  /** 対象外・衝突・外した理由 */
  reason: string | null;
  /** 1 巻 = 1 CBZ でない巻。この巻から「CBZ に整理」(テンプレートの名前)を始められる */
  cbz_volume_id: number | null;
}

/** cbz: 1 ソース → 巻ごとの CBZ / rename: 1 巻 = 1 CBZ の巻をテンプレートの名前へ改名・移動するだけ /
 *  intake: 受け入れフォルダの本を書庫へ入れる(#66。mode が cbz なら CBZ にして、move なら中身を変えずに移す) */
export type OrganizeKind = "cbz" | "rename" | "intake";
/** 出力名: 巻の表示名(title)か命名テンプレート(template) */
export type OrganizeNaming = "title" | "template";

export interface OrganizeOp {
  op_id: string | null;
  kind?: OrganizeKind;
  naming?: OrganizeNaming;
  template?: string | null;
  work_id?: number;
  items?: OrganizeItem[];
  /** 受け入れ(kind が intake)のとき */
  mode?: "cbz" | "move" | null;
  work?: { kind: string; work_id: number | null; title: string; decided: boolean } | null;
  skipped?: { volume_id: number; title: string; reason: string | null }[];
  inbox_path?: string;
  library_path?: string;
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

// 受け入れ(受け入れフォルダ → 同期 → 整理案 → 承認で書庫へ。spinebox/organize/intake.py)
/** ready = 承認できる / choose = 作品・番号・取り込まないの確認が要る / blocked = 取り込めない(理由つき) */
export type InboxStatus = "ready" | "choose" | "blocked";
export type InboxCategory = "existing_work" | "new_work" | "duplicate" | "unreadable" | "unsupported" | "unknown_work";

export interface InboxVolume {
  volume_id: number;
  title: string;
  page_count: number | null;
  number: { kind: "volume" | "episode" | null; start: number; end: number | null; source: "name" | "manual" } | null;
  output_rel_path: string | null;
  duplicate_of: { volume_id: number; title: string; rel_path: string }[];
  skipped: boolean;
  reason: string | null;
}

export interface InboxWork {
  match: "existing" | "new" | "unknown";
  work_id: number | null;
  title: string;
  decided: boolean;
  confident: boolean;
  confidence: number;
  reason: string;
  candidates: { work_id: number; title: string }[];
}

export interface InboxItem {
  source_id: number;
  inbox: string;
  rel_path: string;
  name: string;
  kind: "folder" | "archive";
  status: InboxStatus;
  category: InboxCategory;
  /** cbz: 巻ごとに CBZ にして書庫へ / move: 中身を変えずに書庫へ移す */
  mode: "cbz" | "move" | null;
  reasons: string[];
  warnings: string[];
  issues: string[];
  work: InboxWork | null;
  volumes: InboxVolume[];
  /** 中の巻をまだ調べていない内側アーカイブの巻 id(先に api.analyze が要る) */
  needs_analysis: number[];
  expect_outputs: string[];
}

export interface InboxInfo {
  path: string;
  library: string;
  problems: string[];
  synced_at: string | null;
}

export interface InboxState {
  inboxes: InboxInfo[];
  items: InboxItem[];
  /** 同期したときだけ */
  volumes?: number;
  offline_roots?: string[];
}

/** 利用者が選んだこと。numbers は巻 id(文字列)→ 番号。expect_outputs は承認で、プレビューで見た出力先 */
export interface InboxDecision {
  source_id: number;
  work_id?: number | null;
  new_title?: string | null;
  numbers?: Record<string, number>;
  skip_volume_ids?: number[];
  expect_outputs?: string[];
}

export interface InboxApproveResult {
  source_id: number;
  ok: boolean;
  error?: string;
  warning?: string;
  op?: OrganizeOp | null;
  item: InboxItem;
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
      body: JSON.stringify(p.position === undefined ? { page: p.page, finished: p.finished } : { page: p.page, finished: p.finished, position: p.position }),
      // ページを閉じる直前の送信でも届くようにする
      keepalive,
    }),
  listDuplicates: (status: "open" | "dismissed" = "open") => request<DuplicateList>(`/api/duplicates?status=${status}`),
  /** 見た目の指紋を作って候補を出す(裏で動く。進み具合は listDuplicates の scan)。蔵書は変えない */
  scanDuplicates: () => request<DuplicateScanState>("/api/duplicates/scan", { method: "POST" }),
  dismissDuplicate: (id: number) => request<unknown>(`/api/duplicates/${id}/dismiss`, { method: "POST" }),
  restoreDuplicate: (id: number) => request<unknown>(`/api/duplicates/${id}/restore`, { method: "POST" }),
  pageUrl: (id: VolumeId, index: number) => `/api/volumes/${vid(id)}/pages/${index}`,
  /** 巻の表紙(1 ページ目の縮小)。取り出しが要る巻でまだ作っていなければ 404(#75)。rev は作り直した後の取り直し用 */
  thumbnailUrl: (id: VolumeId, rev = 0) => `/api/volumes/${vid(id)}/thumbnail${rev ? `?rev=${rev}` : ""}`,
  thumbnailStatus: () => request<ThumbnailBuildState>("/api/thumbnails"),
  /** 作品の表紙を、内側アーカイブを取り出してでも作る(裏で動く)。蔵書は変えない */
  buildThumbnails: () => request<ThumbnailBuildState>("/api/thumbnails/build", { method: "POST" }),
  /** 小説の区切り 1 つの HTML(サーバーで無害化済み。画面に入れる前にもう一度 sanitize する) */
  getSection: (id: VolumeId, index: number) => request<{ index: number; html: string }>(`/api/volumes/${vid(id)}/sections/${index}`),
  planOrganize: (volumeId: VolumeId, naming: OrganizeNaming = "title") =>
    request<OrganizeOp>("/api/organize/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ volume_id: Number(volumeId), naming }),
    }),
  /** 作品の全巻をテンプレートの名前へ改名・移動する計画(プレビュー)。excludeVolumeIds は外す巻 */
  planRename: (workId: number | string, excludeVolumeIds: number[] = []) =>
    request<OrganizeOp>("/api/organize/rename-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: Number(workId), exclude_volume_ids: excludeVolumeIds }),
    }),
  executeOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/execute`, { method: "POST" }),
  undoOrganize: (opId: string) => request<OrganizeOp>(`/api/organize/${encodeURIComponent(opId)}/undo`, { method: "POST" }),
  refreshWorks: () => request<unknown>("/api/works/refresh", { method: "POST" }),
  listWorks: (query: WorkQuery = {}) => request<WorkList>(`/api/works${workQueryString(query)}`),
  getWork: (id: number | string) => request<WorkDetail>(`/api/works/${vid(id)}`),
  editWork: (id: number | string, body: WorkEditBody) =>
    request<WorkDetail>(`/api/works/${vid(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  /** 手動の項目を自動に戻す(直す前の値、無ければ公開データベースの値) */
  resetWorkField: (id: number | string, field: WorkFieldName) =>
    request<WorkDetail>(`/api/works/${vid(id)}/fields/${field}/reset`, { method: "POST" }),
  /** この作品の公開データベースの情報を取りに行く(数秒かかる。取得元の失敗は 200 で返り、連携に記録される) */
  fetchWorkMetadata: (id: number | string) => request<WorkDetail>(`/api/works/${vid(id)}/metadata/fetch`, { method: "POST" }),
  /** 確認待ちの候補を選ぶ(sourceId が null なら「該当なし」)。選んだ結果は手動の決定になる */
  chooseLink: (id: number | string, source: SourceName, sourceId: string | null) =>
    request<WorkDetail>(`/api/works/${vid(id)}/links/${source}/choose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_id: sourceId }),
    }),
  resetLink: (id: number | string, source: SourceName) => request<WorkDetail>(`/api/works/${vid(id)}/links/${source}/reset`, { method: "POST" }),
  editVolumeMetadata: (id: VolumeId, body: Partial<Record<"publisher" | "label" | "released" | "isbn", string | null>>) =>
    request<unknown>(`/api/volumes/${vid(id)}/metadata`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  resetVolumeMetadata: (id: VolumeId, field: "publisher" | "label" | "released" | "isbn") =>
    request<unknown>(`/api/volumes/${vid(id)}/metadata/${field}/reset`, { method: "POST" }),
  metadataStatus: () => request<MetadataStatus>("/api/works/metadata"),
  /** 公開データベースの情報を、取得が必要な作品(all なら全作品)について取りに行く(裏で動く) */
  fetchAllMetadata: (all = false) => request<MetadataFetchState>(`/api/works/metadata/fetch${all ? "?all=true" : ""}`, { method: "POST" }),
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
  /** 受け入れフォルダの設定と、いま記録されている受け入れ待ちの本(ファイルは開かない) */
  inbox: () => request<InboxState>("/api/inbox"),
  /** 受け入れフォルダを走査して新しいものを検出する(読み取りだけ) */
  syncInbox: () => request<InboxState>("/api/inbox/sync", { method: "POST" }),
  /** 選んだ内容での整理案を作り直す(何も動かさない) */
  previewInbox: (items: InboxDecision[]) =>
    request<InboxState>("/api/inbox/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }),
  /** 選んだものだけを書庫へ入れる(1 件ずつ。失敗した件は受け入れフォルダに残る) */
  /** データ管理: 実行中・待ち行列・履歴 */
  listJobs: () => request<JobsSnapshot>("/api/jobs"),
  /** データ管理: 並べるジョブと対象の件数 */
  jobCatalog: () => request<{ kinds: JobKind[] }>("/api/jobs/catalog"),
  /** ジョブを待ち行列に入れる(同じジョブが待ち・実行中なら、それが返る) */
  startJob: (kind: string, params: Record<string, boolean> = {}) =>
    request<Job>("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, params }) }),
  /** 待ちなら取り下げ、実行中なら区切りのよいところで止める */
  cancelJob: (id: number) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  approveInbox: (items: InboxDecision[]) =>
    request<{ results: InboxApproveResult[] }>("/api/inbox/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    }),
};
