// 端末ごとの表示設定。localStorage はプライベートモード等で使えないことがあるので、失敗しても既定値で動く。

export type SpreadMode = "auto" | "single";

export interface ReaderSettings {
  coverAlone: boolean;
  spreadMode: SpreadMode;
}

const KEY = "spinebox.reader";
export const DEFAULT_SETTINGS: ReaderSettings = { coverAlone: true, spreadMode: "auto" };

export function loadSettings(): ReaderSettings {
  try {
    const raw = localStorage.getItem(KEY);
    const v = raw ? JSON.parse(raw) : {};
    return {
      coverAlone: typeof v.coverAlone === "boolean" ? v.coverAlone : DEFAULT_SETTINGS.coverAlone,
      spreadMode: v.spreadMode === "single" ? "single" : "auto",
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
