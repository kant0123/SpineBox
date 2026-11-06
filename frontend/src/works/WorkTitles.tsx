import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api, ApiError, type WorkDetail, type WorkKind } from "../api";
import { readHref, workHref, worksHref } from "../route";
import { KIND_TEXT } from "./labels";
import {
  buildTitleRows, percentText, releaseDateText, STATUS_TEXT, volumeStatus, workProgress,
  type OwnedTitleRow, type TitleStatus, type TitleRow,
} from "./titleRows";
import { WorkCover } from "./WorkCover";

/**
 * 作品のタイトル(巻)の一覧(#105 / #115)。ホームなどの作品カードから開く。
 * 巻の名前は作品名と巻番号から作り(補完済みの作品名を使う)、表紙・読書状態・欠け/続刊・
 * 作品全体の進捗を出す。作品の管理(作品名・著者・巻の割り当て・統合)は今までどおり
 * `#/works/<id>` で、このページの「作品を管理」ボタンから移る。
 */
export function WorkTitles({ id }: { id: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [picking, setPicking] = useState<OwnedTitleRow | null>(null);
  // モーダルを閉じたときに、開いた行へフォーカスを戻すため(#115)
  const opener = useRef<HTMLElement | null>(null);

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

  const rows = buildTitleRows(work);
  const overall = workProgress(rows);
  const missing = rows.filter((r) => r.kind === "missing").length;
  const meta = [work.author, work.kind ? KIND_TEXT[work.kind] : "", `${work.volume_count} 巻`].filter(Boolean).join(" / ");
  const unknownNote = overall.unknown > 0 ? `(ページ数未確定 ${overall.unknown} 巻)` : "";
  const overallText = overall.total > 0
    ? `全体 ${overall.percent}%(${overall.read} / ${overall.total})・読了 ${overall.finished} / ${overall.volumes} 巻${unknownNote}`
    : `全体 —・読了 ${overall.finished} / ${overall.volumes} 巻${unknownNote}`;

  const renderOwned = (row: OwnedTitleRow) => {
    const body = (
      <>
        <span className="title-cover">
          <WorkCover volumeId={row.primary.id} kind={work.kind} />
        </span>
        <span className="title-number">{row.label}</span>
        <span className="title-body">
          <span className="volume-title">{row.title}</span>
          <span className="volume-meta">
            {row.progress}
            {row.primary.offline && " / オフライン"}
          </span>
        </span>
        {row.duplicate && <span className="badge dup-badge">重複</span>}
      </>
    );
    const className = `title-item status-${row.status}${row.primary.offline ? " offline" : ""}`;
    if (row.duplicate) {
      // 重複は 1 行にまとめ、押したらどの巻を読むか選ぶ(#115)
      return (
        <li key={row.key}>
          <button
            type="button"
            className={className}
            onClick={(e) => {
              opener.current = e.currentTarget;
              setPicking(row);
            }}
          >
            {body}
          </button>
        </li>
      );
    }
    if (row.primary.offline) {
      return (
        <li key={row.key}>
          <div className={className}>{body}</div>
        </li>
      );
    }
    return (
      <li key={row.key}>
        <a className={className} href={readHref(row.primary.id)}>
          {body}
        </a>
      </li>
    );
  };

  const renderMissing = (row: Extract<TitleRow, { kind: "missing" }>) => (
    <li key={row.key}>
      <div className={`title-item missing ${row.missing}`}>
        <span className="title-cover placeholder" aria-hidden="true" />
        <span className="title-number">{row.label}</span>
        <span className="title-body">
          <span className="volume-title">{row.title}</span>
          <span className="volume-meta">{releaseDateText(row.release.released)}</span>
        </span>
        <span className={`badge ${row.missing}-badge`}>{row.missing === "new" ? "最新巻" : "欠け"}</span>
      </div>
    </li>
  );

  return (
    <div className="library work-titles">
      <header className="library-header">
        <div className="work-titles-head">
          <h1>{work.title}</h1>
          <p className="volume-meta work-titles-meta">{meta}</p>
          <div className="work-progress">
            <div
              className="work-progress-bar"
              role="progressbar"
              aria-label="作品全体の進み具合"
              aria-valuenow={overall.percent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${overall.percent}%` }} />
            </div>
            <p className="volume-meta">{overallText}</p>
          </div>
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
        <div className="title-head">
          <h2 id="work-titles-heading">タイトル一覧({rows.length})</h2>
          <ul className="title-legend" aria-label="読書状態の凡例">
            {(Object.keys(STATUS_TEXT) as TitleStatus[]).map((s) => (
              <li key={s}>
                <span className={`legend-swatch ${s}`} aria-hidden="true" />
                {STATUS_TEXT[s]}
              </li>
            ))}
            {missing > 0 && (
              <li>
                <span className="legend-swatch missing" aria-hidden="true" />
                持っていない巻
              </li>
            )}
          </ul>
        </div>
        {rows.length === 0 ? (
          <p className="muted">巻がありません。</p>
        ) : (
          <ul className="title-list">
            {rows.map((row) => (row.kind === "missing" ? renderMissing(row) : renderOwned(row)))}
          </ul>
        )}
        {missing > 0 && (
          <p className="volume-meta title-note">
            点線の行は持っていない巻です(フォローの「続刊を調べる」の結果)。
          </p>
        )}
      </section>
      {picking && <DuplicatePicker row={picking} kind={work.kind} opener={opener.current} onClose={() => setPicking(null)} />}
    </div>
  );
}

/** 重複した巻(同じ巻番号の別スキャン)から、読む巻を選ぶモーダル(#115) */
function DuplicatePicker({ row, kind, opener, onClose }: { row: OwnedTitleRow; kind?: WorkKind | null; opener: HTMLElement | null; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // 閉じたら開いた行へフォーカスを戻す(キーボードで行き場を失わない)
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus();
    };
  }, [onClose, opener]);
  // Tab でモーダルの外へ出さない(背後の行へフォーカスを移さない)
  const trap = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !dialog.current) return;
    const items = [...dialog.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  return (
    <div className="title-modal-backdrop" onClick={onClose}>
      <div
        ref={dialog}
        className="title-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="duplicate-heading"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trap}
      >
        <h2 id="duplicate-heading">
          {row.label} の重複({row.volumes.length} 冊)
        </h2>
        <p className="volume-meta">同じ巻としてまとめています。読む巻を選んでください。</p>
        <ul className="dup-list">
          {row.volumes.map((v) => (
            <li key={v.id}>
              {v.offline ? (
                <span className="dup-choice">
                  <span className="title-cover">
                    <WorkCover volumeId={v.id} kind={kind} />
                  </span>
                  <span className="title-body">
                    <span className="volume-title">{v.title}</span>
                    <span className="volume-meta">
                      {percentText(v)} / オフライン
                    </span>
                  </span>
                </span>
              ) : (
                <a className="dup-choice" href={readHref(v.id)}>
                  <span className="title-cover">
                    <WorkCover volumeId={v.id} kind={kind} />
                  </span>
                  <span className="title-body">
                    <span className="volume-title">{v.title}</span>
                    <span className="volume-meta">
                      {percentText(v)} / {STATUS_TEXT[volumeStatus(v)]}
                    </span>
                  </span>
                </a>
              )}
            </li>
          ))}
        </ul>
        <button type="button" className="secondary" onClick={onClose} autoFocus>
          閉じる
        </button>
      </div>
    </div>
  );
}
