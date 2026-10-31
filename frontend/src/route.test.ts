import { describe, expect, it } from "vitest";
import { parseRoute, readHref } from "./route";

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
  it("壊れた値・知らないパスは一覧", () => {
    expect(parseRoute("#/read/%E3")).toEqual({ name: "library" });
    expect(parseRoute("#/read/")).toEqual({ name: "library" });
    expect(parseRoute("#/other")).toEqual({ name: "library" });
  });
});
