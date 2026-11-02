// 管理のデータ管理・フォロー管理・タスクセンターのタブ(#85 の組み込み口)。
// それぞれ #83(ジョブ)・#84(整理)・#87(フォロー)の画面をここへ差し込む。それまでは、既にある画面への入口だけを置く。
import { duplicatesHref, inboxHref, organizeHistoryHref, volumesHref, workProposalsHref } from "../route";

/** データ管理: スキャン・補完などのジョブ(#83)と整理(#84)。今は既存の画面への入口 */
export function DataPanel() {
  return (
    <div className="admin-section">
      <p className="muted">スキャン・情報の補完・表紙の作成をまとめて呼び出す画面は準備中です(#83・#84)。今は次の画面から行えます。</p>
      <ul className="admin-links">
        <li><a href={volumesHref}>巻の一覧</a>(スキャン・中の巻を調べる・CBZ に整理)</li>
        <li><a href={duplicatesHref}>重複の候補</a></li>
        <li><a href={workProposalsHref}>統合の提案</a></li>
        <li><a href={inboxHref}>受け入れ</a>(受け入れフォルダ → 整理案 → 承認で書庫へ)</li>
        <li><a href={organizeHistoryHref}>整理の履歴</a>(元に戻す)</li>
      </ul>
    </div>
  );
}

/** フォロー管理(#87) */
export function FollowsPanel() {
  return <p className="muted admin-section">フォローした作品の管理は準備中です(#87)。</p>;
}

/** タスクセンター: ジョブの実行中・待ち・履歴(#83) */
export function TasksPanel() {
  return <p className="muted admin-section">ジョブの実行中・待ち・履歴の一覧は準備中です(#83)。</p>;
}
