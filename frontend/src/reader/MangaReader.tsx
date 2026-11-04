import { useEffect, useRef, useState } from "react";
import MangaViewer, { type HeaderButton } from "@tokagemushi/manga-viewer";
import { api, type VolumeDetail } from "../api";
import { libraryHref } from "../route";
import { loadSettings } from "../settings";
import { useTheme } from "../shell/theme";
import { nextVolumeStep } from "./nextVolume";
import { createProgressSaver } from "./progressSaver";

/** しおり(ページの目印)の API。ブックマーク(巻・作品の目印 #86)とは別の機能 */
export const PAGE_MARKS_API = "/api/page-marks";
/** 再開位置の受け渡しに使う、manga-viewer の localStorage の鍵(巻ごと) */
export const progressKey = (id: string) => `spinebox.progress.${id}`;

const VOLUME_BOOKMARK_CLASS = "spinebox-volume-bookmark";
const NEXT_VOLUME_CLASS = "spinebox-next-volume";

const svg = (path: string) =>
  `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true" focusable="false"><path d="${path}"/></svg>`;
const ICON_STAR_OFF = svg(
  "M22 9.24l-7.19-.62L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27 18.18 21l-1.63-7.03L22 9.24zM12 15.4l-3.76 2.27 1-4.28-3.32-2.88 4.38-.38L12 6.1l1.71 4.04 4.38.38-3.32 2.88 1 4.28L12 15.4z",
);
const ICON_STAR_ON = svg("M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z");
const ICON_NEXT_VOLUME = svg("M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z");

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
    // 最終ページの先へ進む代わりのボタン(#113)。次巻がオフラインなら押したときに理由を出す
    const step = nextVolumeStep(detail);
    if (step) {
      headerButtons.push({
        icon: ICON_NEXT_VOLUME,
        label: "次の巻へ",
        className: NEXT_VOLUME_CLASS,
        onClick: () => {
          if ("href" in step) window.location.hash = step.href;
          else setNotice(step.notice);
        },
      });
    }
    headerButtons.push("fullscreen", "help"); // 共有・リンクコピーは LAN 内のアプリなので出さない

    const viewer = new MangaViewer({
      container: host,
      pages: detail.pages.map((p) => api.pageUrl(id, p.index)),
      direction: "rtl",
      firstPageSingle: loadSettings().coverAlone,
      title: detail.title,
      theme: themeRef.current,
      storageKey: key,
      bookmarks: true,
      bookmarkId: String(id),
      bookmarkApi: PAGE_MARKS_API,
      messages: MESSAGES,
      headerButtons,
      onBack: () => {
        window.location.hash = libraryHref;
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
    ready = true;
    paintVolumeBookmark(host.shadowRoot, bookmarked);

    // 画面を離れる・アプリが裏に回るときは待たずに送る
    const flush = () => saver.flush();
    const onVisibility = () => document.visibilityState === "hidden" && flush();
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      flush();
      viewer.destroy();
    };
  }, [id, detail]);

  return (
    <div className="reader manga-reader">
      <div ref={hostRef} className="manga-host" />
      {notice && (
        <div className="reader-notice" role="status">
          {notice}
        </div>
      )}
    </div>
  );
}
