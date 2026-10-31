// 画面の切り替えはハッシュ(#/read/<id>)で行う。バックエンドは dist を / で配信するだけでよく、
// /read/... のような深いパスを index.html へ振り向ける設定を要しないため。

export type Route = { name: "library" } | { name: "read"; id: string };

export function parseRoute(hash: string): Route {
  const m = hash.replace(/^#/, "").match(/^\/read\/([^/?#]+)\/?$/);
  if (m) {
    try {
      return { name: "read", id: decodeURIComponent(m[1]) };
    } catch {
      // 壊れた % エスケープは一覧へ戻す
    }
  }
  return { name: "library" };
}

export const readHref = (id: string | number) => `#/read/${encodeURIComponent(String(id))}`;
export const libraryHref = "#/";
