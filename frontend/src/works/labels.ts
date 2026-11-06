import type { FieldInfo, LinkFailure, MergeEvidence, ReadingState, SourceName, WorkKind, WorkLink, WorkSource, WorkSummary } from "../api";

const SOURCE_TEXT: Record<WorkSource, string> = {
  filename: "ファイル名から",
  container: "外側のアーカイブ名から",
  duplicate: "重複候補の証拠で統合",
  alias: "別名の一致で統合",
  merge: "統合された作品の名前",
  manual: "手動",
  previous_title: "直す前の作品名",
  public_db: "公開データベース",
};

export const sourceText = (s: WorkSource | null | undefined): string => (s ? SOURCE_TEXT[s] ?? s : "");

/** 確信度(0〜1)を百分率で */
export const confidenceText = (c: number | null | undefined): string => (c == null ? "" : `${Math.round(c * 100)}%`);

/** 欠け番号の表示(「3・5 巻」) */
export function missingText(w: Pick<WorkSummary, "missing_volumes" | "missing_episodes">): string {
  const parts: string[] = [];
  if (w.missing_volumes.length) parts.push(`${w.missing_volumes.join("・")} 巻`);
  if (w.missing_episodes.length) parts.push(`${w.missing_episodes.join("・")} 話`);
  return parts.join("、");
}

/** 欠けの番号を並べて見せる上限。これより多いときは件数だけにし、内訳はツールチップ(title)に回す */
export const MISSING_LIST_MAX = 5;

/** 欠けの表示。title は番号を省略したときだけ入る(全番号) */
export interface MissingLabel {
  text: string;
  title?: string;
}

/**
 * カード・作品ページに出す欠けの表示。番号が多すぎるときは「12 巻」と件数だけにし、
 * カーソルを合わせたときのツールチップ用に全番号を title へ入れる。
 */
export function missingLabel(w: Pick<WorkSummary, "missing_volumes" | "missing_episodes">): MissingLabel {
  const total = w.missing_volumes.length + w.missing_episodes.length;
  if (total === 0) return { text: "" };
  if (total <= MISSING_LIST_MAX) return { text: missingText(w) };
  const counts: string[] = [];
  if (w.missing_volumes.length) counts.push(`${w.missing_volumes.length} 巻`);
  if (w.missing_episodes.length) counts.push(`${w.missing_episodes.length} 話`);
  return { text: counts.join("、"), title: missingText(w) };
}

/** 統合の根拠を一文ずつにする */
export function evidenceLines(ev: MergeEvidence): string[] {
  const lines: string[] = [];
  for (const d of ev.duplicate ?? []) {
    const how = d.rule === "same_number" ? "巻番号の候補が同じで" : "見た目だけの判定で";
    lines.push(`「${d.volume_a.title}」と「${d.volume_b.title}」は、${how}見た目の近いページが ${d.matched_pages} / ${d.compared_pages} ページ(${Math.round(d.ratio * 100)}%)`);
  }
  if (ev.alias) {
    const { a, b } = ev.alias;
    lines.push(`名前「${a.name}」(${a.is_title ? "作品名" : "別名"})と「${b.name}」(${b.is_title ? "作品名" : "別名"})が同じ`);
  }
  return lines;
}

/** 読書状態の表示(#56) */
export const READING_STATE_TEXT: Record<ReadingState, string> = { unread: "未読", reading: "読書中", finished: "既読" };

/** 公開データベースの取得元の名前・サイト・利用している API(表示義務のあるクレジット) */
export const SOURCE_SITES: Record<SourceName, { name: string; api: string; url: string }> = {
  ndl: { name: "国立国会図書館サーチ", api: "NDL サーチ API", url: "https://ndlsearch.ndl.go.jp/" },
  anilist: { name: "AniList", api: "AniList API", url: "https://anilist.co" },
  mangaupdates: { name: "MangaUpdates", api: "MangaUpdates API", url: "https://www.mangaupdates.com" },
};

export const dbName = (s: string): string => (s in SOURCE_SITES ? SOURCE_SITES[s as SourceName].name : s);

export const KIND_TEXT: Record<WorkKind, string> = { manga: "漫画", novel: "小説" };

/** 項目の出所の注記(手動 / 公開データベース(取得元・確信度) / 名前から / 未設定) */
export function fieldNote(info: FieldInfo<unknown> | null | undefined, unsetText = "未設定"): string {
  if (!info) return unsetText;
  if (info.manual) return "手動で編集";
  if (info.source === "public_db") {
    const names = (info.sources ?? []).map(dbName).join("・");
    return `公開データベース${names ? `: ${names}` : ""}(確信度 ${confidenceText(info.confidence)})`;
  }
  return `${sourceText(info.source)}${info.confidence != null ? `(確信度 ${confidenceText(info.confidence)})` : ""}`;
}

const FAILURE_TEXT: Record<LinkFailure["kind"], string> = {
  timeout: "応答が時間内に返りませんでした",
  connect: "接続できませんでした",
  http_status: "サーバーがエラーを返しました",
  rate_limited: "短時間に問い合わせすぎて制限されています",
  invalid_response: "読み取れない応答でした",
  too_large: "応答が大きすぎて受け取れませんでした",
  disabled: "この取得元は無効になっています",
  internal: "取得中に想定外のエラーが起きました",
};

/** 取得に失敗した理由(取得元が落ちていても、すでに入った値はそのまま) */
export function failureText(f: LinkFailure): string {
  const status = f.status ? ` (HTTP ${f.status})` : "";
  const retry = f.retry_after ? `、${f.retry_after} 秒後に再試行できます` : "";
  return `${FAILURE_TEXT[f.kind] ?? f.message}${status}${retry}`;
}

/** 連携の状態を一言で */
export function linkStatusText(l: WorkLink): string {
  if (l.status === "linked") return l.manual ? "選んだ候補と連携中" : `自動で連携(確信度 ${confidenceText(l.confidence)})`;
  if (l.status === "pending") return l.hold === "kind_unresolved" ? "漫画か小説かが決まるまで保留" : "確認待ち";
  if (l.status === "none") return l.manual ? "該当なし(手動で選択)" : "該当なし";
  return "未取得";
}
