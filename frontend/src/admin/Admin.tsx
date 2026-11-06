// 管理(#85)。Peach の管理と同じく、統計・好み・データ管理・フォロー管理・タスクセンターをタブで分ける。
// タブは #/admin/<tab> で直接開ける(統計は #/admin)。
import { useEffect, useRef, type ComponentType } from "react";
import { adminHref, type AdminTab } from "../route";
import { DataPanel, FollowsPanel, ProposalsPanel, TasksPanel } from "./panels";
import { Preferences } from "./Preferences";
import { Stats } from "./Stats";

export const ADMIN_TAB_LABELS: Record<AdminTab, string> = {
  stats: "統計",
  preferences: "好み",
  data: "データ管理",
  proposals: "統合",
  follows: "フォロー管理",
  tasks: "タスクセンター",
};

/**
 * タブの中身。データ管理(#83・#84)・フォロー管理(#87)・タスクセンター(#83)はそれぞれの Issue の画面を
 * panels.tsx の各パネルに差し込む(組み込み口)。
 */
const PANELS: Record<AdminTab, ComponentType<{ focus?: "organize" }>> = {
  stats: Stats,
  preferences: Preferences,
  data: DataPanel,
  proposals: ProposalsPanel,
  follows: FollowsPanel,
  tasks: TasksPanel,
};

/** focus="organize" は単独の入口 #/data/organize(データ管理の「整理」節へ移す) */
export function Admin({ tab, focus }: { tab: AdminTab; focus?: "organize" }) {
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
      <section className="admin-panel" aria-label={ADMIN_TAB_LABELS[tab]}>
        <Panel focus={focus} />
      </section>
    </div>
  );
}
