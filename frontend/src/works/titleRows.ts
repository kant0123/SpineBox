// 作品のタイトル一覧(#115)の行の組み立てと、行に出す値の決まり。WorkTitles.tsx から使う。
// 画面を描かずに試せるよう、判定はここへ分ける。

import type { FollowRelease, WorkDetail, WorkVolume } from "../api";

/** 行の読書状態。未読 = まだ開いていない、読了 = 最後まで読んだ */
export type TitleStatus = "unread" | "reading" | "finished";

/** 凡例に出す状態の文言 */
export const STATUS_TEXT: Record<TitleStatus, string> = { unread: "未読", reading: "読書中", finished: "読了" };

export function volumeStatus(v: Pick<WorkVolume, "progress">): TitleStatus {
  if (v.progress?.finished) return "finished";
  return v.progress ? "reading" : "unread";
}

/**
 * 巻の進み具合「N% (読んだ / 全体)」。未読の文字は出さない(行の色と凡例で示す)。
 * ページ数が未確定の巻(まだ開いていない入れ子の巻)は「—」。
 */
export function percentText(v: Pick<WorkVolume, "progress" | "page_count">): string {
  const total = v.page_count;
  if (total === null || total <= 0) return "—";
  const read = v.progress ? (v.progress.finished ? total : Math.min(v.progress.page + 1, total)) : 0;
  return `${Math.round((read / total) * 100)}% (${read}/${total})`;
}

/**
 * 巻の表示名「作品名 第N巻」。ファイル名の揺れ(英字・作者の括弧・アンダースコア)を出さない(#115)。
 * 番号の無い巻は、その巻の名前をそのまま出す(作品名だけでは見分けられないため)。
 */
export function volumeTitle(workTitle: string, v: Pick<WorkVolume, "volume_number" | "title">): string {
  const n = v.volume_number;
  if (!n) return v.title;
  const unit = n.kind === "volume" ? "巻" : "話";
  return n.start === n.end ? `${workTitle} 第${n.start}${unit}` : `${workTitle} 第${n.start}〜${n.end}${unit}`;
}

export interface OwnedTitleRow {
  kind: "slot" | "unnumbered";
  key: string;
  /** 行の左に出す巻の番号(1巻 / 1〜3巻 / 番号なし) */
  label: string;
  title: string;
  /** 巻の番号(欠けを差し込む位置)。話・番号なしは null */
  number: number | null;
  /** 同じ行にまとめた巻。重複があれば 2 冊以上 */
  volumes: WorkVolume[];
  /** 行の状態と進み具合を持つ巻(重複は読んでいる巻を優先) */
  primary: WorkVolume;
  duplicate: boolean;
  status: TitleStatus;
  progress: string;
}

export interface MissingTitleRow {
  kind: "missing";
  key: string;
  label: string;
  title: string;
  /** gap = 手元の巻の間の欠け、new = 手元の最大より後(最新巻) */
  missing: "gap" | "new";
  release: FollowRelease;
}

export type TitleRow = OwnedTitleRow | MissingTitleRow;

/** 重複の行で代表にする巻。読んでいる巻 > オフラインでない巻 > 先頭 */
function pickPrimary(vols: WorkVolume[]): WorkVolume {
  return vols.find((v) => v.progress?.finished) ?? vols.find((v) => v.progress) ?? vols.find((v) => !v.offline) ?? vols[0];
}

/**
 * 一覧の行を作る。巻の番号の順に、欠けはその番号の位置へ、続刊(最新巻)は後ろへ差し込む。
 * 欠け・最新巻はフォローの「続刊を調べる」(#87)の結果から。フォローしていなければ出さない。
 */
export function buildTitleRows(work: WorkDetail): TitleRow[] {
  const volumeRows: OwnedTitleRow[] = [];
  const episodeRows: OwnedTitleRow[] = [];
  for (const s of work.slots) {
    const primary = pickPrimary(s.volumes);
    const row: OwnedTitleRow = {
      kind: "slot", key: `slot-${s.kind}-${s.start}-${s.end}`, label: s.label, title: volumeTitle(work.title, primary),
      number: s.start, volumes: s.volumes, primary, duplicate: s.duplicate,
      status: volumeStatus(primary), progress: percentText(primary),
    };
    (s.kind === "volume" ? volumeRows : episodeRows).push(row);
  }
  const unnumbered: OwnedTitleRow[] = work.unnumbered.map((v) => ({
    kind: "unnumbered", key: `volume-${v.id}`, label: "番号なし", title: v.title, number: null,
    volumes: [v], primary: v, duplicate: false, status: volumeStatus(v), progress: percentText(v),
  }));

  const releases = work.follow?.followed ? work.follow.releases ?? [] : [];
  const gaps = releases.filter((r) => r.kind === "gap").sort((a, b) => a.volume - b.volume);
  const news = releases.filter((r) => r.kind === "new").sort((a, b) => a.volume - b.volume);

  const out: TitleRow[] = [];
  let i = 0;
  for (const gap of gaps) {
    while (i < volumeRows.length && volumeRows[i].number! < gap.volume) out.push(volumeRows[i++]);
    // 調べた後に蔵書へ入った巻は、欠けの行を出さない(持っている行はそのまま後で並ぶ)
    if (volumeRows[i]?.number === gap.volume) continue;
    out.push(missingRow(work.title, gap));
  }
  while (i < volumeRows.length) out.push(volumeRows[i++]);
  for (const n of news) out.push(missingRow(work.title, n));
  return [...out, ...episodeRows, ...unnumbered];
}

function missingRow(workTitle: string, release: FollowRelease): MissingTitleRow {
  return {
    kind: "missing", key: `release-${release.kind}-${release.volume}`, label: `${release.volume}巻`,
    title: `${workTitle} 第${release.volume}巻`, missing: release.kind === "new" ? "new" : "gap", release,
  };
}

export interface WorkProgress {
  percent: number;
  read: number;
  total: number;
  finished: number;
  volumes: number;
  /** ページ数が未確定で割合に数えられない巻(まだ開いていない小説など) */
  unknown: number;
}

/** 作品全体の進み具合(#115)。重複は 1 巻として数え、読んでいる巻の位置を使う */
export function workProgress(rows: TitleRow[]): WorkProgress {
  let read = 0;
  let total = 0;
  let finished = 0;
  let volumes = 0;
  let unknown = 0;
  for (const row of rows) {
    if (row.kind === "missing") continue;
    volumes++;
    if (row.status === "finished") finished++;
    const pages = row.primary.page_count;
    if (pages === null || pages <= 0) {
      unknown++;
      continue;
    }
    total += pages;
    const p = row.primary.progress;
    read += p?.finished ? pages : p ? Math.min(p.page + 1, pages) : 0;
  }
  return { percent: total > 0 ? Math.round((read / total) * 100) : 0, read, total, finished, volumes, unknown };
}
