// 邵ｦ譖ｸ縺阪Μ繝ｼ繝繝ｼ繧偵Δ繝・け API 縺ｨ騾壹☆縲Ｋsdom 縺ｫ縺ｯ謠冗判縺檎┌縺・・縺ｧ縲∵悽譁・・蟶ｯ縺ｮ蟷・scrollWidth)繧・
// 譁・ｭ玲焚繝ｻ譁・ｭ励し繧､繧ｺ繝ｻ鬮倥＆縺九ｉ險育ｮ励☆繧句ｷｮ縺玲崛縺医〒縲√・繝ｼ繧ｸ蜑ｲ繧翫→譁・ｭ励し繧､繧ｺ縺ｮ螟画峩繧貞・迴ｾ縺吶ｋ
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { VolumeDetail } from "../api";
import { createMockBackend, defaultVolumes, mockSections } from "../mock/mockBackend";
import { readHref } from "../route";
import { NovelReader } from "./NovelReader";

declare global {
  interface Window {
    __novelPwned?: number;
  }
}

let backend: ReturnType<typeof createMockBackend>;
const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollWidth");

function setViewport(w: number, h: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: h });
}

async function open(id: number) {
  await act(async () => {
    window.location.hash = readHref(id);
  });
  render(<App />);
}

const text = () => screen.getByTestId("novel-text");
const at = () => ({ section: Number(text().dataset.section), page: Number(text().dataset.page), pages: Number(text().dataset.pages) });
/** 莉翫・繝壹・繧ｸ縺瑚ｦ・≧蜑ｲ蜷医・遽・峇 [from, to) */
const span = () => ({ from: Number(text().dataset.from), to: Number(text().dataset.to) });
const press = (key: string) => fireEvent.keyDown(window, { key });
const puts = () => backend.log.filter((l) => l.method === "PUT").map((l) => l.body as { page: number; finished: boolean; position?: number });

async function ready() {
  await waitFor(() => expect(text().style.visibility).toBe("visible"));
}

beforeEach(() => {
  backend = createMockBackend();
  localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const r = backend.handle(init?.method ?? "GET", url, init?.body as string | undefined);
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.contentType } });
  });
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get(this: HTMLElement) {
      if (!this.classList.contains("novel-text")) return 0;
      const fs = parseFloat(this.style.fontSize);
      const perColumn = Math.max(1, Math.floor(parseFloat(this.style.height) / fs));
      return Math.ceil((this.textContent ?? "").length / perColumn) * Math.round(fs * 2);
    },
  });
  setViewport(375, 812);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if (original) Object.defineProperty(HTMLElement.prototype, "scrollWidth", original);
  delete window.__novelPwned;
});

describe("邵ｦ譖ｸ縺阪Μ繝ｼ繝繝ｼ", () => {
  it("荳隕ｧ縺ｫ蟆剰ｪｬ縺ｨ縺励※蜃ｺ縺ｦ縲∫ｸｦ譖ｸ縺阪〒譛蛻昴°繧画怙蠕後∪縺ｧ隱ｭ繧√∵里隱ｭ縺ｧ菫晏ｭ倥＆繧後ｋ", async () => {
    await act(async () => {
      window.location.hash = "#/volumes";
    });
    render(<App />);
    const card = (await screen.findByText("邵ｦ譖ｸ縺阪・蟆剰ｪｬ 隨ｬ1蟾ｻ")).closest("li")!;
    expect(card.textContent).toContain("蟆剰ｪｬ");
    expect(card.textContent).toContain("EPUB");
    expect(card.textContent).not.toContain("CBZ 縺ｫ謨ｴ逅・);
    cleanup();

    await open(7);
    await ready();
    expect(at()).toMatchObject({ section: 0, page: 0 });
    expect(at().pages).toBeGreaterThan(1);
    expect(text().textContent).toContain("隨ｬ荳遶");
    expect(text().querySelector("ruby rt")?.textContent).toBe("縺ｭ縺・);

    const seen = new Set<number>();
    for (let i = 0; i < 200; i++) {
      seen.add(at().section);
      const before = at();
      press("ArrowLeft");
      if (screen.queryByText("譛蠕後・繝壹・繧ｸ縺ｧ縺・)) break;
      await waitFor(() => expect(at()).not.toEqual(before));
      await ready();
    }
    expect(screen.getByText("譛蠕後・繝壹・繧ｸ縺ｧ縺・)).toBeTruthy();
    expect([...seen]).toEqual([0, 1, 2]);
    expect(text().textContent).toContain("縺翫＠縺ｾ縺・);
    expect(at().page).toBe(at().pages - 1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    const last = puts().at(-1)!;
    expect(last.page).toBe(2);
    expect(last.finished).toBe(true);
    expect(last.position).toBe(span().from); // 譛蠕後・繝壹・繧ｸ縺ｮ蜈磯ｭ縺ｮ蜑ｲ蜷・
  });

  it("譛ｬ譁・・蜊ｱ髯ｺ縺ｪ HTML(繧ｹ繧ｯ繝ｪ繝励ヨ繝ｻ繧､繝吶Φ繝医ワ繝ｳ繝峨Λ繝ｻ螟夜Κ URL)縺ｯ螳溯｡後ｂ隱ｭ縺ｿ霎ｼ縺ｿ繧ゅ＆繧後↑縺・, async () => {
    backend.handle("PUT", "/api/volumes/7/progress", JSON.stringify({ page: 1, finished: false, position: 0 }));
    await open(7);
    await ready();
    expect(at().section).toBe(1);
    const el = text();
    expect(el.textContent).toContain("蜊ｱ髯ｺ縺ｪ譛ｬ譁・);
    expect(el.querySelectorAll("script, iframe, a").length).toBe(0);
    for (const node of Array.from(el.querySelectorAll("*"))) {
      for (const attr of Array.from(node.attributes)) expect(attr.name.startsWith("on")).toBe(false);
    }
    const srcs = Array.from(el.querySelectorAll("img")).map((i) => i.getAttribute("src"));
    expect(srcs).toEqual(["/api/volumes/7/resources/0"]);
    expect(window.__novelPwned).toBeUndefined();
  });

  it("菫晏ｭ倥＠縺滉ｽ咲ｽｮ(蛹ｺ蛻・ｊ+蜑ｲ蜷・縺九ｉ蜀埼幕縺励∵枚蟄励し繧､繧ｺ繧貞､峨∴縺ｦ繧りｿ代＞菴咲ｽｮ縺ｫ逡吶∪繧・, async () => {
    await open(8); // 蛹ｺ蛻・ｊ 2 縺ｮ 2 縺､逶ｮ縲∝牡蜷・0.5
    await ready();
    const first = at();
    expect(first.section).toBe(1);
    expect(span().from).toBeLessThanOrEqual(0.5);
    expect(span().to).toBeGreaterThan(0.5);

    press("Escape"); // jsdom 縺ｫ PointerEvent 縺檎┌縺・・縺ｧ縲√Γ繝九Η繝ｼ縺ｯ繧ｭ繝ｼ縺ｧ髢九￥
    for (let i = 0; i < 3; i++) fireEvent.click(screen.getByRole("button", { name: "螟ｧ縺阪￥" }));
    await ready();
    const bigger = at();
    expect(bigger.section).toBe(1);
    expect(bigger.pages).toBeGreaterThan(first.pages);
    // 譁・ｭ励し繧､繧ｺ繧貞､峨∴縺ｦ繧ゅ∝酔縺伜牡蜷・0.5)繧貞性繧繝壹・繧ｸ縺ｫ縺・ｋ
    expect(span().from).toBeLessThanOrEqual(0.5);
    expect(span().to).toBeGreaterThan(0.5);
    expect(localStorage.getItem("spinebox.novel")).toContain("24");
  });

  it("蛹ｺ蛻・ｊ縺ｮ蜈磯ｭ縺ｧ謌ｻ繧九→縲∝燕縺ｮ蛹ｺ蛻・ｊ縺ｮ譛蠕後・繝壹・繧ｸ縺ｸ陦後￥", async () => {
    backend.handle("PUT", "/api/volumes/8/progress", JSON.stringify({ page: 1, finished: false, position: 0 }));
    await open(8);
    await ready();
    expect(at()).toMatchObject({ section: 1, page: 0 });
    press("ArrowRight");
    await waitFor(() => expect(at().section).toBe(0));
    await ready();
    expect(at().page).toBe(at().pages - 1);
  });

  it("譛邨ゅ・繝ｼ繧ｸ縺九ｉ縺輔ｉ縺ｫ騾ｲ繧縺ｨ縲∝酔縺倅ｽ懷刀縺ｮ谺｡縺ｮ蟾ｻ縺ｸ遘ｻ繧・#113)", async () => {
    window.location.hash = "";
    const v = defaultVolumes().find((x) => x.id === 7)!;
    const html = mockSections()[7];
    const detail: VolumeDetail = {
      id: 7, title: v.title, format: "epub", page_count: html.length, pages: [],
      sections: html.map((h, i) => ({ index: i, length: h.length })),
      progress: { page: 2, finished: false, position: 0 }, // 譛蠕後・蛹ｺ蛻・ｊ縺九ｉ
      next_volume: { id: 8, title: "髱堤ｩｺ譁・ｺｫ縺ｮ繝・く繧ｹ繝・, volume_number: null, offline: false },
    };
    render(<NovelReader id="7" detail={detail} />);
    await ready();

    for (let i = 0; i < 100; i++) {
      if (at().section === 2 && at().page === at().pages - 1) break;
      const before = at();
      press("ArrowLeft");
      await waitFor(() => expect(at()).not.toEqual(before));
      await ready();
    }
    expect(at().section).toBe(2);
    expect(at().page).toBe(at().pages - 1);

    press("ArrowLeft");
    expect(window.location.hash).toBe("#/read/8");
  });
});
