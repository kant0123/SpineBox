import { useCallback, useEffect, useState } from "react";
import { api, ApiError, isNovel, type Finding, type ScanResult, type VolumeNumber, type VolumeSummary } from "../api";
import { duplicatesHref, organizeHistoryHref, organizeHref, readHref } from "../route";
import { ViewTabs } from "../works/ViewTabs";

/** 一覧のカードに出す読書状態の文言 */
export function progressLabel(v: Pick<VolumeSummary, "page_count" | "progress" | "format">): string {
  if (!v.progress) return "未読";
  if (v.progress.finished) return "既読";
  const total = v.page_count ?? "?";
  // 小説の page は区切り(章など)の番号。ページ数は文字サイズで変わるので出さない
  if (isNovel(v.format)) return `読みかけ(${v.progress.page + 1} / ${total})`;
  return `${v.progress.page + 1} / ${total} ページ`;
}

/** 一覧のカードに出す大きさの文言 */
export function sizeLabel(v: Pick<VolumeSummary, "page_count" | "format">): string {
  if (v.format === "epub") return "EPUB";
  if (v.format === "text") return "テキスト";
  return v.page_count === null ? "ページ数未確定" : `${v.page_count} ページ`;
}

/** 巻番号の範囲の表示(1 / 1〜3) */
export function rangeText(n: Pick<VolumeNumber, "start" | "end">): string {
  return n.start === n.end ? `${n.start}` : `${n.start}〜${n.end}`;
}

const numbersText = (ns: number[]) => (ns.length ? ns.join("・") : "なし");

function FindingDetail({ f }: { f: Finding }) {
  return (
    <div className="finding">
      <p>{f.message}</p>
      <dl>
        <dt>名前の範囲</dt>
        <dd>
          {rangeText(f.name_range)} 巻(「{f.subject}」の「{f.name_range.evidence}」)
        </dd>
        <dt>中身の巻番号</dt>
        <dd>{numbersText(f.found)}</dd>
        {f.missing.length > 0 && (
          <>
            <dt>欠け</dt>
            <dd>{numbersText(f.missing)}</dd>
          </>
        )}
        {f.extra.length > 0 && (
          <>
            <dt>範囲外</dt>
            <dd>{numbersText(f.extra)}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

/** スキャンで読めなかったもの。件数は常に見せ、理由の一覧は開いたときだけ */
function ScanIssues({ result }: { result: ScanResult }) {
  const shown = result.issues.length;
  return (
    <details className="scan-issues">
      <summary>読めなかったもの {result.issues_total} 件</summary>
      <ul>
        {result.issues.map((i, n) => (
          <li key={n}>
            <span className="scan-issue-path">
              {i.path}
              {i.container_path && ` › ${i.container_path}`}
              {i.entry_name && ` › ${i.entry_name}`}
            </span>
            <span className="scan-issue-reason">{i.reason}</span>
          </li>
        ))}
      </ul>
      {result.issues_total > shown && <p className="muted">ほか {result.issues_total - shown} 件は省略しました。</p>}
    </details>
  );
}

export function Library() {
  const [volumes, setVolumes] = useState<VolumeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [analyzing, setAnalyzing] = useState<VolumeSummary["id"] | null>(null);

  const load = useCallback(async () => {
    try {
      setVolumes(await api.listVolumes());
      setError(null);
    } catch {
      setError("蔵書一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const scan = async () => {
    setScanning(true);
    setScanResult(null);
    try {
      setScanResult(await api.scan());
      await load();
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : undefined;
      setError(detail ?? "スキャンに失敗しました。");
    } finally {
      setScanning(false);
    }
  };

  const analyze = async (id: VolumeSummary["id"]) => {
    setAnalyzing(id);
    try {
      await api.analyze(id);
      await load();
    } catch {
      setError("中の巻を調べられませんでした。");
    } finally {
      setAnalyzing(null);
    }
  };

  return (
    <div className="library">
      <header className="library-header">
        <h1>SpineBox</h1>
        <div className="header-actions">
          <a className="header-link" href={duplicatesHref}>
            重複の候補
          </a>
          <a href={organizeHistoryHref}>整理の履歴</a>
          <button type="button" onClick={scan} disabled={scanning}>
            {scanning ? "スキャン中…" : "スキャン"}
          </button>
        </div>
      </header>
      <ViewTabs current="volumes" />

      {scanResult && (
        <div className="notice" role="status">
          {scanResult.volumes} 巻を検出しました。
          {scanResult.offline_roots.length > 0 && (
            <> 見つからない蔵書フォルダ(オフライン扱い): {scanResult.offline_roots.join("、")}</>
          )}
          {scanResult.issues_total > 0 && <ScanIssues result={scanResult} />}
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}

      {volumes === null && !error && <p className="muted">読み込み中…</p>}
      {volumes?.length === 0 && <p className="muted">蔵書がありません。蔵書フォルダを設定して「スキャン」を押してください。</p>}

      <ul className="volume-grid">
        {volumes?.map((v) => {
          const body = (
            <>
              <span className="volume-title">{v.title}</span>
              {v.parent_title && <span className="volume-meta">「{v.parent_title}」の中</span>}
              <span className="volume-meta">{sizeLabel(v)}</span>
              <span className={`volume-progress${v.progress?.finished ? " done" : ""}`}>{progressLabel(v)}</span>
              {isNovel(v.format) && <span className="badge novel-badge">小説</span>}
              {v.offline && <span className="badge">オフライン</span>}
              {v.findings.length > 0 && <span className="badge">巻の食い違い</span>}
            </>
          );
          return (
            <li key={String(v.id)} className={`volume-card${v.offline ? " offline" : ""}`} title={v.source_name}>
              {v.offline ? (
                <div className="volume-link" aria-disabled="true">
                  {body}
                </div>
              ) : (
                <a className="volume-link" href={readHref(v.id)}>
                  {body}
                </a>
              )}
              {v.duplicate_count > 0 && (
                <div className="volume-extra">
                  <a className="badge dup-badge" href={duplicatesHref}>
                    重複の候補あり
                  </a>
                </div>
              )}
              {v.findings.length > 0 && (
                <details className="volume-extra">
                  <summary>食い違いの説明</summary>
                  {v.findings.map((f, i) => (
                    <FindingDetail key={i} f={f} />
                  ))}
                </details>
              )}
              {v.can_analyze && (
                <div className="volume-extra">
                  <button type="button" className="small" onClick={() => analyze(v.id)} disabled={analyzing !== null}>
                    {analyzing === v.id ? "調べています…" : "中の巻を調べる"}
                  </button>
                </div>
              )}
              {!v.offline && !isNovel(v.format) && (
                <a className="organize-link" href={organizeHref(v.id)}>
                  CBZ に整理
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
