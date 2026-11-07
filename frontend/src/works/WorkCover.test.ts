import { describe, expect, it } from "vitest";
import type { WorkKind } from "../api";
import { landscapeClass } from "./WorkCover";

describe("表紙の横長(見開き)画像の表示判定(#186)", () => {
  it("見開きが明らかな画像(ar > 2.0)は全体を縮小表示(contain)", () => {
    expect(landscapeClass("manga", 2.21)).toBe("landscape-contain");
    expect(landscapeClass("novel", 3.0)).toBe("landscape-contain");
    expect(landscapeClass(null, 2.01)).toBe("landscape-contain");
  });

  it("横長(1.2 < ar <= 2.0)は漫画→左寄せ、小説→右寄せ", () => {
    expect(landscapeClass("manga", 1.41)).toBe("landscape-left");
    expect(landscapeClass("manga", 1.21)).toBe("landscape-left");
    expect(landscapeClass("novel", 1.42)).toBe("landscape-right");
    expect(landscapeClass("novel", 1.99)).toBe("landscape-right");
    // kind が null は漫画扱い
    expect(landscapeClass(null, 1.4)).toBe("landscape-left");
  });

  it("縦長・通常(ar <= 1.2)はクラスなし(中央クロップの既定)", () => {
    expect(landscapeClass("manga", 0.7)).toBe("");
    expect(landscapeClass("novel", 1.0)).toBe("");
    expect(landscapeClass("manga", 1.2)).toBe("");
  });

  it("境界値: ar = 2.0 は contain にならず寄せ、ar = 1.2 は寄せない", () => {
    expect(landscapeClass("manga", 2.0)).toBe("landscape-left");
    expect(landscapeClass("manga", 1.2)).toBe("");
  });
});