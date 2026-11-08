import { describe, expect, it } from "vitest";
import { distinctAliases, proposalEvidence, shortVolumeTitle, titleDiff, volumeChips, volumeNumberText } from "./proposalView";

const vn = (start: number, end = start, kind: "volume" | "episode" = "volume") => ({ kind, start, end, evidence: "" });

describe("統合の提案の表示(#243)", () => {
  it("巻番号を短く書く", () => {
    expect(volumeNumberText(vn(3))).toBe("3巻");
    expect(volumeNumberText(vn(1, 3))).toBe("1〜3巻");
    expect(volumeNumberText(vn(5, 5, "episode"))).toBe("第5話");
  });

  it("巻の名前から作品名とその前を落とす。残らなければ元の名前", () => {
    expect(shortVolumeTitle("[著者] 作品名 第01巻s", "作品名")).toBe("第01巻s");
    expect(shortVolumeTitle("作品名", "作品名")).toBe("作品名");
    expect(shortVolumeTitle("06", "作品名")).toBe("06");
  });

  it("同じ巻番号は数でまとめ、番号の無い巻は短い名前で並べる", () => {
    const w = {
      title: "作品",
      volumes: [
        { id: 1, title: "作品 第01巻", volume_number: vn(1) },
        { id: 2, title: "作品 第01巻s", volume_number: vn(1) },
        { id: 3, title: "作品 第02巻", volume_number: vn(2) },
        { id: 4, title: "作品 外伝", volume_number: null },
      ],
    };
    expect(volumeChips(w)).toEqual([
      { label: "1巻", count: 2 },
      { label: "2巻", count: 1 },
      { label: "外伝", count: 1 },
    ]);
  });

  it("近い作品名は違う部分だけを取り出し、まったく違う名前・同じ名前は null", () => {
    expect(titleDiff("植物魔法の件", "植物魔法の件c")).toEqual([
      { head: "植物魔法の件", diff: "", tail: "" },
      { head: "植物魔法の件", diff: "c", tail: "" },
    ]);
    expect(titleDiff("ABCxDEF", "ABCyyDEF")?.[1]).toEqual({ head: "ABC", diff: "yy", tail: "DEF" });
    expect(titleDiff("外付けの作品", "別の読み物")).toBeNull();
    expect(titleDiff("同じ", "同じ")).toBeNull();
  });

  it("根拠は同じ名前を繰り返さず、巻は作品名を落として書く", () => {
    const w = (title: string) => ({ id: 1, title, author: null, aliases: [], volume_count: 0, volumes: [] });
    const alias = (an: string, bn: string) => ({ norm: "", a: { name: an, source: "filename", is_title: true }, b: { name: bn, source: "filename", is_title: false } });
    expect(proposalEvidence({ a: w("作品"), b: w("作品c"), evidence: { alias: alias("作品", "作品") } })).toEqual(["名前「作品」が一致(作品名と別名)"]);
    expect(proposalEvidence({ a: w("作品"), b: w("作品c"), evidence: { alias: alias("作品 1", "作品　1") } })).toEqual(["作品名と別名が同じ名前とみなせる(表記の違いだけ)"]);
    const dup = { matched_pages: 6, compared_pages: 10, ratio: 0.6, rule: "same_number", volume_a: { id: 1, title: "[著] 作品 第01巻" }, volume_b: { id: 2, title: "v01" } };
    expect(proposalEvidence({ a: w("作品"), b: w("別"), evidence: { duplicate: [dup] } })).toEqual(["巻「第01巻」と「v01」は、巻番号の候補が同じで見た目の近いページが 6 / 10 ページ(60%)"]);
  });

  it("どちらかの作品名と空白・大小文字の違いしかない別名は落とす", () => {
    expect(distinctAliases(["作品　名前", "SAKUHIN c", "Other"], ["作品 名前", "sakuhinc"])).toEqual(["Other"]);
  });
});
