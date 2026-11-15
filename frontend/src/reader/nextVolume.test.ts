// エッジから前巻・次巻へ進む判定(#113, #338)
import { describe, expect, it } from "vitest";
import type { VolumeDetail } from "../api";
import { nextVolumeStep, prevVolumeStep } from "./nextVolume";

const detail = (next: VolumeDetail["next_volume"], prev: VolumeDetail["prev_volume"] = null): VolumeDetail => ({
  id: 1,
  title: "1 巻",
  page_count: 3,
  pages: [],
  progress: null,
  prev_volume: prev,
  next_volume: next,
});

describe("nextVolumeStep", () => {
  it("次の巻があれば、そのリーダーへの行き先を返す", () => {
    expect(nextVolumeStep(detail({ id: 3, title: "3 巻", volume_number: null, offline: false }))).toEqual({
      href: "#/read/3",
    });
  });

  it("次の巻がオフラインなら、移動せず案内を出す", () => {
    expect(nextVolumeStep(detail({ id: 3, title: "3 巻", volume_number: null, offline: true }))).toEqual({
      notice: "次の巻「3 巻」はオフラインです",
    });
  });

  it("次の巻が無いときは null(呼び出し側が「最後のページです」を出す)", () => {
    expect(nextVolumeStep(detail(null))).toBeNull();
    expect(nextVolumeStep(null)).toBeNull();
  });
});

describe("prevVolumeStep", () => {
  it("前の巻があれば、そのリーダーへの行き先を返す", () => {
    expect(prevVolumeStep(detail(null, { id: 2, title: "0 巻", volume_number: null, offline: false }))).toEqual({
      href: "#/read/2",
    });
  });

  it("前の巻がオフラインなら、移動せず案内を出す", () => {
    expect(prevVolumeStep(detail(null, { id: 2, title: "0 巻", volume_number: null, offline: true }))).toEqual({
      notice: "前の巻「0 巻」はオフラインです",
    });
  });

  it("前の巻が無いときは null", () => {
    expect(prevVolumeStep(detail(null, null))).toBeNull();
    expect(prevVolumeStep(null)).toBeNull();
  });
});
