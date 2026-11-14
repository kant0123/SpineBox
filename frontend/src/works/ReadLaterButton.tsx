import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "../shell/icons";

/**
 * 後で読むの付け外し(#173)。作品カードの表紙の右下に重ねて置く。アイコンのみの 32px 円で、
 * 見た目は小さくするが ::after で 44px のヒット領域を持たせる(DADS button の sm/xs と同じ方式)。
 * 押した時点で表示を切り替え、失敗したら元に戻して理由を出す(BookmarkButton と同じ振る舞い)。
 */
export function ReadLaterButton({
  id,
  initial,
  label,
  onChange,
}: {
  id: number;
  initial: boolean;
  /** 読み上げ用の対象の名前 */
  label: string;
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
      const r = await api.setReadLater(id, next);
      setOn(r.read_later);
      onChange?.(r.read_later);
    } catch {
      setOn(!next);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="read-later-toggle">
      <button
        type="button"
        className={`read-later-button${on ? " on" : ""}`}
        aria-pressed={on}
        aria-label={on ? `${label} の後で読むを外す` : `${label} を後で読む`}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          void toggle();
        }}
      >
        <Icon name="clock" />
      </button>
      {failed && (
        <span className="read-later-error" role="alert">
          変更できませんでした
        </span>
      )}
    </span>
  );
}
