import type { Progress } from "../api";

export type SaveFn = (progress: Progress) => Promise<unknown>;

/**
 * 読書位置の保存をまとめる。ページ送りのたびに呼ばれ、最後の値だけを待ち時間の後に送る。
 * flush() は画面を離れるとき用で、待っている値があれば即座に送る。
 * 送信の失敗は読書を止めないよう握りつぶす(次のページ送りでまた送られる)。
 */
export function createProgressSaver(save: SaveFn, delayMs = 800) {
  let pending: Progress | null = null;
  let last: Progress | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const send = () => {
    timer = null;
    const p = pending;
    pending = null;
    if (!p || (last && last.page === p.page && last.finished === p.finished && last.position === p.position)) return;
    last = p;
    save(p).catch(() => {
      // 失敗した値は「送れた」扱いにしない。同じ位置で再度呼ばれたら送り直す
      if (last === p) last = null;
    });
  };

  return {
    /** 今の位置を伝える(待ち時間の後に送る) */
    update(p: Progress) {
      pending = p;
      if (timer) clearTimeout(timer);
      timer = setTimeout(send, delayMs);
    },
    /** 再開位置など、既に保存済みの値を知らせる(同じ値を送り直さないため) */
    markSaved(p: Progress | null) {
      last = p;
    },
    flush() {
      if (timer) clearTimeout(timer);
      send();
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
      pending = null;
    },
  };
}
