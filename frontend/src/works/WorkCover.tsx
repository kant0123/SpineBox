import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * 作品カードの表紙(#75)。表紙の巻の 1 ページ目の縮小を出し、無い・まだ作っていない(404)・読めないときは
 * 何も出さない(文字だけのカードのまま)。rev が変わったら取り直す(「表紙を作る」の後)。
 */
export function WorkCover({ volumeId, rev }: { volumeId: number; rev: number }) {
  const [state, setState] = useState<"loading" | "ok" | "none">("loading");
  useEffect(() => setState("loading"), [volumeId, rev]);
  if (state === "none") return null;
  return (
    <img
      className={`work-cover${state === "ok" ? " loaded" : ""}`}
      src={api.thumbnailUrl(volumeId, rev)}
      alt=""
      loading="lazy"
      decoding="async"
      onLoad={() => setState("ok")}
      onError={() => setState("none")}
    />
  );
}
