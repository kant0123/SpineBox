import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type WorkDetail } from "../api";
import { progressLabel, sizeLabel } from "../library/Library";
import { readHref, workHref, worksHref } from "../route";
import { KIND_TEXT } from "./labels";

/**
 * 作品のタイトル(巻)の一覧(#105)。ホームなどの作品カードから開く。
 * 作品の管理(作品名・著者・巻の割り当て・統合)は今までどおり `#/works/<id>` で、
 * このページの「作品を管理」ボタンから移る。
 */
export function WorkTitles({ id }: { id: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const w = await api.getWork(id);
      setWork(w);
      setNotFound(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError("作品を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (notFound) {
    return (
      <div className="library">
        <p>この作品は見つかりません(別の作品に統合された可能性があります)。</p>
        <a className="back-link" href={worksHref}>
          作品の一覧へ戻る
        </a>
      </div>
    );
  }
  if (!work) {
    return (
      <div className="library">
        {error ? (
          <div className="notice error" role="alert">
            {error}
          </div>
        ) : (
          <p className="muted">読み込み中…</p>
        )}
      </div>
    );
  }

  // 巻番号の枠の順(番号の読めない巻は最後)に、巻のタイトルを並べる
  const rows = [
    ...work.slots.flatMap((s) => s.volumes.map((v) => ({ label: s.label, v, duplicate: s.duplicate }))),
    ...work.unnumbered.map((v) => ({ label: "番号なし", v, duplicate: false })),
  ];
  const meta = [work.author, work.kind ? KIND_TEXT[work.kind] : "", `${work.volume_count} 巻`].filter(Boolean).join(" / ");

  return (
    <div className="library work-titles">
      <header className="library-header">
        <div className="work-titles-head">
          <h1>{work.title}</h1>
          <p className="volume-meta work-titles-meta">{meta}</p>
        </div>
        <a className="manage-button" href={workHref(work.id)}>
          作品を管理
        </a>
      </header>
      <p className="scope-back">
        <a className="back-link" href={worksHref}>
          ‹ 作品の一覧へ
        </a>
      </p>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      <section className="work-section" aria-labelledby="work-titles-heading">
        <h2 id="work-titles-heading">タイトル一覧({rows.length})</h2>
        {rows.length === 0 ? (
          <p className="muted">巻がありません。</p>
        ) : (
          <ul className="title-list">
            {rows.map(({ label, v, duplicate }) => (
              <li key={v.id} className={`title-item${v.offline ? " offline" : ""}`}>
                <span className="title-number">{label}</span>
                <span className="title-body">
                  {v.offline ? (
                    <span className="volume-title">{v.title}</span>
                  ) : (
                    <a className="volume-title" href={readHref(v.id)}>
                      {v.title}
                    </a>
                  )}
                  <span className="volume-meta">
                    {sizeLabel(v)} / {progressLabel(v)}
                    {v.offline && " / オフライン"}
                  </span>
                </span>
                {duplicate && <span className="badge dup-badge">重複</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
