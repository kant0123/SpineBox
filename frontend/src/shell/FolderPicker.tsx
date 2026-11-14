// フォルダーを選ぶモーダル(#149 で整理の画面に作り、#313 で蔵書フォルダの追加と共用にした)。
// どこのフォルダーを辿るかは置く側が load で決める(整理は蔵書フォルダの中、蔵書の管理はサーバー機全体)。
// ネイティブの <dialog> を showModal() で開く。背景の無効化・Tab の閉じ込め・Esc・背景のクリックで閉じる動きはブラウザに任せる。
// 開いたら見出しへフォーカスし、閉じたら開いたボタンへ戻す。
import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { ApiError } from "../api";

export interface FolderListing {
  /** 今のフォルダー(一番上なら "") */
  path: string;
  /** 1 つ上(一番上なら null) */
  parent: string | null;
  folders: { name: string; path: string }[];
}

export interface FolderCrumb {
  label: string;
  path: string;
}

interface Props {
  /** path の直下のフォルダーを読む */
  load: (path: string) => Promise<FolderListing>;
  initialPath: string;
  /** 一番上("")の呼び名。パンくずの先頭と、読めなかったときの案内に使う */
  topLabel: string;
  hint: string;
  /** 今のパスを、一番上より下のパンくずに分ける */
  crumbs: (path: string) => FolderCrumb[];
  /** 一番上そのものを選べるか(整理は蔵書フォルダ全体を選べる。ドライブの一覧は選べない) */
  allowTop: boolean;
  onPick: (path: string) => void;
  onClose: () => void;
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

export function FolderPicker({ load, initialPath, topLabel, hint, crumbs, allowTop, onPick, onClose }: Props) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const requestSeq = useRef(0);

  const open = useCallback(
    async (path: string) => {
      const seq = ++requestSeq.current;
      setBusy(true);
      setError(null);
      try {
        const next = await load(path);
        if (seq === requestSeq.current) setListing(next);
      } catch (e) {
        if (seq !== requestSeq.current) return;
        const message = errorText(e, "フォルダーを読めませんでした。");
        if (path !== "") {
          // 前回のパスが無くなっていたら、一番上から選び直せるようにする
          setError(`${message} ${topLabel}を表示しています。`);
          try {
            const whole = await load("");
            if (seq === requestSeq.current) setListing(whole);
          } catch {
            if (seq === requestSeq.current) setListing(null);
          }
        } else {
          setError(message);
          setListing(null);
        }
      } finally {
        if (seq === requestSeq.current) setBusy(false);
      }
    },
    [load, topLabel],
  );

  useEffect(() => {
    void open(initialPath);
  }, [open, initialPath]);

  // 開く: 開いたボタンを覚え、モーダルとして表示し、見出しへフォーカスする。
  // 取り除かれたときはブラウザが最上位レイヤーから外すので close() は呼ばない(close イベントで親を閉じてしまう)
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!el.open) el.showModal();
    heading.current?.focus();
    return () => {
      opener.current?.focus?.();
    };
  }, []);

  // closedby="any" の無いブラウザ(Safari)向け: ダイアログの外(::backdrop)のクリックで閉じる
  const dismissOnBackdrop = (e: ReactMouseEvent<HTMLDialogElement>) => {
    const el = e.currentTarget;
    if ("closedBy" in HTMLDialogElement.prototype || e.target !== el) return;
    const rect = el.getBoundingClientRect();
    const inside =
      rect.top <= e.clientY && e.clientY <= rect.top + rect.height && rect.left <= e.clientX && e.clientX <= rect.left + rect.width;
    if (!inside) el.close();
  };

  const path = listing?.path ?? initialPath;
  const trail = crumbs(path);

  return (
    <dialog
      ref={dialog}
      className="organize-picker"
      aria-labelledby="organize-picker-title"
      {...({ closedby: "any" } as object)}
      onClose={onClose}
      onClick={dismissOnBackdrop}
    >
      <h2 id="organize-picker-title" ref={heading} tabIndex={-1}>
        フォルダーを選ぶ
      </h2>
      <p className="organize-hint">{hint}</p>
      <nav aria-label="今のフォルダー">
        <ol className="organize-breadcrumb">
          <li>
            {trail.length === 0 ? (
              <span aria-current="location">{topLabel}</span>
            ) : (
              <button type="button" className="link-button" onClick={() => void open("")}>
                {topLabel}
              </button>
            )}
          </li>
          {trail.map((crumb, i) => (
            <li key={crumb.path}>
              <span className="organize-breadcrumb-sep" aria-hidden="true">/</span>
              {i === trail.length - 1 ? (
                <span aria-current="location">{crumb.label}</span>
              ) : (
                <button type="button" className="link-button" onClick={() => void open(crumb.path)}>
                  {crumb.label}
                </button>
              )}
            </li>
          ))}
        </ol>
      </nav>
      {error && (
        <div className="notice error" role="alert">
          {error}
        </div>
      )}
      {busy && <p className="muted">読み込み中…</p>}
      {listing && (
        <ul className="organize-folder-list">
          {listing.parent !== null && (
            <li>
              <button type="button" className="organize-folder-item" onClick={() => void open(listing.parent!)}>
                <span className="organize-folder-mark" aria-hidden="true">↑</span> 1 つ上へ
              </button>
            </li>
          )}
          {listing.folders.map((f) => (
            <li key={f.path}>
              <button type="button" className="organize-folder-item" onClick={() => void open(f.path)}>
                <span className="organize-folder-mark" aria-hidden="true">▸</span> {f.name}
              </button>
            </li>
          ))}
          {listing.folders.length === 0 && <li className="organize-picker-empty">この中にフォルダーはありません</li>}
        </ul>
      )}
      <div className="organize-picker-actions">
        {listing && (allowTop || listing.path !== "") && (
          <button type="button" onClick={() => onPick(listing.path)}>
            このフォルダーにする
          </button>
        )}
        <button type="button" className="secondary" onClick={onClose}>
          キャンセル
        </button>
      </div>
    </dialog>
  );
}

/** サーバー機の絶対パス(Windows の `D:\本\漫画`・UNC・`/srv/lib`)をパンくずに分ける */
export function serverCrumbs(path: string): FolderCrumb[] {
  if (!path) return [];
  const windows = /^[A-Za-z]:/.test(path) || path.startsWith("\\\\");
  if (!windows) {
    const parts = path.split("/").filter(Boolean);
    return [{ label: "/", path: "/" }, ...parts.map((p, i) => ({ label: p, path: `/${parts.slice(0, i + 1).join("/")}` }))];
  }
  const parts = path.split(/[\\/]+/).filter(Boolean);
  // UNC は \\サーバー\共有 までが 1 つ(その上は辿れない)
  const head = path.startsWith("\\\\") ? `\\\\${parts.splice(0, 2).join("\\")}` : `${parts.shift()}\\`;
  const out: FolderCrumb[] = [{ label: head, path: head }];
  parts.forEach((p, i) => {
    out.push({ label: p, path: `${head.replace(/\\$/, "")}\\${parts.slice(0, i + 1).join("\\")}` });
  });
  return out;
}
