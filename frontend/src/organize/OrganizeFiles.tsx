// 範囲指定の整理(#84)。蔵書フォルダの中の範囲を選び、ファイル名テンプレート・フォルダーテンプレート(空ならファイル名の整理だけ)で
// 改名・移動をまとめてプレビュー → 実行 → 1 回分をまとめて元に戻す。蔵書に書き込むのは「実行」「元に戻す」の明示操作だけ。
//
// データ管理ページ(#83。frontend/src/data/DataJobs.tsx)の「整理」節に置く部品として OrganizeFilesSection を export する。
// 単独の入口 #/data/organize は同じページの「整理」節へ移す。
import { useCallback, useEffect, useRef, useState } from "react";
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

/** 整理後の名前の下に、元のファイル名を ├─ └─ で並べる */
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
            {group.items.map((item, i) => {
              const pages = pagesText(item);
              const superseded = (item.superseded_by?.length ?? 0) > 0;
              const active = item.status === "conflict" || item.status === "move" || item.status === "cbz";
              return (
                <li key={item.volume_id} className={`organize-item ${item.status}`}>
                  <span className="organize-branch" aria-hidden="true">
                    {i === group.items.length - 1 ? "└─" : "├─"}
                  </span>
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
            <section key={s}>
              <h3 className="organize-subhead">衝突({clashGroups.length} 組)</h3>
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
          <details key={s}>
            <summary>
              {ITEM_LABELS[s]}({list.length} 巻)
            </summary>
            {rows}
          </details>
        ) : (
          <section key={s}>
            <h3 className="organize-subhead">
              {ITEM_LABELS[s]}({list.length} 巻)
            </h3>
            {rows}
          </section>
        );
      })}
    </>
  );
}

function FolderPicker({ rootId, onPick, onClose }: { rootId: number; onPick: (path: string) => void; onClose: () => void }) {
  const [listing, setListing] = useState<RangeFolders | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(
    (path: string) => {
      setError(null);
      api.rangeFolders(rootId, path).then(setListing, (e) => setError(errorText(e, "フォルダを読めませんでした。")));
    },
    [rootId],
  );

  useEffect(() => open(""), [open]);

  const join = (name: string) => (listing?.path ? `${listing.path}/${name}` : name);
  return (
    <div className="organize-picker" role="dialog" aria-label="フォルダを選ぶ">
      <p>
        今のフォルダ: <code>{listing?.path || "(蔵書フォルダ全体)"}</code>
      </p>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <ul className="organize-picker-list">
        {listing?.parent !== null && listing?.parent !== undefined && (
          <li>
            <button type="button" className="link-button" onClick={() => open(listing.parent!)}>
              ↑ 1 つ上へ
            </button>
          </li>
        )}
        {listing?.folders.map((name) => (
          <li key={name}>
            <button type="button" className="link-button" onClick={() => open(join(name))}>
              {name}/
            </button>
          </li>
        ))}
        {listing && listing.folders.length === 0 && <li className="muted">この中にフォルダはありません</li>}
      </ul>
      <div className="organize-actions">
        <button type="button" onClick={() => listing && onPick(listing.path)} disabled={!listing}>
          このフォルダにする
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          閉じる
        </button>
      </div>
    </div>
  );
}

/** データ管理ページの「整理」節の中身(見出しは置く側が付ける) */
export function OrganizeFilesSection() {
  const saved = useRef(loadSaved()).current;
  const [options, setOptions] = useState<RangeOptions | null>(null);
  const [rootId, setRootId] = useState<number | null>(saved.rootId ?? null);
  const [path, setPath] = useState(saved.path ?? "");
  const [fileTemplate, setFileTemplate] = useState(saved.fileTemplate ?? "");
  const [folderTemplate, setFolderTemplate] = useState(saved.folderTemplate ?? "");
  const [includeCbz, setIncludeCbz] = useState(saved.includeCbz ?? false);
  const [focused, setFocused] = useState<"file" | "folder">("file");
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const [examples, setExamples] = useState<RangeExample[]>([]);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.rangeOptions().then(
      (o) => {
        setOptions(o);
        // 前回の蔵書フォルダが無くなっていたら最初のものにする
        setRootId((id) => (id !== null && o.roots.some((r) => r.id === id) ? id : (o.roots[0]?.id ?? null)));
      },
      (e) => setError(errorText(e, "整理の選択肢を読み込めませんでした。")),
    );
  }, []);

  useEffect(() => {
    save({ rootId: rootId ?? undefined, path, fileTemplate, folderTemplate, includeCbz });
  }, [rootId, path, fileTemplate, folderTemplate, includeCbz]);

  // 入力を変えたら前のプレビューは使えない(実行させない)
  useEffect(() => {
    setOp(null);
    setExcluded([]);
  }, [rootId, path, fileTemplate, folderTemplate, includeCbz]);

  // パスの例とテンプレートの誤り。打つたびに呼ばないよう少し待つ
  useEffect(() => {
    const timer = setTimeout(() => {
      api.rangeExample(fileTemplate, folderTemplate, path).then(
        (r) => {
          setExamples(r.examples);
          setTemplateError(r.error);
        },
        () => undefined,
      );
    }, 250);
    return () => clearTimeout(timer);
  }, [fileTemplate, folderTemplate, path]);

  /** カーソル位置にパラメーターを入れる(選択中の文字は置き換える) */
  const insert = (token: string) => {
    const el = focused === "file" ? fileRef.current : folderRef.current;
    const value = focused === "file" ? fileTemplate : folderTemplate;
    const set = focused === "file" ? setFileTemplate : setFolderTemplate;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    set(value.slice(0, start) + token + value.slice(end));
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(start + token.length, start + token.length);
    });
  };

  const plan = async (exclude: number[]) => {
    if (rootId === null) return;
    setBusy("plan");
    setError(null);
    try {
      setOp(await api.planRange({
        root_id: rootId,
        path,
        file_template: fileTemplate,
        folder_template: folderTemplate,
        exclude_volume_ids: exclude,
        // false のときは今までと同じ形の要求にする(このオプションを足す前と API の使い方を変えない)
        ...(includeCbz ? { include_cbz: true } : {}),
      }));
      setExcluded(exclude);
    } catch (e) {
      setOp(null);
      setError(errorText(e, "プレビューを作れませんでした。"));
    } finally {
      setBusy(null);
    }
  };

  const run = async (kind: "execute" | "undo") => {
    if (!op?.op_id) return;
    setBusy(kind);
    setError(null);
    try {
      setOp(await (kind === "execute" ? api.executeOrganize(op.op_id) : api.undoOrganize(op.op_id)));
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

  const applyTemplates = (file: string, folder: string) => {
    setFileTemplate(file);
    setFolderTemplate(folder);
  };

  const planned = op?.state === "planned";
  const editable = op === null || planned || op.state === "blocked";
  return (
    <div className="organize-files">
      <p className="muted">
        ファイル名の特殊記号・全角と半角の揺れ・文字化けを整理します。テンプレートでの名前の変更とフォルダーへの振り分けもできます。
        まずプレビューで確認してから実行します。実行した 1 回分はまとめて元に戻せます(
        {includeCbz ? "元のファイルは消さず、退避フォルダへ移します。" : "元のファイルは消さず、中身も変えません。"})。
      </p>

      <div className="organize-field">
        <label htmlFor="organize-root">蔵書フォルダ</label>
        <select id="organize-root" value={rootId ?? ""} onChange={(e) => setRootId(Number(e.target.value))} disabled={!editable}>
          {options?.roots.map((r) => (
            <option key={r.id} value={r.id}>
              {r.path}
              {r.online ? "" : "(オフライン)"}
            </option>
          ))}
        </select>
      </div>
      <div className="organize-field">
        <label htmlFor="organize-path">範囲のパス</label>
        <p id="organize-path-hint" className="muted organize-hint">空欄なら蔵書フォルダ全体。パスを指定するとサブフォルダーも対象になり、フォルダーテンプレートはそのパスを起点にします。</p>

        <div className="organize-inline">
          <input id="organize-path" value={path} onChange={(e) => setPath(e.target.value)} aria-describedby="organize-path-hint" disabled={!editable} />
          <button type="button" className="secondary" onClick={() => setPicker((x) => !x)} disabled={rootId === null || !editable}>
            フォルダを選ぶ
          </button>
        </div>

        {picker && rootId !== null && (
          <FolderPicker
            rootId={rootId}
            onPick={(p) => {
              setPath(p);
              setPicker(false);
            }}
            onClose={() => setPicker(false)}
          />
        )}
      </div>
      <div className="organize-field">
        <label htmlFor="organize-file-template">ファイル名テンプレート</label>
        <p id="organize-file-template-hint" className="muted organize-hint">空欄なら今の名前を整理するだけです。</p>
        <input
          id="organize-file-template"
          ref={fileRef}
          value={fileTemplate}
          onFocus={() => setFocused("file")}
          onChange={(e) => setFileTemplate(e.target.value)}
          aria-describedby={templateError ? "organize-template-error organize-file-template-hint" : "organize-file-template-hint"}
          aria-invalid={templateError !== null || undefined}
          disabled={!editable}
        />
      </div>
      <div className="organize-field">
        <label htmlFor="organize-folder-template">フォルダーテンプレート</label>
        <p id="organize-folder-template-hint" className="muted organize-hint">空欄なら同じ場所で名前だけ変更します。拡張子は元のまま。/ でフォルダーを区切ります。[ ] の中は、項目が空のとき全体が省略されます(角括弧そのものは [[ ]])。</p>

        <input
          id="organize-folder-template"
          ref={folderRef}
          value={folderTemplate}
          onFocus={() => setFocused("folder")}
          onChange={(e) => setFolderTemplate(e.target.value)}
          aria-describedby={templateError ? "organize-template-error organize-folder-template-hint" : "organize-folder-template-hint"}
          aria-invalid={templateError !== null || undefined}
          disabled={!editable}
        />

      </div>
      <div className="organize-field">
        <label className="organize-check" htmlFor="organize-include-cbz">
          <input
            id="organize-include-cbz"
            type="checkbox"
            checked={includeCbz}
            onChange={(e) => setIncludeCbz(e.target.checked)}
            aria-describedby="organize-include-cbz-hint"
            disabled={!editable}
          />
          <span>CBZ 化もまとめて行う</span>
        </label>
        <p id="organize-include-cbz-hint" className="muted organize-hint">
          1 巻 = 1 CBZ でない元(複数巻のまとめ・入れ子の巻・画像フォルダ・CBZ でないアーカイブ)も、読める巻ごとにテンプレートの名前の .cbz にします。
          小説(EPUB・テキスト)は対象外です。
        </p>
      </div>

      {options && editable && (
        <>
          <div className="organize-field">
            <span className="organize-label">テンプレートを適用</span>
            <div className="organize-chips">
              <button type="button" className="small secondary" onClick={() => applyTemplates("", "")}>
                ファイル名の整理だけ
              </button>
              <button type="button" className="small secondary" onClick={() => applyTemplates(options.defaults.file_template, options.defaults.folder_template)}>
                設定のテンプレート
              </button>
              {options.presets.map((p) => (
                <button key={p.label} type="button" className="small secondary" onClick={() => applyTemplates(p.file_template, p.folder_template)}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="organize-field">
            <span className="organize-label">パラメーターを挿入({focused === "file" ? "ファイル名" : "フォルダー"}へ)</span>
            <div className="organize-chips">
              {options.parameters.map((p) => (
                <button
                  key={p.token}
                  type="button"
                  className="small chip"
                  // 押してもテンプレートの入力欄からカーソル(選択)を外さない
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => insert(p.token)}
                  title={p.token}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {templateError ? (
        <div className="notice error" role="alert">
          <span id="organize-template-error">{templateError}</span>
        </div>
      ) : (
        examples.length > 0 && (
          <div className="organize-examples">
            <span className="organize-label">パスの例</span>
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

      <div className="organize-actions">
        {editable && (
          <button type="button" onClick={() => plan(excluded)} disabled={rootId === null || busy !== null || templateError !== null}>
            {busy === "plan" ? "プレビューを作っています…" : "プレビュー"}
          </button>
        )}
        {excluded.length > 0 && editable && (
          <button type="button" className="secondary" onClick={() => plan([])} disabled={busy !== null}>
            外した巻を戻す
          </button>
        )}
      </div>

      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      {op && (
        <div className="organize-op">
          <p>
            状態: <strong>{STATE_LABELS[op.state]}</strong>
          </p>
          {planned && <p className="muted">
            「改名・移動」と「CBZ 化」の巻を同じドライブの中で処理します。衝突・対象外の巻は動かしません。
          </p>}
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
          <div className="organize-actions">
            {planned && (
              <button type="button" onClick={() => run("execute")} disabled={busy !== null}>
                {busy === "execute" ? "実行中…" : `実行(${op.volumes.length} 巻)`}
              </button>
            )}
            {op.state === "done" && (
              <button type="button" onClick={() => run("undo")} disabled={busy !== null}>
                {busy === "undo" ? "元に戻し中…" : "まとめて元に戻す"}
              </button>
            )}
            {!editable && <a href={organizeHistoryHref}>整理の履歴へ</a>}
          </div>
          <RangeItems
            op={op}
            onExclude={editable ? (id) => plan([...excluded, id]) : undefined}
            onKeep={editable ? (drop) => plan([...new Set([...excluded, ...drop])]) : undefined}
          />
        </div>
      )}
    </div>
  );
}
