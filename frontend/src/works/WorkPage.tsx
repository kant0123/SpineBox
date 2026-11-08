import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type SourceName, type WorkDetail, type WorkEditBody, type WorkFieldName, type WorkKind, type WorkSummary, type WorkVolume } from "../api";
import { progressLabel } from "../library/Library";
import { readHref, reviewHref, workHref, workOrganizeHref, workTitlesHref, worksHref } from "../route";
import { BookmarkButton } from "../bookmarks/BookmarkButton";
import { Credits } from "./Credits";
import { KIND_TEXT, confidenceText, evidenceLines, fieldNote, missingLabel, sourceText } from "./labels";
import { VolumeMeta } from "./VolumeMeta";
import { WorkLinks } from "./WorkLinks";
import { PROPOSAL_RULE, ProposalCard } from "./WorkProposals";
import { WorkFollowPanel } from "../follows/Follows";

const FIELD_NAMES: Record<WorkFieldName, string> = {
  title: "作品名", author: "著者", kind: "種別", publisher: "出版社", genres: "ジャンル", tags: "タグ",
};
const splitList = (text: string): string[] => text.split(/[、,，\n]/).map((x) => x.trim()).filter(Boolean);

function assignmentText(v: WorkVolume): string {
  const a = v.assignment;
  if (a.manual) return "手動で決定";
  const how = a.source === "filename" && a.evidence?.level === "enclosing" ? "外側の名前から推測" : sourceText(a.source);
  return `${how}(確信度 ${confidenceText(a.confidence)})`;
}

function VolumeRow({ v, selected, onToggle, busy, run, onChanged }: { v: WorkVolume; selected: boolean; onToggle: () => void; busy: boolean; run: (fn: () => Promise<unknown>) => Promise<void>; onChanged: () => Promise<void> }) {
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
        <BookmarkButton target="volume" id={v.id} initial={v.bookmarked ?? false} label={v.title} />
        <VolumeMeta v={v} busy={busy} run={run} onChanged={onChanged} />
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
  const [kind, setKind] = useState("");
  const [publisher, setPublisher] = useState("");
  const [genres, setGenres] = useState("");
  const [tags, setTags] = useState("");
  const [alias, setAlias] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [moveTarget, setMoveTarget] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = useCallback((w: WorkDetail) => {
    // 取得・自動に戻すなどで別の作品に自動統合され、吸収先の詳細が返ったときは、吸収先のページへ移る
    if (String(w.id) !== id) {
      window.location.hash = workHref(w.id);
      return;
    }
    setWork(w);
    setTitle(w.title);
    setAuthor(w.author ?? "");
    setKind(w.kind ?? "");
    setPublisher(w.publisher ?? "");
    setGenres(w.genres.join("、"));
    setTags(w.tags.join("、"));
    setSelected(new Set());
  }, [id]);

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

  const missing = missingLabel({
    missing_volumes: work.numbers.volume.missing,
    missing_episodes: work.numbers.episode.missing,
  });
  const limited = work.numbers.volume.limited || work.numbers.episode.limited;
  const ids = [...selected];

  const save = () => {
    const body: WorkEditBody = {};
    if (title !== work.title) body.title = title;
    if (author !== (work.author ?? "")) body.author = author;
    if (kind !== (work.kind ?? "")) body.kind = (kind || null) as WorkKind | null;
    if (publisher !== (work.publisher ?? "")) body.publisher = publisher;
    if (genres !== work.genres.join("、")) body.genres = splitList(genres);
    if (tags !== work.tags.join("、")) body.tags = splitList(tags);
    if (Object.keys(body).length) void run(async () => apply(await api.editWork(work.id, body)));
  };

  const note = (name: WorkFieldName, unset?: string) => {
    const info = work.fields[name];
    return (
      <span className="volume-meta">
        {fieldNote(info, unset)}
        {info?.manual && (
          <button
            type="button"
            className="small secondary"
            disabled={busy}
            onClick={() => void run(async () => apply(await api.resetWorkField(work.id, name)))}
            aria-label={`${FIELD_NAMES[name]}を自動に戻す`}
          >
            自動に戻す
          </button>
        )}
      </span>
    );
  };
  const enabledSources = work.links.map((l) => l.source) as SourceName[];

  return (
    <div className="library work-page">
      <header className="library-header">
        <h1>{work.title}</h1>
        <a className="back-link" href={workTitlesHref(work.id)}>
          タイトル一覧へ
        </a>
      </header>
      <div className="work-actions">
        <BookmarkButton key={work.id} target="work" id={work.id} initial={work.bookmarked ?? false} label={work.title} />
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      <WorkFollowPanel workId={work.id} follow={work.follow} onChanged={(follow) => setWork({ ...work, follow })} />

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
          {note("title")}
          <label>
            著者
            <input value={author} onChange={(e) => setAuthor(e.target.value)} />
          </label>
          {note("author", "名前からは読み取れていません")}
          <label>
            種別
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">未設定</option>
              <option value="manga">{KIND_TEXT.manga}</option>
              <option value="novel">{KIND_TEXT.novel}</option>
            </select>
          </label>
          {note("kind")}
          <label>
            出版社
            <input value={publisher} onChange={(e) => setPublisher(e.target.value)} />
          </label>
          {note("publisher")}
          <label>
            ジャンル(「、」区切り)
            <input value={genres} onChange={(e) => setGenres(e.target.value)} />
          </label>
          {note("genres")}
          <label>
            タグ(「、」区切り)
            <input value={tags} onChange={(e) => setTags(e.target.value)} />
          </label>
          {note("tags")}
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
                const updated = await api.addAlias(work.id, alias);
                setAlias("");
                // 別名の一致で自動統合され、この作品が別の作品に吸収されたときは、吸収先のページへ移る
                if (updated.id !== work.id) window.location.hash = workHref(updated.id);
                else apply(updated);
              });
          }}
        >
          <label htmlFor="add-alias-input" className="organize-label">別名を足す</label>
          <input id="add-alias-input" value={alias} onChange={(e) => setAlias(e.target.value)} aria-label="別名を足す" />
          <button type="submit" className="small" disabled={busy || !alias.trim()}>
            足す
          </button>
        </form>
      </section>

      <section className="work-section" aria-label="公開データベース">
        <div className="section-head">
          <h2>公開データベース</h2>
          <button
            type="button"
            className="small"
            disabled={busy || enabledSources.length === 0}
            onClick={() => void run(async () => apply(await api.fetchWorkMetadata(work.id)))}
          >
            {busy ? "取得中…" : "情報を取得"}
          </button>
        </div>
        {work.link_pending > 0 && (
          <p className="review-hint">
            確認待ちの候補があります。ほかの作品の候補とまとめて選ぶなら <a href={reviewHref("links")}>確認 &gt; 公開DBの候補</a>
          </p>
        )}
        <WorkLinks work={work} busy={busy} run={run} onWork={apply} />
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
          <p className="muted">{PROPOSAL_RULE}</p>
          <ul className="dup-list">
            {work.proposals.map((p) => (
              <ProposalCard key={p.id} p={p} onChanged={() => void load()} />
            ))}
          </ul>
        </section>
      )}

      <section className="work-section" aria-label="巻">
        <h2>巻({work.volume_count})</h2>
        <p>
          <a className="organize-link-inline" href={workOrganizeHref(work.id)}>
            この作品をテンプレートどおりに整理
          </a>
          <span className="muted">(全巻の変更前 → 変更後をプレビューしてから実行します)</span>
        </p>
        {missing.text ? (
          <div className="notice warn" role="status" title={missing.title}>
            欠けている巻: {missing.text}
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
                  <VolumeRow key={v.id} v={v} selected={selected.has(v.id)} onToggle={() => toggle(v.id)} busy={busy} run={run} onChanged={load} />
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
                  <VolumeRow key={v.id} v={v} selected={selected.has(v.id)} onToggle={() => toggle(v.id)} busy={busy} run={run} onChanged={load} />
                ))}
              </ul>
            </li>
          )}
        </ul>

        <div className="work-move">
          <h3>選んだ巻({ids.length})を動かす</h3>
          <div className="work-inline">
            <label htmlFor="move-new-title-input" className="organize-label">新しい作品名(省略可)</label>
            <input id="move-new-title-input" value={newTitle} onChange={(e) => setNewTitle(e.target.value)} aria-label="新しい作品名" />
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
      <Credits sources={enabledSources} />
    </div>
  );
}
