// 画面の切り替えはハッシュ(#/read/<id>)で行う。バックエンドは dist を / で配信するだけでよく、
// /read/... のような深いパスを index.html へ振り向ける設定を要しないため。

export type Route =
  | { name: "library" }
  | { name: "read"; id: string }
  | { name: "organize-history" }
  | { name: "organize-plan"; id: string }
  | { name: "duplicates" };

export function parseRoute(hash: string): Route {
  const h = hash.replace(/^#/, "");
  if (/^\/organize\/?$/.test(h)) return { name: "organize-history" };
  if (/^\/duplicates\/?$/.test(h)) return { name: "duplicates" };
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
