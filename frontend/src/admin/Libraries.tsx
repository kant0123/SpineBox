// 管理の「蔵書」タブ(#313)。蔵書(サイドバーで切り替える単位)と、その蔵書フォルダ(サーバー機のフォルダ)を足す・外す・移す。
// 蔵書フォルダは DB が正で、ここが唯一の編集口(設定ファイルの library_roots は初回の取り込み元)。
// フォルダを外しても本・読書の記録は消えず、同じフォルダを足し直すと戻る。
// 開いている入力フォームは 1 つだけ(塗りのボタンを 1 画面 1 つにするため)。
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api, type LibraryFolder, type LibraryGroup } from "../api";
import { adminHref } from "../route";
import { FolderPicker, serverCrumbs } from "../shell/FolderPicker";
import { useLibraryScope } from "../shell/libraryScope";

function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.detail ? e.detail : fallback;
}

/** 開いている入力フォーム。group が null の add-folder は、蔵書が 1 つも無いとき(「デフォルト」を作って入れる) */
type Editing = { kind: "add-folder"; group: number | null } | { kind: "rename"; group: number } | null;

interface Notice {
  text: string;
  /** フォルダを足した後だけ、スキャンのボタンを添える */
  offerScan?: boolean;
}

export function Libraries() {
  const { libraries, reload } = useLibraryScope();
  const [loadFailed, setLoadFailed] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newNameError, setNewNameError] = useState<string | null>(null);
  const newNameInput = useRef<HTMLInputElement>(null);
  /** フォームを閉じた後にフォーカスを戻す先(開いたボタンの id) */
  const returnTo = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      await reload();
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, [reload]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const close = useCallback(() => {
    setEditing(null);
    const id = returnTo.current;
    returnTo.current = null;
    // フォームが消えた後の描画で、開いたボタンへ戻す
    if (id) setTimeout(() => document.getElementById(id)?.focus(), 0);
  }, []);

  const openForm = (next: Editing, openerId: string) => {
    returnTo.current = openerId;
    setError(null);
    setEditing(next);
  };

  /** 変更を 1 つ行い、一覧を取り直す。失敗したら理由を出して false */
  const run = async (action: () => Promise<unknown>, done: Notice, fallback: string): Promise<boolean> => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
      await refresh();
      setNotice(done);
      return true;
    } catch (e) {
      setError(errorText(e, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const addGroup = async (e: FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name) {
      setNewNameError("蔵書の名前を入力してください。");
      newNameInput.current?.focus();
      return;
    }
    setNewNameError(null);
    if (await run(() => api.createLibraryGroup(name), { text: `蔵書「${name}」を追加しました。` }, "蔵書を追加できませんでした。")) {
      setNewName("");
    }
  };

  const scan = async () => {
    setError(null);
    try {
      await api.startJob("scan");
      setNotice({ text: "スキャンを依頼しました。終わると本が一覧に現れます。" });
    } catch (e) {
      setError(errorText(e, "スキャンを依頼できませんでした。"));
    }
  };

  if (libraries === null) {
    return (
      <div className="libraries-admin">
        {loadFailed ? (
          <>
            <div className="notice error" role="alert">蔵書を読み込めませんでした。</div>
            <button type="button" className="secondary" onClick={() => void refresh()}>もう一度読み込む</button>
          </>
        ) : (
          <p className="muted" role="status">読み込み中…</p>
        )}
      </div>
    );
  }

  const others = (g: LibraryGroup) => libraries.filter((x) => x.id !== g.id);

  return (
    <div className="libraries-admin" aria-busy={busy}>
      <p className="muted libraries-lead">
        蔵書はサイドバーで切り替える単位です。1 つの蔵書に、サーバー機のフォルダをいくつでも入れられます。
        フォルダを外しても本と読書の記録は消えず、同じフォルダを追加し直すと元に戻ります。
      </p>
      {error && <div className="notice error" role="alert">{error}</div>}
      {notice && (
        <div className="notice success libraries-notice" role="status">
          <span>{notice.text}</span>
          {notice.offerScan && (
            <button type="button" className="secondary small" onClick={() => void scan()}>スキャンする</button>
          )}
          {!notice.offerScan && notice.text.startsWith("スキャンを依頼") && (
            <a href={adminHref("tasks")}>タスクセンターで進み具合を見る</a>
          )}
        </div>
      )}

      {libraries.length === 0 && (
        <section className="job-section" aria-labelledby="libraries-empty-title">
          <h2 id="libraries-empty-title">蔵書がまだありません</h2>
          <p>フォルダを追加すると、「デフォルト」という蔵書を作ってそこへ入れます。名前はあとから変えられます。</p>
          {editing?.kind === "add-folder" && editing.group === null ? (
            <AddFolderForm
              idPrefix="libraries-first"
              busy={busy}
              onCancel={close}
              onSubmit={async (path) => {
                if (await run(() => api.addLibraryFolder(path, null),
                  { text: "フォルダを追加しました。スキャンすると本が一覧に現れます。", offerScan: true },
                  "フォルダを追加できませんでした。")) close();
              }}
            />
          ) : (
            <button type="button" id="libraries-first-add"
              onClick={() => openForm({ kind: "add-folder", group: null }, "libraries-first-add")}>
              フォルダを追加
            </button>
          )}
        </section>
      )}

      {libraries.map((g) => {
        const adding = editing?.kind === "add-folder" && editing.group === g.id;
        const renaming = editing?.kind === "rename" && editing.group === g.id;
        const base = `library-${g.id}`;
        return (
          <section key={g.id} className="job-section library-group" aria-labelledby={`${base}-title`}>
            <h2 id={`${base}-title`}>
              {g.name}
              <span className="library-group-count">({g.volume_count} 巻)</span>
            </h2>
            {g.folders.length === 0 ? (
              <p className="muted">フォルダがありません。</p>
            ) : (
              <ul className="library-folders">
                {g.folders.map((f) => (
                  <FolderRow
                    key={f.id}
                    folder={f}
                    groupName={g.name}
                    targets={others(g)}
                    busy={busy}
                    onMove={(to) => void run(() => api.moveLibraryFolder(f.id, to.id),
                      { text: `フォルダを「${to.name}」へ移しました。` }, "フォルダを移せませんでした。")}
                    onRemove={() => void run(() => api.removeLibraryFolder(f.id),
                      { text: "フォルダを外しました。本と読書の記録は残っています(同じフォルダを追加し直すと戻ります)。" },
                      "フォルダを外せませんでした。")}
                  />
                ))}
              </ul>
            )}
            {adding && (
              <AddFolderForm
                idPrefix={base}
                busy={busy}
                onCancel={close}
                onSubmit={async (path) => {
                  if (await run(() => api.addLibraryFolder(path, g.id),
                    { text: `「${g.name}」にフォルダを追加しました。スキャンすると本が一覧に現れます。`, offerScan: true },
                    "フォルダを追加できませんでした。")) close();
                }}
              />
            )}
            {renaming && (
              <RenameForm
                idPrefix={base}
                initial={g.name}
                busy={busy}
                onCancel={close}
                onSubmit={async (name) => {
                  if (await run(() => api.renameLibraryGroup(g.id, name),
                    { text: `蔵書の名前を「${name}」に変えました。` }, "名前を変えられませんでした。")) close();
                }}
              />
            )}
            {!adding && !renaming && (
              <div className="library-group-actions">
                <button type="button" className="secondary" id={`${base}-add`}
                  onClick={() => openForm({ kind: "add-folder", group: g.id }, `${base}-add`)}>
                  フォルダを追加<span className="sr-only">(蔵書「{g.name}」)</span>
                </button>
                <button type="button" className="secondary" id={`${base}-rename`}
                  onClick={() => openForm({ kind: "rename", group: g.id }, `${base}-rename`)}>
                  名前を変える<span className="sr-only">(蔵書「{g.name}」)</span>
                </button>
                {g.folders.length === 0 && (
                  <button type="button" className="secondary"
                    onClick={() => void run(() => api.deleteLibraryGroup(g.id),
                      { text: `蔵書「${g.name}」を削除しました。` }, "蔵書を削除できませんでした。")}>
                    蔵書を削除<span className="sr-only">(蔵書「{g.name}」)</span>
                  </button>
                )}
              </div>
            )}
          </section>
        );
      })}

      <section className="job-section" aria-labelledby="libraries-new-title">
        <h2 id="libraries-new-title">蔵書を追加</h2>
        <form className="library-form" onSubmit={(e) => void addGroup(e)} noValidate>
          <div className="organize-field">
            <label htmlFor="libraries-new-name">蔵書の名前</label>
            <p className="organize-hint muted" id="libraries-new-name-hint">例: 本、同人。フォルダは追加したあとで入れます。</p>
            {newNameError && <p className="library-field-error" id="libraries-new-name-error">{newNameError}</p>}
            <div className="organize-inline">
              <input
                id="libraries-new-name"
                ref={newNameInput}
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                aria-describedby={newNameError ? "libraries-new-name-error libraries-new-name-hint" : "libraries-new-name-hint"}
                aria-invalid={newNameError ? true : undefined}
              />
              <button type="submit" className="secondary">蔵書を追加</button>
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}

function FolderRow({ folder, groupName, targets, busy, onMove, onRemove }: {
  folder: LibraryFolder;
  groupName: string;
  targets: LibraryGroup[];
  busy: boolean;
  onMove: (to: LibraryGroup) => void;
  onRemove: () => void;
}) {
  const [target, setTarget] = useState("");
  const [targetError, setTargetError] = useState(false);
  const select = useRef<HTMLSelectElement>(null);
  const selectId = `library-folder-${folder.id}-target`;

  const move = () => {
    const to = targets.find((t) => String(t.id) === target);
    if (!to) {
      setTargetError(true);
      select.current?.focus();
      return;
    }
    setTargetError(false);
    setTarget("");
    onMove(to);
  };

  return (
    <li className="library-folder">
      <div className="library-folder-text">
        <span className="library-folder-path">{folder.path}</span>
        <span className="muted library-folder-meta">
          {folder.volume_count} 巻{folder.online ? "" : "・オフライン(今はサーバー機から見えません)"}
        </span>
      </div>
      <div className="library-folder-actions" aria-busy={busy}>
        {targets.length > 0 && (
          <div className="library-folder-move">
            <label htmlFor={selectId}>移す先<span className="sr-only">({folder.path})</span></label>
            {targetError && <p className="library-field-error" id={`${selectId}-error`}>移す先の蔵書を選んでください。</p>}
            <div className="organize-inline">
              <select
                id={selectId}
                ref={select}
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                aria-invalid={targetError ? true : undefined}
                aria-describedby={targetError ? `${selectId}-error` : undefined}
              >
                <option value="">選択してください</option>
                {targets.map((t) => (
                  <option key={t.id} value={String(t.id)}>{t.name}</option>
                ))}
              </select>
              <button type="button" className="secondary" onClick={move}>
                移す<span className="sr-only">({folder.path})</span>
              </button>
            </div>
          </div>
        )}
        <button type="button" className="secondary" onClick={onRemove}>
          外す<span className="sr-only">({folder.path} を蔵書「{groupName}」から)</span>
        </button>
      </div>
    </li>
  );
}

function AddFolderForm({ idPrefix, busy, onSubmit, onCancel }: {
  idPrefix: string;
  busy: boolean;
  onSubmit: (path: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [path, setPath] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const id = `${idPrefix}-path`;

  useEffect(() => {
    input.current?.focus();
  }, []);

  const closePicker = useCallback(() => setPicker(false), []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!path.trim()) {
      setProblem("フォルダのパスを入力するか、「フォルダーを選ぶ」から選んでください。");
      input.current?.focus();
      return;
    }
    setProblem(null);
    void onSubmit(path.trim());
  };

  return (
    <form className="library-form" onSubmit={submit} noValidate aria-busy={busy}>
      <div className="organize-field">
        <label htmlFor={id}>追加するフォルダ(サーバー機のパス)</label>
        <p className="organize-hint muted" id={`${id}-hint`}>
          例: D:\本。SpineBox を動かしているサーバー機から見えるフォルダを指定します。登録済みのフォルダの中や外側は指定できません。
        </p>
        {problem && <p className="library-field-error" id={`${id}-error`}>{problem}</p>}
        <div className="organize-inline">
          <input
            id={id}
            ref={input}
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            aria-describedby={problem ? `${id}-error ${id}-hint` : `${id}-hint`}
            aria-invalid={problem ? true : undefined}
            autoComplete="off"
            spellCheck={false}
          />
          <button type="button" className="secondary" onClick={() => setPicker(true)} aria-haspopup="dialog">
            フォルダーを選ぶ
          </button>
        </div>
      </div>
      <div className="library-form-actions">
        <button type="button" className="link-button" onClick={onCancel}>やめる</button>
        <button type="submit">追加する</button>
      </div>
      {picker && (
        <FolderPicker
          load={api.serverFolders}
          initialPath={path.trim()}
          topLabel="ドライブの一覧"
          hint="蔵書に入れるサーバー機のフォルダーを開いて選びます。"
          crumbs={serverCrumbs}
          allowTop={false}
          onPick={(p) => {
            setPath(p);
            setProblem(null);
            setPicker(false);
          }}
          onClose={closePicker}
        />
      )}
    </form>
  );
}

function RenameForm({ idPrefix, initial, busy, onSubmit, onCancel }: {
  idPrefix: string;
  initial: string;
  busy: boolean;
  onSubmit: (name: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = `${idPrefix}-name`;

  useEffect(() => {
    input.current?.focus();
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setProblem("蔵書の名前を入力してください。");
      input.current?.focus();
      return;
    }
    setProblem(null);
    void onSubmit(name.trim());
  };

  return (
    <form className="library-form" onSubmit={submit} noValidate aria-busy={busy}>
      <div className="organize-field">
        <label htmlFor={id}>蔵書の名前</label>
        {problem && <p className="library-field-error" id={`${id}-error`}>{problem}</p>}
        <input
          id={id}
          ref={input}
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-describedby={problem ? `${id}-error` : undefined}
          aria-invalid={problem ? true : undefined}
        />
      </div>
      <div className="library-form-actions">
        <button type="button" className="link-button" onClick={onCancel}>やめる</button>
        <button type="submit">名前を変える</button>
      </div>
    </form>
  );
}
