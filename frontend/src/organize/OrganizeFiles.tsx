// 範囲指定の整理(#84)。蔵書フォルダの中の範囲を選び、ファイル名テンプレート・フォルダーテンプレート(空ならファイル名の整理だけ)で
// 改名・移動をまとめてプレビュー → 実行 → 1 回分をまとめて元に戻す。蔵書に書き込むのは「実行」「元に戻す」の明示操作だけ。
//
// データ管理ページ(#83。frontend/src/data/DataJobs.tsx)の「整理」節に置く部品として OrganizeFilesSection を export する。
// 単独の入口 #/data/organize は同じページの「整理」節へ移す。
//
// 画面の構成(#149): 1. 対象 → 2. 名前の決め方(任意) → 3. プレビューして実行、の 3 段に分ける。
// パラメーターは入力欄ごとの「項目を差し込む」から入れる(最後にフォーカスした欄へ入れる規則は廃止)。
// プレビューの結果は見出しへフォーカスとスクロールを移し、実行のボタンを 1 か所にまとめる。
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  ApiError,
  api,
  type OrganizeItem,
  type OrganizeItemStatus,
  type OrganizeOp,
  type RangeExample,
  type RangeFolders,
  type RangeOptions,
} from "../api";
import { organizeHistoryHref, organizeTemplateHref } from "../route";
import { ITEM_LABELS, STATE_LABELS } from "./Organize";

const STORAGE_KEY = "spinebox.organizeFiles";
const STATUS_ORDER: OrganizeItemStatus[] = ["move", "cbz", "conflict", "skipped", "excluded", "unchanged"];

interface Saved {
  rootId?: number;
  path?: string;
  fileTemplate?: string;
  folderTemplate?: string;
  includeCbz?: boolean;
}

function loadSaved(): Saved {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Saved;
  } catch {
    return {};
  }
}

function save(value: Saved) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 保存できなくても画面は動く(前回の入力を覚えないだけ)
  }
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

function pagesText(item: OrganizeItem): string | null {
  return item.page_count === null || item.page_count === undefined ? null : `${item.page_count} ファイル`;
}

/** 実行前の計画の内訳を 1 文にする */
function countsText(op: OrganizeOp): string {
  const items = op.items ?? [];
  const n = (s: OrganizeItemStatus) => items.filter((it) => it.status === s).length;
  const parts: string[] = [];
  if (n("move")) parts.push(`${n("move")} 巻を改名・移動`);
  if (n("cbz")) parts.push(`${n("cbz")} 巻を CBZ 化`);
  if (n("conflict")) parts.push(`${n("conflict")} 巻は名前が衝突`);
  if (n("excluded")) parts.push(`${n("excluded")} 巻は対象外`);
  if (n("skipped")) parts.push(`${n("skipped")} 巻は外した`);
  if (n("unchanged")) parts.push(`${n("unchanged")} 巻はそのまま`);
  return parts.join("、");
}

function RangeItemRow({ item, onExclude }: { item: OrganizeItem; onExclude?: (volumeId: number) => void }) {
  return (
    <li className={`organize-item ${item.status}`}>
      <span className={`organize-badge ${item.status}`}>{ITEM_LABELS[item.status]}</span> {item.title}
      <div className="organize-paths">
        <code>{item.from_rel_path}</code>
        {(item.status === "move" || item.status === "cbz" || item.status === "conflict") && item.output_rel_path && (
          <>
            {" "}→ <code>{item.output_rel_path}</code>
          </>
        )}
      </div>
      {(item.changes?.length ?? 0) > 0 && <div className="muted organize-changes">{item.changes!.join("・")}</div>}
      {item.reason && item.status !== "move" && item.status !== "cbz" && <div className="muted">{item.reason}</div>}
      {onExclude && (item.status === "conflict" || item.status === "move" || item.status === "cbz" || item.cbz_volume_id !== null) && (
        <div className="organize-item-actions">
          {(item.status === "conflict" || item.status === "move" || item.status === "cbz") && (
            <button type="button" className="small secondary" onClick={() => onExclude(item.volume_id)}>
              {item.conversion ? "この元を外す" : "この巻を外す"}
            </button>
          )}
          {item.cbz_volume_id !== null && <a href={organizeTemplateHref(item.cbz_volume_id)}>テンプレートの名前で CBZ に整理</a>}
        </div>
      )}
    </li>
  );
}

/** 整理後の名前(正規化した名前)の組。同じ名前になる巻が複数あれば衝突の組 */
interface NameGroup {
  name: string;
  items: OrganizeItem[];
}

const nameKey = (it: OrganizeItem) => (it.output_rel_path ? it.output_rel_path.toLowerCase() : `volume:${it.volume_id}`);

function groupByOutputName(items: OrganizeItem[]): NameGroup[] {
  const groups = new Map<string, NameGroup>();
  for (const it of items) {
    const key = nameKey(it);
    const group = groups.get(key) ?? { name: it.output_rel_path ?? it.title, items: [] };
    group.items.push(it);
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** 整理後の名前の下に、元のファイル名を並べる */
function NameTree({
  groups,
  onExclude,
  onKeep,
}: {
  groups: NameGroup[];
  onExclude?: (volumeId: number) => void;
  onKeep?: (dropIds: number[]) => void;
}) {
  return (
    <ul className="organize-groups">
      {groups.map((group) => (
        <li key={group.name} className="organize-group">
          <div className="organize-group-name">{group.name}</div>
          <ul className="organize-tree">
            {group.items.map((item) => {
              const pages = pagesText(item);
              const superseded = (item.superseded_by?.length ?? 0) > 0;
              const active = item.status === "conflict" || item.status === "move" || item.status === "cbz";
              return (
                <li key={item.volume_id} className={`organize-item ${item.status}`}>
                  <div className="organize-tree-body">
                    <span className={`organize-badge ${item.status}`}>{ITEM_LABELS[item.status]}</span>{" "}
                    <code>{item.from_rel_path}</code>
                    {pages && <span className="muted"> ({pages})</span>}
                    {(item.changes?.length ?? 0) > 0 && <div className="muted organize-changes">{item.changes!.join("・")}</div>}
                    {item.reason && item.status !== "move" && item.status !== "cbz" && <div className="muted">{item.reason}</div>}
                    {onExclude && (active || superseded) && (
                      <div className="organize-item-actions">
                        {active && (
                          <button type="button" className="small secondary" onClick={() => onExclude(item.volume_id)}>
                            {item.conversion ? "この元を外す" : "この巻を外す"}
                          </button>
                        )}
                        {superseded && onKeep && (
                          <button
                            type="button"
                            className="small secondary"
                            onClick={() =>
                              // 組のほかの巻をすべて外す(自動で外した巻も。ファイル数の多い別の巻に、また譲らないように)
                              onKeep(
                                group.items
                                  .filter((o) => o.volume_id !== item.volume_id && !(o.status === "skipped" && !o.superseded_by?.length))
                                  .map((o) => o.volume_id),
                              )
                            }
                          >
                            {item.conversion ? "この元を残す" : "こちらを残す"}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** 計画の各巻を扱いごとに分けて出す。動かす・衝突・外したは開いておき、対象外・そのままは畳む。
 *  同じ名前になる巻の組(衝突と、ファイル数の多い方に譲って外した巻)は「衝突」に、CBZ 化は、
 *  整理後の名前ごとに元のファイル名をぶら下げて出す(#143) */
export function RangeItems({
  op,
  onExclude,
  onKeep,
}: {
  op: OrganizeOp;
  onExclude?: (volumeId: number) => void;
  /** 「こちらを残す」: 同じ名前の組のほかの巻を外して計画を作り直す */
  onKeep?: (dropIds: number[]) => void;
}) {
  const items = op.items ?? [];
  const clashNames = new Set(
    items.filter((it) => it.status === "conflict" || (it.superseded_by?.length ?? 0) > 0).map(nameKey),
  );
  // 衝突の組には、名前が重なった相手(残す巻・そのままの巻)も入れる
  const clashItems = items.filter((it) => clashNames.has(nameKey(it)) && it.status !== "excluded");
  const clashGroups = groupByOutputName(clashItems);
  const inClash = new Set(clashItems.map((it) => it.volume_id));
  const rest = items.filter((it) => !inClash.has(it.volume_id));
  return (
    <>
      <p className="organize-counts">
        {STATUS_ORDER.map((s) => {
          const n = items.filter((it) => it.status === s).length;
          return n ? (
            <span key={s} className={`organize-badge ${s}`}>
              {ITEM_LABELS[s]} {n}
            </span>
          ) : null;
        })}
      </p>
      {STATUS_ORDER.map((s) => {
        if (s === "conflict") {
          return clashGroups.length ? (
            <section key={s} className="organize-list-section">
              <h4 className="organize-subhead">衝突({clashGroups.length} 組)</h4>
              <p className="muted organize-hint">
                同じ名前になる巻です。ファイル数の多い方を残し、少ない方は外しました。同数の巻は衝突のままなので、どちらかを外してください。
              </p>
              <NameTree groups={clashGroups} onExclude={onExclude} onKeep={onKeep} />
            </section>
          ) : null;
        }
        const list = rest.filter((it) => it.status === s);
        if (!list.length) return null;
        const rows =
          s === "cbz" ? (
            <NameTree groups={groupByOutputName(list)} onExclude={onExclude} />
          ) : (
            <ul className="organize-volumes">
              {list.map((it) => (
                <RangeItemRow key={it.volume_id} item={it} onExclude={onExclude} />
              ))}
            </ul>
          );
        return s === "excluded" || s === "unchanged" ? (
          <details key={s} className="organize-fold">
            <summary>
              {ITEM_LABELS[s]}({list.length} 巻)
            </summary>
            {rows}
          </details>
        ) : (
          <section key={s} className="organize-list-section">
            <h4 className="organize-subhead">
              {ITEM_LABELS[s]}({list.length} 巻)
            </h4>
            {rows}
          </section>
        );
      })}
    </>
  );
}

/** 入力欄の直下に置く「項目を差し込む」。押した欄へ入る(最後のフォーカスに依存しない) */
function ParameterChips({
  target,
  label,
  parameters,
  onInsert,
}: {
  target: "file" | "folder";
  label: string;
  parameters: RangeOptions["parameters"];
  onInsert: (token: string, target: "file" | "folder") => void;
}) {
  return (
    <div className="organize-insert" role="group" aria-label={label}>
      <span className="organize-insert-label">項目を差し込む</span>
      <div className="organize-chips">
        {parameters.map((p) => (
          <button
            key={p.token}
            type="button"
            className="small chip"
            // 押しても入力欄からカーソル(選択)を外さない
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onInsert(p.token, target)}
            title={p.token}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * 範囲のフォルダーを選ぶモーダル。開いたら見出しへフォーカスし、Esc で閉じ、閉じたら開いたボタンへ戻す。
 * 背後の要素へ Tab で抜けない(focus trap)。今のパスから開き、読めなければ蔵書フォルダ全体へ戻す。
 */
function FolderPicker({
  rootId,
  initialPath,
  onPick,
  onClose,
}: {
  rootId: number;
  initialPath: string;
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<RangeFolders | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const requestSeq = useRef(0);

  const open = useCallback(
    async (path: string) => {
      const seq = ++requestSeq.current;
      setBusy(true);
      setError(null);
      try {
        const next = await api.rangeFolders(rootId, path);
        if (seq === requestSeq.current) setListing(next);
      } catch (e) {
        if (seq !== requestSeq.current) return;
        const message = errorText(e, "フォルダーを読めませんでした。");
        if (path !== "") {
          // 前回のパスが無くなっていたら、蔵書フォルダ全体から選び直せるようにする
          setError(`${message} 蔵書フォルダ全体を表示しています。`);
          try {
            const whole = await api.rangeFolders(rootId, "");
            if (seq === requestSeq.current) setListing(whole);
          } catch {
            if (seq === requestSeq.current) setListing(null);
          }
        } else {
          setError(message);
          setListing(null);
        }
      } finally {
        if (seq === requestSeq.current) setBusy(false);
      }
    },
    [rootId],
  );

  useEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    void open(initialPath);
  }, [open, initialPath]);

  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener.current?.focus?.();
    };
  }, [onClose]);

  // Tab でモーダルの外へ出さない(背後のフォームへフォーカスを移さない)
  const trap = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !dialog.current) return;
    const items = [...dialog.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled])")];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const path = listing?.path ?? initialPath;
  const segments = path.split("/").filter(Boolean);
  const join = (name: string) => (listing?.path ? `${listing.path}/${name}` : name);

  return (
    <div className="organize-picker-backdrop" onClick={onClose}>
      <div
        ref={dialog}
        className="organize-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="organize-picker-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trap}
      >
        <h2 id="organize-picker-title" ref={heading} tabIndex={-1}>
          フォルダーを選ぶ
        </h2>
        <p className="organize-hint">整理する範囲のフォルダーを開いて選びます。</p>
        <nav aria-label="今のフォルダー">
          <ol className="organize-breadcrumb">
            <li>
              {segments.length === 0 ? (
                <span aria-current="location">蔵書フォルダ全体</span>
              ) : (
                <button type="button" className="link-button" onClick={() => void open("")}>
                  蔵書フォルダ全体
                </button>
              )}
            </li>
            {segments.map((seg, i) => {
              const sub = segments.slice(0, i + 1).join("/");
              return (
                <li key={sub}>
                  <span className="organize-breadcrumb-sep" aria-hidden="true">/</span>
                  {i === segments.length - 1 ? (
                    <span aria-current="location">{seg}</span>
                  ) : (
                    <button type="button" className="link-button" onClick={() => void open(sub)}>
                      {seg}
                    </button>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {busy && <p className="muted" role="status">読み込み中…</p>}
        {listing && (
          <ul className="organize-folder-list">
            {listing.parent !== null && (
              <li>
                <button type="button" className="organize-folder-item" onClick={() => void open(listing.parent!)}>
                  <span className="organize-folder-mark" aria-hidden="true">↑</span> 1 つ上へ
                </button>
              </li>
            )}
            {listing.folders.map((name) => (
              <li key={name}>
                <button type="button" className="organize-folder-item" onClick={() => void open(join(name))}>
                  <span className="organize-folder-mark" aria-hidden="true">▸</span> {name}
                </button>
              </li>
            ))}
            {listing.folders.length === 0 && <li className="organize-picker-empty">この中にフォルダーはありません</li>}
          </ul>
        )}
        <div className="organize-picker-actions">
          {listing && (
            <button type="button" onClick={() => onPick(listing.path)}>
              このフォルダーにする
            </button>
          )}
          <button type="button" className="secondary" onClick={onClose}>
            キャンセル
          </button>
        </div>
      </div>
    </div>
  );
}

/** データ管理ページの「整理」節の中身(見出しは置く側が付ける) */
export function OrganizeFilesSection() {
  const saved = useRef(loadSaved()).current;
  const [options, setOptions] = useState<RangeOptions | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [rootId, setRootId] = useState<number | null>(saved.rootId ?? null);
  const [path, setPath] = useState(saved.path ?? "");
  const [fileTemplate, setFileTemplate] = useState(saved.fileTemplate ?? "");
  const [folderTemplate, setFolderTemplate] = useState(saved.folderTemplate ?? "");
  const [includeCbz, setIncludeCbz] = useState(saved.includeCbz ?? false);
  const [advancedOpen, setAdvancedOpen] = useState(Boolean(saved.folderTemplate));
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLSelectElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusResult = useRef(false);
  const opRef = useRef<OrganizeOp | null>(null);
  const exampleSeq = useRef(0);
  const [examples, setExamples] = useState<RangeExample[]>([]);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"plan" | "execute" | "undo" | null>(null);
  const [cleared, setCleared] = useState(false);

  const loadOptions = useCallback((preferred: number | null) => {
    setOptionsError(null);
    api.rangeOptions().then(
      (o) => {
        setOptions(o);
        // 前回の蔵書フォルダが無くなっていたら最初のものにし、そのフォルダに合わない範囲は消す
        if (preferred !== null && o.roots.some((r) => r.id === preferred)) {
          setRootId(preferred);
        } else {
          setRootId(o.roots[0]?.id ?? null);
          setPath("");
        }
      },
      (e) => setOptionsError(errorText(e, "整理の選択肢を読み込めませんでした。")),
    );
  }, []);

  useEffect(() => {
    loadOptions(saved.rootId ?? null);
  }, [loadOptions, saved.rootId]);

  useEffect(() => {
    save({ rootId: rootId ?? undefined, path, fileTemplate, folderTemplate, includeCbz });
  }, [rootId, path, fileTemplate, folderTemplate, includeCbz]);

  useEffect(() => {
    opRef.current = op;
  }, [op]);

  // 入力を変えたら前のプレビューは使えない(実行させない)。何が起きたか分かるように 1 行残す
  useEffect(() => {
    if (opRef.current) setCleared(true);
    setOp(null);
    setExcluded([]);
  }, [rootId, path, fileTemplate, folderTemplate, includeCbz]);

  // パスの例とテンプレートの誤り。打つたびに呼ばないよう少し待つ。古い応答で新しい入力を上書きしない
  useEffect(() => {
    const seq = ++exampleSeq.current;
    const timer = window.setTimeout(() => {
      api.rangeExample(fileTemplate, folderTemplate, path).then(
        (r) => {
          if (seq !== exampleSeq.current) return;
          setExamples(r.examples);
          setTemplateError(r.error);
        },
        () => undefined,
      );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [fileTemplate, folderTemplate, path]);

  /** カーソル位置にパラメーターを入れる(選択中の文字は置き換える)。入れる欄は呼び出し側が決める */
  const insert = (token: string, target: "file" | "folder") => {
    const el = target === "file" ? fileRef.current : folderRef.current;
    const value = target === "file" ? fileTemplate : folderTemplate;
    const set = target === "file" ? setFileTemplate : setFolderTemplate;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    set(value.slice(0, start) + token + value.slice(end));
    window.requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const applyTemplates = (file: string, folder: string) => {
    setFileTemplate(file);
    setFolderTemplate(folder);
    if (folder) setAdvancedOpen(true);
  };

  const plan = async (exclude: number[], focus: boolean) => {
    if (rootId === null || busy !== null) return;
    setBusy("plan");
    setError(null);
    setCleared(false);
    try {
      const next = await api.planRange({
        root_id: rootId,
        path,
        file_template: fileTemplate,
        folder_template: folderTemplate,
        exclude_volume_ids: exclude,
        // false のときは今までと同じ形の要求にする(このオプションを足す前と API の使い方を変えない)
        ...(includeCbz ? { include_cbz: true } : {}),
      });
      setOp(next);
      setExcluded(exclude);
      if (focus) focusResult.current = true;
    } catch (e) {
      setOp(null);
      setError(errorText(e, "プレビューを作れませんでした。"));
    } finally {
      setBusy(null);
    }
  };

  const run = async (kind: "execute" | "undo") => {
    if (!op?.op_id || busy !== null) return;
    setBusy(kind);
    setError(null);
    try {
      const next = await (kind === "execute" ? api.executeOrganize(op.op_id) : api.undoOrganize(op.op_id));
      if (kind === "execute") setExcluded([]);
      focusResult.current = true;
      setOp(next);
    } catch (e) {
      setError(errorText(e, kind === "execute" ? "整理に失敗しました。" : "元に戻せませんでした。"));
      if (e instanceof ApiError && e.status === 409) {
        const id = op.op_id;
        api.organizeHistory().then((h) => setOp(h.find((x) => x.op_id === id) ?? op), () => undefined);
      }
    } finally {
      setBusy(null);
    }
  };

  // プレビュー・実行のあとは結果の見出しへ移す(ボタンの下に出るだけだと、結果に気づけない)
  useEffect(() => {
    if (!op || !focusResult.current) return;
    focusResult.current = false;
    resultRef.current?.scrollIntoView?.({ block: "start" });
    resultHeadingRef.current?.focus?.({ preventScroll: true });
  }, [op]);

  const preview = () => {
    if (busy !== null) return;
    // 誤りがあるときは通信せず、直す欄へフォーカスする(disabled で塞がない)
    if (templateError) {
      fileRef.current?.focus();
      return;
    }
    void plan(excluded, true);
  };

  const scrollToForm = () => {
    formRef.current?.scrollIntoView?.({ block: "start" });
    // キーボードでも条件を直し始められるよう、先頭の欄へ移す
    rootRef.current?.focus?.({ preventScroll: true });
  };

  const closePicker = useCallback(() => setPicker(false), []);

  if (options === null) {
    return (
      <div className="organize-files">
        {optionsError ? (
          <div className="notice error" role="alert">
            <p>{optionsError}</p>
            <button type="button" className="secondary" onClick={() => loadOptions(rootId)}>
              読み込み直す
            </button>
          </div>
        ) : (
          <p className="muted" role="status">整理の設定を読み込んでいます…</p>
        )}
      </div>
    );
  }

  if (options.roots.length === 0) {
    return (
      <div className="organize-files">
        <p className="notice">
          蔵書フォルダがありません。サーバー機の設定ファイル(config.toml)に蔵書フォルダを追加してから、もう一度開いてください。
        </p>
      </div>
    );
  }

  const root = options.roots.find((r) => r.id === rootId) ?? options.roots[0];
  const planned = op?.state === "planned";
  const canEditPlan = planned || op?.state === "blocked";
  const previewLabel = busy === "plan" ? "プレビューを作っています…" : "プレビュー";

  return (
    <div className="organize-files">
      <p className="organize-lead">
        ファイル名の特殊記号・全角と半角の揺れ・文字化けを整理します。テンプレートで名前の変更とフォルダーへの振り分けもできます。
        蔵書を動かすのでジョブには入れず、まずプレビューで確認してから実行します(実行した 1 回分はまとめて元に戻せます。元のファイルは消さず、中身も変えません)。
      </p>

      <form
        ref={formRef}
        className="organize-form"
        aria-busy={busy !== null || undefined}
        onSubmit={(e) => {
          e.preventDefault();
          preview();
        }}
      >
        <fieldset className="organize-step-group">
          <legend className="organize-step-group-title">
            <span className="organize-step" aria-hidden="true">1</span>対象を選ぶ
          </legend>
          <div className="organize-step-group-body">
            <div className="organize-field">
              <label htmlFor="organize-root">蔵書フォルダ</label>
              <select
                id="organize-root"
                ref={rootRef}
                value={rootId ?? ""}
                onChange={(e) => {
                  setRootId(Number(e.target.value));
                  // 別の蔵書フォルダでは前の範囲は無いので、全体から選び直す
                  setPath("");
                }}
                disabled={busy !== null}
              >
                {options.roots.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.path}
                    {r.online ? "" : "(オフライン)"}
                  </option>
                ))}
              </select>
              {root && !root.online && (
                <p className="notice warn">この蔵書フォルダは今オフラインです。接続してからプレビューしてください。</p>
              )}
            </div>
            <div className="organize-field">
              <label htmlFor="organize-path">範囲のパス</label>
              <p id="organize-path-hint" className="organize-hint">
                空欄なら蔵書フォルダ全体。サブフォルダーを指定すると、その中だけが対象になります(フォルダーテンプレートはこのパスが起点です)。
              </p>
              <div className="organize-inline">
                <input
                  id="organize-path"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  aria-describedby="organize-path-hint"
                  disabled={busy !== null}
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setPicker((x) => !x)}
                  aria-haspopup="dialog"
                  disabled={busy !== null}
                >
                  フォルダーを選ぶ
                </button>
              </div>
            </div>
          </div>
        </fieldset>

        <fieldset className="organize-step-group">
          <legend className="organize-step-group-title">
            <span className="organize-step" aria-hidden="true">2</span>名前の決め方(任意)
          </legend>
          <div className="organize-step-group-body">
            <p className="organize-hint">
              両方とも空欄なら、今の名前を整えるだけです。拡張子は元のまま残ります。
            </p>
            <div className="organize-presets" role="group" aria-label="よく使う設定">
              <span className="organize-label">よく使う設定</span>
              <p className="organize-hint">選ぶと、下の 2 つのテンプレートの欄に入ります。</p>
              <div className="organize-chips">
                <button type="button" className="small secondary" onClick={() => applyTemplates("", "")}>
                  ファイル名の整理だけ
                </button>
                <button
                  type="button"
                  className="small secondary"
                  onClick={() => applyTemplates(options.defaults.file_template, options.defaults.folder_template)}
                >
                  設定のテンプレート
                </button>
                {options.presets.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    className="small secondary"
                    onClick={() => applyTemplates(p.file_template, p.folder_template)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="organize-field">
              <label htmlFor="organize-file-template">ファイル名テンプレート</label>
              <p id="organize-file-template-hint" className="organize-hint">空欄なら今の名前を整理するだけです。</p>
              <input
                id="organize-file-template"
                ref={fileRef}
                value={fileTemplate}
                onChange={(e) => setFileTemplate(e.target.value)}
                aria-describedby={templateError ? "organize-template-error organize-file-template-hint" : "organize-file-template-hint"}
                aria-invalid={templateError !== null || undefined}
                disabled={busy !== null}
              />
              <ParameterChips
                target="file"
                label="ファイル名テンプレートに項目を差し込む"
                parameters={options.parameters}
                onInsert={insert}
              />
            </div>

            <details
              className="organize-advanced"
              open={advancedOpen}
              onToggle={(e) => setAdvancedOpen(e.currentTarget.open)}
            >
              <summary>
                フォルダーへ振り分ける(任意)
                <span className="organize-summary-state">
                  {folderTemplate ? `: ${folderTemplate}` : ": 元のフォルダのまま"}
                </span>
              </summary>
              <div className="organize-advanced-body">
                <div className="organize-field">
                  <label htmlFor="organize-folder-template">フォルダーテンプレート</label>
                  <p id="organize-folder-template-hint" className="organize-hint">
                    空欄なら同じ場所で名前だけ変更します。拡張子は元のまま。/ でフォルダーを区切ります。[ ] の中は、項目が空のとき全体が省略されます(角括弧そのものは [[ ]])。
                  </p>
                  <input
                    id="organize-folder-template"
                    ref={folderRef}
                    value={folderTemplate}
                    onChange={(e) => setFolderTemplate(e.target.value)}
                    aria-describedby={templateError ? "organize-template-error organize-folder-template-hint" : "organize-folder-template-hint"}
                    aria-invalid={templateError !== null || undefined}
                    disabled={busy !== null}
                  />
                  <ParameterChips
                    target="folder"
                    label="フォルダーテンプレートに項目を差し込む"
                    parameters={options.parameters}
                    onInsert={insert}
                  />
                </div>
              </div>
            </details>

            {templateError ? (
              <div className="notice error" role="alert">
                <span id="organize-template-error">{templateError}</span>
              </div>
            ) : (
              examples.length > 0 && (
                <div className="organize-examples">
                  <span className="organize-label">この設定でできる名前の例</span>
                  <ul>
                    {examples.map((ex) => (
                      <li key={ex.label}>
                        <span className="muted">{ex.label}: </span>
                        {ex.path ? <code>{ex.path}</code> : <span className="muted">対象外({ex.reason})</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )
            )}
          </div>
        </fieldset>

        <div className="organize-run">
          <div className="organize-run-text">
            <h3 className="organize-run-title">
              <span className="organize-step" aria-hidden="true">3</span>プレビューして実行
            </h3>
            <p className="organize-hint">プレビューでは蔵書を変更しません。結果を見てから実行してください。</p>
            <label className="organize-check" htmlFor="organize-include-cbz">
              <input
                id="organize-include-cbz"
                type="checkbox"
                checked={includeCbz}
                onChange={(e) => setIncludeCbz(e.target.checked)}
                aria-describedby="organize-include-cbz-hint"
                disabled={busy !== null}
              />
              <span>CBZ 化もまとめて行う</span>
            </label>
            <p id="organize-include-cbz-hint" className="organize-hint">
              1 巻 = 1 CBZ でない元(複数巻のまとめ・入れ子の巻・画像フォルダ・CBZ でないアーカイブ)も、読める巻ごとにテンプレートの名前の .cbz にします。小説(EPUB・テキスト)は対象外です。
            </p>
          </div>
          <div className="organize-run-actions">
            <button type="button" onClick={preview} disabled={busy !== null} className={planned ? "secondary" : undefined}>
              {previewLabel}
            </button>
          </div>
        </div>
      </form>

      {cleared && !op && (
        <p className="organize-cleared" role="status">
          条件を変えたので、前のプレビューを取り消しました。もう一度プレビューしてください。
        </p>
      )}

      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      {op && (
        <div ref={resultRef} className={`organize-result ${op.state}`}>
          <div className="organize-result-head">
            <h3 ref={resultHeadingRef} tabIndex={-1}>
              {op.state === "planned" || op.state === "blocked" ? "プレビュー結果" : "実行結果"}
            </h3>
            <span className={`organize-state ${op.state}`}>{STATE_LABELS[op.state]}</span>
          </div>
          {countsText(op) && <p className="organize-result-summary">{countsText(op)}。</p>}
          {op.state === "done" && (
            <div className="notice success" role="status">
              整理しました。元に戻すと元の場所と名前に戻ります。
            </div>
          )}
          {op.state === "undone" && (
            <div className="notice" role="status">
              元に戻しました。
            </div>
          )}
          {op.problems.map((p) => (
            <div key={p} className="notice error" role="alert">
              {p}
            </div>
          ))}
          {op.warnings.map((w) => (
            <div key={w} className="notice warn">
              {w}
            </div>
          ))}
          {op.error && (
            <div className="notice error" role="alert">
              {op.error}
            </div>
          )}
          <div className="organize-result-actions">
            <button type="button" className="link-button" onClick={scrollToForm}>
              条件を変える
            </button>
            {op.state !== "planned" && op.state !== "blocked" && <a href={organizeHistoryHref}>整理の履歴へ</a>}
            {excluded.length > 0 && (op.state === "planned" || op.state === "blocked") && (
              <button type="button" className="secondary" onClick={() => void plan([], false)} disabled={busy !== null}>
                外した巻を戻す
              </button>
            )}
            {op.state === "done" && (
              <button type="button" className="secondary" onClick={() => void run("undo")} disabled={busy !== null}>
                {busy === "undo" ? "元に戻し中…" : "まとめて元に戻す"}
              </button>
            )}
            {planned && (
              <button type="button" className="organize-commit" onClick={() => void run("execute")} disabled={busy !== null}>
                {busy === "execute" ? "実行中…" : `実行(${op.volumes.length} 巻)`}
              </button>
            )}
          </div>
          <RangeItems
            op={op}
            onExclude={canEditPlan ? (id) => void plan([...new Set([...excluded, id])], false) : undefined}
            onKeep={canEditPlan ? (drop) => void plan([...new Set([...excluded, ...drop])], false) : undefined}
          />
        </div>
      )}

      {picker && rootId !== null && (
        <FolderPicker
          rootId={rootId}
          initialPath={path}
          onPick={(p) => {
            setPath(p);
            setPicker(false);
          }}
          onClose={closePicker}
        />
      )}
    </div>
  );
}
