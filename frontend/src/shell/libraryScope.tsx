// 蔵書の切り替え(#85)。サイドバーで選んだ蔵書(蔵書フォルダのグループ。#313)で、作品一覧・巻の一覧・統計を絞る。
// 選んだものは端末ごとに localStorage へ記憶する。消された蔵書を覚えていたら「すべての蔵書」に戻す。
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type LibraryGroup } from "../api";

// #313 より前は蔵書フォルダの id を "spinebox.library" に覚えていた。蔵書(グループ)の id と取り違えないようキーを分ける
const KEY = "spinebox.libraryGroup";

export function loadLibrary(): string | null {
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
}

function saveLibrary(id: string | null): void {
  try {
    if (id) localStorage.setItem(KEY, id);
    else localStorage.removeItem(KEY);
  } catch {
    // 保存できなくても今の画面では効かせる
  }
}

interface LibraryScope {
  /** 選んだ蔵書の id(すべての蔵書なら null) */
  root: string | null;
  /** 切り替えの候補。取れていなければ null */
  libraries: LibraryGroup[] | null;
  setRoot: (id: string | null) => void;
  /** 蔵書を取り直す(蔵書の管理で変えた後、サイドバーへすぐ映す)。取れなければ例外 */
  reload: () => Promise<void>;
}

const Context = createContext<LibraryScope>({
  root: null, libraries: null, setRoot: () => undefined, reload: () => Promise.resolve(),
});

export function LibraryScopeProvider({ children }: { children: ReactNode }) {
  const [root, setRootState] = useState<string | null>(loadLibrary);
  const [libraries, setLibraries] = useState<LibraryGroup[] | null>(null);

  const alive = useRef(true);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    const list = await api.listLibraries();
    if (!alive.current || mine !== seq.current) return; // 後から頼んだ取り直しを古い応答で上書きしない
    setLibraries(list);
    // 覚えていた蔵書が無くなっていたら「すべての蔵書」に戻す
    setRootState((cur) => {
      if (cur && !list.some((l) => String(l.id) === cur)) {
        saveLibrary(null);
        return null;
      }
      return cur;
    });
  }, []);

  useEffect(() => {
    alive.current = true;
    reload().catch(() => alive.current && setLibraries(null)); // 取れなくても一覧は「すべての蔵書」で出す
    return () => {
      alive.current = false;
    };
  }, [reload]);

  const setRoot = useCallback((id: string | null) => {
    saveLibrary(id);
    setRootState(id);
  }, []);

  const value = useMemo(() => ({ root, libraries, setRoot, reload }), [root, libraries, setRoot, reload]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export const useLibraryScope = () => useContext(Context);
