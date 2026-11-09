// WorkCover の読み込み状態(キャッシュ済みの表紙が出ない不具合 #279 の回帰)
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { WorkCover } from "./WorkCover";

afterEach(cleanup);

describe("WorkCover", () => {
  it("読み込めたら loaded を付け、巻が変わったら読み込み待ちに戻す", () => {
    const { container, rerender } = render(<WorkCover volumeId={1} />);
    const img = () => container.querySelector("img.work-cover") as HTMLImageElement;
    fireEvent.load(img());
    expect(img().classList.contains("loaded")).toBe(true);
    rerender(<WorkCover volumeId={2} />);
    expect(img().classList.contains("loaded")).toBe(false);
    fireEvent.load(img());
    expect(img().classList.contains("loaded")).toBe(true);
  });

  it("マウント直後の再描画で loaded を落とさない(onLoad の後に状態を戻さない)", () => {
    const { container, rerender } = render(<WorkCover volumeId={1} />);
    const img = container.querySelector("img.work-cover") as HTMLImageElement;
    fireEvent.load(img);
    rerender(<WorkCover volumeId={1} />);
    expect(img.classList.contains("loaded")).toBe(true);
  });
});
