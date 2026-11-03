// 受け入れの画面。受け入れフォルダ → 同期で検出 → 整理案のプレビュー → 承認で書庫へ → 履歴から元に戻す。
// 同期とプレビューは何も動かさない。蔵書に書き込むのは「承認」だけ(自動では動かさない)。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  type InboxApproveResult,
  type InboxCategory,
  type InboxDecision,
  type InboxItem,
  type InboxState,
  type InboxStatus,
  type OrganizeOp,
  type WorkSummary,
} from "../api";
import { libraryHref, organizeHistoryHref, worksHref } from "../route";
import { STATE_LABELS } from "./Organize";

const CATEGORY_LABELS: Record<InboxCategory, string> = {
  existing_work: "既存の作品の巻",
  new_work: "新しい作品",
  duplicate: "既に持っている巻の疑い",
  unreadable: "読めない",
  unsupported: "取り込めない",
  unknown_work: "作品が決まらない",
};

const STATUS_LABELS: Record<InboxStatus, string> = {
  ready: "承認できます",
  choose: "確認が要ります",
  blocked: "取り込めません",
};

const MODE_LABELS = { cbz: "巻ごとに CBZ にして書庫へ", move: "中身を変えずに書庫へ移動" } as const;

/** 利用者が画面で選んだこと。work は "" = 自動 / "new" = 新しい作品 / 作品の id */
interface Choice {
  work: string;
  newTitle: string;
  numbers: Record<string, string>;
  skip: Record<string, boolean>;
}

const emptyChoice = (): Choice => ({ work: "", newTitle: "", numbers: {}, skip: {} });

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

function decisionOf(item: InboxItem, c: Choice | undefined): InboxDecision {
  const d: InboxDecision = { source_id: item.source_id };
  if (!c) return d;
  if (c.work === "new") {
    if (c.newTitle.trim()) d.new_title = c.newTitle.trim();
  } else if (c.work) {
    d.work_id = Number(c.work);
  }
  const numbers: Record<string, number> = {};
  for (const [id, text] of Object.entries(c.numbers)) {
    if (/^\d{1,4}$/.test(text.trim())) numbers[id] = Number(text.trim());
  }
  if (Object.keys(numbers).length > 0) d.numbers = numbers;
  const skip = Object.entries(c.skip)
    .filter(([, on]) => on)
    .map(([id]) => Number(id));
  if (skip.length > 0) d.skip_volume_ids = skip;
  return d;
}

function numberLabel(n: NonNullable<InboxItem["volumes"][number]["number"]>): string {
  const unit = n.kind === "episode" ? "話" : "巻";
  return n.end !== null && n.end !== n.start ? `第${n.start}-${n.end}${unit}` : `第${n.start}${unit}`;
}

function VolumeRow({
  volume,
  choice,
  onChange,
}: {
  volume: InboxItem["volumes"][number];
  choice: Choice;
  onChange: (c: Choice) => void;
}) {
  const id = String(volume.volume_id);
  const editNumber = volume.number === null || volume.number.source === "manual" || id in choice.numbers;
  return (
    <li className={`inbox-volume${volume.skipped ? " skipped" : ""}`}>
      <div>
        {volume.title}
        {volume.page_count !== null && <span className="muted"> ({volume.page_count} ページ)</span>}
        {volume.number && !editNumber && <span className="inbox-number"> {numberLabel(volume.number)}</span>}
      </div>
      {editNumber ? (
        <label className="inbox-field">
          番号
          <input
            type="number"
            inputMode="numeric"
            min={0}
            max={9999}
            aria-label={`${volume.title} の番号`}
            value={choice.numbers[id] ?? (volume.number ? String(volume.number.start) : "")}
            onChange={(e) => onChange({ ...choice, numbers: { ...choice.numbers, [id]: e.target.value } })}
          />
        </label>
      ) : (
        <button type="button" className="link-button" onClick={() => onChange({ ...choice, numbers: { ...choice.numbers, [id]: String(volume.number!.start) } })}>
          番号を直す
        </button>
      )}
      {volume.output_rel_path && !volume.skipped && (
        <div className="organize-paths">
          → <code>{volume.output_rel_path}</code>
        </div>
      )}
      {volume.duplicate_of.length > 0 && (
        <div className="notice warn">
          書庫の{volume.duplicate_of.map((d) => `「${d.title}」`).join("・")}と同じ巻かもしれません(同じ作品・同じ番号)。
        </div>
      )}
      {volume.reason && !volume.skipped && volume.duplicate_of.length === 0 && <div className="muted">{volume.reason}</div>}
      <label className="inbox-field">
        <input type="checkbox" checked={!!choice.skip[id]} onChange={(e) => onChange({ ...choice, skip: { ...choice.skip, [id]: e.target.checked } })} />
        取り込まない
      </label>
    </li>
  );
}

function ItemCard({
  item,
  choice,
  works,
  selected,
  busy,
  onSelect,
  onChange,
  onAnalyze,
}: {
  item: InboxItem;
  choice: Choice;
  works: WorkSummary[];
  selected: boolean;
  busy: boolean;
  onSelect: (on: boolean) => void;
  onChange: (c: Choice) => void;
  onAnalyze: () => void;
}) {
  const w = item.work;
  const candidateIds = new Set(w?.candidates.map((c) => c.work_id));
  const autoLabel = w && (w.confident || w.decided) ? `自動: ${w.match === "new" ? "新しい作品「" : "「"}${w.title}」` : "(選んでください)";
  return (
    <li className={`inbox-item ${item.status}`}>
      <label className="inbox-select">
        <input type="checkbox" checked={selected} disabled={item.status !== "ready" || busy} onChange={(e) => onSelect(e.target.checked)} aria-label={`${item.name} を承認する`} />
        <strong>{item.name}</strong>
      </label>
      <div className="organize-paths">
        <code>{item.rel_path}</code>({item.kind === "folder" ? "画像フォルダ" : "ファイル"})
      </div>
      <div className="inbox-badges">
        <span className={`organize-badge inbox-${item.status}`}>{STATUS_LABELS[item.status]}</span>
        <span className="organize-badge">{CATEGORY_LABELS[item.category]}</span>
        {item.mode && item.status !== "blocked" && <span className="organize-badge">{MODE_LABELS[item.mode]}</span>}
      </div>
      {item.status === "blocked" &&
        item.reasons.map((r) => (
          <div key={r} className="notice error">
            {r}
          </div>
        ))}
      {item.status === "blocked" && item.needs_analysis.length > 0 && (
        <div className="organize-item-actions">
          <button type="button" onClick={onAnalyze} disabled={busy}>
            中の巻を調べる
          </button>
        </div>
      )}
      {item.status === "choose" &&
        item.reasons.map((r) => (
          <div key={r} className="notice warn">
            {r}
          </div>
        ))}
      {w && (
        <div className="inbox-work">
          <label className="inbox-field">
            作品
            <select aria-label={`${item.name} の作品`} value={choice.work} onChange={(e) => onChange({ ...choice, work: e.target.value })}>
              <option value="">{autoLabel}</option>
              {w.candidates.length > 0 && (
                <optgroup label="近い名前の作品">
                  {w.candidates.map((c) => (
                    <option key={c.work_id} value={String(c.work_id)}>
                      {c.title}
                    </option>
                  ))}
                </optgroup>
              )}
              <option value="new">新しい作品として入れる…</option>
              <optgroup label="ほかの作品">
                {works
                  .filter((x) => !candidateIds.has(x.id))
                  .map((x) => (
                    <option key={x.id} value={String(x.id)}>
                      {x.title}
                    </option>
                  ))}
              </optgroup>
            </select>
          </label>
          {choice.work === "new" && (
            <label className="inbox-field">
              作品名
              <span id={`inbox-title-hint-${item.source_id}`} className="organize-hint muted">(空欄なら「{w.title}」)</span>
              <input
                type="text"
                aria-label={`${item.name} の新しい作品名`}
                value={choice.newTitle}
                aria-describedby={`inbox-title-hint-${item.source_id}`}
                onChange={(e) => onChange({ ...choice, newTitle: e.target.value })}
              />
            </label>
          )}
          <div className="muted">{w.reason}</div>
        </div>
      )}
      {item.volumes.length > 0 && (
        <ul className="inbox-volumes">
          {item.volumes.map((v) => (
            <VolumeRow key={v.volume_id} volume={v} choice={choice} onChange={onChange} />
          ))}
        </ul>
      )}
      {item.warnings.map((m) => (
        <div key={m} className="notice warn">
          {m}
        </div>
      ))}
    </li>
  );
}

export function Inbox() {
  const [state, setState] = useState<InboxState | null>(null);
  const [works, setWorks] = useState<WorkSummary[]>([]);
  const [choices, setChoices] = useState<Record<number, Choice>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [history, setHistory] = useState<OrganizeOp[]>([]);
  const [results, setResults] = useState<InboxApproveResult[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [synced, setSynced] = useState<{ volumes: number; offline: string[] } | null>(null);
  const choicesRef = useRef(choices);
  choicesRef.current = choices;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const stateRef = useRef(state);
  stateRef.current = state;
  const timers = useRef<Record<number, number>>({});

  const loadHistory = useCallback(async () => {
    try {
      setHistory((await api.organizeHistory()).filter((o) => o.kind === "intake"));
    } catch {
      /* 履歴が読めなくても受け入れ自体は使える */
    }
  }, []);

  const show = useCallback((s: InboxState) => {
    setState(s);
    setSelected(new Set(s.items.filter((i) => i.status === "ready").map((i) => i.source_id)));
  }, []);

  useEffect(() => {
    api.inbox().then(show, (e) => setError(errorText(e, "受け入れの一覧を読み込めませんでした。")));
    api.listWorks().then((l) => setWorks(l.works), () => undefined);
    void loadHistory();
  }, [show, loadHistory]);

  const sync = async () => {
    setBusy("sync");
    setError(null);
    setResults(null);
    try {
      const s = await api.syncInbox();
      show(s);
      setChoices({});
      setSynced({ volumes: s.volumes ?? 0, offline: s.offline_roots ?? [] });
    } catch (e) {
      setError(errorText(e, "同期に失敗しました。"));
    } finally {
      setBusy(null);
    }
  };

  const replan = async (item: InboxItem, next: Choice) => {
    try {
      const fresh = (await api.previewInbox([decisionOf(item, next)])).items[0];
      // 返事を待つ間にさらに選び直されていたら、古い返事は捨てる
      if (choicesRef.current[item.source_id] !== next) return;
      setState((s) => (s ? { ...s, items: s.items.map((i) => (i.source_id === fresh.source_id ? fresh : i)) } : s));
      setSelected((sel) => {
        const out = new Set(sel);
        if (fresh.status === "ready") out.add(fresh.source_id);
        else out.delete(fresh.source_id);
        return out;
      });
    } catch (e) {
      setError(errorText(e, "整理案を作り直せませんでした。"));
    }
  };

  /** 選び直したら、その 1 件の整理案だけを作り直す(何も動かさない)。入力の途中で何度も呼ばないよう少し待つ */
  const change = (item: InboxItem, next: Choice) => {
    setChoices((c) => ({ ...c, [item.source_id]: next }));
    window.clearTimeout(timers.current[item.source_id]);
    timers.current[item.source_id] = window.setTimeout(() => void replan(item, next), 250);
  };

  const analyze = async (item: InboxItem) => {
    setBusy("analyze");
    setError(null);
    try {
      for (const id of item.needs_analysis) await api.analyze(id);
      const fresh = (await api.previewInbox([decisionOf(item, choices[item.source_id])])).items[0];
      setState((s) => (s ? { ...s, items: s.items.map((i) => (i.source_id === fresh.source_id ? fresh : i)) } : s));
    } catch (e) {
      setError(errorText(e, "中の巻を調べられませんでした。"));
    } finally {
      setBusy(null);
    }
  };

  /** 受け入れ待ちを読み直す。まだ承認していない本は、利用者が選んだ内容(作品・番号・取り込まない)と選択をそのまま残す */
  const reload = async (keep: Record<number, Choice>) => {
    const fresh = await api.inbox();
    const withChoice = fresh.items.filter((i) => keep[i.source_id]);
    let items = fresh.items;
    if (withChoice.length > 0) {
      const redone = new Map(
        (await api.previewInbox(withChoice.map((i) => decisionOf(i, keep[i.source_id])))).items.map((i) => [i.source_id, i]),
      );
      items = fresh.items.map((i) => redone.get(i.source_id) ?? i);
    }
    const had = new Set(selectedRef.current);
    const known = new Set((stateRef.current?.items ?? []).map((i) => i.source_id));
    setState({ ...fresh, items });
    // 前から並んでいた本は選んでいたままに、新しく出てきた本(元に戻したもの)は承認できれば選ぶ
    setSelected(new Set(items.filter((i) => i.status === "ready" && (had.has(i.source_id) || !known.has(i.source_id))).map((i) => i.source_id)));
  };

  const chosen = (state?.items ?? []).filter((i) => selected.has(i.source_id) && i.status === "ready");

  const approve = async () => {
    setBusy("approve");
    setError(null);
    try {
      const res = await api.approveInbox(chosen.map((i) => ({ ...decisionOf(i, choices[i.source_id]), expect_outputs: i.expect_outputs })));
      setResults(res.results);
      const keep = { ...choicesRef.current };
      for (const r of res.results) if (r.ok) delete keep[r.source_id];
      setChoices(keep);
      await reload(keep);
      await loadHistory();
    } catch (e) {
      setError(errorText(e, "承認に失敗しました。"));
    } finally {
      setBusy(null);
    }
  };

  const undo = async (opId: string) => {
    setBusy(opId);
    setError(null);
    try {
      await api.undoOrganize(opId);
      await reload(choicesRef.current);
    } catch (e) {
      setError(errorText(e, "元に戻せませんでした。"));
    } finally {
      setBusy(null);
      await loadHistory();
    }
  };

  return (
    <div className="organize inbox">
      <p>
        <a href={libraryHref}>← 一覧へ</a>
      </p>
      <h1>受け入れ</h1>
      <p className="muted">
        受け入れフォルダに置いた本を「同期」で見つけ、整理案(書庫のどこに何という名前で入るか)を確かめてから、選んだものだけを書庫へ入れます。同期と整理案では何も動かしません。元は退避フォルダへ移すだけで消さず、履歴から元に戻せます。
      </p>
      {state?.inboxes.length === 0 && (
        <div className="notice warn" role="status">
          受け入れフォルダが設定されていません。設定ファイルの <code>inbox_dirs</code> に受け入れフォルダを書いてください。
        </div>
      )}
      {state?.inboxes.map((b) => (
        <div key={b.path} className="inbox-info">
          <code>{b.path}</code> → 書庫 <code>{b.library}</code>
          {b.problems.map((p) => (
            <div key={p} className="notice error" role="alert">
              {p}
            </div>
          ))}
        </div>
      ))}
      <div className="organize-actions">
        <button type="button" onClick={sync} disabled={busy !== null || state?.inboxes.length === 0}>
          {busy === "sync" ? "同期中…" : "同期"}
        </button>
        <a href={worksHref}>作品へ</a>
        <a href={organizeHistoryHref}>整理の履歴へ</a>
      </div>
      {synced && (
        <div className="notice" role="status">
          受け入れフォルダで {synced.volumes} 巻を検出しました。
          {synced.offline.length > 0 && <> 見えない受け入れフォルダ: {synced.offline.join("、")}</>}
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {results && (
        <ul className="inbox-results" aria-label="承認の結果">
          {results.map((r) => (
            <li key={r.source_id} className={r.ok ? "notice" : "notice error"} role={r.ok ? "status" : "alert"}>
              <strong>{r.item.name}</strong>:{" "}
              {r.ok
                ? `書庫に入れました${r.op ? `(${r.op.volumes.map((v) => v.output_rel_path).join("、")})` : ""}${r.warning ? ` — ${r.warning}` : ""}`
                : `取り込めませんでした。受け入れフォルダに残っています — ${r.error}`}
            </li>
          ))}
        </ul>
      )}

      {state === null && !error && <p className="muted">読み込み中…</p>}
      {state && state.items.length === 0 && state.inboxes.length > 0 && (
        <p className="muted">受け入れ待ちの本はありません。受け入れフォルダに本を置いて「同期」を押してください。</p>
      )}
      <ul className="inbox-list">
        {state?.items.map((item) => (
          <ItemCard
            key={item.source_id}
            item={item}
            choice={choices[item.source_id] ?? emptyChoice()}
            works={works}
            selected={selected.has(item.source_id)}
            busy={busy !== null}
            onSelect={(on) =>
              setSelected((sel) => {
                const out = new Set(sel);
                if (on) out.add(item.source_id);
                else out.delete(item.source_id);
                return out;
              })
            }
            onChange={(c) => change(item, c)}
            onAnalyze={() => void analyze(item)}
          />
        ))}
      </ul>
      {state && state.items.length > 0 && (
        <div className="organize-actions inbox-approve">
          <button type="button" onClick={approve} disabled={busy !== null || chosen.length === 0}>
            {busy === "approve" ? "書庫へ入れています…" : `承認して書庫へ(${chosen.length} 件)`}
          </button>
          <span className="muted">選ばなかった本は受け入れフォルダに残り、次の同期でも出ます。</span>
        </div>
      )}

      <h2>受け入れの履歴</h2>
      {history.length === 0 && <p className="muted">まだ受け入れていません。</p>}
      <ul className="organize-op-list">
        {history.map((op) => (
          <li key={op.op_id ?? op.source_rel_path} className="organize-op">
            <p>
              <strong>{STATE_LABELS[op.state]}</strong> <span className="muted">{op.executed_at ?? op.created_at}</span>
            </p>
            <p>
              <code>{op.source_rel_path}</code> → {op.mode === "move" ? "そのまま書庫へ移動" : "CBZ にして書庫へ"}
            </p>
            <ul className="organize-volumes">
              {op.volumes.map((v) => (
                <li key={v.volume_id}>
                  {v.title}
                  {v.output_rel_path && (
                    <>
                      {" "}→ <code>{v.output_rel_path}</code>
                    </>
                  )}
                </li>
              ))}
            </ul>
            {op.error && (
              <div className="notice error" role="alert">
                {op.error}
              </div>
            )}
            {op.state === "done" && op.op_id && (
              <div className="organize-actions">
                <button type="button" onClick={() => undo(op.op_id!)} disabled={busy !== null}>
                  {busy === op.op_id ? "元に戻し中…" : "元に戻す"}
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
