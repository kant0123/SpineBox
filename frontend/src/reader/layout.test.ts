import { describe, expect, it } from "vitest";
import {
  buildSpreads,
  isLastSpread,
  keyAction,
  preloadPages,
  shouldSpread,
  spreadIndexOf,
  stepPage,
  swipeAction,
  tapAction,
} from "./layout";

const single = { spread: false, coverAlone: true };
const spreadCover = { spread: true, coverAlone: true };
const spreadNoCover = { spread: true, coverAlone: false };

describe("buildSpreads", () => {
  it("単ページは 1 枚ずつ(表紙単独の設定は関係ない)", () => {
    expect(buildSpreads(3, single)).toEqual([[0], [1], [2]]);
    expect(buildSpreads(3, { spread: false, coverAlone: false })).toEqual([[0], [1], [2]]);
  });

  it("見開き・表紙単独 ON・偶数ページ: 最終ページが単独で残る", () => {
    expect(buildSpreads(6, spreadCover)).toEqual([[0], [1, 2], [3, 4], [5]]);
  });

  it("見開き・表紙単独 ON・奇数ページ: 最後まで 2 枚組", () => {
    expect(buildSpreads(5, spreadCover)).toEqual([[0], [1, 2], [3, 4]]);
  });

  it("見開き・表紙単独 OFF・偶数ページ: 最後まで 2 枚組", () => {
    expect(buildSpreads(6, spreadNoCover)).toEqual([[0, 1], [2, 3], [4, 5]]);
  });

  it("見開き・表紙単独 OFF・奇数ページ: 最終ページが単独", () => {
    expect(buildSpreads(5, spreadNoCover)).toEqual([[0, 1], [2, 3], [4]]);
  });

  it("1 ページ・0 ページ・不正な値", () => {
    expect(buildSpreads(1, spreadCover)).toEqual([[0]]);
    expect(buildSpreads(1, spreadNoCover)).toEqual([[0]]);
    expect(buildSpreads(0, spreadCover)).toEqual([]);
    expect(buildSpreads(-3, single)).toEqual([]);
    expect(buildSpreads(Number.NaN, single)).toEqual([]);
    expect(buildSpreads(2.7, single)).toEqual([[0], [1]]);
  });

  it("全ページがちょうど 1 回ずつ現れる", () => {
    for (let n = 0; n <= 9; n++) {
      for (const o of [single, spreadCover, spreadNoCover]) {
        expect(buildSpreads(n, o).flat()).toEqual(Array.from({ length: n }, (_, i) => i));
      }
    }
  });
});

describe("spreadIndexOf", () => {
  const s = buildSpreads(6, spreadCover); // [0] [1,2] [3,4] [5]
  it("見開きの 2 枚目からも同じ画面を指す", () => {
    expect(spreadIndexOf(s, 1)).toBe(1);
    expect(spreadIndexOf(s, 2)).toBe(1);
    expect(spreadIndexOf(s, 5)).toBe(3);
  });
  it("範囲外は端に寄せる(保存位置が今のページ数を超えていても開ける)", () => {
    expect(spreadIndexOf(s, -1)).toBe(0);
    expect(spreadIndexOf(s, 99)).toBe(3);
    expect(spreadIndexOf(s, Number.NaN)).toBe(0);
  });
  it("画面が無ければ -1", () => {
    expect(spreadIndexOf([], 0)).toBe(-1);
  });
});

describe("stepPage", () => {
  it("単ページで 1 枚ずつ進み、端では null", () => {
    const s = buildSpreads(3, single);
    expect(stepPage(s, 0, 1)).toBe(1);
    expect(stepPage(s, 2, 1)).toBeNull();
    expect(stepPage(s, 0, -1)).toBeNull();
    expect(stepPage(s, 2, -1)).toBe(1);
  });

  it("見開き(表紙単独)で 1 ページ目から最終ページまで辿れる", () => {
    const s = buildSpreads(6, spreadCover);
    const visited = [0];
    let p: number | null = 0;
    while ((p = stepPage(s, p, 1)) !== null) visited.push(p);
    expect(visited).toEqual([0, 1, 3, 5]);
    expect(isLastSpread(s, 5)).toBe(true);
  });

  it("見開きの 2 枚目の位置から戻ると前の画面の先頭へ", () => {
    const s = buildSpreads(6, spreadCover);
    expect(stepPage(s, 4, -1)).toBe(1);
    expect(stepPage(s, 2, -1)).toBe(0);
  });

  it("見開きの切り替え後も、読んでいたページを含む画面から進む", () => {
    // 単ページで 4 ページ目(index 3)まで読んでから横向きにした
    const s = buildSpreads(6, spreadNoCover); // [0,1] [2,3] [4,5]
    expect(spreadIndexOf(s, 3)).toBe(1);
    expect(stepPage(s, 3, 1)).toBe(4);
  });

  it("空の巻では動かない", () => {
    expect(stepPage([], 0, 1)).toBeNull();
  });
});

describe("isLastSpread", () => {
  it("最終ページを含む画面だけ true", () => {
    const s = buildSpreads(5, spreadCover); // [0] [1,2] [3,4]
    expect(isLastSpread(s, 3)).toBe(true);
    expect(isLastSpread(s, 4)).toBe(true);
    expect(isLastSpread(s, 2)).toBe(false);
    expect(isLastSpread([], 0)).toBe(false);
  });
  it("1 ページだけの巻は開いた時点で最終ページ", () => {
    expect(isLastSpread(buildSpreads(1, single), 0)).toBe(true);
  });
});

describe("preloadPages", () => {
  it("前後の画面のページを近い順に返し、今の画面は含めない", () => {
    const s = buildSpreads(9, spreadCover); // [0] [1,2] [3,4] [5,6] [7,8]
    expect(preloadPages(s, 3, 2)).toEqual([5, 6, 1, 2, 7, 8, 0]);
  });
  it("端では範囲内だけ", () => {
    const s = buildSpreads(3, single);
    expect(preloadPages(s, 0, 2)).toEqual([1, 2]);
    expect(preloadPages([], 0)).toEqual([]);
  });
});

describe("shouldSpread", () => {
  it("スマホ縦は単ページ、横長で十分な幅なら見開き", () => {
    expect(shouldSpread(375, 812)).toBe(false);
    expect(shouldSpread(812, 375)).toBe(true);
    expect(shouldSpread(1280, 800)).toBe(true);
    expect(shouldSpread(500, 300)).toBe(false); // 横長でも狭い
    expect(shouldSpread(800, 800)).toBe(false); // 正方形
  });
});

describe("操作の判定(右綴じ)", () => {
  it("タップ: 左が次・右が前・中央がメニュー", () => {
    expect(tapAction(10, 375)).toBe("next");
    expect(tapAction(187, 375)).toBe("menu");
    expect(tapAction(370, 375)).toBe("prev");
    expect(tapAction(10, 0)).toBe("menu");
  });
  it("スワイプ: 右へ払うと次、左へ払うと前、縦や短い動きは無視", () => {
    expect(swipeAction(80, 5)).toBe("next");
    expect(swipeAction(-80, 5)).toBe("prev");
    expect(swipeAction(20, 0)).toBeNull();
    expect(swipeAction(60, 70)).toBeNull();
  });
  it("キーボード: ← が次、→ が前", () => {
    expect(keyAction("ArrowLeft")).toBe("next");
    expect(keyAction("ArrowRight")).toBe("prev");
    expect(keyAction("Escape")).toBe("menu");
    expect(keyAction("a")).toBeNull();
  });
});
