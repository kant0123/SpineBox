import { useEffect } from "react";
import type { VolumeDetail } from "../api";
import { displayVolumeTitle } from "../works/titleRows";

/** リーダー画面の正規化後タイトルを算出し、document.title も「<タイトル> | SpineBox」に同期する(#351) */
export function useReaderTitle(detail: Pick<VolumeDetail, "title" | "work_title" | "volume_number">): string {
  const title = displayVolumeTitle(detail, detail.work_title);
  useEffect(() => {
    const prev = document.title;
    document.title = `${title} | SpineBox`;
    return () => {
      document.title = prev;
    };
  }, [title]);
  return title;
}
