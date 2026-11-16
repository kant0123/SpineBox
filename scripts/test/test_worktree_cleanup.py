"""scripts/worktree-cleanup.ps1 の回帰テスト(Windows 専用。ジャンクションと Windows PowerShell 5.1 が要る)。

壊れると worktree の残骸がマージ後に黙って残るか、ジャンクションの先の共有ディレクトリを巻き込む。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "worktree-cleanup.ps1"

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32" or shutil.which("powershell") is None,
                       reason="Windows のジャンクションと Windows PowerShell が要る"),
    pytest.mark.skipif(not SCRIPT.exists(), reason="worktree-cleanup.ps1 を採用していない"),
]


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


def cleanup(cwd, *args):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        cwd=cwd, capture_output=True, text=True, encoding="oem", errors="replace")  # 5.1 はコンソールのコードページで書く


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


# 非 ASCII のパス: Windows PowerShell 5.1 は git の出力(UTF-8)をコンソールのコードページで読むので、
# 出力のエンコーディングを合わせないとパスが化けて 1 件も処理できない。
@pytest.fixture(params=["ascii", "日本語 パス"])
def env(tmp_path, request):
    """メインツリー repo・worktree repo-w1(共有ディレクトリへのジャンクション入り)・共有ディレクトリ。"""
    tmp_path = tmp_path / request.param
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text("node_modules\n", encoding="utf-8")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    git(repo, "add", ".gitignore", "a.txt")
    git(repo, "commit", "-qm", "init")
    shared = tmp_path / "shared" / "pkg"
    shared.mkdir(parents=True)
    (shared / "keep.txt").write_text("precious", encoding="utf-8")
    wt = tmp_path / "repo-w1"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "w1")
    junction(wt / "node_modules", tmp_path / "shared")
    return repo, wt, shared / "keep.txt"


def test_remove_deletes_worktree_with_junction_and_keeps_link_target(env):
    """素の git worktree remove は exit 0 でジャンクションと親ディレクトリを残す。-Remove は消し切る。"""
    repo, wt, keep = env
    r = cleanup(repo, "-Remove", str(wt))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not wt.exists()
    assert keep.read_text(encoding="utf-8") == "precious"


def test_remove_targets_residue_without_dot_git_only(env):
    """.git が先に消えた残骸を -Remove で指せば、その 1 件だけ消す。他の残骸には触らない。"""
    repo, wt, keep = env
    git(repo, "worktree", "remove", str(wt))  # exit 0 でもジャンクションと親ディレクトリが残る
    assert wt.exists() and not (wt / ".git").exists()
    other = wt.parent / "repo-other"
    other.mkdir()
    (other / "a.txt").write_text("uncommitted", encoding="utf-8")

    r = cleanup(repo, "-Remove", str(wt))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not wt.exists()
    assert other.exists()
    assert keep.exists()


def test_removedirs_detects_residue_without_dot_git(env):
    """.git が無くてもメインツリーと同じ名前を持つ / 空の兄弟は残骸。無関係な兄弟とクローンは対象外。"""
    repo, wt, keep = env
    git(repo, "worktree", "remove", str(wt))
    looks_like = wt.parent / "repo-old"
    looks_like.mkdir()
    (looks_like / "a.txt").write_text("x", encoding="utf-8")
    unrelated = wt.parent / "repo-notes"
    unrelated.mkdir()
    (unrelated / "memo.md").write_text("x", encoding="utf-8")
    clone = wt.parent / "repo-clone"
    clone.mkdir()
    git(clone, "init", "-q")

    listed = cleanup(repo)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert str(wt) in listed.stdout and str(looks_like) in listed.stdout
    assert str(unrelated) not in listed.stdout and str(clone) not in listed.stdout
    assert wt.exists()  # 列挙だけでは消さない

    r = cleanup(repo, "-RemoveDirs")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not wt.exists() and not looks_like.exists()
    assert unrelated.exists() and clone.exists()
    assert keep.exists()


def test_remove_refuses_unrelated_directory(env):
    repo, wt, _ = env
    unrelated = wt.parent / "repo-notes"
    unrelated.mkdir()
    (unrelated / "memo.md").write_text("x", encoding="utf-8")
    r = cleanup(repo, "-Remove", str(unrelated))
    assert r.returncode != 0
    assert unrelated.exists()


def test_remove_keeps_worktree_when_git_refuses_untracked(env):
    """未追跡ファイルがあれば何も触らずに止める(稼働中の worktree のジャンクションも外さない)。"""
    repo, wt, keep = env
    (wt / "draft.txt").write_text("wip", encoding="utf-8")
    r = cleanup(repo, "-Remove", str(wt))
    assert r.returncode != 0
    assert (wt / "draft.txt").exists()
    assert (wt / "node_modules" / "pkg" / "keep.txt").exists()
    assert keep.exists()


def test_no_args_does_not_report_live_worktree(env):
    """稼働中の worktree を孤立エントリにも残骸にも数えない(非 ASCII のパスで .git/worktrees/*/gitdir を読み違えない)。"""
    repo, wt, _ = env
    r = cleanup(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "repo-w1" not in r.stdout
    assert wt.exists()
