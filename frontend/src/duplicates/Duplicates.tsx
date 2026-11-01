import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type DuplicateCandidate, type DuplicateScanState, type DuplicateVolume } from "../api";
import { rangeText } from "../library/Library";
import { libraryHref, readHref } from "../route";

/** 候補の根拠を一文にする */
export function evidenceText(c: DuplicateCandidate): string {
  const pages = `見た目の近いページが ${c.matched_pages} / ${c.compared_pages} ページ`;
  if (c.rule === "same_number") {
    return `巻番号の候補が同じ(「${c.evidence.number_a}」と「${c.evidence.number_b}」)で、${pages}。`;
  }
  return `どちらかの巻番号が分からないため、見た目だけで判定: ${pages}。`;
}

export function scanText(s: DuplicateScanState): string {
  if (s.running) return s.total > 0 ? `調べています… ${s.done} / ${s.total} 巻` : "調べています…";
  if (s.error) return `調べられませんでした: ${s.error}`;
  if (s.summary) {
    const errors = s.summary.errors > 0 ? `、読めなかった巻 ${s.summary.errors} 巻` : "";
    return `調べました: 新しく指紋を作った巻 ${s.summary.fingerprinted} 巻${errors}。`;
  }
  return "";
}

function Side({ v }: { v: DuplicateVolume }) {
  return (
    <div className="dup-side">
      {v.offline ? (
        <div className="dup-cover dup-cover-missing">オフライン</div>
      ) : (
        <img className="dup-cover" src={api.pageUrl(v.id, 0)} alt={`${v.title} の表紙`} loading="lazy" />
      )}
      <div className="dup-title">{v.title}</div>
      <dl className="dup-facts">
        <dt>ファイル</dt>
        <dd>{v.parent_title ? `「${v.parent_title}」の中 / ${v.source_name}` : v.source_name}</dd>
        <dt>ページ数</dt>
        <dd>{v.page_count === null ? "未確定" : `${v.page_count} ページ`}</dd>
        <dt>巻番号の候補</dt>
        <dd>{v.volume_number ? `${rangeText(v.volume_number)}(「${v.volume_number.evidence}」)` : "分からない"}</dd>
      </dl>
      {!v.offline && (
        <a className="dup-read" href={readHref(v.id)}>
          この巻を読む
        </a>
      )}
    </div>
  );
}

function Candidate({ c, onChanged }: { c: DuplicateCandidate; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const act = async () => {
    setBusy(true);
    setFailed(false);
    try {
      await (c.dismissed ? api.restoreDuplicate(c.id) : api.dismissDuplicate(c.id));
      onChanged();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="dup-card">
      <div className="dup-sides">
        <Side v={c.a} />
        <Side v={c.b} />
      </div>
      <p className="dup-evidence">{evidenceText(c)}</p>
      {c.evidence.samples.length > 0 && (
        <details className="dup-samples">
          <summary>一致したページの例</summary>
          <ul>
            {c.evidence.samples.map((s, i) => (
              <li key={i}>
                {!c.a.offline && <img src={api.pageUrl(c.a.id, s.a_page)} alt={`左 ${s.a_page + 1} ページ`} loading="lazy" />}
                {!c.b.offline && <img src={api.pageUrl(c.b.id, s.b_page)} alt={`右 ${s.b_page + 1} ページ`} loading="lazy" />}
                <span className="muted">
                  {s.a_page + 1} ページと {s.b_page + 1} ページ(差 {s.distance}/64)
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="dup-actions">
        <button type="button" className="small" onClick={act} disabled={busy}>
          {c.dismissed ? "候補に戻す" : "重複ではない"}
        </button>
        {failed && <span className="notice error">操作に失敗しました。</span>}
      </div>
    </li>
  );
}

export function Duplicates({ pollMs = 1500 }: { pollMs?: number }) {
  const [showDismissed, setShowDismissed] = useState(false);
  const [candidates, setCandidates] = useState<DuplicateCandidate[] | null>(null);
  const [scan, setScan] = useState<DuplicateScanState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const r = await api.listDuplicates(showDismissed ? "dismissed" : "open");
      if (!alive.current) return;
      setCandidates(r.candidates);
      setScan(r.scan);
      setError(null);
    } catch {
      if (alive.current) setError("重複候補を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [showDismissed]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  // 調べている間は進み具合と候補を取り直す
  const running = scan?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => void load(), pollMs);
    return () => clearInterval(t);
  }, [running, pollMs, load]);

  const start = async () => {
    try {
      setScan(await api.scanDuplicates());
      setError(null);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : undefined;
      setError(detail ? `調べられませんでした: ${detail}` : "調べられませんでした。");
    }
  };

  return (
    <div className="library duplicates">
      <header className="library-header">
        <h1>重複の候補</h1>
        <a className="back-link" href={libraryHref}>
          一覧へ戻る
        </a>
      </header>
      <p className="muted">
        同じ本の別スキャンかもしれない巻の組です。ここでは何も削除せず、動かしません。
        ページの見た目と巻番号の候補から選んだ「疑い」で、確定ではありません。
      </p>
      <div className="dup-toolbar">
        <button type="button" onClick={start} disabled={running}>
          {running ? "調べています…" : "重複を調べる"}
        </button>
        <label>
          <input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />
          却下したものを表示
        </label>
      </div>
      {scan && scanText(scan) && (
        <div className={`notice${scan.error ? " error" : ""}`} role="status">
          {scanText(scan)}
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {candidates === null && !error && <p className="muted">読み込み中…</p>}
      {candidates?.length === 0 && (
        <p className="muted">
          {showDismissed
            ? "却下した候補はありません。"
            : "重複の候補はありません。初めてのときは「重複を調べる」を押してください(蔵書の量によっては時間がかかります)。"}
        </p>
      )}
      <ul className="dup-list">
        {candidates?.map((c) => (
          <Candidate key={c.id} c={c} onChanged={() => void load()} />
        ))}
      </ul>
    </div>
  );
}
