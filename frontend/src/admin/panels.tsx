// 管理のタブの中身(#85)。スキャンと補完・タスクセンターは #83 のジョブ(`DataJobs`)、整理は #84、フォロー管理は #87 の画面を載せる。
// #207 で整理を独立したタブにし、「ほかの画面」の節を廃止した(重複の候補は確認へ、整理の履歴は整理タブへ)。
import { DataJobs } from "../data/DataJobs";
import { FollowManage } from "../follows/Follows";
import { OrganizeHistory } from "../organize/Organize";
import { OrganizeFilesSection } from "../organize/OrganizeFiles";
import { inboxHref } from "../route";

/** スキャンと補完: スキャン・補完・表紙などの処理を依頼する(#83) */
export function DataPanel() {
  return <DataJobs view="data" />;
}

/** 整理: 範囲指定の整理(#84)と、整理の履歴(元に戻す) */
export function OrganizePanel() {
  return (
    <div className="admin-section">
      <section className="job-section">
        <h2>ファイル名・フォルダーの整理</h2>
        <OrganizeFilesSection />
      </section>
      <section className="job-section organize-history-section">
        <h2>整理の履歴</h2>
        <OrganizeHistory embedded />
      </section>
      {/* 受け入れは利用が少なく、今後の扱いを決めていない(#207)。入口だけ残す */}
      <p className="muted">
        受け入れフォルダから書庫へ入れるときは <a href={inboxHref}>受け入れ</a>。
      </p>
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
