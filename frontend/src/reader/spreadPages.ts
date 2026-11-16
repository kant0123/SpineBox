// 見開きスキャン画像(横長画像)の分割・単独表示スロット計算と進捗表示の純粋関数。
// DOM・React・manga-viewer 本体に依存せず単体テスト可能にする。

/** 横長画像(見開きスキャン)と判定するアスペクト比(width / height)のしきい値。WorkCover.tsx と同じ 1.2 */
export const LANDSCAPE_AR_THRESHOLD = 1.2;

export type SlotHalf = "left" | "right";

export interface AdaptiveSlot {
  pages: number[];
  spread: boolean;
  hasBlank?: boolean;
  blankAlign?: "start" | "end";
  half?: SlotHalf;
  aspect?: number;
  landscapeSingle?: boolean;
}

export interface BuildSlotsOptions {
  pageCount: number;
  direction: "rtl" | "ltr";
  spreadMode: boolean;
  firstPageSingle: boolean;
  lastPageAlign: "center" | "start" | "end";
  pageAspects: ReadonlyMap<number, number>;
  splitEnabled: boolean;
  nonImageIndices?: ReadonlySet<number>;
}

/** 画像のアスペクト比が横長(見開き)か */
export function isLandscapeAspect(aspect: number | undefined): boolean {
  return typeof aspect === "number" && Number.isFinite(aspect) && aspect > LANDSCAPE_AR_THRESHOLD;
}

/**
 * 縦長画面かつ分割有効時に、そのページを左右 2 スロットへ分割すべきか。
 * 1 ページ目(index === 0: 表紙)は「左=表表紙・右=裏表紙」のスキャンがあるため分割しない。
 */
export function shouldSplitPage(
  pageIndex: number,
  aspect: number | undefined,
  spreadMode: boolean,
  splitEnabled: boolean,
): boolean {
  return !spreadMode && splitEnabled && pageIndex > 0 && isLandscapeAspect(aspect);
}

/**
 * 画像のアスペクト比と画面の向きに応じて表示スロット列を組み立てる。
 * - 縦長画面(!spreadMode): 本文の横長画像を右→左(rtl)または左→右(ltr)の 2 スロットに分割
 * - 横長画面(spreadMode): 横長画像は他ページ・ブランクと並べず 1 枚単独スロットにする
 */
export function buildAdaptiveSlots(opts: BuildSlotsOptions): AdaptiveSlot[] {
  const { pageCount, direction, spreadMode, firstPageSingle, lastPageAlign, pageAspects, splitEnabled, nonImageIndices } = opts;
  if (pageCount <= 0) return [];

  const slots: AdaptiveSlot[] = [];

  if (!spreadMode) {
    const halves: [SlotHalf, SlotHalf] = direction === "rtl" ? ["right", "left"] : ["left", "right"];
    for (let i = 0; i < pageCount; i++) {
      const aspect = pageAspects.get(i);
      const isSpecial = nonImageIndices?.has(i) ?? false;
      if (!isSpecial && shouldSplitPage(i, aspect, false, splitEnabled)) {
        slots.push({ pages: [i], spread: false, half: halves[0], aspect });
        slots.push({ pages: [i], spread: false, half: halves[1], aspect });
      } else {
        slots.push({
          pages: [i],
          spread: false,
          ...(!isSpecial && isLandscapeAspect(aspect) ? { landscapeSingle: true } : {}),
        });
      }
    }
    return slots;
  }

  let i = 0;
  if (firstPageSingle) {
    const isSpecial = nonImageIndices?.has(0) ?? false;
    if (!isSpecial && isLandscapeAspect(pageAspects.get(0))) {
      // 横長の表紙をブランクと並べて 50% 幅に縮小しない
      slots.push({ pages: [0], spread: false, landscapeSingle: true });
    } else if (isSpecial) {
      slots.push({ pages: [0], spread: false });
    } else {
      slots.push({ pages: [0], spread: true, hasBlank: true });
    }
    i = 1;
  }

  while (i < pageCount) {
    if (nonImageIndices?.has(i)) {
      slots.push({ pages: [i], spread: false });
      i++;
      continue;
    }
    if (isLandscapeAspect(pageAspects.get(i))) {
      slots.push({ pages: [i], spread: false, landscapeSingle: true });
      i++;
      continue;
    }
    const nextOk =
      i + 1 < pageCount
      && !(nonImageIndices?.has(i + 1) ?? false)
      && !isLandscapeAspect(pageAspects.get(i + 1));
    if (nextOk) {
      slots.push({ pages: [i, i + 1], spread: true });
      i += 2;
    } else if (i === pageCount - 1 && (lastPageAlign === "start" || lastPageAlign === "end")) {
      slots.push({ pages: [i], spread: true, hasBlank: true, blankAlign: lastPageAlign });
      i++;
    } else {
      slots.push({ pages: [i], spread: false });
      i++;
    }
  }

  return slots;
}

/** スロット DOM 再利用用の一意キー */
export function slotKey(slot: AdaptiveSlot): string {
  return `${slot.pages.join(",")}|${slot.half ?? ""}|${slot.spread ? "s" : "1"}|${slot.hasBlank ? (slot.blankAlign ?? "b") : ""}`;
}

/** 再構築・回転後に (pageIdx, half) へ最も合うスロット番号を返す */
export function findSlotForAnchor(slots: readonly AdaptiveSlot[], pageIdx: number, half?: SlotHalf): number {
  if (half !== undefined) {
    const exact = slots.findIndex((s) => s.pages.includes(pageIdx) && s.half === half);
    if (exact >= 0) return exact;
  }
  const byPage = slots.findIndex((s) => s.pages.includes(pageIdx));
  return byPage >= 0 ? byPage : 0;
}

/** フッターおよび読み上げ用のページ表記。分割表示時は (右)/(左) を添える */
export function formatPageDisplay(slot: AdaptiveSlot | undefined, totalPages: number): string {
  if (!slot || slot.pages.length === 0) return `1 / ${totalPages}`;
  const first = slot.pages[0] + 1;
  const last = slot.pages[slot.pages.length - 1] + 1;
  if (slot.spread && slot.pages.length > 1) return `${first}-${last} / ${totalPages}`;
  if (slot.half === "right") return `${first} (右) / ${totalPages}`;
  if (slot.half === "left") return `${first} (左) / ${totalPages}`;
  return `${first} / ${totalPages}`;
}

/**
 * 先読みで後から未ロードページが分割されても進捗 % とスライダー値が跳ねないよう、
 * 元ページ番号(0..totalPages-1)基準で進捗とスライダー値を算出する。
 */
export function computeDisplayProgress(
  slots: readonly AdaptiveSlot[],
  slotIndex: number,
  totalPages: number,
  direction: "rtl" | "ltr",
): { progress: number; sliderValue: number } {
  if (totalPages <= 0 || slots.length === 0) return { progress: 0, sliderValue: 1 };
  if (slots.length === 1) return { progress: 100, sliderValue: totalPages };
  if (slotIndex <= 0) return { progress: 0, sliderValue: 1 };
  if (slotIndex >= slots.length - 1) return { progress: 100, sliderValue: totalPages };

  const slot = slots[slotIndex];
  const p = slot?.pages[0] ?? 0;
  const isSecondHalf =
    slot?.half !== undefined
    && ((direction === "rtl" && slot.half === "left") || (direction === "ltr" && slot.half === "right"));
  const unit = p + (isSecondHalf ? 0.5 : 0);
  const progress = Math.min(99, Math.round((unit / Math.max(1, totalPages - 1)) * 100));
  const sliderValue = Math.min(totalPages, Math.max(1, p + 1));
  return { progress, sliderValue };
}
