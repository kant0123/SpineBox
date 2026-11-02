import { describe, expect, it } from "vitest";
import {
  isEnd,
  mergeColumns,
  overallFraction,
  pageForPosition,
  pageStarts,
  positionForPage,
  stepNovel,
  visibleWidth,
  type Interval,
} from "./pagination";

/** 幅 w の行が pitch ごとに n 本並ぶ帯(右端から) */
const lines = (n: number, pitch: number, w = pitch / 2, drift = 0): Interval[] =>
  Array.from({ length: n }, (_, i) => [i * (pitch + drift) + (pitch - w) / 2, i * (pitch + drift) + (pitch + w) / 2] as Interval);

describe("ページ割り", () => {
  it("行の途中では切らない(行の幅がぶれても)", () => {
    const cols = lines(40, 36, 18, 1); // 1 行ごとに 1px ずつずれる(ルビ・フォントの寸法)
    const starts = pageStarts(cols, 324, 40 * 37);
    expect(starts.length).toBeGreaterThan(4);
    for (const [a, b] of cols) {
      const pa = pageForPosition(a / (40 * 37), starts, 40 * 37);
      // 行の始まりと終わりが同じページに入る
      expect(starts[pa] <= a && (starts[pa + 1] ?? Infinity) >= b).toBe(true);
      expect(b - starts[pa]).toBeLessThanOrEqual(324.5); // 画面の幅に収まる
    }
  });

  it("ルビと親文字はひとつの行にまとめる。画面より広い挿絵だけは画面の幅で切る", () => {
    expect(mergeColumns([[10, 28], [29, 38], [50, 68]])).toEqual([[10, 38], [50, 68]]);
    expect(pageStarts([[0, 700]], 300, 700)).toEqual([0, 300, 600]);
    expect(pageStarts([[0, 20], [40, 340]], 300, 340)).toEqual([0, 40]);
  });

  it("行が測れないとき(jsdom)は画面の幅で等分する", () => {
    expect(pageStarts([], 320, 960)).toEqual([0, 320, 640]);
    expect(pageStarts([], 320, 961)).toEqual([0, 320, 640]); // 丸め誤差の 1px ははみ出しにしない
    expect(pageStarts([], 320, 0)).toEqual([0]);
  });

  it("次のページの最初の行が覗かない幅", () => {
    expect(visibleWidth([0, 300, 590], 0, 324)).toBe(300);
    expect(visibleWidth([0, 300, 590], 2, 324)).toBe(324);
  });

  it("割合とページを行き来でき、1 は最後のページ", () => {
    const starts = [0, 300, 590, 880];
    for (let p = 0; p < starts.length; p++) expect(pageForPosition(positionForPage(p, starts, 1000), starts, 1000)).toBe(p);
    expect(pageForPosition(1, starts, 1000)).toBe(3);
    expect(pageForPosition(Number.NaN, starts, 1000)).toBe(0);
    expect(pageForPosition(0.5, starts, 1000)).toBe(1);
  });

  it("文字サイズを変えてページ割りが変わっても、保存した割合を含むページから再開する", () => {
    const pos = 0.37;
    for (const [n, pitch] of [[30, 28], [60, 36], [90, 48]] as const) {
      const width = n * pitch;
      const starts = pageStarts(lines(n, pitch), 320, width);
      const page = pageForPosition(pos, starts, width);
      expect(positionForPage(page, starts, width)).toBeLessThanOrEqual(pos);
      expect(page + 1 < starts.length ? positionForPage(page + 1, starts, width) : 1).toBeGreaterThan(pos);
    }
  });
});

describe("ページ送り", () => {
  it("区切りの端で隣の区切りへ、巻の端で null", () => {
    expect(stepNovel(0, 0, 3, 2, 1)).toEqual({ kind: "page", page: 1 });
    expect(stepNovel(0, 2, 3, 2, 1)).toEqual({ kind: "section", section: 1, position: 0 });
    expect(stepNovel(1, 0, 3, 2, -1)).toEqual({ kind: "section", section: 0, position: 1 });
    expect(stepNovel(0, 0, 3, 2, -1)).toBeNull();
    expect(stepNovel(1, 2, 3, 2, 1)).toBeNull();
    expect(isEnd(1, 2, 3, 2)).toBe(true);
    expect(isEnd(0, 2, 3, 2)).toBe(false);
  });

  it("全体の進み具合は区切りの長さで重み付けする", () => {
    expect(overallFraction([100, 300], 0, 0)).toBe(0);
    expect(overallFraction([100, 300], 1, 0)).toBe(0.25);
    expect(overallFraction([100, 300], 1, 1)).toBe(1);
    expect(overallFraction([], 0, 0.5)).toBe(0);
  });
});
