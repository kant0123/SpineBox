import { useCallback, useEffect, useState } from "react";
import { api, type ScanResult, type VolumeSummary } from "../api";
import { organizeHistoryHref, organizeHref, readHref } from "../route";

/** 一覧のカードに出す読書状態の文言 */
export function progressLabel(v: Pick<VolumeSummary, "page_count" | "progress">): string {
  if (!v.progress) return "未読";
  if (v.progress.finished) return "既読";
  const total = v.page_count ?? "?";
  return `${v.progress.page + 1} / ${total} ページ`;
}

export function Library() {
  const [volumes, setVolumes] = useState<VolumeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);

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
    } catch {
      setError("スキャンに失敗しました。");
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="library">
      <header className="library-header">
        <h1>SpineBox</h1>
        <div className="header-actions">
          <a href={organizeHistoryHref}>整理の履歴</a>
          <button type="button" onClick={scan} disabled={scanning}>
            {scanning ? "スキャン中…" : "スキャン"}
          </button>
        </div>
      </header>

      {scanResult && (
        <div className="notice" role="status">
          {scanResult.volumes} 巻を検出しました。
          {scanResult.offline_roots.length > 0 && (
            <> 見つからない蔵書フォルダ(オフライン扱い): {scanResult.offline_roots.join("、")}</>
          )}
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
              <span className="volume-meta">{v.page_count === null ? "ページ数未確定" : `${v.page_count} ページ`}</span>
              <span className={`volume-progress${v.progress?.finished ? " done" : ""}`}>{progressLabel(v)}</span>
              {v.offline && <span className="badge">オフライン</span>}
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
              {!v.offline && (
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
