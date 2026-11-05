// 整理の画面。CBZ 化(名前は今の名前かテンプレート)と、作品単位のテンプレートどおりの改名・移動。
// プレビュー(計画)→ 実行、履歴 → 元に戻す。蔵書に書き込むのはここからの明示操作だけ。
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type OrganizeItem, type OrganizeItemStatus, type OrganizeNaming, type OrganizeOp, type OrganizeState } from "../api";
import { libraryHref, organizeHistoryHref, organizeHref, organizeTemplateHref, workHref } from "../route";

export const STATE_LABELS: Record<OrganizeState, string> = {
  blocked: "実行できません",
  planned: "未実行",
  running: "実行中",
  done: "整理済み",
  failed: "失敗(元はそのまま)",
  undoing: "元に戻し中",
  undone: "元に戻しました",
  undo_failed: "元に戻すのに失敗(要確認)",
  rollback_failed: "巻き戻しに失敗(要確認)",
};

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

export const ITEM_LABELS: Record<OrganizeItemStatus, string> = {
  move: "改名・移動",
  cbz: "CBZ 化",
  unchanged: "そのまま",
  excluded: "対象外",
  conflict: "競合",
  skipped: "整理しない",
};

function ItemRow({ item, onExclude }: { item: OrganizeItem; onExclude?: (volumeId: number) => void }) {
  return (
    <li className={`organize-item ${item.status}`}>
      <span className={`organize-badge ${item.status}`}>{ITEM_LABELS[item.status]}</span> {item.title}
      <div className="organize-paths">
        <code>{item.from_rel_path}</code>
        {item.status === "move" && item.output_rel_path && (
          <>
            {" "}→ <code>{item.output_rel_path}</code>
          </>
        )}
      </div>
      {(item.changes?.length ?? 0) > 0 && <div className="muted organize-changes">{item.changes!.join("・")}</div>}
      {item.reason && item.status !== "move" && <div className="muted">{item.reason}</div>}
      {/* 操作はプレビュー中だけ(履歴では出さない) */}
      {onExclude && (item.status === "conflict" || item.cbz_volume_id !== null) && (
        <div className="organize-item-actions">
          {item.status === "conflict" && (
            <button type="button" onClick={() => onExclude(item.volume_id)}>
              この巻を整理しない
            </button>
          )}
          {item.cbz_volume_id !== null && <a href={organizeTemplateHref(item.cbz_volume_id)}>テンプレートの名前で CBZ に整理</a>}
        </div>
      )}
    </li>
  );
}

function OpBody({ op, onExclude }: { op: OrganizeOp; onExclude?: (volumeId: number) => void }) {
  return (
    <>
      {op.kind === "range" && op.scope ? (
        <>
          <p>
            範囲 <code>{op.scope.root_path}{op.scope.path ? `/${op.scope.path}` : ""}</code> の巻を
            {op.scope.file_template || op.scope.folder_template ? "テンプレートの名前へ改名・移動" : "ファイル名の整理で改名"}
          </p>
          {(op.scope.file_template || op.scope.folder_template) && (
            <p className="muted">
              ファイル名: <code>{op.scope.file_template || "(今の名前を整理)"}</code> / フォルダー:{" "}
              <code>{op.scope.folder_template || "(元のフォルダ)"}</code>
            </p>
          )}
        </>
      ) : op.kind === "rename" ? (
        <p>
          作品の巻をテンプレートの名前へ改名・移動{op.work_id != null && <> (<a href={workHref(op.work_id)}>作品へ</a>)</>}
        </p>
      ) : (
        <p>
          元: <code>{op.source_rel_path}</code>({op.source_kind === "folder" ? "画像フォルダ" : "アーカイブ"})
        </p>
      )}
      {op.template && (
        <p className="muted">
          テンプレート: <code>{op.template}</code>
        </p>
      )}
      {op.items ? (
        <ul className="organize-volumes">
          {op.items.map((it) => (
            <ItemRow key={it.volume_id} item={it} onExclude={onExclude} />
          ))}
        </ul>
      ) : (
        <ul className="organize-volumes">
        {op.volumes.map((v) => (
          <li key={v.volume_id}>
            {v.title}({v.page_count === null ? "ページ数未確定" : `${v.page_count} ページ`})
            {v.output_rel_path && (
              <>
                {" "}→ <code>{v.output_rel_path}</code>
              </>
            )}
          </li>
        ))}
        </ul>
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
    </>
  );
}

/** 計画の実行。失敗したら(409)操作の状態(巻き戻し済みか)を履歴から出し直す */
function useExecute(op: OrganizeOp | null, setOp: (op: OrganizeOp) => void, setError: (e: string | null) => void) {
  const [busy, setBusy] = useState(false);
  const execute = async () => {
    if (!op?.op_id) return;
    setBusy(true);
    setError(null);
    try {
      setOp(await api.executeOrganize(op.op_id));
    } catch (e) {
      setError(errorText(e, "整理に失敗しました。"));
      if (e instanceof ApiError && e.status === 409) {
        api.organizeHistory().then((h) => setOp(h.find((x) => x.op_id === op.op_id) ?? op), () => undefined);
      }
    } finally {
      setBusy(false);
    }
  };
  return { busy, setBusy, execute };
}

export function OrganizePlan({ volumeId, naming = "title" }: { volumeId: string; naming?: OrganizeNaming }) {
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { busy, setBusy, execute } = useExecute(op, setOp, setError);

  const makePlan = useCallback(() => {
    api.planOrganize(volumeId, naming).then(setOp, (e) => setError(errorText(e, "整理の計画を作れませんでした。")));
  }, [volumeId, naming]);

  useEffect(() => {
    makePlan();
  }, [makePlan]);

  // 内側アーカイブの中の巻を調べてから計画を作り直す(プレビュー自体は何も書かないので、調べるのは明示操作)
  const analyzeThenReplan = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const id of op?.needs_analysis ?? []) await api.analyze(id);
      makePlan();
    } catch (e) {
      setError(errorText(e, "中の巻を調べられませんでした。"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="organize">
      <p>
        <a href={libraryHref}>← 一覧へ</a>
      </p>
      <h1>CBZ に整理</h1>
      <p className="organize-naming">
        出力名: {naming === "template" ? "テンプレートの名前" : "今の名前"}(
        {naming === "template" ? (
          <a href={organizeHref(volumeId)}>今の名前にする</a>
        ) : (
          <a href={organizeTemplateHref(volumeId)}>テンプレートの名前にする</a>
        )}
        )
      </p>
      {!op && !error && <p className="muted">計画を作っています…</p>}
      {op && (
        <>
          <p>
            状態: <strong>{STATE_LABELS[op.state]}</strong>
          </p>
          {op.state === "planned" && (
            <p className="muted">
              含まれる全巻をそれぞれ 1 つの CBZ にします。すべて作って検証してから確定し、元は退避フォルダへ移します(消しません)。
              履歴から元に戻せます。
            </p>
          )}
          <OpBody op={op} />
        </>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <div className="organize-actions">
        {op?.state === "blocked" && (op.needs_analysis?.length ?? 0) > 0 && (
          <button type="button" onClick={analyzeThenReplan} disabled={busy}>
            {busy ? "調べています…" : "中の巻を調べて計画を作り直す"}
          </button>
        )}
        {op?.state === "planned" && (
          <button type="button" onClick={execute} disabled={busy}>
            {busy ? "実行中…" : "実行"}
          </button>
        )}
        {op && op.state !== "planned" && op.state !== "blocked" && <a href={organizeHistoryHref}>整理の履歴へ</a>}
      </div>
    </div>
  );
}

export function WorkOrganize({ workId }: { workId: string }) {
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [excluded, setExcluded] = useState<number[]>([]);
  const { busy, execute } = useExecute(op, setOp, setError);

  useEffect(() => {
    setError(null);
    api.planRename(workId, excluded).then(setOp, (e) => setError(errorText(e, "整理の計画を作れませんでした。")));
  }, [workId, excluded]);

  const planned = op?.state === "planned";
  const exclude = planned || op?.state === "blocked" ? (id: number) => setExcluded((x) => [...x, id]) : undefined;

  return (
    <div className="organize">
      <p>
        <a href={workHref(workId)}>← 作品へ</a>
      </p>
      <h1>テンプレートどおりに整理</h1>
      {!op && !error && <p className="muted">計画を作っています…</p>}
      {op && (
        <>
          <p>
            状態: <strong>{STATE_LABELS[op.state]}</strong>
          </p>
          {planned && (
            <p className="muted">
              「改名・移動」の巻だけを同じドライブの中で改名・移動します(中身はそのまま。読書位置などは引き継がれます)。
              競合・対象外の巻は整理しません。履歴から元に戻せます。
            </p>
          )}
          <OpBody op={op} onExclude={exclude} />
        </>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <div className="organize-actions">
        {excluded.length > 0 && (op?.state === "planned" || op?.state === "blocked") && (
          <button type="button" onClick={() => setExcluded([])} disabled={busy}>
            整理しない巻を戻す
          </button>
        )}
        {planned && (
          <button type="button" onClick={execute} disabled={busy}>
            {busy ? "実行中…" : "実行"}
          </button>
        )}
        {op && !planned && op.state !== "blocked" && <a href={organizeHistoryHref}>整理の履歴へ</a>}
      </div>
    </div>
  );
}

export function OrganizeHistory() {
  const [ops, setOps] = useState<OrganizeOp[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setOps(await api.organizeHistory());
    } catch {
      setError("整理の履歴を読み込めませんでした。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const undo = async (opId: string) => {
    setBusy(opId);
    setError(null);
    try {
      await api.undoOrganize(opId);
    } catch (e) {
      setError(errorText(e, "元に戻せませんでした。"));
    } finally {
      setBusy(null);
      await load();
    }
  };

  return (
    <div className="organize">
      <p>
        <a href={libraryHref}>← 一覧へ</a>
      </p>
      <h1>整理の履歴</h1>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {ops === null && !error && <p className="muted">読み込み中…</p>}
      {ops?.length === 0 && <p className="muted">まだ整理していません。一覧の「CBZ に整理」、作品ページの「テンプレートどおりに整理」、「ファイル名・フォルダーの整理」から始めます。</p>}
      <ul className="organize-op-list">
        {ops?.map((op) => (
          <li key={op.op_id} className="organize-op">
            <p>
              <strong>{STATE_LABELS[op.state]}</strong> <span className="muted">{op.executed_at ?? op.created_at}</span>
            </p>
            <OpBody op={op} />
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
