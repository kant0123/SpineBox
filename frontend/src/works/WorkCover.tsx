import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * 作品カードの表紙(#75)。表紙の巻の 1 ページ目の縮小を出し、無い・まだ作っていない(404)・読めないときは
 * 何も出さない(文字だけのカードのまま)。表紙を作るのは管理のデータ管理タブ(#83)のジョブ。
 */
export function WorkCover({ volumeId }: { volumeId: number }) {
  const [state, setState] = useState<"loading" | "ok" | "none">("loading");
  useEffect(() => setState("loading"), [volumeId]);
  if (state === "none") return null;
  return (
    <img
      className={`work-cover${state === "ok" ? " loaded" : ""}`}
      src={api.thumbnailUrl(volumeId)}
      alt=""
      loading="lazy"
      decoding="async"
      onLoad={() => setState("ok")}
      onError={() => setState("none")}
    />
  );
}
