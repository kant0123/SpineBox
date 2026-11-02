// タイトル一覧(#115)の行の決まり。表示は Works.test.tsx で確かめるので、ここでは判定だけを試す。
import { describe, expect, it } from "vitest";
import type { FollowRelease, WorkDetail, WorkSlot, WorkVolume } from "../api";
import { buildTitleRows, percentText, volumeStatus, volumeTitle, workProgress } from "./titleRows";

const vol = (id: number, over: Partial<WorkVolume> = {}): WorkVolume => ({
  id, title: `巻 ${id}`, parent_title: null, page_count: 10, offline: false, progress: null,
  volume_number: null, duplicate_count: 0,
  assignment: { source: "filename", manual: false, confidence: 0.9, evidence: null }, ...over,
});

const slot = (start: number, volumes: WorkVolume[], end = start): WorkSlot => ({
  kind: "volume", start, end, label: start === end ? `${start}巻` : `${start}〜${end}巻`,
  duplicate: volumes.length > 1, volumes,
});

const release = (volume: number, kind: "new" | "gap"): FollowRelease => ({
  volume, kind, title: "作品", volume_label: null, released: "2020-01", isbn: null, url: null,
  source: "ndl", found_at: "2020-01-01T00:00:00+00:00", seen: false, seen_at: null,
});

const work = (slots: WorkSlot[], unnumbered: WorkVolume[], releases?: FollowRelease[]): WorkDetail =>
  ({
    title: "作品", slots, unnumbered,
    follow: releases ? { followed: true, releases } : { followed: false },
  }) as unknown as WorkDetail;

describe("巻の表示名", () => {
  it("作品名と巻番号にそろえ、範囲と話と番号なしを区別する", () => {
    expect(volumeTitle("作品", vol(1, { title: "[作者] 作品 第02巻", volume_number: { kind: "volume", start: 2, end: 2, evidence: "第02巻" } }))).toBe("作品 第2巻");
    expect(volumeTitle("作品", vol(1, { volume_number: { kind: "volume", start: 1, end: 3, evidence: "v01-03" } }))).toBe("作品 第1〜3巻");
    expect(volumeTitle("作品", vol(1, { volume_number: { kind: "episode", start: 5, end: 5, evidence: "第5話" } }))).toBe("作品 第5話");
    // 番号の無い巻は、その巻の名前をそのまま出す
    expect(volumeTitle("作品", vol(1, { title: "読了済みの巻" }))).toBe("読了済みの巻");
  });
});

describe("巻の進み具合", () => {
  it("「N% (読んだ / 全体)」で出し、未読は 0%、ページ数が未確定なら —", () => {
    expect(percentText({ progress: null, page_count: 10 })).toBe("0% (0/10)");
    expect(percentText({ progress: { page: 3, finished: false }, page_count: 10 })).toBe("40% (4/10)");
    expect(percentText({ progress: { page: 9, finished: true }, page_count: 10 })).toBe("100% (10/10)");
    expect(percentText({ progress: null, page_count: null })).toBe("—");
    expect(percentText({ progress: { page: 3, finished: false }, page_count: 0 })).toBe("—");
  });

  it("未読 / 読書中 / 読了", () => {
    expect(volumeStatus({ progress: null })).toBe("unread");
    expect(volumeStatus({ progress: { page: 0, finished: false } })).toBe("reading");
    expect(volumeStatus({ progress: { page: 9, finished: true } })).toBe("finished");
  });
});

describe("一覧の行", () => {
  it("重複は 1 行にまとめ、欠けは番号の位置へ、続刊は後ろへ差し込む", () => {
    const rows = buildTitleRows(work(
      [slot(1, [vol(1), vol(6, { progress: { page: 9, finished: true } })]), slot(2, [vol(2)]), slot(4, [vol(4)])],
      [vol(9)],
      [release(5, "new"), release(3, "gap"), release(2, "gap")], // 2 巻は調べた後に蔵書へ入った(古い結果)
    ));
    expect(rows.map((r) => (r.kind === "missing" ? `欠け${r.label}` : r.label))).toEqual([
      "1巻", "2巻", "欠け3巻", "4巻", "欠け5巻", "番号なし",
    ]);
    // 2 巻の行は 1 つだけ(古い欠けの結果で消さない)
    expect(rows.filter((r) => r.kind === "slot" && r.label === "2巻")).toHaveLength(1);
    // 重複の行は読んでいる巻を代表にする
    const dup = rows[0];
    expect(dup.kind === "slot" && dup.volumes).toHaveLength(2);
    expect(dup.kind === "slot" && dup.status).toBe("finished");
    expect(dup.kind === "slot" && dup.progress).toBe("100% (10/10)");
    // 欠け・続刊の行はリーダーへ行かせない(発売日だけ出す)
    expect(rows[2].kind).toBe("missing");
    expect(rows[2].kind === "missing" && rows[2].title).toBe("作品 第3巻");
    expect(rows[4].kind === "missing" && rows[4].missing).toBe("new");
  });

  it("フォローしていなければ欠け・続刊は出さない", () => {
    const rows = buildTitleRows(work([slot(1, [vol(1)])], []));
    expect(rows.map((r) => r.label)).toEqual(["1巻"]);
  });
});

describe("作品全体の進み具合", () => {
  it("重複は 1 巻として数え、読んだページの割合と読了巻数を出す", () => {
    const rows = buildTitleRows(work(
      [
        slot(1, [vol(1), vol(6, { progress: { page: 9, finished: true } })]), // 重複のうち読んだ方(10 / 10 ページ)
        slot(2, [vol(2, { progress: { page: 3, finished: false } })]), // 4 / 10 ページ
        slot(4, [vol(4, { progress: { page: 9, finished: true } })]), // 10 / 10 ページ
      ],
      [],
      [release(3, "gap")],
    ));
    expect(workProgress(rows)).toEqual({ percent: 80, read: 24, total: 30, finished: 2, volumes: 3 });
  });
});
