// アプリの骨組み(#85): サイドバー + 本文。リーダーはこの外で全画面に出す(App.tsx)。
import { useEffect, useState, type ReactNode } from "react";
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

  // 画面が変わったら引き出しを閉じる(戻る操作・本文のリンクでも)
  useEffect(() => setDrawerOpen(false), [route]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawerOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      saveCollapsed(!v);
      return !v;
    });
  };

  const active = activeNav(route);
  const title = NAV_ITEMS.find((n) => n.key === active)?.label ?? "SpineBox";

  return (
    <div className={`app-shell${collapsed ? " collapsed" : ""}${drawerOpen ? " drawer-open" : ""}`}>
      <header className="topbar">
        <button type="button" className="topbar-menu" onClick={() => setDrawerOpen((v) => !v)}
          aria-label={drawerOpen ? "メニューを閉じる" : "メニューを開く"} aria-expanded={drawerOpen} aria-controls={SIDEBAR_ID}>
          <Icon name="menu" />
        </button>
        <span className="topbar-title">{title}</span>
      </header>
      <Sidebar id={SIDEBAR_ID} route={route} collapsed={collapsed} onToggleCollapsed={toggleCollapsed} theme={effective}
        onToggleTheme={toggle} onNavigate={() => setDrawerOpen(false)} />
      {drawerOpen && <div className="drawer-backdrop" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}
      <main className="app-main">{children}</main>
    </div>
  );
}
