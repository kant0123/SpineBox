// 画面の切り替えはハッシュ(#/read/<id>)で行う。バックエンドは dist を / で配信するだけでよく、
// /read/... のような深いパスを index.html へ振り向ける設定を要しないため。

export type Route =
  | { name: "library" }
  | { name: "read"; id: string }
  | { name: "organize-history" }
  | { name: "organize-plan"; id: string }
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
  const w = h.match(/^\/works\/(\d+)\/?$/);
  if (w) return { name: "work", id: w[1] };
  const m = h.match(/^\/(read|organize)\/([^/?#]+)\/?$/);
  if (m) {
    try {
      const id = decodeURIComponent(m[2]);
      return m[1] === "read" ? { name: "read", id } : { name: "organize-plan", id };
    } catch {
      // 壊れた % エスケープは一覧へ戻す
    }
  }
  return { name: "library" };
}

export const readHref = (id: string | number) => `#/read/${encodeURIComponent(String(id))}`;
export const organizeHref = (volumeId: string | number) => `#/organize/${encodeURIComponent(String(volumeId))}`;
export const organizeHistoryHref = "#/organize";
export const libraryHref = "#/";
export const duplicatesHref = "#/duplicates";
export const worksHref = "#/works";
export const workProposalsHref = "#/works/proposals";
export const workHref = (id: string | number) => `#/works/${encodeURIComponent(String(id))}`;

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
