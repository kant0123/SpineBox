// 端末ごとの表示設定。localStorage はプライベートモード等で使えないことがあるので、失敗しても既定値で動く。

// 漫画リーダー(manga-viewer)は横長で見開き・縦長で単ページを自動で選ぶので、見開きの設定は持たない。
// 以前の版が保存した spreadMode が残っていても読み飛ばす。
export interface ReaderSettings {
  /** 1 ページ目(表紙)を単独で表示する。巻を開くときに効く */
  coverAlone: boolean;
}

const KEY = "spinebox.reader";
export const DEFAULT_SETTINGS: ReaderSettings = { coverAlone: true };

export function loadSettings(): ReaderSettings {
  try {
    const raw = localStorage.getItem(KEY);
    const v = raw ? JSON.parse(raw) : {};
    return {
      coverAlone: typeof v.coverAlone === "boolean" ? v.coverAlone : DEFAULT_SETTINGS.coverAlone,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: ReaderSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    // 保存できなくても表示は続ける
  }
}

// 小説(縦書きリーダー)の表示設定。漫画の設定とは別の鍵に置く
export interface NovelSettings {
  fontSize: number;
}

const NOVEL_KEY = "spinebox.novel";

export function loadNovelSettings(sizes: readonly number[], fallback: number): NovelSettings {
  try {
    const raw = localStorage.getItem(NOVEL_KEY);
    const v = raw ? JSON.parse(raw) : {};
    return { fontSize: sizes.includes(v.fontSize) ? v.fontSize : fallback };
  } catch {
    return { fontSize: fallback };
  }
}

export function saveNovelSettings(s: NovelSettings): void {
  try {
    localStorage.setItem(NOVEL_KEY, JSON.stringify(s));
  } catch {
    // 保存できなくても表示は続ける
  }
}
