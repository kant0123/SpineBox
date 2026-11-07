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
 * 横長(見開き)の表紙の表示方針(#75 の続き)。
 * - ar > 2.0: 見開きが明らか → 全体を縮小表示(contain)して欠けを避ける
 * - 1.2 < ar <= 2.0: 横長 → 右寄せ(日本語は右綴じなので、見開きの右ページ=表紙側が残る)
 * - それ以外: 通常の cover(中央クロップ)
 *
 * 漫画・小説とも右綴じなので、寄せ位置は右で統一する(agy レビュー #194 WARNING 1)。
 */
export function landscapeClass(ar: number): string {
  if (ar > 2.0) return "landscape-contain";
  if (ar > 1.2) return "landscape-right";
  return "";
}

/**
 * 作品カードの表紙(#75)。表紙の巻の 1 ページ目の縮小を出し、無い・まだ作っていない(404)・読めないときは
 * `placeholder` なら同じ大きさの枠を残す(作品カード。#132)。付けなければ何も出さない(文字だけのカードのまま)。
 * 表紙を作るのは管理のデータ管理タブ(#83)のジョブ。
 */
export function WorkCover({ volumeId, placeholder = false }: { volumeId: number; placeholder?: boolean }) {
  const [state, setState] = useState<"loading" | "ok" | "none">("loading");
  const [extraClass, setExtraClass] = useState("");
  useEffect(() => {
    setState("loading");
    setExtraClass("");
  }, [volumeId]);
  if (state === "none") return placeholder ? <WorkCoverPlaceholder /> : null;
  return (
    <img
      className={`work-cover${state === "ok" ? " loaded" : ""}${extraClass ? " " + extraClass : ""}`}
      src={api.thumbnailUrl(volumeId)}
      alt=""
      loading="lazy"
      decoding="async"
      onLoad={(e) => {
        const img = e.currentTarget;
        const w = img.naturalWidth;
        const h = img.naturalHeight;
        if (w > 0 && h > 0) {
          const cls = landscapeClass(w / h);
          if (cls) setExtraClass(cls);
        }
        setState("ok");
      }}
      onError={() => setState("none")}
    />
  );
}