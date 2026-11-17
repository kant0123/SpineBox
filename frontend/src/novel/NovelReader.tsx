import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { api, type VolumeDetail } from "../api";
import { readerBackHref } from "../route";
import { loadNovelSettings, saveNovelSettings } from "../settings";
import { keyAction, swipeAction, tapAction, type Action } from "../reader/layout";
import { volumeErrorMessage } from "../reader/loadError";
import { nextVolumeStep, prevVolumeStep } from "../reader/nextVolume";
import { createProgressSaver } from "../reader/progressSaver";
import { ErrorMessage } from "../reader/Reader";
import { useReaderTitle } from "../reader/useReaderTitle";
import { useViewport } from "../reader/useViewport";
import {
  DEFAULT_FONT_SIZE,
  FONT_SIZES,
  LINE_HEIGHT,
  isEnd,
  overallFraction,
  pageForPosition,
  pageStarts,
  positionForPage,
  stepNovel,
  visibleWidth,
  type Interval,
} from "./pagination";
import { sanitizeFragment } from "./sanitize";

/** manga-viewer と揃えた Material Design 塗りつぶし SVG アイコン */
function ReaderIcon({ path, size = 18 }: { path: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden="true" focusable="false">
      <path d={path} />
    </svg>
  );
}

const ICON_BACK = "M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z";
const ICON_STAR_OFF =
  "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z";
const ICON_STAR_ON = "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z";
const ICON_NEXT_VOLUME = "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z";
const ICON_EXPAND = "M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z";
const ICON_COMPRESS = "M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z";
const ICON_QUESTION =
  "M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2 2-2s2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z";
const ICON_CLOSE = "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z";

/** 画面の端からの余白(px) */
const MARGIN_X = 16;
const MARGIN_Y = 32;

/** 描画した本文の文字(ルビを含む)と挿絵の矩形を、帯の右端からの距離の区間にする。測れない環境(jsdom)では空 */
function measureColumns(el: HTMLElement): Interval[] {
  const range = document.createRange();
  if (typeof range.getClientRects !== "function") return [];
  const right = el.getBoundingClientRect().right;
  const out: Interval[] = [];
  const add = (r: DOMRect) => r.width > 0 && out.push([right - r.right, right - r.left]);
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.textContent?.trim()) continue;
    range.selectNodeContents(n);
    Array.from(range.getClientRects()).forEach(add);
  }
  el.querySelectorAll("img").forEach((img) => add(img.getBoundingClientRect()));
  return out;
}

/** 漫画と同じ操作(← が次・→ が前)に、M(メニュー)・Space / PageDown(次)・PageUp(前)を足す */
function novelKeyAction(key: string): Action | null {
  const a = keyAction(key);
  if (a) return a;
  if (key === "m" || key === "M") return "menu";
  if (key === " " || key === "PageDown") return "next";
  if (key === "PageUp") return "prev";
  return null;
}

/**
 * 小説(EPUB・テキスト)の縦書きリーダー。区切りごとに本文を取り、縦書きの帯を画面幅ずつ右から左へずらして見せる。
 * 位置は「区切りの番号(progress.page)+区切りの中の割合(progress.position)」。ページ番号は文字サイズ・画面幅で変わるので保存しない。
 */
export function NovelReader({ id, detail }: { id: string; detail: VolumeDetail }) {
  const lengths = (detail.sections ?? []).map((s) => s.length);
  const count = lengths.length;
  const [section, setSection] = useState(() => Math.min(Math.max(0, detail.progress?.page ?? 0), Math.max(0, count - 1)));
  const [html, setHtml] = useState<{ section: number; html: string } | null>(null);
  const [layout, setLayout] = useState<{ section: number; starts: number[]; contentWidth: number; page: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [fontSize, setFontSize] = useState(() => loadNovelSettings(FONT_SIZES, DEFAULT_FONT_SIZE).fontSize);
  const [bookmarked, setBookmarked] = useState(detail.bookmarked ?? false);
  const [bookmarkBusy, setBookmarkBusy] = useState(false);
  const [isRealFullscreen, setIsRealFullscreen] = useState(false);
  const [isPseudoFullscreen, setIsPseudoFullscreen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const helpTitleId = useId();
  const { w, h } = useViewport();
  const displayTitle = useReaderTitle(detail);

  // 今の位置(区切りの中の割合)。ページ割りをやり直すとき(文字サイズ・画面幅・画像の読み込み)はここから page を決める
  const posRef = useRef(detail.progress?.position ?? 0);
  const finishedRef = useRef(detail.progress?.finished ?? false);
  const saverRef = useRef(createProgressSaver((p) => api.saveProgress(id, p, true)));
  const cacheRef = useRef(new Map<number, string>());
  const mainRef = useRef<HTMLElement>(null);
  const helpDialogRef = useRef<HTMLDialogElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const insertedRef = useRef<{ section: number; html: string } | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);

  const pw = Math.max(fontSize * LINE_HEIGHT, Math.floor(w - MARGIN_X * 2));
  const ph = Math.max(fontSize * 4, Math.floor(h - MARGIN_Y * 2));

  useEffect(() => {
    saverRef.current.markSaved(detail.progress);
    setBookmarked(detail.bookmarked ?? false);
  }, [detail]);

  useEffect(() => {
    const sync = () => {
      const doc = document as Document & { webkitFullscreenElement?: Element | null };
      setIsRealFullscreen(!!(doc.fullscreenElement || doc.webkitFullscreenElement));
    };
    sync();
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    return () => {
      document.removeEventListener("fullscreenchange", sync);
      document.removeEventListener("webkitfullscreenchange", sync);
    };
  }, []);

  // 区切りの本文を取る(取った区切りは覚えておき、次の区切りを先に取っておく)
  useEffect(() => {
    let alive = true;
    const cache = cacheRef.current;
    const fetchSection = (i: number) =>
      cache.has(i) ? Promise.resolve(cache.get(i)!) : api.getSection(id, i).then((r) => (cache.set(i, r.html), r.html));
    fetchSection(section)
      .then((text) => {
        if (!alive) return;
        setHtml({ section, html: text });
        if (section + 1 < count) fetchSection(section + 1).catch(() => undefined);
      })
      .catch((e) => alive && setError(volumeErrorMessage(e)));
    return () => {
      alive = false;
    };
  }, [id, section, count]);

  const measure = useCallback(() => {
    const el = textRef.current;
    const shown = insertedRef.current;
    if (!el || !shown) return;
    const contentWidth = Math.max(el.scrollWidth, el.getBoundingClientRect().width);
    const starts = pageStarts(measureColumns(el), pw, contentWidth);
    const page = pageForPosition(posRef.current, starts, contentWidth);
    setLayout((l) =>
      l && l.section === shown.section && l.page === page && l.contentWidth === contentWidth && l.starts.join() === starts.join()
        ? l
        : { section: shown.section, starts, contentWidth, page },
    );
  }, [pw]);

  // 本文を(もう一度無害化してから)入れて、ページ割りをする。文字サイズ・画面の大きさが変わったらやり直す
  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el || !html || html.section !== section) return;
    if (insertedRef.current !== html) {
      el.replaceChildren(sanitizeFragment(html.html));
      insertedRef.current = html;
    }
    measure();
  }, [html, section, fontSize, pw, ph, measure]);

  // 挿絵は読み込み後に大きさが決まるので、そのたびにページ割りをやり直す(load は泡立たないので捕捉で拾う)
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.addEventListener("load", measure, true);
    return () => el.removeEventListener("load", measure, true);
  }, [measure]);

  const ready = layout !== null && layout.section === section && html?.section === section;

  // 読書位置の保存(デバウンス)
  useEffect(() => {
    if (!ready || !layout) return;
    const finished = finishedRef.current || isEnd(section, layout.page, layout.starts.length, count);
    finishedRef.current = finished;
    saverRef.current.update({ page: section, finished, position: posRef.current });
  }, [ready, layout, section, count]);

  // 画面を離れる・アプリが裏に回るときは待たずに送る
  useEffect(() => {
    const saver = saverRef.current;
    const flush = () => saver.flush();
    const onVisibility = () => document.visibilityState === "hidden" && flush();
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      flush();
    };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 1500);
    return () => clearTimeout(t);
  }, [notice]);

  const goSection = useCallback(
    (s: number, position: number) => {
      posRef.current = position;
      if (s === section) {
        // 同じ区切りの中で動くだけ(本文は取り直さない)
        setLayout((l) => (l ? { ...l, page: pageForPosition(position, l.starts, l.contentWidth) } : l));
        return;
      }
      setLayout(null);
      setSection(s);
    },
    [section],
  );

  const act = useCallback(
    (a: Action) => {
      if (a === "menu") {
        setMenu((m) => !m);
        return;
      }
      if (!ready || !layout) return;
      const step = stepNovel(section, layout.page, layout.starts.length, count, a === "next" ? 1 : -1);
      if (step === null) {
        // 先頭・最終ページからさらに前後へ進もうとしたら、同じ作品の前の巻・次の巻へ移る(#113, #338)
        const adjacent = a === "next" ? nextVolumeStep(detail) : prevVolumeStep(detail);
        if (adjacent && "href" in adjacent) {
          window.location.hash = adjacent.href;
          return;
        }
        setNotice(adjacent && "notice" in adjacent ? adjacent.notice : a === "next" ? "最後のページです" : "最初のページです");
        return;
      }
      setMenu(false);
      if (step.kind === "page") {
        posRef.current = positionForPage(step.page, layout.starts, layout.contentWidth);
        setLayout({ ...layout, page: step.page });
      } else {
        goSection(step.section, step.position);
      }
    },
    [ready, layout, section, count, goSection, detail],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (helpDialogRef.current?.open || helpOpen) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLButtonElement) return;
      const a = novelKeyAction(e.key);
      if (!a) return;
      e.preventDefault();
      act(a);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act, helpOpen]);

  const onPointerDown = (e: ReactPointerEvent) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    pointerRef.current = { x: e.clientX, y: e.clientY };
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const start = pointerRef.current;
    pointerRef.current = null;
    if (!start) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    const swipe = swipeAction(dx, dy);
    if (swipe) return act(swipe);
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) return;
    const rect = e.currentTarget.getBoundingClientRect();
    act(tapAction(e.clientX - rect.left, rect.width));
  };

  const changeFont = (delta: 1 | -1) => {
    const i = FONT_SIZES.indexOf(fontSize as (typeof FONT_SIZES)[number]);
    const next = FONT_SIZES[Math.min(FONT_SIZES.length - 1, Math.max(0, (i < 0 ? FONT_SIZES.indexOf(DEFAULT_FONT_SIZE) : i) + delta))];
    setFontSize(next);
    saveNovelSettings({ fontSize: next });
  };

  const toggleBookmark = async () => {
    if (bookmarkBusy) return;
    setBookmarkBusy(true);
    const next = !bookmarked;
    setBookmarked(next);
    try {
      const r = await api.setBookmark("volume", id, next);
      setBookmarked(r.bookmarked);
    } catch {
      setBookmarked(!next);
      setNotice("ブックマークを変更できませんでした");
    } finally {
      setBookmarkBusy(false);
    }
  };

  const nextStep = nextVolumeStep(detail);
  const goNextVolume = () => {
    if (!nextStep) return;
    if ("href" in nextStep) window.location.hash = nextStep.href;
    else setNotice(nextStep.notice);
  };

  const isFullscreen = isRealFullscreen || isPseudoFullscreen;
  const toggleFullscreen = () => {
    const doc = document as Document & { webkitFullscreenElement?: Element | null; webkitExitFullscreen?: () => Promise<void> | void };
    if (isRealFullscreen || isPseudoFullscreen) {
      if (isRealFullscreen) {
        try {
          (doc.exitFullscreen || doc.webkitExitFullscreen)?.call(doc);
        } catch {
          // ignore
        }
      }
      setIsPseudoFullscreen(false);
      return;
    }
    const el = mainRef.current as (HTMLElement & { webkitRequestFullscreen?: () => Promise<void> }) | null;
    const req = el?.requestFullscreen || el?.webkitRequestFullscreen;
    if (req) {
      try {
        const ret = req.call(el);
        if (ret && typeof ret.catch === "function") ret.catch(() => setIsPseudoFullscreen(true));
      } catch {
        setIsPseudoFullscreen(true);
      }
    } else {
      setIsPseudoFullscreen(true);
    }
  };

  const openHelp = () => {
    const d = helpDialogRef.current;
    if (!d) return;
    if (typeof d.showModal === "function") {
      if (!d.open) d.showModal();
    } else {
      d.setAttribute("open", "");
    }
    setHelpOpen(true);
  };

  const closeHelp = () => {
    const d = helpDialogRef.current;
    if (!d) return;
    if (typeof d.close === "function" && d.open) d.close();
    else d.removeAttribute("open");
    setHelpOpen(false);
  };

  const onHelpDialogClick = (e: ReactMouseEvent<HTMLDialogElement>) => {
    if ("closedBy" in HTMLDialogElement.prototype) return;
    const d = helpDialogRef.current;
    if (!d || e.target !== d) return;
    const rect = d.getBoundingClientRect();
    const inside = rect.top <= e.clientY && e.clientY <= rect.top + rect.height && rect.left <= e.clientX && e.clientX <= rect.left + rect.width;
    if (!inside) closeHelp();
  };

  if (error) return <ErrorMessage text={error} />;
  if (count === 0) return <ErrorMessage text="この本には読める本文がありません。" />;

  const page = ready && layout ? layout.page : 0;
  const starts = ready && layout ? layout.starts : [0];
  const contentWidth = ready && layout ? layout.contentWidth : 0;
  const pages = starts.length;
  const clip = pw - visibleWidth(starts, page, pw);
  const percent = Math.round(overallFraction(lengths, section, posRef.current) * 100);
  const backLabel = detail.work_id == null ? "一覧へ戻る" : "作品へ戻る";
  const bookmarkLabel = bookmarked ? "ブックマークを外す" : "ブックマークに追加";
  const hiddenAttrs = menu ? {} : ({ inert: "" } as Record<string, string>);

  return (
    <main ref={mainRef} className={`reader novel${isPseudoFullscreen ? " is-pseudo-fullscreen" : ""}`}>
      <h1 className="sr-only">{displayTitle}</h1>
      <div
        className="novel-stage"
        data-testid="stage"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (pointerRef.current = null)}
      >
        {/* 次のページの最初の行が左端に覗かないよう、ページの残りは切り取る */}
        {/* 高さには余裕を持たせる(フォントによっては字形が行の端から少しはみ出し、ちょうどの高さだと最後の字が欠ける) */}
        <div
          className="novel-page"
          style={{ width: pw, height: ph + Math.ceil(fontSize / 2), clipPath: clip > 0 ? `inset(0 0 0 ${clip}px)` : undefined }}
        >
          <div
            ref={textRef}
            className="novel-text"
            data-testid="novel-text"
            data-section={section}
            data-page={page}
            data-pages={pages}
            data-from={positionForPage(page, starts, contentWidth)}
            data-to={page + 1 < pages ? positionForPage(page + 1, starts, contentWidth) : 1}
            style={{
              height: ph,
              fontSize,
              lineHeight: LINE_HEIGHT,
              translate: `${starts[page] ?? 0}px`,
              visibility: ready ? "visible" : "hidden",
              ["--novel-page-width" as string]: `${pw}px`,
            }}
          />
        </div>
      </div>

      {!ready && (
        <div className="page-loading">
          <span className="spinner" aria-hidden="true" />
          読み込み中…
        </div>
      )}

      {notice && (
        <div className="reader-notice" role="status">
          {notice}
        </div>
      )}

      <header className={`novel-header${menu ? "" : " is-hidden"}`} aria-hidden={!menu} {...hiddenAttrs}>
        <a href={readerBackHref(detail.work_id)} className="novel-header-btn" aria-label={backLabel}>
          <ReaderIcon path={ICON_BACK} />
        </a>
        <span className="novel-header-title">{displayTitle}</span>
        <div className="novel-header-buttons" role="toolbar" aria-label="リーダー操作">
          <button
            type="button"
            className="novel-header-btn novel-bookmark-btn"
            aria-label={bookmarkLabel}
            aria-pressed={bookmarked}
            onClick={() => void toggleBookmark()}
          >
            <ReaderIcon path={bookmarked ? ICON_STAR_ON : ICON_STAR_OFF} />
          </button>
          {nextStep && (
            <button type="button" className="novel-header-btn" aria-label="次の巻へ" onClick={goNextVolume}>
              <ReaderIcon path={ICON_NEXT_VOLUME} />
            </button>
          )}
          <button
            type="button"
            className="novel-header-btn novel-pc-only"
            aria-label={isFullscreen ? "全画面を終了" : "全画面"}
            aria-pressed={isFullscreen}
            onClick={toggleFullscreen}
          >
            <ReaderIcon path={isFullscreen ? ICON_COMPRESS : ICON_EXPAND} />
          </button>
          <button type="button" className="novel-header-btn" aria-label="ヘルプ" onClick={openHelp}>
            <ReaderIcon path={ICON_QUESTION} />
          </button>
        </div>
      </header>

      <footer className={`novel-footer${menu ? "" : " is-hidden"}`} aria-hidden={!menu} {...hiddenAttrs}>
        <div className="novel-footer-info">
          <span data-testid="page-label">
            {section + 1} / {count}
          </span>
          <span>{percent}%</span>
        </div>
        <input
          type="range"
          dir="rtl"
          className="novel-slider"
          aria-label="区切り"
          min={0}
          max={Math.max(0, count - 1)}
          value={section}
          disabled={count <= 1}
          onChange={(e) => goSection(Number(e.target.value), 0)}
        />
        <div className="novel-footer-controls">
          <div className="novel-font-controls">
            <span>文字サイズ {fontSize}px</span>
            <button
              type="button"
              className="novel-ctrl-btn"
              aria-label="小さく"
              onClick={() => changeFont(-1)}
              disabled={fontSize <= FONT_SIZES[0]}
            >
              A-
            </button>
            <button
              type="button"
              className="novel-ctrl-btn"
              aria-label="大きく"
              onClick={() => changeFont(1)}
              disabled={fontSize >= FONT_SIZES[FONT_SIZES.length - 1]}
            >
              A+
            </button>
          </div>
          <div className="novel-footer-actions">
            <button type="button" className="novel-ctrl-btn" onClick={() => goSection(0, 0)}>
              最初へ
            </button>
            {detail.encoding && <span className="novel-encoding">文字コード: {detail.encoding}</span>}
          </div>
        </div>
      </footer>

      <dialog
        ref={helpDialogRef}
        className="novel-help-dialog"
        {...({ closedby: "any" } as Record<string, string>)}
        aria-labelledby={helpTitleId}
        onClose={() => setHelpOpen(false)}
        onClick={onHelpDialogClick}
      >
        <div className="novel-help-card">
          <div className="novel-help-header">
            <h2 id={helpTitleId} className="novel-help-title">
              <ReaderIcon path={ICON_QUESTION} size={20} />
              ヘルプ
            </h2>
            <button type="button" className="novel-help-close" aria-label="閉じる" onClick={closeHelp}>
              <ReaderIcon path={ICON_CLOSE} size={16} />
            </button>
          </div>
          <div className="novel-help-content">
            <div className="novel-help-section">
              <div className="novel-help-section-title">表示設定</div>
              <div className="novel-help-item">
                <div className="novel-help-item-icon" aria-hidden="true">
                  📖
                </div>
                <div className="novel-help-item-text">
                  <div className="novel-help-item-label">縦書きページ送り</div>
                  <div className="novel-help-item-desc">右から左へ読み進めます</div>
                </div>
              </div>
            </div>
            <div className="novel-help-section">
              <div className="novel-help-section-title">操作方法</div>
              <div className="novel-help-item">
                <div className="novel-help-item-icon" aria-hidden="true">
                  👆
                </div>
                <div className="novel-help-item-text">
                  <div className="novel-help-item-label">タップ / クリック</div>
                  <div className="novel-help-item-desc">左: 次のページ / 右: 前のページ / 中央: メニュー表示</div>
                </div>
              </div>
              <div className="novel-help-item">
                <div className="novel-help-item-icon" aria-hidden="true">
                  👋
                </div>
                <div className="novel-help-item-text">
                  <div className="novel-help-item-label">スワイプ</div>
                  <div className="novel-help-item-desc">左右にスワイプしてページ送り</div>
                </div>
              </div>
              <div className="novel-help-item">
                <div className="novel-help-item-icon" aria-hidden="true">
                  ⌨
                </div>
                <div className="novel-help-item-text">
                  <div className="novel-help-item-label">キーボード</div>
                  <div className="novel-help-item-desc">← / Space / PageDown: 次へ、→ / PageUp: 前へ、Esc / M: メニュー</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </dialog>
    </main>
  );
}
