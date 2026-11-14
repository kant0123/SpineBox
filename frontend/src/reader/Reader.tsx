import { useEffect, useState } from "react";
import { api, isNovel, type VolumeDetail } from "../api";
import { NovelReader } from "../novel/NovelReader";
import { libraryHref } from "../route";
import { volumeErrorMessage } from "./loadError";
import { MangaReader } from "./MangaReader";

/** 初めて開く入れ子のアーカイブは、内側を取り出すまで数秒かかる。その間に出す補足までの待ち時間 */
export const SLOW_HINT_MS = 1500;

export function LoadingMessage({ hint }: { hint: boolean }) {
  return (
    <main className="reader">
      <div className="reader-message">
        <span className="spinner" aria-hidden="true" />
        <p>読み込み中…</p>
        {hint && <p className="reader-hint">初めて開く巻は、アーカイブの展開に数秒かかることがあります。</p>}
        <a href={libraryHref}>一覧へ戻る</a>
      </div>
    </main>
  );
}

export function ErrorMessage({ text }: { text: string }) {
  return (
    <main className="reader">
      <div className="reader-message">
        <p role="alert">{text}</p>
        <a href={libraryHref}>一覧へ戻る</a>
      </div>
    </main>
  );
}

export function Reader({ id }: { id: string }) {
  const [detail, setDetail] = useState<VolumeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getVolume(id)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError(volumeErrorMessage(e)));
    const t = setTimeout(() => alive && setSlow(true), SLOW_HINT_MS);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [id]);

  if (error) return <ErrorMessage text={error} />;
  if (!detail) return <LoadingMessage hint={slow} />;
  // 小説は NovelReader が表示・位置の保存をする
  if (isNovel(detail.format)) return <NovelReader id={id} detail={detail} />;
  if (detail.pages.length === 0) return <ErrorMessage text="この巻にはページがありません。" />;
  return <MangaReader id={id} detail={detail} />;
}
