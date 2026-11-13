// 範囲指定の整理(#84)。蔵書フォルダの中の範囲を選び、ファイル名テンプレート・フォルダーテンプレート(空ならファイル名の整理だけ)で
// 改名・移動をまとめてプレビュー → 実行 → 1 回分をまとめて元に戻す。蔵書に書き込むのは「実行」「元に戻す」の明示操作だけ。
//
// データ管理ページ(#83。frontend/src/data/DataJobs.tsx)の「整理」節に置く部品として OrganizeFilesSection を export する。
// 単独の入口 #/data/organize は同じページの「整理」節へ移す。
//
// 画面の構成(#149): 1. 対象 → 2. 名前の決め方(任意) → 3. プレビューして実行、の 3 段に分ける。
// パラメーターは入力欄ごとの「項目を差し込む」から入れる(最後にフォーカスした欄へ入れる規則は廃止)。
// プレビューの結果は見出しへフォーカスとスクロールを移し、実行のボタンを 1 か所にまとめる。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  type OrganizeItem,
  type OrganizeItemStatus,
  type OrganizeOp,
  type RangeExample,
  type RangeOptions,
} from "../api";
import { FolderPicker, type FolderCrumb, type FolderListing } from "../shell/FolderPicker";
import { adminHref, organizeHistoryHref, organizeTemplateHref } from "../route";
import { ITEM_LABELS, STATE_LABELS, originalsDeleted } from "./Organize";

const STORAGE_KEY = "spinebox.organizeFiles";

interface Saved {
  rootId?: number;
  path?: string;
  fileTemplate?: string;
  folderTemplate?: string;
  includeCbz?: boolean;
  deleteOriginals?: boolean;
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

/** 計画の内訳。画面に出す件数をまとめたもの */
export interface ResultSummary {
  move: number;
  cbz: number;
  skipped: number;
  excluded: number;
  unchanged: number;
  clashes: number;
  /** 実行する巻が 1 つも残らない競合の組(実行しない) */
  clashNoAction: number;
  /** まだ残す方が選ばれていない競合の組 */
  needsChoice: number;
  /** 競合の組の外で実行できない巻(出力先が使えない・同じ元の別の巻の選択待ちなど) */
  blocked: number;
}

export function summarizeResult(op: OrganizeOp): ResultSummary {
  const items = op.items ?? [];
  const n = (s: OrganizeItemStatus) => items.filter((it) => it.status === s).length;
  const clashes = clashGroupsOf(op);
  // 競合の組の中の「整理しない」「そのまま」は、競合の欄で見せる(要約では二重に数えない)
  const inClash = new Set(clashes.flatMap((g) => g.items.map((it) => it.volume_id)));
  const nRest = (s: OrganizeItemStatus) => items.filter((it) => it.status === s && !inClash.has(it.volume_id)).length;
  // 実行する巻が無い組。残す巻が同じ元の別の競合の選択待ち(waiting_for)なら、選べば実行に入るので数えない
  const noAction = clashes.filter(
    (g) => !g.items.some((it) => it.status === "move" || it.status === "cbz") && !(keepOf(g)?.waiting_for?.length),
  );
  // 残す方が決まっていない組(同数)。選べる候補は実行する巻・競合・譲った巻
  const needsChoice = clashes.filter(
    (g) => !g.items.some((it) => it.status === "unchanged") && keepOf(g) === null && g.items.filter(isCandidate).length > 1,
  );
  return {
    move: n("move"),
    cbz: n("cbz"),
    skipped: nRest("skipped"),
    excluded: nRest("excluded"),
    unchanged: nRest("unchanged"),
    clashes: clashes.length,
    clashNoAction: noAction.length,
    needsChoice: needsChoice.length,
    blocked: nRest("conflict"),
  };
}

function resultLead(op: OrganizeOp, s: ResultSummary): string {
  if (op.state === "planned") {
    if (s.move + s.cbz === 0) return "整理する巻がありません。条件を変えてください。";
    return `${s.move + s.cbz} 巻を整理します。実行するまで蔵書は変わりません。`;
  }
  if (op.state === "blocked") return "この条件では実行できません。下の問題を確認してください。";
  if (op.state === "done") {
    return originalsDeleted(op)
      ? "整理しました。CBZ 化した元は削除したので、この整理は元に戻せません。"
      : "整理しました。元に戻すと元の場所と名前に戻ります。";
  }
  if (op.state === "undone") return "元に戻しました。";
  if (op.state === "running") return "整理しています…";
  if (op.state === "undoing") return "元に戻しています…";
  return "実行できませんでした。理由を確認してください。";
}

function ResultFacts({ op, summary }: { op: OrganizeOp; summary: ResultSummary }) {
  const action = summary.move + summary.cbz;
  const done = op.state !== "planned" && op.state !== "blocked" && op.state !== "running";
  return (
    <dl className="organize-result-facts">
      {action > 0 && (
        <div>
          <dt>{done ? "実行した" : "実行する"}</dt>
          <dd>
            {[summary.move ? `改名・移動 ${summary.move} 巻` : "", summary.cbz ? `CBZ 化 ${summary.cbz} 巻` : ""]
              .filter(Boolean)
              .join(" / ")}
          </dd>
        </div>
      )}
      {summary.clashes > 0 && (
        <div>
          <dt>競合</dt>
          <dd>
            {summary.clashes} 組{summary.needsChoice > 0 ? `（未選択 ${summary.needsChoice} 組）` : ""}
          </dd>
        </div>
      )}
      {summary.blocked > 0 && (
        <div>
          <dt>実行できない</dt>
          <dd>{summary.blocked} 巻(理由は下の「整理しない内容」)</dd>
        </div>
      )}
      {summary.cbz > 0 && (
        <div>
          <dt>CBZ 化した元</dt>
          <dd>{op.delete_originals ? "削除する(元に戻せません)" : "退避フォルダへ移す(元に戻せます)"}</dd>
        </div>
      )}
      {summary.skipped > 0 && (
        <div>
          <dt>整理しない</dt>
          <dd>{summary.skipped} 巻</dd>
        </div>
      )}
      {summary.excluded > 0 && (
        <div>
          <dt>対象外</dt>
          <dd>{summary.excluded} 巻</dd>
        </div>
      )}
      {summary.unchanged > 0 && (
        <div>
          <dt>そのまま</dt>
          <dd>{summary.unchanged} 巻</dd>
        </div>
      )}
    </dl>
  );
}

/** 整理後の名前(正規化した名前)の組。同じ名前になる巻が複数あれば競合の組 */
export interface NameGroup {
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

/**
 * 競合の組。同じ整理後の名前になる候補(実行する巻・競合・整理しない巻・そのまま)が 2 つ以上ある名前をまとめる。
 * 1 件だけの競合(出力先が使えない・同じ元の別の巻の選択待ちなど)は組にせず、「実行しない内容」に理由つきで出す。
 */
export function clashGroupsOf(op: OrganizeOp): NameGroup[] {
  const items = (op.items ?? []).filter((it) => it.status !== "excluded");
  const keys = new Set(
    items.filter((it) => it.status === "conflict" || (it.superseded_by?.length ?? 0) > 0).map(nameKey),
  );
  return groupByOutputName(items.filter((it) => keys.has(nameKey(it)))).filter((g) => g.items.length > 1);
}

const isCandidate = (it: OrganizeItem) =>
  it.status === "move" || it.status === "cbz" || it.status === "conflict" || (it.status === "skipped" && (it.superseded_by?.length ?? 0) > 0);

/**
 * 組の中で残す巻。ほかの巻が譲った先(superseded_by)が 1 つにそろっていればその巻(ファイル数で自動に決めた・利用者が選んだ)。
 * 譲った巻が無ければ、実行する巻が 1 つだけのときその巻。同数で決まっていなければ null。
 */
export function keepOf(group: NameGroup): OrganizeItem | null {
  if (group.items.some((it) => it.status === "unchanged")) return null;
  const targets = new Set(group.items.flatMap((it) => (it.status === "skipped" ? it.superseded_by ?? [] : [])));
  if (targets.size === 1) {
    const [id] = [...targets];
    const kept = group.items.find((it) => it.volume_id === id && it.status !== "skipped");
    if (kept) return kept;
  }
  const executable = group.items.filter((it) => it.status === "move" || it.status === "cbz");
  return executable.length === 1 ? executable[0] : null;
}

function choiceLabel(item: OrganizeItem): string {
  return item.conversion ? "この本" : "この巻";
}

/** 元と先のパス。リストの行の中で 2 行に分ける */
function RowPaths({ item, hideFrom }: { item: OrganizeItem; hideFrom?: boolean }) {
  return (
    <dl className="organize-path-list">
      {!hideFrom && (
        <div>
          <dt>元</dt>
          <dd>
            <code>{item.from_rel_path}</code>
          </dd>
        </div>
      )}
      {item.output_rel_path && (
        <div>
          <dt>先</dt>
          <dd>
            <code>{item.output_rel_path}</code>
          </dd>
        </div>
      )}
    </dl>
  );
}

/** 実行する内容の 1 行(改名・移動 / CBZ 化) */
function WorkRow({
  item,
  onExclude,
  hideFrom,
}: {
  item: OrganizeItem;
  onExclude?: (volumeId: number) => void;
  /** 元ごとにまとめた見出しの下に置くとき、元のパスを繰り返さない */
  hideFrom?: boolean;
}) {
  const pages = pagesText(item);
  return (
    <li className={`organize-result-row ${item.status}`}>
      <div className="organize-row-head">
        <span className={`organize-badge ${item.status}`}>{ITEM_LABELS[item.status]}</span>
        <span className="organize-row-title">{item.title}</span>
        {pages && <span className="organize-row-meta">{pages}</span>}
        {onExclude && (
          <button type="button" className="link-button organize-row-action" onClick={() => onExclude(item.volume_id)}>
            {choiceLabel(item)}を整理しない
          </button>
        )}
        {item.cbz_volume_id !== null && (
          <a className="organize-row-action" href={organizeTemplateHref(item.cbz_volume_id)}>
            {choiceLabel(item)}をテンプレートの名前で CBZ に整理
          </a>
        )}
      </div>
      <RowPaths item={item} hideFrom={hideFrom} />
      {(item.changes?.length ?? 0) > 0 && <div className="muted organize-changes">{item.changes!.join("・")}</div>}
      {item.reason && <div className="muted">{item.reason}</div>}
    </li>
  );
}

/** 整理しない内容(対象外・そのまま・利用者が整理しないにした巻)の 1 行 */
function RestRow({ item }: { item: OrganizeItem }) {
  return (
    <li className={`organize-result-row ${item.status}`}>
      <div className="organize-row-head">
        <span className={`organize-badge ${item.status}`}>{ITEM_LABELS[item.status]}</span>
        <span className="organize-row-title">{item.title}</span>
      </div>
      <div className="organize-paths">
        <code>{item.from_rel_path}</code>
      </div>
      {(item.changes?.length ?? 0) > 0 && <div className="muted organize-changes">{item.changes!.join("・")}</div>}
      {item.reason && <div className="muted">{item.reason}</div>}
      {item.cbz_volume_id !== null && (
        <div className="organize-item-actions">
          <a className="organize-row-action" href={organizeTemplateHref(item.cbz_volume_id)}>テンプレートの名前で CBZ に整理</a>
        </div>
      )}
    </li>
  );
}

/** 競合の候補 1 行。チェックが入っている行が残す巻 */
function ClashRow({
  item,
  checked,
  selectable,
  name,
  onChoose,
}: {
  item: OrganizeItem;
  checked: boolean;
  selectable: boolean;
  name: string;
  onChoose?: () => void;
}) {
  const pages = pagesText(item);
  const status =
    item.status === "unchanged"
      ? ITEM_LABELS.unchanged
      : checked
        ? "残す"
        : ITEM_LABELS[item.status];
  const badgeClass =
    item.status === "conflict" && !checked ? "conflict" : checked && item.status !== "unchanged" ? "keep" : "drop";
  const body = (
    <>
      <span className={`organize-choice-mark ${checked ? "on" : ""}`} aria-hidden="true">
        {checked ? "✓" : ""}
      </span>
      <span className={`organize-badge ${badgeClass}`}>{status}</span>
      <span className="organize-row-title">{item.title}</span>
      {pages && <span className="organize-row-meta">{pages}</span>}
      <span className="organize-row-path">
        <code>{item.from_rel_path}</code>
      </span>
      {item.reason && <span className="organize-row-reason">{item.reason}</span>}
    </>
  );
  return (
    <li className={`organize-result-row organize-clash-row ${checked ? "on" : ""}`}>
      {selectable ? (
        <label className="organize-choice-row selectable">
          <input
            type="radio"
            name={name}
            checked={checked}
            onChange={onChoose}
            className="organize-choice-input"
            data-keep-volume={item.volume_id}
          />
          {body}
        </label>
      ) : (
        <div className="organize-choice-row">{body}</div>
      )}
      {item.cbz_volume_id !== null && (
        <a className="organize-row-action" href={organizeTemplateHref(item.cbz_volume_id)}>{choiceLabel(item)}をテンプレートの名前で CBZ に整理</a>
      )}
    </li>
  );
}

function ClashGroup({
  group,
  index,
  onChoose,
  onExclude,
  pendingKeep,
}: {
  group: NameGroup;
  index: number;
  onChoose?: (dropIds: number[], chosen: OrganizeItem) => void;
  onExclude?: (volumeId: number) => void;
  pendingKeep?: number | null;
}) {
  const unchanged = group.items.filter((it) => it.status === "unchanged");
  const choices = group.items.filter(isCandidate).sort((a, b) => (b.page_count ?? -1) - (a.page_count ?? -1));
  // 利用者が明示的に整理しないにした巻は、選び直しの候補にはしない(実行されないため)。行だけ残す
  const userSkipped = group.items.filter((it) => it.status === "skipped" && (it.superseded_by?.length ?? 0) === 0);
  const editable = onChoose !== undefined;
  const defaultKeep = keepOf(group);
  const selectable = editable && unchanged.length === 0 && choices.length > 1;
  const hintId = `organize-clash-hint-${index}`;
  const singleConflict = !selectable && choices.length === 1 && choices[0].status === "conflict";
  // 残す巻だけがいちばん多いときに「ファイル数が多い方」と言う(同数を利用者が選んだときは「選んだ方」)
  const keptIsLargest =
    defaultKeep !== null &&
    choices.every((it) => it === defaultKeep || (it.page_count ?? -1) < (defaultKeep.page_count ?? -1));
  const waiting = (defaultKeep?.waiting_for?.length ?? 0) > 0;
  // 選び直し中は、その組の選んだ行をチェックしたままにする(ほかの組の既定は変えない)
  const pendingHere = pendingKeep != null && group.items.some((it) => it.volume_id === pendingKeep);
  const selectedId = pendingHere ? pendingKeep : defaultKeep?.volume_id;
  const choose = (item: OrganizeItem) => {
    const drop = group.items
      .filter((o) => o.volume_id !== item.volume_id)
      .filter((o) => o.status !== "unchanged" && o.status !== "excluded")
      .filter((o) => !(o.status === "skipped" && !o.superseded_by?.length))
      .map((o) => o.volume_id);
    onChoose?.(drop, item);
  };
  return (
    <fieldset className="organize-clash" aria-describedby={hintId}>
      <legend>
        整理後の名前: <code>{group.name}</code>
      </legend>
      <p id={hintId} className="organize-clash-hint">
        {defaultKeep
          ? `${keptIsLargest ? "ファイル数が多い方" : "選んだ方"}を残${editable ? "します" : "しました"}。` +
            (waiting ? "同じ元の別の本の競合で残す方を選ぶと、実行に入ります。" : "") +
            (editable && selectable ? "別の行を選ぶと入れ替わります。" : "")
          : selectable
            ? "同じ名前になるため、残す方を選んでください。"
            : !editable && choices.length > 1
              ? "同じ名前になるため、この組は整理していません。"
              : "この名前は既に使われているため、この巻は整理しません。"}
      </p>
      <ul className="organize-result-list">
        {choices.map((item) => (
          <ClashRow
            key={item.volume_id}
            item={item}
            checked={item.volume_id === selectedId}
            selectable={selectable}
            name={`organize-keep-${index}`}
            onChoose={() => choose(item)}
          />
        ))}
        {unchanged.map((item) => (
          <ClashRow key={item.volume_id} item={item} checked selectable={false} name={`organize-keep-${index}`} />
        ))}
        {userSkipped.map((item) => (
          <ClashRow key={item.volume_id} item={item} checked={false} selectable={false} name={`organize-keep-${index}`} />
        ))}
      </ul>
      {singleConflict && onExclude && (
        <button type="button" className="link-button" onClick={() => onExclude(choices[0].volume_id)}>
          {choiceLabel(choices[0])}を整理しない
        </button>
      )}
    </fieldset>
  );
}

/** 競合の組。同じ整理後の名前になる候補をリストで出し、残す方を選べる */
export function ClashGroups({
  op,
  onChoose,
  onExclude,
  busy,
  pendingKeep,
}: {
  op: OrganizeOp;
  onChoose?: (dropIds: number[], chosen: OrganizeItem) => void;
  onExclude?: (volumeId: number) => void;
  busy?: boolean;
  pendingKeep?: number | null;
}) {
  const groups = clashGroupsOf(op);
  if (groups.length === 0) return null;
  return (
    <section className="organize-clashes" aria-labelledby="organize-clashes-title">
      <h4 id="organize-clashes-title">競合（{groups.length} 組）</h4>
      <p className="organize-clashes-lead">
        同じ整理後の名前になる組です。残す方を 1 つ選んでください。ファイル数が多い方を既定で選んでいます。
      </p>
      {busy && (
        <p className="muted" role="status">
          選び直しています…
        </p>
      )}
      <div className="organize-clash-list">
        {groups.map((group, index) => (
          <ClashGroup
            key={group.name}
            group={group}
            index={index}
            onChoose={onChoose}
            onExclude={onExclude}
            pendingKeep={pendingKeep}
          />
        ))}
      </div>
    </section>
  );
}

/** 実行する内容(改名・移動 / CBZ 化)。競合で残した巻もここに出る */
export function RangeWork({ op, onExclude }: { op: OrganizeOp; onExclude?: (volumeId: number) => void }) {
  const items = op.items ?? [];
  const move = items.filter((it) => it.status === "move");
  const cbz = items.filter((it) => it.status === "cbz");
  if (!move.length && !cbz.length) return null;
  const done = op.state !== "planned" && op.state !== "blocked" && op.state !== "running";
  return (
    <section className="organize-result-work" aria-labelledby="organize-work-title">
      <h4 id="organize-work-title">{done ? "実行した内容" : "実行する内容"}</h4>
      {move.length > 0 && (
        <div className="organize-work-group">
          <h5>改名・移動（{move.length} 巻）</h5>
          <ul className="organize-result-list">
            {move.map((it) => (
              <WorkRow key={it.volume_id} item={it} onExclude={onExclude} />
            ))}
          </ul>
        </div>
      )}
      {cbz.length > 0 && (
        <div className="organize-work-group">
          <h5>CBZ 化（{cbz.length} 巻）</h5>
          {!done && (
            <p className="organize-hint">
              {op.delete_originals
                ? "元のファイル・フォルダは、全巻を作って確かめた後で削除します(元に戻せません)。"
                : "元のファイル・フォルダは、全巻を作って確かめた後で退避フォルダへ移します(元に戻せます)。"}
            </p>
          )}
          {cbzBySource(op, cbz).map((src) => (
            <div key={src.path} className="organize-source">
              <p className="organize-source-head">
                <span className="organize-source-label">元</span>
                <code>{src.path}</code>
                <span className="organize-row-meta">{src.items.length} 巻を CBZ に</span>
              </p>
              {src.dropped.length > 0 && (
                <p className="organize-source-note">
                  {src.dropped.map((d) => `「${d.title}」`).join("・")}は同じ名前で残す本がほかにあるため CBZ にしません(元といっしょに
                  {op.delete_originals ? "削除します" : "退避します"})。
                </p>
              )}
              <ul className="organize-result-list">
                {src.items.map((it) => (
                  <WorkRow key={it.volume_id} item={it} onExclude={onExclude} hideFrom />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** CBZ 化する巻を元(1 つのファイル・フォルダ)ごとにまとめる。元のパスを行ごとに繰り返さない */
function cbzBySource(op: OrganizeOp, cbz: OrganizeItem[]) {
  const byId = new Map((op.items ?? []).map((it) => [it.volume_id, it]));
  const groups = new Map<string, { path: string; items: OrganizeItem[]; dropped: OrganizeItem[] }>();
  for (const it of cbz) {
    const g = groups.get(it.from_rel_path) ?? { path: it.from_rel_path, items: [], dropped: [] };
    g.items.push(it);
    groups.set(it.from_rel_path, g);
  }
  for (const s of op.cbz_sources ?? []) {
    const g = groups.get(s.rel_path);
    if (!g) continue;
    g.dropped = (s.dropped_volume_ids ?? []).map((id) => byId.get(id)).filter((x): x is OrganizeItem => x !== undefined);
  }
  return [...groups.values()];
}

/** 実行しない内容(整理しない / 対象外 / そのまま)。競合の組は上で出すので除く */
export function RangeRest({ op }: { op: OrganizeOp }) {
  const inClash = new Set(clashGroupsOf(op).flatMap((g) => g.items.map((it) => it.volume_id)));
  const rest = (op.items ?? []).filter((it) => !inClash.has(it.volume_id));
  const groups = [
    { key: "conflict", label: "実行できない", items: rest.filter((it) => it.status === "conflict") },
    { key: "skipped", label: ITEM_LABELS.skipped, items: rest.filter((it) => it.status === "skipped") },
    { key: "excluded", label: ITEM_LABELS.excluded, items: rest.filter((it) => it.status === "excluded") },
    { key: "unchanged", label: ITEM_LABELS.unchanged, items: rest.filter((it) => it.status === "unchanged") },
  ].filter((g) => g.items.length > 0);
  if (groups.length === 0) return null;
  const summary = groups.map((g) => `${g.label} ${g.items.length} 巻`).join("・");
  return (
    // 実行できない巻(理由を読んで直す必要がある)があるときは、最初から開いておく
    <details className="organize-result-rest" open={groups[0].key === "conflict" || undefined}>
      <summary>整理しない内容（{summary}）</summary>
      {groups.map((g) => (
        <section key={g.key}>
          <h5>
            {g.label}（{g.items.length} 巻）
          </h5>
          <ul className="organize-result-list">
            {g.items.map((it) => (
              <RestRow key={it.volume_id} item={it} />
            ))}
          </ul>
        </section>
      ))}
    </details>
  );
}

/** 結果の中身。テストと、結果の外側だけ差し替えたいとき用 */
export function RangeItems({
  op,
  onChoose,
  onExclude,
  busy,
}: {
  op: OrganizeOp;
  onChoose?: (dropIds: number[], chosen: OrganizeItem) => void;
  onExclude?: (volumeId: number) => void;
  busy?: boolean;
}) {
  return (
    <>
      <ClashGroups op={op} onChoose={onChoose} onExclude={onExclude} busy={busy} />
      <RangeWork op={op} onExclude={onExclude} />
      <RangeRest op={op} />
    </>
  );
}


/** 入力欄の直下に置く「項目を差し込む」。押した欄へ入る(最後のフォーカスに依存しない) */
function ParameterChips({
  target,
  label,
  parameters,
  disabled,
  onInsert,
}: {
  target: "file" | "folder";
  label: string;
  parameters: RangeOptions["parameters"];
  disabled?: boolean;
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
            disabled={disabled}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** 範囲のパス(蔵書フォルダからの相対。`/` 区切り)をパンくずに分ける */
function rangeCrumbs(path: string): FolderCrumb[] {
  const segments = path.split("/").filter(Boolean);
  return segments.map((label, i) => ({ label, path: segments.slice(0, i + 1).join("/") }));
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
  // 元の削除は毎回選び直す(前回の選択を覚えて黙って消さない)
  const [deleteOriginals, setDeleteOriginals] = useState(false);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [confirmError, setConfirmError] = useState(false);
  const confirmRef = useRef<HTMLInputElement>(null);
  const [advancedOpen, setAdvancedOpen] = useState(Boolean(saved.folderTemplate));
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLSelectElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusResult = useRef(false);
  const keepFocus = useRef<number | null>(null);
  const opRef = useRef<OrganizeOp | null>(null);
  const exampleSeq = useRef(0);
  const [examples, setExamples] = useState<RangeExample[]>([]);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const loadRangeFolders = useCallback(
    async (at: string): Promise<FolderListing> => {
      const res = await api.rangeFolders(rootId ?? -1, at);
      return {
        path: res.path,
        parent: res.parent,
        folders: res.folders.map((name) => ({ name, path: res.path ? `${res.path}/${name}` : name })),
      };
    },
    [rootId],
  );
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [excluded, setExcluded] = useState<number[]>([]);
  const [pendingKeep, setPendingKeep] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"plan" | "execute" | "undo" | null>(null);
  const [cleared, setCleared] = useState(false);
  const needsDeleteConfirm = op?.state === "planned" && originalsDeleted(op);

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
    setPendingKeep(null);
    setDeleteConfirmed(false);
    setConfirmError(false);
  }, [rootId, path, fileTemplate, folderTemplate, includeCbz, deleteOriginals]);

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
        ...(includeCbz && deleteOriginals ? { delete_originals: true } : {}),
      });
      setOp(next);
      setExcluded(exclude);
      if (focus) focusResult.current = true;
    } catch (e) {
      // 選び直しに失敗したときは、直前のプレビューを消さずに理由だけ出す
      setPendingKeep(null);
      setError(errorText(e, "プレビューを作れませんでした。"));
    } finally {
      setBusy(null);
    }
  };

  const run = async (kind: "execute" | "undo") => {
    if (!op?.op_id || busy !== null) return;
    // 元を削除する整理は、確認のチェックが入るまで実行しない(押したら確認欄へ案内する)
    if (kind === "execute" && needsDeleteConfirm && !deleteConfirmed) {
      setConfirmError(true);
      confirmRef.current?.focus();
      return;
    }
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

  // 競合の残す方を選び直したら、選んだ行へフォーカスを戻す(計画の作り直しで行方不明にしない)
  useEffect(() => {
    const id = keepFocus.current;
    if (id === null || !op) return;
    keepFocus.current = null;
    const input = document.querySelector<HTMLInputElement>(`input[data-keep-volume="${id}"]`);
    if (input) input.focus({ preventScroll: true });
    else resultHeadingRef.current?.focus?.({ preventScroll: true });
    setPendingKeep(null);
  }, [op]);

  const preview = () => {
    if (busy !== null) return;
    // 誤りがあるときは通信せず、直す欄へフォーカスする(disabled で塞がない)
    if (templateError) {
      fileRef.current?.focus();
      return;
    }
    setPendingKeep(null);
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
          蔵書フォルダがありません。管理の<a href={adminHref("libraries")}>「蔵書」タブ</a>で蔵書フォルダを追加してから、もう一度開いてください。
        </p>
      </div>
    );
  }

  const root = options.roots.find((r) => r.id === rootId) ?? options.roots[0];
  const planned = op?.state === "planned";
  const canEditPlan = planned || op?.state === "blocked";
  const summary = op ? summarizeResult(op) : null;
  const excludeOne = canEditPlan
    ? (id: number) => {
        if (busy !== null) return;
        setPendingKeep(null);
        void plan([...new Set([...excluded, id])], false);
      }
    : undefined;
  const chooseKeep = canEditPlan
    ? (drop: number[], chosen: OrganizeItem) => {
        if (busy !== null) return;
        // 計画が返るまで選んだ行のチェックを保つ(通信中に既定へ戻って見えないように)
        setPendingKeep(chosen.volume_id);
        keepFocus.current = chosen.volume_id;
        // 前に整理しないにした巻を選び直したときは、整理しないから外す(残したままだと組の全部が整理しないになる)
        void plan([...new Set([...excluded.filter((id) => id !== chosen.volume_id), ...drop])], false);
      }
    : undefined;
  const previewLabel = busy === "plan" ? "プレビューを作っています…" : "プレビュー";

  return (
    <div className="organize-files">
      <p className="organize-lead">
        ファイル名の特殊記号・全角と半角の揺れ・文字化けを整理します。テンプレートで名前の変更とフォルダーへの振り分けもできます。
        蔵書を動かすのでジョブには入れず、まずプレビューで確認してから実行します。実行した 1 回分はまとめて元に戻せます
        (CBZ 化した元を削除するオプションを選んだときだけは戻せません)。
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
                <button type="button" className="small secondary" onClick={() => applyTemplates("", "")} disabled={busy !== null}>
                  ファイル名の整理だけ
                </button>
                <button
                  type="button"
                  className="small secondary"
                  onClick={() => applyTemplates(options.defaults.file_template, options.defaults.folder_template)}
                  disabled={busy !== null}
                >
                  設定のテンプレート
                </button>
                {options.presets.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    className="small secondary"
                    onClick={() => applyTemplates(p.file_template, p.folder_template)}
                    disabled={busy !== null}
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
                disabled={busy !== null}
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
                    disabled={busy !== null}
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
              1 巻 = 1 CBZ でない元(複数巻のまとめ・入れ子の巻・画像フォルダ・CBZ でないアーカイブ・画像だけの EPUB)も、読める巻ごとにテンプレートの名前の .cbz にします。表紙と本文の画像だけを入れます。文字のある EPUB・テキスト(小説)は対象外です。
            </p>
            {includeCbz && (
              <>
                <label className="organize-check" htmlFor="organize-delete-originals">
                  <input
                    id="organize-delete-originals"
                    type="checkbox"
                    checked={deleteOriginals}
                    onChange={(e) => setDeleteOriginals(e.target.checked)}
                    aria-describedby="organize-delete-originals-hint"
                    disabled={busy !== null}
                  />
                  <span>CBZ 化した元を削除する</span>
                </label>
                <p id="organize-delete-originals-hint" className="organize-hint">
                  選ばないときは、元を退避フォルダ(蔵書フォルダの .spinebox/trash)へ移し、あとで元に戻せます。選ぶと、全巻を作って確かめた後で元を削除します。元の中の画像以外のファイル(説明の .txt・.url など)も消え、この整理は元に戻せません。
                </p>
              </>
            )}
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

      {op && summary && (
        <div ref={resultRef} className={`organize-result ${op.state}`}>
          <div className="organize-result-head">
            <h3 ref={resultHeadingRef} tabIndex={-1}>
              {op.state === "planned" || op.state === "blocked" ? "プレビュー結果" : "実行結果"}
            </h3>
            {op.state !== "planned" && <span className={`organize-state ${op.state}`}>{STATE_LABELS[op.state]}</span>}
          </div>
          <p className="organize-result-lead">{resultLead(op, summary)}</p>
          <ResultFacts op={op} summary={summary} />
          {op.problems.map((p) => (
            <div key={p} className="notice error" role="alert">
              {p}
            </div>
          ))}
          {op.warnings
            // 同じことを画面の別の場所で出している注意は重ねない(競合の欄・要約の「CBZ 化した元」・元ごとのリストの注記)
            .filter(
              (w) =>
                !(summary.clashes > 0 && w.startsWith("競合している巻は")) &&
                !w.startsWith("CBZ 化する元は") &&
                !w.includes("同じ名前で残す巻がほかにあるため"),
            )
            .map((w) => (
              <div key={w} className="notice warn">
                {w}
              </div>
            ))}
          {op.error && (
            <div className="notice error" role="alert">
              {op.error}
            </div>
          )}
          <ClashGroups
            op={op}
            onChoose={chooseKeep}
            onExclude={excludeOne}
            busy={busy === "plan"}
            pendingKeep={pendingKeep}
          />
          {summary.needsChoice > 0 && (
            <p className="organize-result-note">
              競合 {summary.needsChoice} 組はまだ選ばれていません。残す方の行を選ぶと実行に含まれます。
            </p>
          )}
          {summary.clashNoAction > 0 && summary.needsChoice === 0 && (
            <p className="organize-result-note">競合 {summary.clashNoAction} 組は整理しません。</p>
          )}
          {needsDeleteConfirm && (
            <div className={`organize-confirm ${confirmError ? "error" : ""}`}>
              <label className="organize-check" htmlFor="organize-delete-confirm">
                <input
                  id="organize-delete-confirm"
                  ref={confirmRef}
                  type="checkbox"
                  checked={deleteConfirmed}
                  onChange={(e) => {
                    setDeleteConfirmed(e.target.checked);
                    if (e.target.checked) setConfirmError(false);
                  }}
                  aria-invalid={confirmError || undefined}
                  aria-describedby={confirmError ? "organize-delete-confirm-error" : undefined}
                />
                <span>
                  CBZ 化した元 {op.cbz_sources?.length ?? 0} 件を削除し、この整理を元に戻せないことを確認しました
                </span>
              </label>
              {confirmError && (
                <p id="organize-delete-confirm-error" className="organize-confirm-error">
                  元を削除して実行するには、確認のチェックを入れてください。
                </p>
              )}
            </div>
          )}
          <div className="organize-result-actions">
            <button type="button" className="link-button" onClick={scrollToForm}>
              条件を変える
            </button>
            {op.state !== "planned" && op.state !== "blocked" && <a href={organizeHistoryHref}>整理の履歴へ</a>}
            {excluded.length > 0 && canEditPlan && (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setPendingKeep(null);
                  void plan([], false);
                }}
                disabled={busy !== null}
              >
                整理しない巻を戻す
              </button>
            )}
            {op.state === "done" && originalsDeleted(op) && (
              <span className="muted">元を削除したので、元に戻せません。</span>
            )}
            {op.state === "done" && !originalsDeleted(op) && (
              <button type="button" className="secondary" onClick={() => void run("undo")} disabled={busy !== null}>
                {busy === "undo" ? "元に戻し中…" : "まとめて元に戻す"}
              </button>
            )}
            {planned && op.volumes.length > 0 && (
              <button type="button" className="organize-commit" onClick={() => void run("execute")} disabled={busy !== null}>
                {busy === "execute"
                  ? "実行中…"
                  : `${summary.clashNoAction > 0 || summary.needsChoice > 0 ? "競合を除いて" : ""}${
                      needsDeleteConfirm ? "実行して元を削除" : "実行"
                    }(${op.volumes.length} 巻)`}
              </button>
            )}
          </div>
          <RangeWork op={op} onExclude={excludeOne} />
          <RangeRest op={op} />
        </div>
      )}
      {picker && rootId !== null && (
        <FolderPicker
          load={loadRangeFolders}
          initialPath={path}
          topLabel="蔵書フォルダ全体"
          hint="整理する範囲のフォルダーを開いて選びます。"
          crumbs={rangeCrumbs}
          allowTop
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
