import { useState } from "react";
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
 * - 1.2 < ar <= 2.0: 横長 → 左寄せ(自炊本の見開きスキャン等では左側に表表紙が配置される #222)
 * - それ以外: 通常の cover(中央クロップ)
 */
export function landscapeClass(ar: number): string {
  if (ar > 2.0) return "landscape-contain";
  if (ar > 1.2) return "landscape-left";
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
  const [prevId, setPrevId] = useState(volumeId);
  // 巻が変わったときの状態のリセットは描画中に行う。useEffect で戻すと、キャッシュ済みの画像は
  // onLoad が effect より先に発火して ok になった後で loading に戻され、表紙が出ないまま残る。
  if (prevId !== volumeId) {
    setPrevId(volumeId);
    setState("loading");
    setExtraClass("");
  }
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
