// リーダーの最終ページからさらに次へ進んだときの行き先(#113)。
// 次の巻があるかはバックエンドが作品の並びから決める(同じ番号の重複は飛ばし、次の番号の先頭)。

import type { VolumeDetail } from "../api";
import { readHref } from "../route";

/** 次へ進んだときの動き。href = 次巻のリーダーへ、notice = 移動せず理由を出す(オフライン)、null = 次巻なし */
export type NextVolumeStep = { href: string } | { notice: string } | null;

export function nextVolumeStep(detail: VolumeDetail | null | undefined): NextVolumeStep {
  const next = detail?.next_volume;
  if (!next) return null;
  if (next.offline) return { notice: `次の巻「${next.title}」はオフラインです` };
  return { href: readHref(next.id) };
}
