// 統合の提案のカードを読みやすく見せるための純粋な関数(#243)
import type { VolumeNumber, WorkBrief, WorkProposal } from "../api";
import { confidenceText, dbName } from "./labels";

/** 巻番号の短い表示(「3巻」「1〜3巻」「第5話」) */
export function volumeNumberText(n: VolumeNumber): string {
  const range = n.start === n.end ? `${n.start}` : `${n.start}〜${n.end}`;
  return n.kind === "episode" ? `第${range}話` : `${range}巻`;
}

/** 巻の名前から作品名とその前(著者の角括弧など)を落とし、残りを返す。残らなければ元の名前 */
export function shortVolumeTitle(title: string, workTitle: string): string {
  const at = workTitle ? title.indexOf(workTitle) : -1;
  if (at < 0) return title;
  const rest = title.slice(at + workTitle.length).trim();
  return rest || title;
}

const squash = (s: string) => s.replace(/\s+/gu, "").toLowerCase();

/**
 * カードに出ている作品名(両方)と空白・大小文字の違いしかない別名を落とす(同じ名前を 2 回見せない。
 * 相手の作品名と一致した別名は、根拠の文に 1 回だけ出る)
 */
export function distinctAliases(aliases: string[], titles: string[]): string[] {
  const known = new Set(titles.map(squash));
  return aliases.filter((a) => !known.has(squash(a)));
}

export interface VolumeChip {
  label: string;
  count: number;
}

/**
 * 巻を短い札にまとめる。巻番号の候補があるものは番号で、同じ番号は「×2」と数でまとめる。
 * 番号の無いものは作品名を落とした短い名前で並べる(並びは受け取った順 = 番号順)。
 */
export function volumeChips(w: Pick<WorkBrief, "title" | "volumes">): VolumeChip[] {
  const chips: VolumeChip[] = [];
  const byLabel = new Map<string, VolumeChip>();
  for (const v of w.volumes) {
    const label = v.volume_number ? volumeNumberText(v.volume_number) : shortVolumeTitle(v.title, w.title);
    const found = byLabel.get(label);
    if (found) found.count += 1;
    else {
      const chip = { label, count: 1 };
      byLabel.set(label, chip);
      chips.push(chip);
    }
  }
  return chips;
}

export interface TitleParts {
  head: string;
  diff: string;
  tail: string;
}

const chars = (s: string) => Array.from(s); // サロゲートペアを割らない

/**
 * 2 つの作品名の、共通の頭と尻を除いた違う部分。名前が十分近いとき(共通部分が短い方の半分以上)だけ返し、
 * まったく違う名前なら null(全体が違うので強調しても読みやすくならない)。
 */
export function titleDiff(a: string, b: string): [TitleParts, TitleParts] | null {
  if (a === b) return null;
  const ca = chars(a);
  const cb = chars(b);
  const min = Math.min(ca.length, cb.length);
  let head = 0;
  while (head < min && ca[head] === cb[head]) head += 1;
  let tail = 0;
  while (tail < min - head && ca[ca.length - 1 - tail] === cb[cb.length - 1 - tail]) tail += 1;
  if (head + tail < min / 2) return null;
  const split = (c: string[]): TitleParts => ({
    head: c.slice(0, head).join(""),
    diff: c.slice(head, c.length - tail).join(""),
    tail: c.slice(c.length - tail).join(""),
  });
  return [split(ca), split(cb)];
}

const role = (is_title: boolean) => (is_title ? "作品名" : "別名");

/**
 * カードに出す根拠の短い文。作品名・巻の名前はカードの上に出ているので、同じ長い名前を繰り返さない
 * (一致した名前は 1 回だけ、巻は作品名を落とした短い名前で書く)。
 */
export function proposalEvidence(p: Pick<WorkProposal, "evidence" | "a" | "b">): string[] {
  const lines: string[] = [];
  for (const d of p.evidence.duplicate ?? []) {
    const how = d.rule === "same_number" ? "巻番号の候補が同じで" : "見た目だけの判定で";
    const va = shortVolumeTitle(d.volume_a.title, p.a.title);
    const vb = shortVolumeTitle(d.volume_b.title, p.b.title);
    lines.push(`巻「${va}」と「${vb}」は、${how}見た目の近いページが ${d.matched_pages} / ${d.compared_pages} ページ(${Math.round(d.ratio * 100)}%)`);
  }
  const al = p.evidence.alias;
  if (al) {
    const same = al.a.name === al.b.name;
    lines.push(same
      ? `名前「${al.a.name}」が一致(${role(al.a.is_title)}と${role(al.b.is_title)})`
      : `${role(al.a.is_title)}と${role(al.b.is_title)}が同じ名前とみなせる(表記の違いだけ)`);
    if (al.shared_item) {
      lines.push(`同じ公開データベースの項目(${dbName(al.shared_item[0])} ${al.shared_item[1]})につながっている(確信度 ${confidenceText(al.alias_confidence ?? 0.9)})`);
    }
  }
  return lines;
}
