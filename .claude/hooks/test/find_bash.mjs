// hook のテストが `bash` を起動するための実行ファイルの場所を決める (#6)。
//
// Windows では PATH に `Git\cmd` だけがあり `Git\bin` (bash.exe) が無いことがある (PowerShell の既定など)。
// そのまま `spawnSync('bash')` すると ENOENT になるので、git の場所から Git for Windows の bash.exe を探す。
// 同名の `C:\Windows\System32\bash.exe` は WSL のもので、Windows のパスを渡す hook は動かないため、
// Windows では PATH 上の bash より Git の bash.exe を優先する。

import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { win32 } from 'node:path';

/** git.exe の置き場所 (`Git\cmd\git.exe`・`Git\mingw64\bin\git.exe` など) から、bash.exe がありうる場所を並べる。 */
export function bashCandidates(gitPaths) {
  const out = [];
  for (const gitPath of gitPaths) {
    const dir = win32.dirname(gitPath);
    for (const rel of [['..', 'bin'], ['..', '..', 'bin'], ['..', 'usr', 'bin'], ['..', '..', 'usr', 'bin']]) {
      const candidate = win32.normalize(win32.join(dir, ...rel, 'bash.exe'));
      if (!out.includes(candidate)) out.push(candidate);
    }
  }
  return out;
}

/** PATH 上の git の場所 (`where git` の各行)。見つからなければ空配列。 */
function locateGit() {
  const r = spawnSync('where', ['git'], { encoding: 'utf8' });
  if (r.error || r.status !== 0) return [];
  return r.stdout.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

/** テストで `spawnSync` に渡す bash の実行ファイル。見つからなければ `'bash'` (PATH 任せ)。 */
export function findBash({ platform = process.platform, locate = locateGit, exists = existsSync } = {}) {
  if (platform !== 'win32') return 'bash';
  return bashCandidates(locate()).find((p) => exists(p)) ?? 'bash';
}
