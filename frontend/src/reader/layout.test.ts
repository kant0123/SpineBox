import { describe, expect, it } from "vitest";
import { keyAction, swipeAction, tapAction } from "./layout";

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
