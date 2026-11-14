// アプリの骨組み(#85): サイドバー + 本文。リーダーはこの外で全画面に出す(App.tsx)。
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import type { Route } from "../route";
import { Icon } from "./icons";
import { NAV_ITEMS, Sidebar, activeNav } from "./Sidebar";
import { useTheme } from "./theme";

const COLLAPSE_KEY = "spinebox.sidebar";

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function saveCollapsed(v: boolean): void {
  try {
    if (v) localStorage.setItem(COLLAPSE_KEY, "collapsed");
    else localStorage.removeItem(COLLAPSE_KEY);
  } catch {
    // 保存できなくても今の画面では効かせる
  }
}

const SIDEBAR_ID = "app-sidebar";

export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { effective, toggle } = useTheme();
  const active = activeNav(route);
  const title = NAV_ITEMS.find((n) => n.key === active)?.label ?? "SpineBox";
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);

  // 画面が変わったら引き出しを閉じる(戻る操作・本文のリンクでも)
  useEffect(() => setDrawerOpen(false), [route]);

  // 引き出しを開いたら中へフォーカスを移し、Esc で閉じたらメニューのボタンへ戻す
  useEffect(() => {
    if (!drawerOpen) return;
    document.getElementById(SIDEBAR_ID)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setDrawerOpen(false);
      menuButton.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  // 横長に広がったら引き出しの状態を捨てる(本文を無効にしたまま残さない)
  useEffect(() => {
    const mq = window.matchMedia?.("(min-width: 768px)");
    if (!mq) return;
    const onChange = () => mq.matches && setDrawerOpen(false);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  // 本文へ移動する(ハッシュを書き換えるとルーティングが動くので、href は飾りにして直接フォーカスする)
  const skipToMain = (e: ReactMouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    main.current?.focus();
  };

  // 画面が変わったらタブのタイトルも変える
  useEffect(() => {
    document.title = title === "SpineBox" ? "SpineBox" : `${title} | SpineBox`;
  }, [title]);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      saveCollapsed(!v);
      return !v;
    });
  };

  return (
    <div className={`app-shell${collapsed ? " collapsed" : ""}${drawerOpen ? " drawer-open" : ""}`}>
      <a className="skip-link" href="#app-main" onClick={skipToMain}>本文へ移動</a>
      <header className="topbar">
        <button type="button" className="topbar-menu" ref={menuButton} onClick={() => setDrawerOpen((v) => !v)}
          aria-label={drawerOpen ? "メニューを閉じる" : "メニューを開く"} aria-expanded={drawerOpen} aria-controls={SIDEBAR_ID}>
          <Icon name="menu" />
        </button>
        <span className="topbar-title">{title}</span>
      </header>
      <Sidebar id={SIDEBAR_ID} route={route} collapsed={collapsed} onToggleCollapsed={toggleCollapsed} theme={effective}
        onToggleTheme={toggle} onNavigate={() => setDrawerOpen(false)} />
      {drawerOpen && <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}
      <main className="app-main" id="app-main" tabIndex={-1} ref={main} {...(drawerOpen ? ({ inert: "" } as object) : {})}>
        {children}
      </main>
    </div>
  );
}
