// 管理のデータ管理・フォロー管理・タスクセンターのタブ(#85)。
// フォロー管理は #87、データ管理の整理は #84 の画面を載せた。ジョブ(スキャン・補完など)と
// タスクセンターは #83 の画面を差し込む(組み込み口)。それまではジョブを起こす入口だけを置く。
import { FollowManage } from "../follows/Follows";
import { OrganizeFilesSection } from "../organize/OrganizeFiles";
import { duplicatesHref, inboxHref, organizeHistoryHref, volumesHref, workProposalsHref } from "../route";

/** データ管理: スキャン・補完などのジョブ(#83)と、ファイル名・フォルダーの整理(#84) */
export function DataPanel() {
  return (
    <div className="admin-section">
      <p className="muted">スキャン・情報の補完・表紙の作成をまとめて呼び出す画面は準備中です(#83)。今は次の画面から行えます。</p>
      <ul className="admin-links">
        <li><a href={volumesHref}>巻の一覧</a>(スキャン・中の巻を調べる・CBZ に整理)</li>
        <li><a href={duplicatesHref}>重複の候補</a></li>
        <li><a href={workProposalsHref}>統合の提案</a></li>
        <li><a href={inboxHref}>受け入れ</a>(受け入れフォルダ → 整理案 → 承認で書庫へ)</li>
        <li><a href={organizeHistoryHref}>整理の履歴</a>(元に戻す)</li>
      </ul>
      <h2>ファイル名・フォルダーの整理</h2>
      <OrganizeFilesSection />
    </div>
  );
}

/** フォロー管理(#87) */
export function FollowsPanel() {
  return <FollowManage embedded />;
}

/** タスクセンター: ジョブの実行中・待ち・履歴(#83) */
export function TasksPanel() {
  return <p className="muted admin-section">ジョブの実行中・待ち・履歴の一覧は準備中です(#83)。</p>;
}
