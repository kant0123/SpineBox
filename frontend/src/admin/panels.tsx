// 管理のデータ管理・フォロー管理・タスクセンターのタブ(#85)。
// データ管理とタスクセンターは #83 のジョブ(`DataJobs`)、整理は #84、フォロー管理は #87 の画面を載せている。
import { DataJobs } from "../data/DataJobs";
import { FollowManage } from "../follows/Follows";
import { duplicatesHref, inboxHref, organizeHistoryHref, workProposalsHref } from "../route";

/** データ管理: ジョブ(スキャン・補完・表紙など。#83)・整理(#84)と、ほかの画面への入口 */
export function DataPanel({ focus }: { focus?: "organize" }) {
  return (
    <div className="admin-section">
      <DataJobs view="data" focus={focus} />
      <section className="admin-links-block" aria-labelledby="admin-links-title">
        <h2 id="admin-links-title">ほかの画面</h2>
        <ul className="admin-links">
          <li><a href={duplicatesHref}>重複の候補</a></li>
          <li><a href={workProposalsHref}>統合の提案</a></li>
          <li><a href={inboxHref}>受け入れ</a>(受け入れフォルダ → 整理案 → 承認で書庫へ)</li>
          <li><a href={organizeHistoryHref}>整理の履歴</a>(元に戻す)</li>
        </ul>
      </section>
    </div>
  );
}

/** フォロー管理(#87) */
export function FollowsPanel() {
  return <FollowManage embedded />;
}

/** タスクセンター: ジョブの実行中・待ち・履歴(#83) */
export function TasksPanel() {
  return <DataJobs view="tasks" />;
}
