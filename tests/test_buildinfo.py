import subprocess

import pytest

from spinebox import buildinfo
from spinebox.buildinfo import current_commit

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def no_git(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("git")
    monkeypatch.setattr(buildinfo.subprocess, "run", boom)


def test_uses_git_rev_parse(monkeypatch, tmp_path):
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw["cwd"])
        return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")
    monkeypatch.setattr(buildinfo.subprocess, "run", fake)
    assert current_commit(tmp_path) == "abc1234"
    assert seen["cmd"][:2] == ["git", "rev-parse"] and seen["cwd"] == tmp_path


def test_git_failure_falls_back_to_dot_git(no_git, tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    assert current_commit(tmp_path) == SHA[:7]


def test_packed_refs_and_detached_head(no_git, tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(f"# pack-refs\n{SHA} refs/heads/main\n", encoding="utf-8")
    assert current_commit(tmp_path) == SHA[:7]
    (git / "HEAD").write_text(SHA + "\n", encoding="utf-8")
    assert current_commit(tmp_path) == SHA[:7]


def test_worktree_dot_git_file(no_git, tmp_path):
    main_git = tmp_path / "main" / ".git"
    wt_git = main_git / "worktrees" / "wt"
    (main_git / "refs" / "heads").mkdir(parents=True)
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (wt_git / "commondir").write_text("../..\n", encoding="utf-8")
    (main_git / "refs" / "heads" / "feature").write_text(SHA + "\n", encoding="utf-8")
    work = tmp_path / "wt"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")
    assert current_commit(work) == SHA[:7]


def test_unresolvable_is_none_not_an_error(no_git, tmp_path):
    assert current_commit(tmp_path) is None  # .git が無い
    (tmp_path / ".git").mkdir()
    assert current_commit(tmp_path) is None  # HEAD が無い
    (tmp_path / ".git" / "HEAD").write_bytes(b"\xff\xfe\x00broken")
    assert current_commit(tmp_path) is None  # 読めない中身
