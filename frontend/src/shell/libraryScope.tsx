// 蔵書の切り替え(#85)。サイドバーで選んだ蔵書ルートで、作品一覧・巻の一覧・統計を絞る。
// 選んだものは端末ごとに localStorage へ記憶する。設定から外したルートを覚えていたら「すべての蔵書」に戻す。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type LibraryRoot } from "../api";

const KEY = "spinebox.library";

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
  /** 選んだ蔵書ルートの id(すべての蔵書なら null) */
  root: string | null;
  /** 切り替えの候補。取れていなければ null */
  libraries: LibraryRoot[] | null;
  setRoot: (id: string | null) => void;
}

const Context = createContext<LibraryScope>({ root: null, libraries: null, setRoot: () => undefined });

export function LibraryScopeProvider({ children }: { children: ReactNode }) {
  const [root, setRootState] = useState<string | null>(loadLibrary);
  const [libraries, setLibraries] = useState<LibraryRoot[] | null>(null);

  useEffect(() => {
    let alive = true;
    api.listLibraries().then(
      (list) => {
        if (!alive) return;
        setLibraries(list);
        // 覚えていたルートが無くなっていたら「すべての蔵書」に戻す
        setRootState((cur) => {
          if (cur && !list.some((l) => String(l.id) === cur)) {
            saveLibrary(null);
            return null;
          }
          return cur;
        });
      },
      () => alive && setLibraries(null), // 取れなくても一覧は「すべての蔵書」で出す
    );
    return () => {
      alive = false;
    };
  }, []);

  const setRoot = useCallback((id: string | null) => {
    saveLibrary(id);
    setRootState(id);
  }, []);

  const value = useMemo(() => ({ root, libraries, setRoot }), [root, libraries, setRoot]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export const useLibraryScope = () => useContext(Context);
