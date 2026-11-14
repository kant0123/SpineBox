// 管理(#85)。Peach の管理と同じく、統計・好み・データ管理・フォロー管理・タスクセンターをタブで分ける。蔵書(#313)は SpineBox で足したタブ。
// タブは #/admin/<tab> で直接開ける(統計は #/admin)。
import { useEffect, useRef, type ComponentType } from "react";
import { adminHref, type AdminTab } from "../route";
import { DataPanel, FollowsPanel, OrganizePanel, TasksPanel } from "./panels";
import { Libraries } from "./Libraries";
import { Preferences } from "./Preferences";
import { Stats } from "./Stats";

export const ADMIN_TAB_LABELS: Record<AdminTab, string> = {
  stats: "統計",
  preferences: "好み",
  libraries: "蔵書",
  data: "スキャンと補完",
  organize: "整理",
  follows: "フォロー管理",
  tasks: "タスクセンター",
};

/**
 * タブの中身。データ管理(#83・#84)・フォロー管理(#87)・タスクセンター(#83)はそれぞれの Issue の画面を
 * panels.tsx の各パネルに差し込む(組み込み口)。
 */
const PANELS: Record<AdminTab, ComponentType> = {
  stats: Stats,
  preferences: Preferences,
  libraries: Libraries,
  data: DataPanel,
  organize: OrganizePanel,
  follows: FollowsPanel,
  tasks: TasksPanel,
};

export function Admin({ tab }: { tab: AdminTab }) {
  const Panel = PANELS[tab];
  const tabs = useRef<HTMLElement>(null);
  // 375px ではタブの列が横に流れるので、開いたタブが隠れない位置まで送る
  useEffect(() => {
    const current = tabs.current?.querySelector('[aria-current="page"]');
    current?.scrollIntoView?.({ block: "nearest", inline: "center" });
  }, [tab]);
  return (
    <div className="page admin">
      <header className="page-header">
        <h1>管理</h1>
      </header>
      <nav className="admin-tabs" aria-label="管理のタブ" ref={tabs}>
        {(Object.keys(ADMIN_TAB_LABELS) as AdminTab[]).map((t) => (
          <a key={t} href={adminHref(t)} aria-current={t === tab ? "page" : undefined}>
            {ADMIN_TAB_LABELS[t]}
          </a>
        ))}
      </nav>
      <div className="admin-panel">
        <Panel />
      </div>
    </div>
  );
}
