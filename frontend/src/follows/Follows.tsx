import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type FollowList, type FollowRelease, type FollowReleaseWork, type FollowStatus, type WorkFollow } from "../api";
import { followManageHref, followsHref, workHref, worksHref } from "../route";
import { failureText } from "../works/labels";

// フォロー(#87)。フォローした作品の続刊(持っていない新しい巻)と欠けた巻を、国立国会図書館サーチの巻ごとの書誌で調べて知らせる。
// 知らせるだけで、本の入手(ダウンロード・購入)はしない。

export const FOLLOW_STATUS_TEXT: Record<FollowStatus, string> = {
  ok: "調べました",
  unlinked: "国立国会図書館サーチと未連携(作品ページで候補を選んでください)",
  no_numbers: "巻番号が読めないため数えられません",
  kind_mismatch: "連携した本の種別(漫画/小説)が作品と違うため数えていません",
  disabled: "この作品の蔵書では国立国会図書館サーチが無効です",
  failed: "取得できませんでした",
};

/** ISO の日時を「2026/11/02 10:05」の形に */
export function dateTimeText(iso: string | null | undefined): string {
  if (!iso) return "未確認";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 公開データベースの発売日(`2015-03` / `2015`)を「2015年3月」に */
export function releasedText(released: string | null): string {
  if (!released) return "発売日不明";
  const m = released.match(/^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?/);
  if (!m) return released;
  return `${m[1]}年${m[2] ? `${Number(m[2])}月` : ""}${m[3] ? `${Number(m[3])}日` : ""}`;
}

export function ReleaseLine({ r }: { r: FollowRelease }) {
  return (
    <li className={`follow-release${r.seen ? " seen" : ""}`}>
      <span className={`follow-kind ${r.kind}`}>{r.kind === "new" ? "続刊" : "欠け"}</span>
      <span className="follow-volume">{r.volume_label ?? `${r.volume} 巻`}</span>
      <span className="volume-meta">{releasedText(r.released)}</span>
      {r.url && (
        <a className="volume-meta" href={r.url} target="_blank" rel="noreferrer noopener">
          書誌
        </a>
      )}
      {r.seen && <span className="volume-meta">確認済み</span>}
    </li>
  );
}

function ReleaseCard({ w, onChanged }: { w: FollowReleaseWork; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const unseen = w.releases.filter((r) => !r.seen).length;
  const seen = async () => {
    setBusy(true);
    setFailed(false);
    try {
      await api.markFollowSeen(w.work_id);
      onChanged();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="dup-card follow-card">
      <div className="follow-card-head">
        <a className="dup-title" href={workHref(w.work_id)}>
          {w.title}
        </a>
        <span className="volume-meta">手元は {w.owned_max ?? "?"} 巻まで / 調べた日時 {dateTimeText(w.checked_at)}</span>
      </div>
      <ul className="follow-releases">
        {w.releases.map((r) => (
          <ReleaseLine key={r.volume} r={r} />
        ))}
      </ul>
      {w.failure && <p className="volume-meta">最後の確認は {failureText(w.failure)}(前回までの結果を表示しています)</p>}
      <div className="dup-actions">
        {unseen > 0 && (
          <button type="button" className="small" onClick={() => void seen()} disabled={busy}>
            確認済みにする
          </button>
        )}
        {failed && <span className="notice error">操作に失敗しました。</span>}
      </div>
    </li>
  );
}

/** サイドバーの「フォロー」: 続刊・欠けのある作品(新しい発売日順) */
export function FollowReleases() {
  const [showSeen, setShowSeen] = useState(false);
  const [items, setItems] = useState<FollowReleaseWork[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems((await api.listFollowReleases(showSeen)).works);
      setError(null);
    } catch {
      setError("フォローの一覧を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, [showSeen]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="library follows">
      <header className="library-header">
        <h1>フォロー</h1>
        <div className="header-actions">
          <a className="header-link" href={followManageHref}>
            フォロー管理
          </a>
          <a className="back-link" href={worksHref}>
            作品へ戻る
          </a>
        </div>
      </header>
      <p className="muted">
        フォローした作品のうち、手元に無い巻(続刊・欠け)が国立国会図書館サーチにあるものです。知らせるだけで、本の入手はしません。
      </p>
      <div className="dup-toolbar">
        <label>
          <input type="checkbox" checked={showSeen} onChange={(e) => setShowSeen(e.target.checked)} />
          確認済みの巻も表示
        </label>
      </div>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {items === null && !error && <p className="muted">読み込み中…</p>}
      {items?.length === 0 && (
        <p className="muted">
          新しい続刊・欠けはありません。<a href={followManageHref}>フォロー管理</a>で続刊を調べられます。
        </p>
      )}
      <ul className="dup-list">
        {items?.map((w) => (
          <ReleaseCard key={w.work_id} w={w} onChanged={() => void load()} />
        ))}
      </ul>
    </div>
  );
}

/** 管理の「フォロー管理」: フォロー中の作品の一覧・解除・最後に調べた日時、続刊を調べる */
export function FollowManage({ embedded = false }: { embedded?: boolean }) {
  const [data, setData] = useState<FollowList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.listFollows());
      setError(null);
    } catch {
      setError("フォロー中の作品を読み込めませんでした。サーバーが動いているか確認してください。");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const running = data?.state.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => {
      api.listFollows().then(setData, () => undefined);
    }, 2000);
    return () => window.clearInterval(t);
  }, [running]);

  const check = async (ids?: number[]) => {
    setError(null);
    try {
      const state = await api.checkFollows(ids);
      setData((d) => (d ? { ...d, state } : d));
      if (!state.running) await load();
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : "続刊を調べられませんでした。");
    }
  };

  const unfollow = async (id: number) => {
    setBusy(true);
    try {
      await api.unfollowWork(id);
      await load();
    } catch {
      setError("フォローを外せませんでした。");
    } finally {
      setBusy(false);
    }
  };

  const state = data?.state;
  return (
    <div className="library follows">
      <header className="library-header">
        {embedded ? <span /> : <h1>フォロー管理</h1>}
        <div className="header-actions">
          <a className="header-link" href={followsHref}>
            続刊・欠けの一覧を見る
          </a>
          {data && (
            <button type="button" onClick={() => void check()} disabled={running || !data.available || data.follows.length === 0}>
              {running ? `調べています… ${state!.done} / ${state!.total}` : "すべての続刊を調べる"}
            </button>
          )}
        </div>
      </header>
      <p className="muted">
        作品ページの「フォローする」で追加します。続刊は国立国会図書館サーチの巻ごとの書誌で調べます
        {data?.background ? `(自動で ${data.interval_hours} 時間ごとにも調べます)` : "(自動の定期確認は設定で有効にできます。既定は無効)"}。
      </p>
      {data && !data.available && (
        <div className="notice warn" role="status">
          国立国会図書館サーチが設定でもどの蔵書でも有効になっていないため、続刊を調べられません(管理の「蔵書」タブで選べます)。
        </div>
      )}
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {state?.error && (
        <div className="notice warn" role="status">
          前回の確認は途中で止まりました: {state.error}
        </div>
      )}
      {!running && state?.summary && (
        <p className="muted" role="status">
          {state.summary.works} 作品を調べました。
        </p>
      )}
      {data === null && !error && <p className="muted">読み込み中…</p>}
      {data?.follows.length === 0 && <p className="muted">フォロー中の作品はありません。</p>}
      <ul className="dup-list">
        {data?.follows.map((f) => (
          <li key={f.work_id} className="dup-card follow-entry">
            <a className="dup-title" href={workHref(f.work_id)}>
              {f.title}
            </a>
            <span className="volume-meta">
              最後に調べた日時: {dateTimeText(f.checked_at)}
              {f.status && ` / ${FOLLOW_STATUS_TEXT[f.status]}`}
            </span>
            {f.failure && <span className="volume-meta">取得の失敗: {failureText(f.failure)}</span>}
            <span className="volume-meta">
              手元に無い巻 {f.found}(未確認 {f.unseen})
            </span>
            <div className="dup-actions">
              <button type="button" className="small" onClick={() => void check([f.work_id])} disabled={running || !data.available}>
                この作品を調べる
              </button>
              <button type="button" className="small secondary" onClick={() => void unfollow(f.work_id)} disabled={busy}>
                フォローを外す
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 作品ページの「フォローする」と、見つけた続刊・欠け */
export function WorkFollowPanel({ workId, follow, onChanged }: { workId: number; follow: WorkFollow | undefined; onChanged: (f: WorkFollow) => void }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const followed = follow?.followed ?? false;
  const toggle = async () => {
    setBusy(true);
    setFailed(false);
    try {
      onChanged(followed ? await api.unfollowWork(workId) : await api.followWork(workId));
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const releases = follow?.releases ?? [];
  return (
    <div className="work-section follow-panel" role="group" aria-label="フォロー">
      <div className="dup-actions">
        <button type="button" className={followed ? "small secondary" : "small"} onClick={() => void toggle()} disabled={busy} aria-pressed={followed}>
          {followed ? "フォロー中(外す)" : "フォローする"}
        </button>
        {followed && (
          <span className="volume-meta">
            続刊の確認: {dateTimeText(follow?.checked_at)}
            {follow?.status && ` / ${FOLLOW_STATUS_TEXT[follow.status]}`} ・ <a href={followManageHref}>フォロー管理</a>
          </span>
        )}
        {failed && <span className="notice error">操作に失敗しました。</span>}
      </div>
      {followed && releases.length > 0 && (
        <ul className="follow-releases">
          {releases.map((r) => (
            <ReleaseLine key={r.volume} r={r} />
          ))}
        </ul>
      )}
    </div>
  );
}
