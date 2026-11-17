import { useEffect, useId, useRef, useState } from "react";
import { api, type WorkVolume } from "../api";

type NumberKind = "volume" | "episode" | "none";

function formatVolumeNumberLabel(v: WorkVolume): string {
  const vn = v.volume_number;
  if (!vn) return v.number_manual ? "番号なし（手動）" : "未判定";
  const unit = vn.kind === "episode" ? "話" : "巻";
  const range = vn.end > vn.start ? `${vn.start}〜${vn.end}` : `${vn.start}`;
  return `${range}${unit}${v.number_manual ? "（手動）" : ""}`;
}

function parsePositiveInt(raw: string): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const n = Number(trimmed);
  return Number.isSafeInteger(n) && n >= 1 ? n : null;
}

/** 各巻の巻番号・話数を手動で指定・リセットする編集 UI */
export function VolumeNumberEdit({
  v,
  busy,
  run,
  onChanged,
}: {
  v: WorkVolume;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<void>;
  onChanged: () => Promise<void>;
}) {
  const uid = useId();
  const initialKind: NumberKind = v.volume_number?.kind ?? "none";
  const initialStart = v.volume_number ? String(v.volume_number.start) : "";
  const initialEnd = v.volume_number && v.volume_number.end > v.volume_number.start ? String(v.volume_number.end) : "";

  const [kind, setKind] = useState<NumberKind>(initialKind);
  const [start, setStart] = useState<string>(initialStart);
  const [end, setEnd] = useState<string>(initialEnd);
  const [startError, setStartError] = useState<string | null>(null);
  const [endError, setEndError] = useState<string | null>(null);
  const [startTouched, setStartTouched] = useState(false);
  const [endTouched, setEndTouched] = useState(false);
  const [open, setOpen] = useState(false);

  const startRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setKind(v.volume_number?.kind ?? "none");
    setStart(v.volume_number ? String(v.volume_number.start) : "");
    setEnd(v.volume_number && v.volume_number.end > v.volume_number.start ? String(v.volume_number.end) : "");
    setStartError(null);
    setEndError(null);
    setStartTouched(false);
    setEndTouched(false);
  }, [v.volume_number?.kind, v.volume_number?.start, v.volume_number?.end, v.number_manual]);

  const validateStart = (val: string): string | null => {
    return parsePositiveInt(val) === null ? "1 以上の整数を入力してください。" : null;
  };

  const validateEnd = (endVal: string, startVal: string): string | null => {
    if (!endVal.trim()) return null;
    const parsedEnd = parsePositiveInt(endVal);
    if (parsedEnd === null) return "1 以上の整数を入力してください。";
    const parsedStart = parsePositiveInt(startVal);
    if (parsedStart !== null && parsedEnd < parsedStart) {
      return "開始番号以上の整数を入力してください。";
    }
    return null;
  };

  const save = () => {
    if (kind === "none") {
      void run(async () => {
        await api.updateVolumeNumber(v.id, { kind: null, start: null, end: null });
        await onChanged();
        setOpen(false);
      });
      return;
    }
    const sErr = validateStart(start);
    const eErr = validateEnd(end, start);
    setStartError(sErr);
    setEndError(eErr);
    setStartTouched(true);
    setEndTouched(true);
    if (sErr) {
      startRef.current?.focus();
      return;
    }
    if (eErr) {
      endRef.current?.focus();
      return;
    }
    const startNum = parsePositiveInt(start)!;
    const endNum = end.trim() ? parsePositiveInt(end)! : startNum;
    void run(async () => {
      await api.updateVolumeNumber(v.id, { kind, start: startNum, end: endNum });
      await onChanged();
      setOpen(false);
    });
  };

  const reset = () => {
    void run(async () => {
      await api.resetVolumeNumber(v.id);
      await onChanged();
      setOpen(false);
    });
  };

  const kindGroupName = `vol-num-kind-${uid}`;
  const kindVolId = `vol-num-kind-vol-${uid}`;
  const kindEpId = `vol-num-kind-ep-${uid}`;
  const kindNoneId = `vol-num-kind-none-${uid}`;
  const startId = `vol-num-start-${uid}`;
  const startHintId = `vol-num-start-hint-${uid}`;
  const startErrId = `vol-num-start-err-${uid}`;
  const endId = `vol-num-end-${uid}`;
  const endHintId = `vol-num-end-hint-${uid}`;
  const endErrId = `vol-num-end-err-${uid}`;

  return (
    <div className="volume-number-wrap">
      {v.number_manual && (
        <div className="volume-number-manual-bar">
          <span className="volume-meta">番号: 手動設定済み</span>
          <button
            type="button"
            className="small secondary"
            disabled={busy}
            aria-label={`${v.title} の巻番号を自動に戻す`}
            onClick={reset}
          >
            自動に戻す
          </button>
        </div>
      )}
      <details
        className="volume-bib volume-number-edit"
        open={open}
        onToggle={(e) => setOpen(e.currentTarget.open)}
      >
        <summary>番号: {formatVolumeNumberLabel(v)}</summary>
        <form
          className="work-edit volume-number-form"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <fieldset className="volume-number-kind">
            <legend>種別</legend>
            <div className="volume-number-radios">
              <div className="volume-number-radio">
                <input
                  id={kindVolId}
                  type="radio"
                  name={kindGroupName}
                  value="volume"
                  checked={kind === "volume"}
                  onChange={() => {
                    setKind("volume");
                    setStartError(null);
                    setEndError(null);
                  }}
                />
                <label htmlFor={kindVolId}>巻</label>
              </div>
              <div className="volume-number-radio">
                <input
                  id={kindEpId}
                  type="radio"
                  name={kindGroupName}
                  value="episode"
                  checked={kind === "episode"}
                  onChange={() => {
                    setKind("episode");
                    setStartError(null);
                    setEndError(null);
                  }}
                />
                <label htmlFor={kindEpId}>話</label>
              </div>
              <div className="volume-number-radio">
                <input
                  id={kindNoneId}
                  type="radio"
                  name={kindGroupName}
                  value="none"
                  checked={kind === "none"}
                  onChange={() => {
                    setKind("none");
                    setStartError(null);
                    setEndError(null);
                  }}
                />
                <label htmlFor={kindNoneId}>番号なし</label>
              </div>
            </div>
          </fieldset>

          {kind !== "none" && (
            <div className="volume-number-fields">
              <div className="volume-number-field">
                <label htmlFor={startId}>
                  開始番号 <span aria-hidden="true">*</span>
                </label>
                <span id={startHintId} className="organize-hint muted">
                  1 以上の整数
                </span>
                <input
                  ref={startRef}
                  id={startId}
                  name="start"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  required
                  value={start}
                  aria-invalid={startError ? "true" : "false"}
                  aria-describedby={startError ? `${startHintId} ${startErrId}` : startHintId}
                  onChange={(e) => {
                    const next = e.target.value;
                    setStart(next);
                    if (startTouched) {
                      setStartError(validateStart(next));
                    }
                    if (endTouched) {
                      setEndError(validateEnd(end, next));
                    }
                  }}
                  onBlur={() => {
                    if (startTouched || start !== "") {
                      setStartTouched(true);
                      setStartError(validateStart(start));
                    }
                  }}
                />
                {startError && (
                  <div id={startErrId} className="field-error">
                    <span role="img" aria-label="エラー">⚠</span> {startError}
                  </div>
                )}
              </div>

              <div className="volume-number-field">
                <label htmlFor={endId}>終了番号（任意）</label>
                <span id={endHintId} className="organize-hint muted">
                  単巻の場合は空欄
                </span>
                <input
                  ref={endRef}
                  id={endId}
                  name="end"
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={end}
                  aria-invalid={endError ? "true" : "false"}
                  aria-describedby={endError ? `${endHintId} ${endErrId}` : endHintId}
                  onChange={(e) => {
                    const next = e.target.value;
                    setEnd(next);
                    if (endTouched) {
                      setEndError(validateEnd(next, start));
                    }
                  }}
                  onBlur={() => {
                    if (endTouched || end !== "") {
                      setEndTouched(true);
                      setEndError(validateEnd(end, start));
                    }
                  }}
                />
                {endError && (
                  <div id={endErrId} className="field-error">
                    <span role="img" aria-label="エラー">⚠</span> {endError}
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="volume-number-actions">
            <button type="submit" className="small" disabled={busy}>
              番号を保存
            </button>
          </div>
        </form>
      </details>
    </div>
  );
}
