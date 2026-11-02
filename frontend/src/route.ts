// 画面の切り替えはハッシュ(#/read/<id>)で行う。バックエンドは dist を / で配信するだけでよく、
// /read/... のような深いパスを index.html へ振り向ける設定を要しないため。

export type Route =
  | { name: "library" }
  | { name: "read"; id: string }
  | { name: "organize-history" }
  | { name: "organize-plan"; id: string; naming: "title" | "template" }
  | { name: "work-organize"; id: string }
  | { name: "duplicates" }
  | { name: "works" }
  | { name: "work"; id: string }
  | { name: "work-proposals" }
  | { name: "inbox" }
  | { name: "follows" }
  | { name: "follow-manage" }
  | { name: "bookmarks" };

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");
  if (/^\/organize\/?$/.test(h)) return { name: "organize-history" };
  if (/^\/duplicates\/?$/.test(h)) return { name: "duplicates" };
  if (/^\/inbox\/?$/.test(h)) return { name: "inbox" };
  if (/^\/follows\/?$/.test(h)) return { name: "follows" };
  if (/^\/follows\/manage\/?$/.test(h)) return { name: "follow-manage" };
  if (/^\/bookmarks\/?$/.test(h)) return { name: "bookmarks" };
  if (/^\/works\/?$/.test(h)) return { name: "works" };
  if (/^\/works\/proposals\/?$/.test(h)) return { name: "work-proposals" };
  const w = h.match(/^\/works\/(\d+)(\/organize)?\/?$/);
  if (w) return w[2] ? { name: "work-organize", id: w[1] } : { name: "work", id: w[1] };
  const m = h.match(/^\/(read|organize)\/([^/?#]+)(\/template)?\/?$/);
  if (m && !(m[1] === "read" && m[3])) {
    try {
      const id = decodeURIComponent(m[2]);
      return m[1] === "read" ? { name: "read", id } : { name: "organize-plan", id, naming: m[3] ? "template" : "title" };
    } catch {
      // 壊れた % エスケープは一覧へ戻す
    }
  }
  return { name: "library" };
}

export const readHref = (id: string | number) => `#/read/${encodeURIComponent(String(id))}`;
export const organizeHref = (volumeId: string | number) => `#/organize/${encodeURIComponent(String(volumeId))}`;
/** テンプレートの名前で CBZ にする計画 */
export const organizeTemplateHref = (volumeId: string | number) => `${organizeHref(volumeId)}/template`;
export const organizeHistoryHref = "#/organize";
export const libraryHref = "#/";
export const duplicatesHref = "#/duplicates";
/** 受け入れ(受け入れフォルダ → 同期 → 整理案 → 承認で書庫へ) */
export const inboxHref = "#/inbox";
export const worksHref = "#/works";
/** フォロー: 続刊・欠けのある作品(#87。#85 のサイドバーに載せる入口) */
export const followsHref = "#/follows";
/** フォロー管理: フォロー中の作品・解除・続刊を調べる(#85 の管理のタブに載せる入口) */
export const followManageHref = "#/follows/manage";
/** ブックマーク(#86)。サイドバー(#85)の「ブックマーク」の行き先 */
export const bookmarksHref = "#/bookmarks";
export const workProposalsHref = "#/works/proposals";
export const workHref = (id: string | number) => `#/works/${encodeURIComponent(String(id))}`;
/** 作品の全巻をテンプレートどおりに整理(改名・移動)するプレビュー */
export const workOrganizeHref = (id: string | number) => `${workHref(id)}/organize`;

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
