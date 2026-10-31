import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api, type VolumeDetail } from "../api";
import { libraryHref } from "../route";
import { loadSettings, saveSettings, type ReaderSettings } from "../settings";
import {
  buildSpreads,
  isLastSpread,
  keyAction,
  preloadPages,
  shouldSpread,
  spreadIndexOf,
  stepPage,
  swipeAction,
  tapAction,
  type Action,
} from "./layout";
import { createProgressSaver } from "./progressSaver";

function useViewport() {
  const [size, setSize] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  useEffect(() => {
    const onResize = () => setSize({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return size;
}

export function Reader({ id }: { id: string }) {
  const [detail, setDetail] = useState<VolumeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [menu, setMenu] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [settings, setSettings] = useState<ReaderSettings>(loadSettings);
  const [broken, setBroken] = useState<Set<number>>(() => new Set());
  const { w, h } = useViewport();

  // 一度読み終えた巻は、前のページへ戻っても既読のままにする
  const finishedRef = useRef(false);
  const saverRef = useRef(createProgressSaver((p) => api.saveProgress(id, p, true)));
  const preloadRef = useRef<HTMLImageElement[]>([]);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getVolume(id)
      .then((d) => {
        if (!alive) return;
        finishedRef.current = d.progress?.finished ?? false;
        saverRef.current.markSaved(d.progress);
        setPage(d.progress?.page ?? 0);
        setDetail(d);
      })
      .catch(() => alive && setError("この巻を開けませんでした。蔵書フォルダがオフラインの可能性があります。"));
    return () => {
      alive = false;
    };
  }, [id]);

  const pageCount = detail?.pages.length ?? 0;
  const spreads = useMemo(
    () =>
      buildSpreads(pageCount, {
        spread: settings.spreadMode === "auto" && shouldSpread(w, h),
        coverAlone: settings.coverAlone,
      }),
    [pageCount, settings, w, h],
  );
  const current = spreads[spreadIndexOf(spreads, page)] ?? [];

  // 読書位置の保存(デバウンス)
  useEffect(() => {
    if (!detail || spreads.length === 0) return;
    const finished = finishedRef.current || isLastSpread(spreads, page);
    finishedRef.current = finished;
    saverRef.current.update({ page: Math.min(Math.max(0, page), pageCount - 1), finished });
  }, [detail, page, spreads, pageCount]);

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

  // 先読み。Image を参照し続けないと読み込みが打ち切られることがあるので ref に持つ
  useEffect(() => {
    if (!detail) return;
    preloadRef.current = preloadPages(spreads, page).map((i) => {
      const img = new Image();
      img.src = api.pageUrl(id, i);
      return img;
    });
  }, [detail, spreads, page, id]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 1500);
    return () => clearTimeout(t);
  }, [notice]);

  const act = useCallback(
    (a: Action) => {
      if (a === "menu") {
        setMenu((m) => !m);
        return;
      }
      const next = stepPage(spreads, page, a === "next" ? 1 : -1);
      if (next === null) {
        setNotice(a === "next" ? "最後のページです" : "最初のページです");
        return;
      }
      setMenu(false);
      setPage(next);
    },
    [spreads, page],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return; // スライダー操作の矢印キーは奪わない
      const a = keyAction(e.key);
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
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) return; // 縦の払いや迷った指は無視
    const rect = e.currentTarget.getBoundingClientRect();
    act(tapAction(e.clientX - rect.left, rect.width));
  };

  const updateSettings = (patch: Partial<ReaderSettings>) => {
    const s = { ...settings, ...patch };
    setSettings(s);
    saveSettings(s);
  };

  if (error) {
    return (
      <div className="reader-message">
        <p role="alert">{error}</p>
        <a href={libraryHref}>一覧へ戻る</a>
      </div>
    );
  }
  if (!detail) return <div className="reader-message">読み込み中…</div>;
  if (pageCount === 0) {
    return (
      <div className="reader-message">
        <p>この巻にはページがありません。</p>
        <a href={libraryHref}>一覧へ戻る</a>
      </div>
    );
  }

  const pageLabel = current.map((i) => i + 1).join("-");

  return (
    <div className="reader">
      <div
        className={`stage${current.length === 2 ? " is-spread" : ""}`}
        data-testid="stage"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={() => (pointerRef.current = null)}
      >
        {current.map((i) =>
          broken.has(i) ? (
            <div key={i} className="page page-broken">
              {i + 1} ページ目を読み込めませんでした
            </div>
          ) : (
            <img
              key={i}
              className="page"
              src={api.pageUrl(id, i)}
              alt={`${i + 1} ページ`}
              draggable={false}
              onError={() => setBroken((b) => new Set(b).add(i))}
            />
          ),
        )}
      </div>

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
            <span className="reader-page" data-testid="page-label">
              {pageLabel} / {pageCount}
            </span>
          </div>
          <div className="reader-bar bottom">
            <input
              type="range"
              dir="rtl"
              aria-label="ページ"
              min={0}
              max={pageCount - 1}
              value={current[0] ?? 0}
              onChange={(e) => setPage(Number(e.target.value))}
            />
            <div className="reader-options">
              <label>
                <input
                  type="checkbox"
                  checked={settings.spreadMode === "auto"}
                  onChange={(e) => updateSettings({ spreadMode: e.target.checked ? "auto" : "single" })}
                />
                横長画面で見開き
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={settings.coverAlone}
                  onChange={(e) => updateSettings({ coverAlone: e.target.checked })}
                />
                表紙を単独で表示
              </label>
              <button type="button" onClick={() => setPage(0)}>
                最初へ
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
