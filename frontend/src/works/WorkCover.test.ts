import { describe, expect, it } from "vitest";
import { landscapeClass } from "./WorkCover";

describe("表紙の横長(見開き)画像の表示判定(#186, #222)", () => {
  it("見開きが明らかな画像(ar > 2.0)は全体を縮小表示(contain)", () => {
    expect(landscapeClass(2.21)).toBe("landscape-contain");
    expect(landscapeClass(3.0)).toBe("landscape-contain");
    expect(landscapeClass(2.01)).toBe("landscape-contain");
  });

  it("横長(1.2 < ar <= 2.0)は左寄せ(自炊本の見開きスキャン等で表表紙側が表示される #222)", () => {
    expect(landscapeClass(1.41)).toBe("landscape-left");
    expect(landscapeClass(1.21)).toBe("landscape-left");
    expect(landscapeClass(1.42)).toBe("landscape-left");
    expect(landscapeClass(1.99)).toBe("landscape-left");
  });

  it("縦長・通常(ar <= 1.2)はクラスなし(中央クロップの既定)", () => {
    expect(landscapeClass(0.7)).toBe("");
    expect(landscapeClass(1.0)).toBe("");
    expect(landscapeClass(1.2)).toBe("");
  });

  it("境界値: ar = 2.0 は contain にならず左寄せ、ar = 1.2 は寄せない", () => {
    expect(landscapeClass(2.0)).toBe("landscape-left");
    expect(landscapeClass(1.2)).toBe("");
  });
});
