import { describe, expect, it } from "vitest";
import { adminHref, canonicalUrl, facetListHref, facetWorksHref, organizeHistoryHref, organizeHref, organizeTemplateHref, parseRoute, readHref, workHref, workOrganizeHref, workTitlesHref } from "./route";

const HOME = { name: "works", scope: { by: "all" } };

describe("parseRoute", () => {
  it("一覧とリーダーを見分ける", () => {
    expect(parseRoute("")).toEqual(HOME);
    expect(parseRoute("#/")).toEqual(HOME);
    expect(parseRoute("#/volumes")).toEqual({ name: "library" });
    expect(parseRoute("#/read/12")).toEqual({ name: "read", id: "12" });
    expect(parseRoute("#/read/12/")).toEqual({ name: "read", id: "12" });
  });
  it("readHref と往復できる(記号を含む id も)", () => {
    for (const id of [3, "a b/c?#%"]) {
      expect(parseRoute(readHref(id))).toEqual({ name: "read", id: String(id) });
    }
  });
  it("整理のプレビューと履歴", () => {
    expect(parseRoute(organizeHref(7))).toEqual({ name: "organize-plan", id: "7", naming: "title" });
    expect(parseRoute(organizeTemplateHref(7))).toEqual({ name: "organize-plan", id: "7", naming: "template" });
    expect(parseRoute(workOrganizeHref(4))).toEqual({ name: "work-organize", id: "4" });
    expect(parseRoute(workHref(4))).toEqual({ name: "work", id: "4" });
    expect(parseRoute(workTitlesHref(4))).toEqual({ name: "work-titles", id: "4" });
    expect(parseRoute("#/works/4/titles/")).toEqual({ name: "work-titles", id: "4" });
    expect(workTitlesHref(4)).toBe("#/works/4/titles");
    expect(parseRoute("#/read/7/template")).toEqual(HOME);
    expect(parseRoute(organizeHistoryHref)).toEqual({ name: "organize-history" });
    expect(parseRoute("#/organize/")).toEqual({ name: "organize-history" });
  });
  it("壊れた値・知らないパスはホーム", () => {
    expect(parseRoute("#/read/%E3")).toEqual(HOME);
    expect(parseRoute("#/read/")).toEqual(HOME);
    expect(parseRoute("#/other")).toEqual(HOME);
  });
  it("サイドバーのナビ(#85)", () => {
    expect(parseRoute("#/manga")).toEqual({ name: "works", scope: { by: "kind", kind: "manga" } });
    expect(parseRoute("#/novels/")).toEqual({ name: "works", scope: { by: "kind", kind: "novel" } });
    expect(parseRoute("#/follows")).toEqual({ name: "follows" });
    expect(parseRoute("#/bookmarks")).toEqual({ name: "bookmarks" });
    expect(parseRoute(facetListHref("author"))).toEqual({ name: "facets", field: "author" });
    expect(parseRoute("#/tags")).toEqual({ name: "facets", field: "tag" });
    expect(parseRoute("#/publishers")).toEqual({ name: "facets", field: "publisher" });
    // 値の一覧から開いた作品一覧(記号を含む値も往復できる)
    for (const v of ["東村アキコ", "a/b?c#d %"]) {
      expect(parseRoute(facetWorksHref("tag", v))).toEqual({ name: "works", scope: { by: "tag", value: v } });
    }
    expect(parseRoute("#/authors/%E3")).toEqual({ name: "facets", field: "author" });
  });
  it("管理のタブ", () => {
    expect(parseRoute(adminHref())).toEqual({ name: "admin", tab: "stats" });
    expect(adminHref()).toBe("#/admin");
    for (const tab of ["preferences", "data", "follows", "tasks"] as const) {
      expect(parseRoute(adminHref(tab))).toEqual({ name: "admin", tab });
    }
    expect(parseRoute("#/admin/unknown")).toEqual({ name: "admin", tab: "stats" });
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
