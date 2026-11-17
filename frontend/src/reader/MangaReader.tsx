import { useEffect, useRef, useState } from "react";
import MangaViewer, { type HeaderButton, type MangaPage } from "@tokagemushi/manga-viewer";
import { api, type VolumeDetail } from "../api";
import { readerBackHref } from "../route";
import { loadSettings } from "../settings";
import { useTheme } from "../shell/theme";
import { nextVolumeStep, prevVolumeStep, type NextVolumeStep } from "./nextVolume";
import { createProgressSaver } from "./progressSaver";
import { useReaderTitle } from "./useReaderTitle";
import { blankPages, canWindowImages, enlargeSlider, setupAdaptiveSpreads, watchVolumeEdges, windowPageImages, type AdaptiveSpreadController } from "./viewerHooks";

/** しおり(ページの目印)の API。ブックマーク(巻・作品の目印 #86)とは別の機能 */
export const PAGE_MARKS_API = "/api/page-marks";
/** 再開位置の受け渡しに使う、manga-viewer の localStorage の鍵(巻ごと) */
export const progressKey = (id: string) => `spinebox.progress.${id}`;

const VOLUME_BOOKMARK_CLASS = "spinebox-volume-bookmark";
const NEXT_VOLUME_CLASS = "spinebox-next-volume";
const SPLIT_TOGGLE_CLASS = "spinebox-split-toggle";

const svg = (path: string) =>
  `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" focusable="false"><path d="${path}"/></svg>`;
const ICON_STAR_OFF = svg(
  "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z",
);
const ICON_STAR_ON = svg("M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z");
const ICON_NEXT_VOLUME = svg("M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z");
const ICON_SPLIT_ON = svg("M3 5v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2zm8 0v14H5V5h6zm2 14V5h6v14h-6z");
const ICON_SPLIT_OFF = svg("M3 5v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2zm16 14H5V5h14v14z");

/** しおりの文言は「ブックマーク」と紛らわしいので、SpineBox では「しおり」と呼ぶ */
const MESSAGES = { bookmarkAdd: " 現在のページにしおりを挟む" };

/** 巻のブックマークのボタンを今の状態に合わせる。ボタンは構築時に作られるので、あとから DOM を直接直す */
function paintVolumeBookmark(root: ShadowRoot | null | undefined, on: boolean) {
  const btn = root?.querySelector<HTMLElement>(`.${VOLUME_BOOKMARK_CLASS}`);
  if (!btn) return;
  const label = on ? "ブックマークを外す" : "ブックマークに追加";
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.setAttribute("aria-pressed", String(on));
  btn.innerHTML = on ? ICON_STAR_ON : ICON_STAR_OFF;
}

/** 見開き画像の分割表示ボタンを今の状態に合わせる */
function paintSplitToggle(root: ShadowRoot | null | undefined, enabled: boolean, visible: boolean) {
  const btn = root?.querySelector<HTMLButtonElement>(`.${SPLIT_TOGGLE_CLASS}`);
  if (!btn) return;
  btn.hidden = !visible;
  btn.setAttribute("aria-pressed", String(enabled));
  btn.title = enabled ? "見開き画像を分割表示中（全体表示に切り替え）" : "見開き画像を全体表示中（分割表示に切り替え）";
  btn.innerHTML = enabled ? ICON_SPLIT_ON : ICON_SPLIT_OFF;
}

/**
 * 漫画(画像)のリーダー。表示・ページ送り・見開き・ズーム・しおりは manga-viewer(MIT)に任せ、
 * ここでは読書位置のサーバー保存・再開・巻のブックマーク・次巻への移動をつなぐ。
 */
export function MangaReader({ id, detail }: { id: string; detail: VolumeDetail }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { effective } = useTheme();
  // 読んでいる途中でテーマが変わっても作り直さない(作り直すとページが戻る)
  const themeRef = useRef(effective);
  const displayTitle = useReaderTitle(detail);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 2500);
    return () => clearTimeout(t);
  }, [notice]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    // 再開位置はサーバーの値を正とする。manga-viewer は localStorage の値で「続きから読みますか?」を出す
    const key = progressKey(id);
    const resumePage = detail.progress?.page ?? 0;
    try {
      if (resumePage > 0) {
        localStorage.setItem(key, JSON.stringify({ slotIndex: 0, pageIndex: resumePage, timestamp: Date.now() }));
      } else {
        localStorage.removeItem(key);
      }
    } catch {
      // 使えなくても読める(再開の確認が出ないだけ)
    }

    const saver = createProgressSaver((p) => api.saveProgress(id, p, true));
    saver.markSaved(detail.progress);
    // 一度読み終えた巻は、前のページへ戻っても既読のままにする
    let finished = detail.progress?.finished ?? false;
    let page = resumePage;
    // 構築中(init)の onPageChange は 1 ページ目を告げるだけなので、サーバーの位置を上書きさせない
    let ready = false;
    const resumeDialogOpen = () => !!host.shadowRoot?.querySelector(".mv-resume-dialog");
    const track = () => {
      if (!ready || resumeDialogOpen()) return; // 続きから読むか選ぶ前の位置は保存しない
      saver.update({ page, finished });
    };

    let bookmarked = detail.bookmarked ?? false;
    let busy = false;
    const toggleBookmark = async () => {
      if (busy) return;
      busy = true;
      const next = !bookmarked;
      bookmarked = next;
      paintVolumeBookmark(host.shadowRoot, next);
      try {
        const r = await api.setBookmark("volume", id, next);
        bookmarked = r.bookmarked;
      } catch {
        bookmarked = !next;
        setNotice("ブックマークを変更できませんでした");
      } finally {
        busy = false;
        paintVolumeBookmark(host.shadowRoot, bookmarked);
      }
    };

    const headerButtons: Array<string | HeaderButton> = [
      "back",
      "bookmark",
      {
        icon: bookmarked ? ICON_STAR_ON : ICON_STAR_OFF,
        label: bookmarked ? "ブックマークを外す" : "ブックマークに追加",
        className: VOLUME_BOOKMARK_CLASS,
        onClick: () => void toggleBookmark(),
      },
    ];
    // 次の巻・前の巻へ(#113, #324, #338)。次の巻はヘッダーのボタンと最後の画面からさらに進む操作の両方、前の巻は先頭画面からさらに戻る操作。オフラインなら理由を出す
    const nextStep = nextVolumeStep(detail);
    const prevStep = prevVolumeStep(detail);
    const goVolume = (step: NextVolumeStep) => {
      if (!step) return;
      if ("href" in step) window.location.hash = step.href;
      else setNotice(step.notice);
    };
    let adaptive: AdaptiveSpreadController | null = null;
    if (nextStep) {
      headerButtons.push({
        icon: ICON_NEXT_VOLUME,
        label: "次の巻へ",
        className: NEXT_VOLUME_CLASS,
        onClick: () => goVolume(nextStep),
      });
    }
    headerButtons.push({
      icon: ICON_SPLIT_ON,
      label: "見開き画像を分割して表示",
      ariaLabel: "見開き画像を分割して表示",
      className: SPLIT_TOGGLE_CLASS,
      onClick: () => {
        if (!adaptive?.active) return;
        const next = adaptive.toggleSplit();
        paintSplitToggle(host.shadowRoot, next, adaptive.canToggleSplit());
        setNotice(next ? "見開き画像を分割して表示します" : "見開き画像を全体表示します");
      },
    });
    headerButtons.push("fullscreen", "help"); // 共有・リンクコピーは LAN 内のアプリなので出さない

    const urls = detail.pages.map((p) => api.pageUrl(id, p.index));
    const build = (pages: MangaPage[]) => new MangaViewer({
      container: host,
      pages,
      direction: "rtl",
      firstPageSingle: loadSettings().coverAlone,
      title: displayTitle,
      theme: themeRef.current,
      storageKey: key,
      bookmarks: true,
      bookmarkId: String(id),
      bookmarkApi: PAGE_MARKS_API,
      messages: MESSAGES,
      headerButtons,
      onBack: () => {
        window.location.hash = readerBackHref(detail.work_id);
      },
      onPageChange: (current) => {
        page = Math.max(0, current - 1);
        track();
      },
      onComplete: () => {
        if (!ready || resumeDialogOpen()) return;
        finished = true;
        track();
      },
    });
    // ページ画像は今のスロットの近くにだけ持たせる(長い巻でタブレットのメモリが尽き、ページごと読み直されるのを防ぐ。#343)。
    // ライブラリには URL なしのページを渡し、近くのスロットにだけ windowPageImages が URL を付ける。
    // ライブラリの内部の形が違うときは、従来どおり URL つきのページを渡す(同じ host には作り直せないので、作る前に決める)
    const windowed = canWindowImages(MangaViewer);
    const viewer = build(windowed ? blankPages(urls.length) : urls);
    if (windowed && !windowPageImages(viewer, urls)) setNotice("ページの画像を表示できませんでした");
    ready = true;
    paintVolumeBookmark(host.shadowRoot, bookmarked);
    enlargeSlider(host.shadowRoot);
    adaptive = setupAdaptiveSpreads(viewer, {
      onStateChange: () => {
        if (adaptive) paintSplitToggle(host.shadowRoot, adaptive.isSplitEnabled(), adaptive.canToggleSplit());
      },
    });
    paintSplitToggle(host.shadowRoot, adaptive.isSplitEnabled(), adaptive.canToggleSplit());
    // 続きから読むか選ぶ前の操作では移らない
    watchVolumeEdges(viewer, {
      onNext: () => !resumeDialogOpen() && goVolume(nextStep),
      onPrev: () => !resumeDialogOpen() && goVolume(prevStep),
    });

    // 画面を離れる・アプリが裏に回るときは待たずに送る
    const flush = () => saver.flush();
    const onVisibility = () => document.visibilityState === "hidden" && flush();
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      adaptive?.destroy();
      flush();
      viewer.destroy();
    };
  }, [id, detail, displayTitle]);

  return (
    <main className="reader manga-reader">
      <h1 className="sr-only">{displayTitle}</h1>
      <div ref={hostRef} className="manga-host" />
      {notice && (
        <div className="reader-notice" role="status">
          {notice}
        </div>
      )}
    </main>
  );
}
