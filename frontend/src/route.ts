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
  | { name: "work-proposals" };

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");
  if (/^\/organize\/?$/.test(h)) return { name: "organize-history" };
  if (/^\/duplicates\/?$/.test(h)) return { name: "duplicates" };
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
export const worksHref = "#/works";
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
