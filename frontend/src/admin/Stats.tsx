// 管理の統計タブ(#85)。数はすべて GET /api/stats(DB と展開キャッシュの今の状態)。蔵書の切り替えに従う。
import { useEffect, useState } from "react";
import { api, type CacheUsage, type LibraryStats } from "../api";
import { duplicatesHref } from "../route";
import { useLibraryScope } from "../shell/libraryScope";
import { formatBytes, formatCount } from "./format";

function Tile({ label, value, note, href }: { label: string; value: number | string; note?: string; href?: string }) {
  const body = (
    <>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{typeof value === "number" ? formatCount(value) : value}</span>
      {note && <span className="stat-note">{note}</span>}
    </>
  );
  return <li className="stat-tile">{href ? <a className="stat-body" href={href}>{body}</a> : <div className="stat-body">{body}</div>}</li>;
}

function CacheTile({ label, usage }: { label: string; usage: CacheUsage }) {
  const pct = usage.limit_bytes > 0 ? Math.min(100, Math.round((usage.used_bytes / usage.limit_bytes) * 100)) : 0;
  return (
    <li className="stat-tile"><div className="stat-body">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{formatBytes(usage.used_bytes)}</span>
      <span className="stat-note">{usage.limit_bytes > 0 ? `上限 ${formatBytes(usage.limit_bytes)}(${pct}%)` : "使わない設定"}</span>
      {usage.limit_bytes > 0 && (
        <span className="stat-meter" role="meter" aria-label={`${label}の使用率`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <span style={{ width: `${pct}%` }} />
        </span>
      )}
    </div></li>
  );
}

export function Stats() {
  const { root, libraries } = useLibraryScope();
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setStats(null);
    api.stats(root).then(
      (s) => {
        if (!alive) return;
        setStats(s);
        setError(null);
      },
      () => alive && setError("統計を読み込めませんでした。サーバーが動いているか確認してください。"),
    );
    return () => {
      alive = false;
    };
  }, [root]);

  const scopeName = root ? libraries?.find((l) => String(l.id) === root)?.name ?? "選んだ蔵書" : "すべての蔵書";
  if (error) return <div className="notice error" role="alert">{error}</div>;
  if (!stats) return <p className="muted">読み込み中…</p>;
  const { works: w, volumes: v, pages } = stats;
  return (
    <div className="stats">
      <p className="muted stats-scope">対象: {scopeName}</p>
      <h2>蔵書</h2>
      <ul className="stat-grid">
        <Tile label="作品" value={w.total} note={`漫画 ${formatCount(w.manga)} / 小説 ${formatCount(w.novel)} / 種別未定 ${formatCount(w.kind_unknown)}`} />
        <Tile label="巻" value={v.total} note={`画像 ${formatCount(v.images)} / 小説 ${formatCount(v.novels)}`} />
        <Tile label="ページ" value={pages.total} note={pages.unknown_volumes ? `ページ数未確定の巻 ${formatCount(pages.unknown_volumes)}` : "画像の巻の合計"} />
        <Tile label="オフラインの巻" value={v.offline} />
      </ul>
      <h2>読書</h2>
      <ul className="stat-grid">
        <Tile label="既読の作品" value={w.finished} note={`巻 ${formatCount(v.finished)}`} />
        <Tile label="読書中の作品" value={w.reading} note={`巻 ${formatCount(v.reading)}`} />
        <Tile label="未読の作品" value={w.unread} note={`巻 ${formatCount(v.unread)}`} />
      </ul>
      <h2>確認待ち</h2>
      <ul className="stat-grid">
        <Tile label="重複の候補" value={stats.duplicate_candidates} href={duplicatesHref} />
        <Tile label="情報の補完待ち" value={w.link_pending} note="公開 DB の候補の確認待ち" />
        <Tile label="欠けのある作品" value={w.with_missing} />
        <Tile label="表紙なし" value={w.no_cover} note="まだ表紙を作っていない作品" />
        <Tile label="作品にならなかった巻" value={stats.unassigned_volumes} />
        <Tile label="読めなかったもの" value={stats.scan_issues} note="すべての蔵書(前回のスキャン)" />
      </ul>
      <h2>キャッシュ</h2>
      <ul className="stat-grid">
        <CacheTile label="展開キャッシュ(ディスク)" usage={stats.cache.disk} />
        <CacheTile label="ページのキャッシュ(RAM)" usage={stats.cache.memory} />
        <CacheTile label="表紙" usage={stats.cache.thumbnails} />
      </ul>
    </div>
  );
}
