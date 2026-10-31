import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createProgressSaver } from "./progressSaver";

describe("createProgressSaver", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("連続したページ送りは最後の位置だけを 1 回送る", () => {
    const save = vi.fn(() => Promise.resolve());
    const s = createProgressSaver(save, 500);
    s.update({ page: 1, finished: false });
    s.update({ page: 2, finished: false });
    vi.advanceTimersByTime(499);
    expect(save).not.toHaveBeenCalled();
    s.update({ page: 3, finished: false });
    vi.advanceTimersByTime(500);
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ page: 3, finished: false });
  });

  it("保存済みと同じ位置は送らない", () => {
    const save = vi.fn(() => Promise.resolve());
    const s = createProgressSaver(save, 100);
    s.markSaved({ page: 4, finished: false });
    s.update({ page: 4, finished: false });
    vi.advanceTimersByTime(100);
    expect(save).not.toHaveBeenCalled();
    s.update({ page: 4, finished: true });
    vi.advanceTimersByTime(100);
    expect(save).toHaveBeenCalledWith({ page: 4, finished: true });
  });

  it("flush は待たずに送り、待ちが無ければ何もしない", () => {
    const save = vi.fn(() => Promise.resolve());
    const s = createProgressSaver(save, 10_000);
    s.flush();
    expect(save).not.toHaveBeenCalled();
    s.update({ page: 7, finished: false });
    s.flush();
    expect(save).toHaveBeenCalledWith({ page: 7, finished: false });
    vi.advanceTimersByTime(10_000);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("送信に失敗した位置は、次に同じ位置で呼ばれたら送り直す", async () => {
    const save = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined);
    const s = createProgressSaver(save, 100);
    s.update({ page: 2, finished: false });
    vi.advanceTimersByTime(100);
    await vi.runAllTimersAsync();
    s.update({ page: 2, finished: false });
    vi.advanceTimersByTime(100);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it("cancel で待ちを捨てる", () => {
    const save = vi.fn(() => Promise.resolve());
    const s = createProgressSaver(save, 100);
    s.update({ page: 1, finished: false });
    s.cancel();
    vi.advanceTimersByTime(100);
    s.flush();
    expect(save).not.toHaveBeenCalled();
  });
});
