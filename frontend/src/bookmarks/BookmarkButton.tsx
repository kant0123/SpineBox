import { useEffect, useState } from "react";
import { api, type VolumeId } from "../api";

/**
 * ブックマークの付け外し(#86)。押した時点で表示を切り替え、失敗したら元に戻して理由を出す。
 * dark はリーダーのメニュー(暗い帯)の上に置くとき。
 */
export function BookmarkButton({
  target,
  id,
  initial,
  label,
  dark = false,
  onChange,
}: {
  target: "work" | "volume";
  id: VolumeId;
  initial: boolean;
  /** 読み上げ用の対象の名前 */
  label: string;
  dark?: boolean;
  onChange?: (on: boolean) => void;
}) {
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => setOn(initial), [initial]);

  const toggle = async () => {
    const next = !on;
    setOn(next);
    setBusy(true);
    setFailed(false);
    try {
      const r = await api.setBookmark(target, id, next);
      setOn(r.bookmarked);
      onChange?.(r.bookmarked);
    } catch {
      setOn(!next);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="bookmark-toggle">
      <button
        type="button"
        className={`bookmark-button${on ? " on" : ""}${dark ? " dark" : ""}`}
        aria-pressed={on}
        aria-label={`${label} をブックマーク`}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          void toggle();
        }}
      >
        <span aria-hidden="true">{on ? "★" : "☆"}</span>
        <span className="bookmark-text">{on ? "ブックマーク済み" : "ブックマーク"}</span>
      </button>
      {failed && (
        <span className="bookmark-error" role="alert">
          変更できませんでした
        </span>
      )}
    </span>
  );
}
