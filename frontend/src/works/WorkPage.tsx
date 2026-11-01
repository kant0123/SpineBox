import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type WorkDetail, type WorkSummary, type WorkVolume } from "../api";
import { progressLabel } from "../library/Library";
import { readHref, workHref, worksHref } from "../route";
import { confidenceText, evidenceLines, sourceText } from "./labels";
import { ProposalCard } from "./WorkProposals";

function assignmentText(v: WorkVolume): string {
  const a = v.assignment;
  if (a.manual) return "手動で決定";
  const how = a.source === "filename" && a.evidence?.level === "enclosing" ? "外側の名前から推測" : sourceText(a.source);
  return `${how}(確信度 ${confidenceText(a.confidence)})`;
}

function VolumeRow({ v, selected, onToggle }: { v: WorkVolume; selected: boolean; onToggle: () => void }) {
  return (
    <li className={`work-volume${v.offline ? " offline" : ""}`}>
      <label className="work-volume-check">
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`${v.title} を選ぶ`} />
      </label>
      <div className="work-volume-body">
        {v.offline ? (
          <span className="volume-title">{v.title}</span>
        ) : (
          <a className="volume-title" href={readHref(v.id)}>
            {v.title}
          </a>
        )}
        <span className="volume-meta">
          {v.page_count === null ? "ページ数未確定" : `${v.page_count} ページ`} / {progressLabel(v)}
          {v.offline && " / オフライン"}
        </span>
        <span className="volume-meta">{assignmentText(v)}</span>
      </div>
    </li>
  );
}

export function WorkPage({ id }: { id: string }) {
  const [work, setWork] = useState<WorkDetail | null>(null);
  const [others, setOthers] = useState<WorkSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [alias, setAlias] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [moveTarget, setMoveTarget] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = useCallback((w: WorkDetail) => {
    setWork(w);
    setTitle(w.title);
    setAuthor(w.author ?? "");
    setSelected(new Set());
  }, []);

  const load = useCallback(async () => {
    try {
      const [w, list] = await Promise.all([api.getWork(id), api.listWorks()]);
      apply(w);
      setOthers(list.works.filter((x) => String(x.id) !== id));
      setNotFound(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError("作品を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [id, apply]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : "操作に失敗しました。");
    } finally {
      setBusy(false);
    }
  };

  const toggle = (vid: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(vid)) n.delete(vid);
      else n.add(vid);
      return n;
    });

  if (notFound) {
    return (
      <div className="library">
        <p>この作品は見つかりません(別の作品に統合された可能性があります)。</p>
        <a className="back-link" href={worksHref}>
          作品の一覧へ戻る
        </a>
      </div>
    );
  }
  if (!work) {
    return (
      <div className="library">
        {error ? (
          <div className="notice error" role="alert">
            {error}
          </div>
        ) : (
          <p className="muted">読み込み中…</p>
        )}
      </div>
    );
  }

  const missing = [
    work.numbers.volume.missing.length ? `${work.numbers.volume.missing.join("・")} 巻` : "",
    work.numbers.episode.missing.length ? `${work.numbers.episode.missing.join("・")} 話` : "",
  ].filter(Boolean);
  const limited = work.numbers.volume.limited || work.numbers.episode.limited;
  const ids = [...selected];

  const save = () => {
    const body: { title?: string; author?: string } = {};
    if (title !== work.title) body.title = title;
    if (author !== (work.author ?? "")) body.author = author;
    if (Object.keys(body).length) void run(async () => apply(await api.editWork(work.id, body)));
  };

  return (
    <div className="library work-page">
      <header className="library-header">
        <h1>{work.title}</h1>
        <a className="back-link" href={worksHref}>
          作品へ戻る
        </a>
      </header>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      <section className="work-section" aria-label="作品の情報">
        <form
          className="work-edit"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <label>
            作品名
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <span className="volume-meta">
            {work.title_manual ? "手動で編集" : `${sourceText(work.title_source)}(確信度 ${confidenceText(work.title_confidence)})`}
          </span>
          <label>
            著者
            <input value={author} onChange={(e) => setAuthor(e.target.value)} />
          </label>
          <span className="volume-meta">
            {work.author_manual ? "手動で編集" : work.author ? sourceText(work.author_source) : "名前からは読み取れていません"}
          </span>
          <button type="submit" className="small" disabled={busy || !title.trim()}>
            保存
          </button>
        </form>
        <p className="muted">手で直した項目は、再スキャンや再計算、外部の情報で上書きしません。</p>

        <h2>別名</h2>
        <ul className="alias-list">
          {work.aliases.length === 0 && <li className="muted">なし</li>}
          {work.aliases.map((a) => (
            <li key={a.id}>
              <span>{a.alias}</span>
              <span className="volume-meta">{a.manual ? "手動" : `${sourceText(a.source)}(${confidenceText(a.confidence)})`}</span>
              <button
                type="button"
                className="small secondary"
                disabled={busy}
                onClick={() => void run(async () => apply(await api.removeAlias(work.id, a.id)))}
              >
                外す
              </button>
            </li>
          ))}
        </ul>
        <form
          className="work-inline"
          onSubmit={(e) => {
            e.preventDefault();
            if (alias.trim())
              void run(async () => {
                apply(await api.addAlias(work.id, alias));
                setAlias("");
              });
          }}
        >
          <input value={alias} onChange={(e) => setAlias(e.target.value)} placeholder="別名を足す" aria-label="別名を足す" />
          <button type="submit" className="small" disabled={busy || !alias.trim()}>
            足す
          </button>
        </form>
      </section>

      {work.merges.length > 0 && (
        <section className="work-section" aria-label="統合の根拠">
          <h2>統合の根拠</h2>
          {work.merges.map((m) => (
            <div key={m.id} className="merge-note">
              <p>
                「{m.titles.join("」と「")}」を{m.status === "auto" ? "自動で統合" : m.rule === "manual" ? "手動で統合" : "承認して統合"}
                (確信度 {confidenceText(m.confidence)})
              </p>
              <ul className="evidence-lines">
                {evidenceLines(m.evidence).map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {work.proposals.length > 0 && (
        <section className="work-section" aria-label="統合の提案">
          <h2>統合の提案</h2>
          <ul className="dup-list">
            {work.proposals.map((p) => (
              <ProposalCard key={p.id} p={p} onChanged={() => void load()} />
            ))}
          </ul>
        </section>
      )}

      <section className="work-section" aria-label="巻">
        <h2>巻({work.volume_count})</h2>
        {missing.length > 0 ? (
          <div className="notice warn" role="status">
            欠けている巻: {missing.join("、")}
          </div>
        ) : limited ? (
          <div className="notice" role="status">
            巻番号が大きすぎるため、欠けは数えていません。
          </div>
        ) : (
          <p className="muted">欠けている巻はありません。</p>
        )}
        <ul className="slot-list">
          {work.slots.map((s) => (
            <li key={`${s.kind}-${s.start}-${s.end}`} className={`slot${s.duplicate ? " duplicate" : ""}`}>
              <div className="slot-head">
                <strong>{s.label}</strong>
                {s.duplicate && <span className="badge dup-badge">重複 {s.volumes.length} 冊</span>}
              </div>
              <ul className="work-volumes">
                {s.volumes.map((v) => (
                  <VolumeRow key={v.id} v={v} selected={selected.has(v.id)} onToggle={() => toggle(v.id)} />
                ))}
              </ul>
            </li>
          ))}
          {work.unnumbered.length > 0 && (
            <li className="slot">
              <div className="slot-head">
                <strong>番号の読めない巻</strong>
              </div>
              <ul className="work-volumes">
                {work.unnumbered.map((v) => (
                  <VolumeRow key={v.id} v={v} selected={selected.has(v.id)} onToggle={() => toggle(v.id)} />
                ))}
              </ul>
            </li>
          )}
        </ul>

        <div className="work-move">
          <h3>選んだ巻({ids.length})を動かす</h3>
          <div className="work-inline">
            <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="新しい作品名(省略可)" aria-label="新しい作品名" />
            <button
              type="button"
              className="small"
              disabled={busy || ids.length === 0}
              onClick={() =>
                void run(async () => {
                  const r = await api.assignVolumes(ids, null, newTitle.trim() || undefined);
                  window.location.hash = workHref(r.work_id);
                })
              }
            >
              新しい作品に分ける
            </button>
          </div>
          <div className="work-inline">
            <select value={moveTarget} onChange={(e) => setMoveTarget(e.target.value)} aria-label="移す先の作品">
              <option value="">移す先の作品を選ぶ</option>
              {others.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.title}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="small"
              disabled={busy || ids.length === 0 || !moveTarget}
              onClick={() =>
                void run(async () => {
                  await api.assignVolumes(ids, Number(moveTarget));
                  setMoveTarget("");
                  await load();
                })
              }
            >
              この作品へ移す
            </button>
          </div>
          <p className="muted">動かした巻は手動の決定になり、再スキャンや再計算で戻りません。</p>
        </div>
      </section>

      <section className="work-section" aria-label="作品の統合">
        <h2>ほかの作品を統合する</h2>
        <div className="work-inline">
          <select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)} aria-label="統合する作品">
            <option value="">統合する作品を選ぶ</option>
            {others.map((o) => (
              <option key={o.id} value={o.id}>
                {o.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="small"
            disabled={busy || !mergeTarget}
            onClick={() =>
              void run(async () => {
                await api.mergeWorks(work.id, Number(mergeTarget));
                setMergeTarget("");
                await load();
              })
            }
          >
            この作品に統合する
          </button>
        </div>
      </section>
    </div>
  );
}
