// ライト/ダークのテーマ(#85)。既定は OS の設定に従い(system)、選んだものは端末ごとに localStorage へ記憶する。
// 色は styles.css の CSS 変数で、<html data-theme="light|dark"> が付いていればそちら、無ければ OS の設定で決まる。
import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "spinebox.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";
/** 同じタブの中で、テーマを変えたことを他の useTheme に知らせる */
const THEME_EVENT = "spinebox-theme";

export function loadTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // 保存できなくても今の画面には反映する
  }
}

function systemTheme(): Theme {
  try {
    return window.matchMedia?.(DARK_QUERY).matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/** <html> に選んだテーマを付ける(system なら外して OS の設定に任せる) */
export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement): void {
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  // ブラウザの部品・キャンバスにも伝える(<meta name="color-scheme">。system は "light dark")
  const meta = root.ownerDocument.querySelector<HTMLMetaElement>('meta[name="color-scheme"]');
  if (meta) meta.content = choice === "system" ? "light dark" : choice;
}

/** テーマの選択と、いま実際に効いているテーマ(OS の設定の変化にも追従する) */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(loadTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);

  useEffect(() => {
    applyTheme(choice);
  }, [choice]);

  useEffect(() => {
    let mq: MediaQueryList | undefined;
    try {
      mq = window.matchMedia?.(DARK_QUERY);
    } catch {
      mq = undefined;
    }
    if (!mq) return;
    const onChange = () => setSystem(mq!.matches ? "dark" : "light");
    mq.addEventListener?.("change", onChange);
    return () => mq!.removeEventListener?.("change", onChange);
  }, []);

  // 別のタブ・管理の好みタブで変えたものにも追従する
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY || e.key === null) setChoice(loadTheme());
    };
    const onLocal = () => setChoice(loadTheme());
    window.addEventListener("storage", onStorage);
    window.addEventListener(THEME_EVENT, onLocal);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(THEME_EVENT, onLocal);
    };
  }, []);

  const choose = useCallback((next: ThemeChoice) => {
    saveTheme(next);
    setChoice(next);
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);

  const effective: Theme = choice === "system" ? system : choice;
  /** サイドバーの切り替え: 今見えているものの逆へ。逆が OS の設定と同じなら保存を外して OS に従う(OS を固定してしまわない) */
  const toggle = useCallback(() => {
    const target: Theme = effective === "dark" ? "light" : "dark";
    choose(target === system ? "system" : target);
  }, [choose, effective, system]);
  return { choice, effective, choose, toggle };
}
