import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api, type VolumeDetail } from "../api";
import { libraryHref } from "../route";
import { loadNovelSettings, saveNovelSettings } from "../settings";
import { keyAction, swipeAction, tapAction, type Action } from "../reader/layout";
import { volumeErrorMessage } from "../reader/loadError";
import { nextVolumeStep } from "../reader/nextVolume";
import { createProgressSaver } from "../reader/progressSaver";
import { ErrorMessage } from "../reader/Reader";
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
import { BookmarkButton } from "../bookmarks/BookmarkButton";

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

/** 漫画と同じ操作(← が次・→ が前)に、Space / PageDown(次)・PageUp(前)を足す */
function novelKeyAction(key: string): Action | null {
  const a = keyAction(key);
  if (a) return a;
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
  const { w, h } = useViewport();

  // 今の位置(区切りの中の割合)。ページ割りをやり直すとき(文字サイズ・画面幅・画像の読み込み)はここから page を決める
  const posRef = useRef(detail.progress?.position ?? 0);
  const finishedRef = useRef(detail.progress?.finished ?? false);
  const saverRef = useRef(createProgressSaver((p) => api.saveProgress(id, p, true)));
  const cacheRef = useRef(new Map<number, string>());
  const textRef = useRef<HTMLDivElement>(null);
  const insertedRef = useRef<{ section: number; html: string } | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);

  const pw = Math.max(fontSize * LINE_HEIGHT, Math.floor(w - MARGIN_X * 2));
  const ph = Math.max(fontSize * 4, Math.floor(h - MARGIN_Y * 2));

  useEffect(() => {
    saverRef.current.markSaved(detail.progress);
  }, [detail]);

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
        // 最終ページからさらに進もうとしたら、同じ作品の次の巻へ移る(#113)
        const next = a === "next" ? nextVolumeStep(detail) : null;
        if (next && "href" in next) {
          window.location.hash = next.href;
          return;
        }
        setNotice(next && "notice" in next ? next.notice : a === "next" ? "最後のページです" : "最初のページです");
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
    [ready, layout, section, count, goSection],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLButtonElement) return;
      const a = novelKeyAction(e.key);
      if (!a) return;
      e.preventDefault();
      act(a);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [act]);

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

  if (error) return <ErrorMessage text={error} />;
  if (count === 0) return <ErrorMessage text="この本には読める本文がありません。" />;

  const page = ready && layout ? layout.page : 0;
  const starts = ready && layout ? layout.starts : [0];
  const contentWidth = ready && layout ? layout.contentWidth : 0;
  const pages = starts.length;
  const clip = pw - visibleWidth(starts, page, pw);
  const percent = Math.round(overallFraction(lengths, section, posRef.current) * 100);

  return (
    <main className="reader novel">
      <h1 className="sr-only">{detail.title}</h1>
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

      {menu && (
        <>
          <div className="reader-bar top">
            <a href={libraryHref}>‹ 一覧へ</a>
            <span className="reader-title">{detail.title}</span>
            <BookmarkButton target="volume" id={id} initial={detail.bookmarked ?? false} label={detail.title} dark />
            <span className="reader-page" data-testid="page-label">
              {section + 1} / {count}・{percent}%
            </span>
          </div>
          <div className="reader-bar bottom">
            <input
              type="range"
              dir="rtl"
              aria-label="区切り"
              min={0}
              max={count - 1}
              value={section}
              onChange={(e) => goSection(Number(e.target.value), 0)}
            />
            <div className="reader-options">
              <span>文字サイズ {fontSize}px</span>
              <button type="button" onClick={() => changeFont(-1)} disabled={fontSize <= FONT_SIZES[0]}>
                小さく
              </button>
              <button type="button" onClick={() => changeFont(1)} disabled={fontSize >= FONT_SIZES[FONT_SIZES.length - 1]}>
                大きく
              </button>
              <button type="button" onClick={() => goSection(0, 0)}>
                最初へ
              </button>
              {detail.encoding && <span className="muted-dark">文字コード: {detail.encoding}</span>}
            </div>
          </div>
        </>
      )}
    </main>
  );
}
