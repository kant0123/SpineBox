"""scripts/ci_local.py のうち、壊れると「検査していない結果が成功として載る」部分の回帰テスト。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ci_local  # noqa: E402


class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


# --- jobs: 検査の中身 ------------------------------------------------------------
def test_job_names_match_actions_jobs():
    """ローカルの status 名 (local/<名前>) は Actions のジョブ名と一致していないと代替に使えない。"""
    root = Path(ci_local.__file__).resolve().parents[1]
    names = set()
    for wf in (root / ".github" / "workflows").glob("*.yml"):
        lines = wf.read_text(encoding="utf-8").splitlines()
        start = lines.index("jobs:")
        names |= {l.strip().rstrip(":") for l in lines[start + 1:] if l.startswith("  ") and not l.startswith("   ")}
    assert set(ci_local.jobs()) <= names


def test_tests_dir_is_included_only_when_present(tmp_path):
    (tmp_path / "review" / "tests").mkdir(parents=True)
    assert "tests" not in ci_local.jobs(tmp_path)["test"][0]
    (tmp_path / "tests").mkdir()
    assert "tests" in ci_local.jobs(tmp_path)["test"][0]


# --- precondition_error: 結果を HEAD のものとして載せてよいか -------------------------
def fake_git(monkeypatch, status="", remote="  origin/x\n", status_rc=0):
    def fake_run(cmd, **kw):
        if cmd[:2] == ["git", "status"]:
            return Proc(status_rc, status)
        if cmd[:3] == ["git", "branch", "-r"]:
            return Proc(0, remote)
        if cmd[:2] == ["git", "rev-parse"]:
            return Proc(0, "a" * 40 + "\n")
        raise AssertionError(cmd)
    monkeypatch.setattr(ci_local, "run", fake_run)


@pytest.mark.parametrize("kw", [
    {"status": " M CLAUDE.md\n"},   # 未コミットの変更
    {"status": "?? tests/new.py\n"},  # 未追跡 (テストが増えているかもしれない)
    {"remote": ""},                  # push していない
    {"status_rc": 128},              # git が失敗
])
def test_precondition_blocks_report(monkeypatch, kw):
    fake_git(monkeypatch, **kw)
    assert ci_local.precondition_error() is not None


def test_precondition_ok(monkeypatch):
    fake_git(monkeypatch)
    assert ci_local.precondition_error() is None


# --- main: 実行と記録 -------------------------------------------------------------
@pytest.fixture
def recorded(monkeypatch):
    fake_git(monkeypatch)
    posted = []
    monkeypatch.setattr(ci_local, "post_status", lambda sha, job, state, desc: posted.append((job, state)) or True)
    return posted


def test_failed_job_is_reported_as_failure(monkeypatch, recorded):
    monkeypatch.setattr(ci_local, "run_job", lambda name, cmds: (name != "test", 1.0))
    assert ci_local.main([]) == 1
    assert ("test", "failure") in recorded and ("wiki-lint", "success") in recorded
    assert ("test", "success") not in recorded


def test_all_success(monkeypatch, recorded):
    monkeypatch.setattr(ci_local, "run_job", lambda name, cmds: (True, 1.0))
    assert ci_local.main([]) == 0
    assert [s for _, s in recorded].count("success") == 2


def test_dirty_tree_reports_nothing(monkeypatch):
    fake_git(monkeypatch, status=" M x\n")
    posted = []
    monkeypatch.setattr(ci_local, "post_status", lambda *a: posted.append(a) or True)
    monkeypatch.setattr(ci_local, "run_job", lambda name, cmds: pytest.fail("前提を満たさないのに実行した"))
    assert ci_local.main([]) == 2 and posted == []


def test_no_report_does_not_touch_github(monkeypatch):
    monkeypatch.setattr(ci_local, "post_status", lambda *a: pytest.fail("--no-report なのに載せた"))
    monkeypatch.setattr(ci_local, "run_job", lambda name, cmds: (True, 1.0))
    assert ci_local.main(["--no-report", "--job", "test"]) == 0


def test_unknown_job_is_error():
    assert ci_local.main(["--no-report", "--job", "nope"]) == 2


def test_run_job_stops_at_first_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_local, "run", lambda cmd, **kw: calls.append(cmd) or Proc(1))
    ok, _ = ci_local.run_job("test", [["a"], ["b"]])
    assert ok is False and calls == [["a"]]


def test_run_job_missing_command_is_failure(monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(ci_local, "run", boom)
    assert ci_local.run_job("test", [["node"]])[0] is False


def test_post_status_sends_prefixed_context_and_short_description(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_local, "run", lambda cmd, **kw: calls.append(cmd) or Proc(0))
    assert ci_local.post_status("f" * 40, "test", "success", "あ" * 200) is True
    cmd = calls[0]
    assert cmd[:4] == ["gh", "api", "-X", "POST"]
    assert cmd[4] == "repos/{owner}/{repo}/statuses/" + "f" * 40
    assert "context=local/test" in cmd and "state=success" in cmd
    assert any(a.startswith("description=") and len(a) == len("description=") + 140 for a in cmd)


def test_post_status_failure_is_reported(monkeypatch):
    monkeypatch.setattr(ci_local, "run", lambda cmd, **kw: Proc(1, "", "HTTP 404"))
    assert ci_local.post_status("f" * 40, "test", "success", "x") is False
