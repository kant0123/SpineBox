"""稼働中コミットの解決。`/healthz` が出す。

ツリーの HEAD ではなく「プロセスが起動時に読んだコミット」を出すため、起動時に一度だけ呼ぶ。
git が無い・リポジトリ外(zip 展開した配置など)なら None を返し、例外でアプリを落とさない。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

SHORT_LEN = 7


def current_commit(repo_root: Path) -> str | None:
    """short SHA。`git rev-parse` を優先し、失敗したら `.git` を直接読む。解決できなければ None。"""
    try:
        out = subprocess.run(["git", "rev-parse", f"--short={SHORT_LEN}", "HEAD"], cwd=repo_root,
                             capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        sha = _read_head(repo_root)
    except (OSError, ValueError):
        return None
    return sha[:SHORT_LEN] if sha else None


def _read_head(repo_root: Path) -> str | None:
    git = repo_root / ".git"
    if git.is_file():  # worktree では .git がファイルで `gitdir: <path>` を指す
        text = git.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text[len("gitdir:"):].strip())
        if not gitdir.is_absolute():
            gitdir = repo_root / gitdir
    else:
        gitdir = git
    head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head or None  # detached HEAD は SHA そのもの
    ref = head[len("ref:"):].strip()
    # ref の実体は共有の git dir(worktree なら commondir が指す先)にある
    common = gitdir
    if (gitdir / "commondir").is_file():
        common = gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()
    for base in (gitdir, common):
        ref_file = base / ref
        if ref_file.is_file():
            return ref_file.read_text(encoding="utf-8").strip() or None
    packed = common / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref and not line.startswith(("#", "^")):
                return sha
    return None
