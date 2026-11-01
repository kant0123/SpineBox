import type { MergeEvidence, WorkSource, WorkSummary } from "../api";

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
