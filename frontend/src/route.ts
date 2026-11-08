// 画面の切り替えはハッシュ(#/read/<id>)で行う。バックエンドは dist を / で配信するだけでよく、
// /read/... のような深いパスを index.html へ振り向ける設定を要しないため。

/** 一覧を絞る項目の一覧画面(サイドバーの著者・タグ・出版社 #85) */
export type FacetField = "author" | "tag" | "publisher";

/** 作品一覧に固定で掛ける条件(サイドバーの漫画・小説、著者・タグ・出版社の一覧から開いた先) */
export type WorksScope =
  | { by: "all" }
  | { by: "kind"; kind: "manga" | "novel" }
  | { by: FacetField; value: string };

/** 管理のタブ(#85。Peach の管理と同じ並び)。統合の提案は確認(#207)へ移した */
export const ADMIN_TABS = ["stats", "preferences", "data", "follows", "tasks"] as const;
export type AdminTab = (typeof ADMIN_TABS)[number];

/** 確認(#207)のタブ: 利用者の判断を待っているもの(公開 DB の候補・統合の提案・重複の候補)を 1 か所に集める */
export const REVIEW_TABS = ["links", "proposals", "duplicates"] as const;
export type ReviewTab = (typeof REVIEW_TABS)[number];

export type Route =
  | { name: "works"; scope: WorksScope }
  | { name: "read"; id: string }
  | { name: "organize-history" }
  | { name: "organize-plan"; id: string; naming: "title" | "template" }
  | { name: "work-organize"; id: string }
  | { name: "work"; id: string }
  /** 作品のタイトル(巻)の一覧。作品カードから開き、管理ページ(`#/works/<id>`)へはボタンで移る */
  | { name: "work-titles"; id: string }
  | { name: "inbox" }
  | { name: "organize-files" }
  | { name: "facets"; field: FacetField }
  | { name: "follows" }
  | { name: "bookmarks" }
  | { name: "admin"; tab: AdminTab }
  | { name: "review"; tab: ReviewTab };

const FACET_PATHS: Record<string, FacetField> = { authors: "author", tags: "tag", publishers: "publisher" };
const FACET_PATH_OF: Record<FacetField, string> = { author: "authors", tag: "tags", publisher: "publishers" };

const HOME: Route = { name: "works", scope: { by: "all" } };

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");
  if (/^\/?$/.test(h) || /^\/works\/?$/.test(h)) return HOME;
  if (/^\/volumes\/?$/.test(h)) return HOME;
  if (/^\/manga\/?$/.test(h)) return { name: "works", scope: { by: "kind", kind: "manga" } };
  if (/^\/novels\/?$/.test(h)) return { name: "works", scope: { by: "kind", kind: "novel" } };
  if (/^\/follows\/?$/.test(h)) return { name: "follows" };
  if (/^\/follows\/manage\/?$/.test(h)) return { name: "admin", tab: "follows" };
  if (/^\/bookmarks\/?$/.test(h)) return { name: "bookmarks" };
  // 統合の提案は管理のタブ・作品の下の画面から確認(#207)へ移した。旧 URL も同じ画面を開く
  if (/^\/admin\/proposals\/?$/.test(h) || /^\/works\/proposals\/?$/.test(h)) return { name: "review", tab: "proposals" };
  if (/^\/duplicates\/?$/.test(h)) return { name: "review", tab: "duplicates" };
  const review = h.match(/^\/review(?:\/([a-z]+))?\/?$/);
  if (review) {
    const tab = (REVIEW_TABS as readonly string[]).includes(review[1] ?? "") ? (review[1] as ReviewTab) : "links";
    return { name: "review", tab };
  }
  const admin = h.match(/^\/admin(?:\/([a-z]+))?\/?$/);
  if (admin) {
    const tab = (ADMIN_TABS as readonly string[]).includes(admin[1] ?? "") ? (admin[1] as AdminTab) : "stats";
    return { name: "admin", tab };
  }
  const facet = h.match(/^\/(authors|tags|publishers)(?:\/([^/?#]+))?\/?$/);
  if (facet) {
    const field = FACET_PATHS[facet[1]];
    if (!facet[2]) return { name: "facets", field };
    try {
      return { name: "works", scope: { by: field, value: decodeURIComponent(facet[2]) } };
    } catch {
      return { name: "facets", field }; // 壊れた % エスケープは項目の一覧へ戻す
    }
  }
  if (/^\/organize\/?$/.test(h)) return { name: "organize-history" };
  if (/^\/inbox\/?$/.test(h)) return { name: "inbox" };
  if (/^\/data\/?$/.test(h)) return { name: "admin", tab: "data" };
  if (/^\/data\/organize\/?$/.test(h)) return { name: "organize-files" };
  const w = h.match(/^\/works\/(\d+)(?:\/(organize|titles))?\/?$/);
  if (w) {
    if (w[2] === "organize") return { name: "work-organize", id: w[1] };
    if (w[2] === "titles") return { name: "work-titles", id: w[1] };
    return { name: "work", id: w[1] };
  }
  const m = h.match(/^\/(read|organize)\/([^/?#]+)(\/template)?\/?$/);
  if (m && !(m[1] === "read" && m[3])) {
    try {
      const id = decodeURIComponent(m[2]);
      return m[1] === "read" ? { name: "read", id } : { name: "organize-plan", id, naming: m[3] ? "template" : "title" };
    } catch {
      // 壊れた % エスケープはホームへ戻す
    }
  }
  return HOME;
}

export const readHref = (id: string | number) => `#/read/${encodeURIComponent(String(id))}`;
export const organizeHref = (volumeId: string | number) => `#/organize/${encodeURIComponent(String(volumeId))}`;
/** テンプレートの名前で CBZ にする計画 */
export const organizeTemplateHref = (volumeId: string | number) => `${organizeHref(volumeId)}/template`;
export const organizeHistoryHref = "#/organize";
/** ホーム(作品一覧)。リーダー・整理の画面の「一覧へ」もここへ戻る */
export const libraryHref = "#/";
export const homeHref = libraryHref;
/** 巻の一覧は廃止し、ホーム(作品一覧)へ戻す */
export const volumesHref = homeHref;
export const reviewHref = (tab: ReviewTab = "links") => (tab === "links" ? "#/review" : `#/review/${tab}`);
/** 重複の候補(確認のタブ。#207) */
export const duplicatesHref = reviewHref("duplicates");
/** 受け入れ(受け入れフォルダ → 同期 → 整理案 → 承認で書庫へ) */
export const inboxHref = "#/inbox";
/** 範囲指定の整理(ファイル名・フォルダーのテンプレート、ファイル名の整理)。データ管理ページ(#83)の「整理」節の単独版 */
export const organizeFilesHref = "#/data/organize";
export const worksHref = "#/works";
/** データ管理(スキャン・補完・表紙などのジョブと履歴。#83) */
export const dataHref = "#/admin/data";
/** タスクセンター: ジョブの実行中・待ち・履歴(管理のタブ) */
export const tasksHref = "#/admin/tasks";
/** フォロー: 続刊・欠けのある作品(#87。#85 のサイドバーに載せる入口) */
export const followsHref = "#/follows";
/** フォロー管理: フォロー中の作品・解除・続刊を調べる(管理の「フォロー管理」タブ。旧 `#/follows/manage` も同じタブを開く) */
export const followManageHref = "#/admin/follows";
/** ブックマーク(#86)。サイドバー(#85)の「ブックマーク」の行き先 */
export const bookmarksHref = "#/bookmarks";
/** 統合の提案(確認のタブ。#207。旧 `#/works/proposals`・`#/admin/proposals` も同じ画面を開く) */
export const workProposalsHref = reviewHref("proposals");
export const workHref = (id: string | number) => `#/works/${encodeURIComponent(String(id))}`;
/** 作品のタイトル(巻)の一覧。作品カードの行き先 */
export const workTitlesHref = (id: string | number) => `${workHref(id)}/titles`;
/** 作品の全巻をテンプレートどおりに整理(改名・移動)するプレビュー */
export const workOrganizeHref = (id: string | number) => `${workHref(id)}/organize`;
export const mangaHref = "#/manga";
export const novelsHref = "#/novels";
export const facetListHref = (field: FacetField) => `#/${FACET_PATH_OF[field]}`;
export const facetWorksHref = (field: FacetField, value: string) => `${facetListHref(field)}/${encodeURIComponent(value)}`;
export const adminHref = (tab: AdminTab = "stats") => (tab === "stats" ? "#/admin" : `#/admin/${tab}`);

/**
 * `/read/<id>` を直接開いた(ブックマーク・共有されたリンク)ときに、画面の正準の形 `/#/read/<id>` を返す。
 * バックエンドは未知のパスに index.html を返すので画面は開くが、ルートはハッシュで決まるため、
 * パスのままだと一覧になってしまう。ハッシュがあるときはそちらを正とし、何もしない(null)。
 */
export function canonicalUrl(pathname: string, hash: string): string | null {
  if (hash.replace(/^#/, "") !== "") return null;
  const m = pathname.match(/^\/read\/([^/]+)\/?$/);
  return m ? `/#/read/${m[1]}` : null;
}
