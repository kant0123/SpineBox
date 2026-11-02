// 縦書きリーダーのページ割り・位置の換算・ページ送り。DOM に触れない純粋関数だけを置く。
//
// 1 つの区切り(EPUB の本文 1 つ / テキストの一定文字数)を縦書き(vertical-rl)で 1 本の横長の帯に流し、
// 画面の幅ずつ右から左へずらして見せる。ページの切れ目は、描画した後の実際の行(と挿絵)の位置から決め、
// 行の途中では切らない(行の幅はフォントの寸法やルビで 1px 単位でぶれるので、行の高さの倍数では揃わない)。
//
// 位置はページ番号ではなく「区切りの番号 + 区切りの中の割合(帯の右端からの距離 / 帯の幅)」で持つ。
// 割合は文字サイズ・画面幅に依らないので、どちらが変わっても近い位置から再開できる。

/** 行の高さ(縦書きでは 1 行の幅)= 文字サイズ × この値。ルビ(0.5em)が行間に収まる大きさ */
export const LINE_HEIGHT = 2;
export const FONT_SIZES = [14, 16, 18, 20, 22, 24, 28, 32] as const;
export const DEFAULT_FONT_SIZE = 18;

/** 帯の右端からの距離で表した区間 [始まり, 終わり](px) */
export type Interval = [number, number];

/** これより狭い隙間は同じ行とみなす(親文字とルビが 1px 離れて測れることがある) */
const JOIN_GAP = 2;
const EPS = 0.5;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** 文字・挿絵の矩形を、重なる(ほぼ接する)ものどうしでまとめて行にする */
export function mergeColumns(intervals: Interval[]): Interval[] {
  const sorted = intervals.filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  const out: Interval[] = [];
  for (const [a, b] of sorted) {
    const last = out[out.length - 1];
    if (last && a <= last[1] + JOIN_GAP) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

/**
 * 各ページの始まり(帯の右端からの距離)。行が収まらなくなったら、その行の手前でページを切る。
 * 1 行が画面より広い(大きな挿絵)ときだけ、画面の幅で切る。行が分からない(測れない)ときは画面の幅で等分する。
 */
export function pageStarts(columns: Interval[], width: number, contentWidth: number): number[] {
  if (!(width > 0)) return [0];
  const starts = [0];
  if (columns.length === 0) {
    for (let s = width; s < contentWidth - 1; s += width) starts.push(s);
    return starts;
  }
  let s = 0;
  for (const [a, b] of mergeColumns(columns)) {
    if (b - s <= width + EPS) continue;
    if (a - s > EPS) {
      s = a;
      starts.push(s);
    }
    while (b - s > width + EPS) {
      s += width;
      starts.push(s);
    }
  }
  return starts;
}

/** ページ k で見せる幅(次のページの最初の行が覗かないよう、そこで切る) */
export function visibleWidth(starts: number[], page: number, width: number): number {
  const next = starts[page + 1];
  return next === undefined ? width : clamp(next - starts[page], 1, width);
}

/** 割合(0〜1)の位置を含むページ。1 は最後のページ */
export function pageForPosition(position: number, starts: number[], contentWidth: number): number {
  if (starts.length === 0) return 0;
  if (!Number.isFinite(position)) return 0;
  if (position >= 1) return starts.length - 1;
  const target = clamp(position, 0, 1) * Math.max(0, contentWidth);
  let page = 0;
  for (let k = 0; k < starts.length; k++) if (starts[k] <= target + EPS) page = k;
  return page;
}

/** そのページの先頭の割合 */
export function positionForPage(page: number, starts: number[], contentWidth: number): number {
  if (!(contentWidth > 0) || starts.length === 0) return 0;
  return clamp(starts[clamp(page, 0, starts.length - 1)] / contentWidth, 0, 1);
}

/** 巻全体での進み具合(0〜1)。区切りの長さで重み付けする */
export function overallFraction(lengths: number[], section: number, position: number): number {
  const total = lengths.reduce((a, b) => a + Math.max(0, b), 0);
  if (total <= 0) return 0;
  const i = clamp(section, 0, lengths.length - 1);
  const before = lengths.slice(0, i).reduce((a, b) => a + Math.max(0, b), 0);
  return clamp((before + Math.max(0, lengths[i]) * clamp(position, 0, 1)) / total, 0, 1);
}

export type NovelStep =
  | { kind: "page"; page: number }
  /** 隣の区切りへ。position は着いた区切りの中の割合(次なら先頭 0、前なら末尾 1) */
  | { kind: "section"; section: number; position: number }
  | null;

/** 1 画面ぶん進む / 戻る。区切りの端では隣の区切りへ、巻の端では null */
export function stepNovel(section: number, page: number, pages: number, sectionCount: number, delta: 1 | -1): NovelStep {
  const next = page + delta;
  if (next >= 0 && next < pages) return { kind: "page", page: next };
  const s = section + delta;
  if (s < 0 || s >= sectionCount) return null;
  return { kind: "section", section: s, position: delta > 0 ? 0 : 1 };
}

/** 巻の最後の画面か(= 読み終えた) */
export const isEnd = (section: number, page: number, pages: number, sectionCount: number) =>
  section >= sectionCount - 1 && page >= pages - 1;
