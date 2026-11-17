// リーダーの端(先頭/最終ページ)からさらに前・次へ進んだときの行き先(#113, #338)。
// 前後の巻があるかはバックエンドが作品の並びから決める(同じ番号の重複は飛ばし、前後番号の先頭)。

import type { NextVolume, VolumeDetail } from "../api";
import { readHref } from "../route";
import { displayVolumeTitle } from "../works/titleRows";

/** 前後へ進んだときの動き。href = 前/次巻のリーダーへ、notice = 移動せず理由を出す(オフライン)、null = 該当巻なし */
export type NextVolumeStep = { href: string } | { notice: string } | null;

function volumeStep(
  vol: NextVolume | null | undefined,
  label: string,
  workTitle: string | null | undefined,
): NextVolumeStep {
  if (!vol) return null;
  if (vol.offline) return { notice: `${label}「${displayVolumeTitle(vol, workTitle)}」はオフラインです` };
  return { href: readHref(vol.id) };
}

export function nextVolumeStep(detail: VolumeDetail | null | undefined): NextVolumeStep {
  return volumeStep(detail?.next_volume, "次の巻", detail?.work_title);
}

export function prevVolumeStep(detail: VolumeDetail | null | undefined): NextVolumeStep {
  return volumeStep(detail?.prev_volume, "前の巻", detail?.work_title);
}
