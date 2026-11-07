// 常に表示するサイドバー(#85。Peach の画面構成)。上に蔵書の切り替えと開閉、ナビ、下にライト/ダークと設定。
// 横長では常に表示し、開閉ボタンでアイコンだけの細い帯にできる。スマホ幅(CSS の 768px 未満)ではハンバーガーで開く引き出し。
import {
  adminHref, bookmarksHref, facetListHref, followsHref, homeHref, mangaHref, novelsHref,
  type Route,
} from "../route";
import { Icon, type IconName } from "./icons";
import { useLibraryScope } from "./libraryScope";
import type { Theme } from "./theme";

export type NavKey = "home" | "follow" | "manga" | "novel" | "author" | "tag" | "publisher" | "bookmark" | "admin";

interface NavItem {
  key: NavKey;
  label: string;
  href: string;
  icon: IconName;
}

export const NAV_ITEMS: NavItem[] = [
  { key: "home", label: "ホーム", href: homeHref, icon: "home" },
  { key: "follow", label: "フォロー", href: followsHref, icon: "follow" },
  { key: "manga", label: "漫画", href: mangaHref, icon: "manga" },
  { key: "novel", label: "小説", href: novelsHref, icon: "novel" },
  { key: "author", label: "著者", href: facetListHref("author"), icon: "author" },
  { key: "tag", label: "タグ", href: facetListHref("tag"), icon: "tag" },
  { key: "publisher", label: "出版社", href: facetListHref("publisher"), icon: "publisher" },
  { key: "bookmark", label: "ブックマーク", href: bookmarksHref, icon: "bookmark" },
  { key: "admin", label: "管理", href: adminHref(), icon: "admin" },
];

/** いまの画面がどのナビの下にあるか(既存の画面も、近いナビの下に収める) */
export function activeNav(route: Route): NavKey | null {
  switch (route.name) {
    case "works":
      if (route.scope.by === "all") return "home";
      if (route.scope.by === "kind") return route.scope.kind === "manga" ? "manga" : "novel";
      return route.scope.by;
    case "facets":
      return route.field;
    case "work":
    case "work-titles":
    case "work-proposals":
    case "work-organize":
      return "home";
    case "follows":
      return "follow";
    case "bookmarks":
      return "bookmark";
    case "admin":
    case "organize-files":
    case "duplicates":
    case "inbox":
    case "organize-history":
    case "organize-plan":
      return "admin";
    default:
      return null;
  }
}

interface Props {
  route: Route;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  theme: Theme;
  onToggleTheme: () => void;
  /** スマホ幅の引き出しを閉じる(ナビを選んだとき) */
  onNavigate: () => void;
  id: string;
}

export function Sidebar({ route, collapsed, onToggleCollapsed, theme, onToggleTheme, onNavigate, id }: Props) {
  const { root, libraries, setRoot } = useLibraryScope();
  const active = activeNav(route);
  const current = libraries?.find((l) => String(l.id) === root);
  return (
    <aside className="sidebar" id={id} aria-label="サイドバー">
      <div className="sidebar-top">
        <label className="library-switch" title={current ? current.path : "すべての蔵書"}>
          <span className="sidebar-icon"><Icon name="library" /></span>
          <span className="sr-only">蔵書</span>
          <select className="sidebar-label" aria-label="蔵書" value={root ?? ""} onChange={(e) => setRoot(e.target.value || null)}>
            <option value="">すべての蔵書</option>
            {(libraries ?? []).map((l) => (
              <option key={l.id} value={String(l.id)}>
                {l.name}{l.online ? "" : "(オフライン)"}({l.volume_count} 巻)
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="sidebar-collapse" onClick={onToggleCollapsed}
          aria-label={collapsed ? "サイドバーを広げる" : "サイドバーを細くする"} aria-expanded={!collapsed} aria-controls={id}>
          <Icon name={collapsed ? "expand" : "collapse"} />
        </button>
      </div>
      <nav className="sidebar-nav" aria-label="メイン">
        <ul>
          {NAV_ITEMS.map((item) => (
            <li key={item.key}>
              <a href={item.href} aria-current={active === item.key ? "page" : undefined} title={item.label} onClick={onNavigate}>
                <span className="sidebar-icon"><Icon name={item.icon} /></span>
                <span className="sidebar-label">{item.label}</span>
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="sidebar-bottom">
        <button type="button" className="sidebar-item" onClick={onToggleTheme}
          aria-label={theme === "dark" ? "ライトにする" : "ダークにする"} title={theme === "dark" ? "ライトにする" : "ダークにする"}>
          <span className="sidebar-icon"><Icon name={theme === "dark" ? "sun" : "moon"} /></span>
          <span className="sidebar-label">{theme === "dark" ? "ライト" : "ダーク"}</span>
        </button>
        <a className="sidebar-item" href={adminHref("preferences")} title="設定" onClick={onNavigate}
          aria-current={route.name === "admin" && route.tab === "preferences" ? "page" : undefined}>
          <span className="sidebar-icon"><Icon name="settings" /></span>
          <span className="sidebar-label">設定</span>
        </a>
      </div>
    </aside>
  );
}
