import { describe, expect, it } from "vitest";
import {
  buildAdaptiveSlots,
  computeDisplayProgress,
  findSlotForAnchor,
  formatPageDisplay,
  isLandscapeAspect,
  shouldSplitPage,
  slotKey,
} from "./spreadPages";

describe("isLandscapeAspect / shouldSplitPage", () => {
  it("アスペクト比が 1.2 を超えるときだけ横長とみなす", () => {
    expect(isLandscapeAspect(1.2)).toBe(false);
    expect(isLandscapeAspect(1.21)).toBe(true);
    expect(isLandscapeAspect(0.7)).toBe(false);
    expect(isLandscapeAspect(undefined)).toBe(false);
    expect(isLandscapeAspect(Number.NaN)).toBe(false);
    expect(isLandscapeAspect(Number.POSITIVE_INFINITY)).toBe(false);
  });

  it("1 ページ目(表紙)は横長でも分割しない", () => {
    expect(shouldSplitPage(0, 1.5, false, true)).toBe(false);
    expect(shouldSplitPage(1, 1.5, false, true)).toBe(true);
    expect(shouldSplitPage(1, 1.5, true, true)).toBe(false);
    expect(shouldSplitPage(1, 1.5, false, false)).toBe(false);
  });
});

describe("buildAdaptiveSlots", () => {
  it("縦長画面(RTL)では 1 ページ目を除き横長画像を右→左に 2 分割する", () => {
    const aspects = new Map([
      [0, 1.5], // 表紙(横長だが分割しない)
      [1, 1.45],
      [2, 0.7], // 縦長
      [3, 1.5],
    ]);
    const slots = buildAdaptiveSlots({
      pageCount: 4,
      direction: "rtl",
      spreadMode: false,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: aspects,
      splitEnabled: true,
    });
    expect(slots).toEqual([
      { pages: [0], spread: false, landscapeSingle: true },
      { pages: [1], spread: false, half: "right", aspect: 1.45 },
      { pages: [1], spread: false, half: "left", aspect: 1.45 },
      { pages: [2], spread: false },
      { pages: [3], spread: false, half: "right", aspect: 1.5 },
      { pages: [3], spread: false, half: "left", aspect: 1.5 },
    ]);
  });

  it("縦長画面(LTR)では左→右に 2 分割する", () => {
    const slots = buildAdaptiveSlots({
      pageCount: 2,
      direction: "ltr",
      spreadMode: false,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[1, 1.5]]),
      splitEnabled: true,
    });
    expect(slots.map((s) => ({ pages: s.pages, half: s.half }))).toEqual([
      { pages: [0], half: undefined },
      { pages: [1], half: "left" },
      { pages: [1], half: "right" },
    ]);
  });

  it("splitEnabled が false なら縦長画面でも分割しない", () => {
    const slots = buildAdaptiveSlots({
      pageCount: 3,
      direction: "rtl",
      spreadMode: false,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[1, 1.5], [2, 1.5]]),
      splitEnabled: false,
    });
    expect(slots).toEqual([
      { pages: [0], spread: false },
      { pages: [1], spread: false, landscapeSingle: true },
      { pages: [2], spread: false, landscapeSingle: true },
    ]);
  });

  it("横長画面では横長表紙・横長本文を単独スロットにし、縦長ページのペア組みと共存する", () => {
    // 0:横長表紙, 1:縦長, 2:横長見開き, 3:縦長, 4:縦長, 5:縦長(末尾孤立)
    const aspects = new Map([
      [0, 1.5],
      [1, 0.7],
      [2, 1.5],
      [3, 0.7],
      [4, 0.7],
      [5, 0.7],
    ]);
    const slots = buildAdaptiveSlots({
      pageCount: 6,
      direction: "rtl",
      spreadMode: true,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: aspects,
      splitEnabled: true,
    });
    expect(slots).toEqual([
      { pages: [0], spread: false, landscapeSingle: true },
      { pages: [1], spread: false },
      { pages: [2], spread: false, landscapeSingle: true },
      { pages: [3, 4], spread: true },
      { pages: [5], spread: true, hasBlank: true, blankAlign: "start" },
    ]);
  });

  it("横長画面で 1 ページ目が縦長なら従来どおりブランクと並べる", () => {
    const slots = buildAdaptiveSlots({
      pageCount: 3,
      direction: "rtl",
      spreadMode: true,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[0, 0.7], [1, 1.5], [2, 1.5]]),
      splitEnabled: true,
    });
    expect(slots).toEqual([
      { pages: [0], spread: true, hasBlank: true },
      { pages: [1], spread: false, landscapeSingle: true },
      { pages: [2], spread: false, landscapeSingle: true },
    ]);
  });
});

describe("findSlotForAnchor / formatPageDisplay / computeDisplayProgress / slotKey", () => {
  it("アンカー位置から適切なスロットを見つけられる", () => {
    const portraitSlots = buildAdaptiveSlots({
      pageCount: 3,
      direction: "rtl",
      spreadMode: false,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[1, 1.5]]),
      splitEnabled: true,
    });
    expect(findSlotForAnchor(portraitSlots, 1, "left")).toBe(2);
    expect(findSlotForAnchor(portraitSlots, 1)).toBe(1); // 読み始め側(right)

    const landscapeSlots = buildAdaptiveSlots({
      pageCount: 3,
      direction: "rtl",
      spreadMode: true,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[1, 1.5]]),
      splitEnabled: true,
    });
    expect(findSlotForAnchor(landscapeSlots, 1, "left")).toBe(1);
    expect(slotKey(portraitSlots[1])).not.toBe(slotKey(portraitSlots[2]));
  });

  it("ページ表示と進捗率・スライダー位置を安定して計算する", () => {
    const slots = buildAdaptiveSlots({
      pageCount: 4,
      direction: "rtl",
      spreadMode: false,
      firstPageSingle: true,
      lastPageAlign: "start",
      pageAspects: new Map([[1, 1.5], [3, 1.5]]),
      splitEnabled: true,
    });
    // [0], [1 right], [1 left], [2], [3 right], [3 left]
    expect(formatPageDisplay(slots[1], 4)).toBe("2 (右) / 4");
    expect(formatPageDisplay(slots[2], 4)).toBe("2 (左) / 4");
    expect(formatPageDisplay({ pages: [1, 2], spread: true }, 4)).toBe("2-3 / 4");

    expect(computeDisplayProgress(slots, 0, 4, "rtl")).toEqual({ progress: 0, sliderValue: 1 });
    expect(computeDisplayProgress(slots, 4, 4, "rtl")).toEqual({ progress: 99, sliderValue: 4 }); // 最後のページの右半分はまだ 99%
    expect(computeDisplayProgress(slots, 5, 4, "rtl")).toEqual({ progress: 100, sliderValue: 4 }); // 左半分で 100%
  });
});
