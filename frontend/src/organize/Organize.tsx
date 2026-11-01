// 整理(CBZ 化)の画面。プレビュー(計画)→ 実行、履歴 → 元に戻す。蔵書に書き込むのはここからの明示操作だけ。
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type OrganizeOp, type OrganizeState } from "../api";
import { libraryHref, organizeHistoryHref } from "../route";

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

function OpBody({ op }: { op: OrganizeOp }) {
  return (
    <>
      <p>
        元: <code>{op.source_rel_path}</code>({op.source_kind === "folder" ? "画像フォルダ" : "アーカイブ"})
      </p>
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

export function OrganizePlan({ volumeId }: { volumeId: string }) {
  const [op, setOp] = useState<OrganizeOp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.planOrganize(volumeId).then(setOp, (e) => setError(errorText(e, "整理の計画を作れませんでした。")));
  }, [volumeId]);

  const execute = async () => {
    if (!op?.op_id) return;
    setBusy(true);
    setError(null);
    try {
      setOp(await api.executeOrganize(op.op_id));
    } catch (e) {
      setError(errorText(e, "整理に失敗しました。"));
      if (e instanceof ApiError && e.status === 409) {
        // 失敗した操作の状態(巻き戻し済みか)を出し直す
        api.organizeHistory().then((h) => setOp(h.find((x) => x.op_id === op.op_id) ?? op), () => undefined);
      }
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
      {ops?.length === 0 && <p className="muted">まだ整理していません。一覧の「CBZ に整理」から始めます。</p>}
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
