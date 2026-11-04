import { useEffect, useState } from "react";
import { api } from "../api";

/** 表紙の無い作品カードの枠。表紙付きカードと高さを揃え、タイトルの位置をそろえる(#132)。 */
export function WorkCoverPlaceholder() {
  return (
    <span className="work-cover work-cover-placeholder" aria-hidden="true">
      <span className="work-cover-placeholder-text">表紙なし</span>
    </span>
  );
}

/**
 * 作品カードの表紙(#75)。表紙の巻の 1 ページ目の縮小を出し、無い・まだ作っていない(404)・読めないときは
 * `placeholder` なら同じ大きさの枠を残す(作品カード。#132)。付けなければ何も出さない(文字だけのカードのまま)。
 * 表紙を作るのは管理のデータ管理タブ(#83)のジョブ。
 */
export function WorkCover({ volumeId, placeholder = false }: { volumeId: number; placeholder?: boolean }) {
  const [state, setState] = useState<"loading" | "ok" | "none">("loading");
  useEffect(() => setState("loading"), [volumeId]);
  if (state === "none") return placeholder ? <WorkCoverPlaceholder /> : null;
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
