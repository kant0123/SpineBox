import { describe, expect, it } from "vitest";
import { canonicalUrl, organizeHistoryHref, organizeHref, parseRoute, readHref } from "./route";

describe("parseRoute", () => {
  it("一覧とリーダーを見分ける", () => {
    expect(parseRoute("")).toEqual({ name: "library" });
    expect(parseRoute("#/")).toEqual({ name: "library" });
    expect(parseRoute("#/read/12")).toEqual({ name: "read", id: "12" });
    expect(parseRoute("#/read/12/")).toEqual({ name: "read", id: "12" });
  });
  it("readHref と往復できる(記号を含む id も)", () => {
    for (const id of [3, "a b/c?#%"]) {
      expect(parseRoute(readHref(id))).toEqual({ name: "read", id: String(id) });
    }
  });
  it("整理のプレビューと履歴", () => {
    expect(parseRoute(organizeHref(7))).toEqual({ name: "organize-plan", id: "7" });
    expect(parseRoute(organizeHistoryHref)).toEqual({ name: "organize-history" });
    expect(parseRoute("#/organize/")).toEqual({ name: "organize-history" });
  });
  it("壊れた値・知らないパスは一覧", () => {
    expect(parseRoute("#/read/%E3")).toEqual({ name: "library" });
    expect(parseRoute("#/read/")).toEqual({ name: "library" });
    expect(parseRoute("#/other")).toEqual({ name: "library" });
  });
});

describe("canonicalUrl", () => {
  it("/read/<id> の直リンクをハッシュの形にする", () => {
    expect(canonicalUrl("/read/12", "")).toBe("/#/read/12");
    expect(canonicalUrl("/read/12/", "#")).toBe("/#/read/12");
    expect(parseRoute((canonicalUrl("/read/12", "") ?? "").slice(1))).toEqual({ name: "read", id: "12" });
  });
  it("ハッシュがあれば正とし、他のパスは触らない", () => {
    expect(canonicalUrl("/read/12", "#/")).toBeNull();
    expect(canonicalUrl("/", "")).toBeNull();
    expect(canonicalUrl("/other/12", "")).toBeNull();
  });
});
