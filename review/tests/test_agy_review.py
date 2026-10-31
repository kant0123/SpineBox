"""agy_review.py のうち、壊れると「レビューしていないのに通る」部分の回帰テスト。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agy_review as ar  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40


def result_line(**result):
    return json.dumps({"event": "result", "result": result}, ensure_ascii=False)


# --- parse_output: 空・失敗を APPROVE にしない ---------------------------------
@pytest.mark.parametrize("stdout", ["", "\n", '{"event":"step_update"}'])
def test_output_without_result_is_error(stdout):
    with pytest.raises(ar.ReviewError):
        ar.parse_output(stdout)


def test_failed_status_is_error():
    with pytest.raises(ar.ReviewError):
        ar.parse_output(result_line(status="ERROR", error="quota"))


def test_missing_findings_is_error():
    with pytest.raises(ar.ReviewError):
        ar.parse_output(result_line(status="SUCCESS", response="レビューしました"))


def test_structured_output_is_used_and_severity_normalized():
    out = "\n".join([
        '{"event":"step_update"}',
        result_line(status="SUCCESS", structured_output={"findings": [
            {"severity": "critical", "category": "x", "location": "a.py:1", "issue": "i", "impact": "m"}]}),
    ])
    findings = ar.parse_output(out)
    assert findings[0]["severity"] == "CRITICAL"


def test_fenced_response_is_parsed():
    body = '```json\n{"findings": [{"severity": "NITPICK", "fix_code": "```py\\nx\\n```"}]}\n```'
    assert ar.parse_output(result_line(status="SUCCESS", response=body))[0]["severity"] == "NITPICK"


def test_unknown_severity_is_error():
    with pytest.raises(ar.ReviewError):
        ar.parse_output(result_line(status="SUCCESS", structured_output={"findings": [{"severity": "INFO"}]}))


# --- verdict / render ----------------------------------------------------------
def test_verdict():
    assert ar.verdict_of([]) == "APPROVE"
    assert ar.verdict_of([{"severity": "NITPICK"}]) == "APPROVE"
    assert ar.verdict_of([{"severity": "WARNING"}]) == "CHANGES_REQUESTED"


def test_render_marker_is_recognized_by_check():
    report = ar.render([{"severity": "WARNING", "fix_code": "```\nx\n```"}], SHA, "m", "general")
    assert ar.REVIEW_MARKER_RE.search(report).groups() == (SHA, "CHANGES_REQUESTED")
    # fix_code 内のフェンスで Markdown が壊れない
    assert "````" in report


# --- ci_state: CI 緑の判定 -----------------------------------------------------
class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def checks(*buckets):
    return json.dumps([{"name": f"c{i}", "bucket": b} for i, b in enumerate(buckets)])


@pytest.mark.parametrize("proc, green", [
    (Proc(0, checks("pass", "skipping")), True),
    (Proc(1, checks("pass", "fail")), False),
    (Proc(8, checks("pass", "pending")), False),
    (Proc(0, "[]"), False),
    # push 直後でチェックが未登録
    (Proc(1, "", "no checks reported on the 'x' branch"), False),
])
def test_ci_state(monkeypatch, proc, green):
    monkeypatch.setattr(ar, "run", lambda *a, **k: proc)
    assert ar.ci_state(1)[0] is green


@pytest.mark.parametrize("proc", [
    Proc(1, "", "HTTP 502"),
    Proc(0, "not json"),
])
def test_ci_state_error_is_not_green(monkeypatch, proc):
    monkeypatch.setattr(ar, "run", lambda *a, **k: proc)
    with pytest.raises(ar.ReviewError):
        ar.ci_state(1)


# --- ci_state: Actions が起動しなかったチェックをローカル CI で代替する ---------------
NOT_STARTED = ("The job was not started because recent account payments have failed "
               "or your spending limit needs to be increased.")


def fake_github(monkeypatch, pr_checks, runs, annotations, statuses):
    """gh の呼び出しを、引数の中身で振り分けて返す偽物に差し替える。"""
    def fake_run(cmd, **kw):
        joined = " ".join(cmd)
        if cmd[:3] == ["gh", "pr", "checks"]:
            return Proc(1 if any(c["bucket"] == "fail" for c in pr_checks) else 0, json.dumps(pr_checks))
        if "/check-runs?" in joined:
            return Proc(0, json.dumps(runs))
        if "/annotations" in joined:
            run_id = int(joined.split("/check-runs/")[1].split("/")[0])
            return Proc(0, "\n".join(annotations.get(run_id, [])))
        if joined.endswith("[.statuses[] | {context, state}]"):
            return Proc(0, json.dumps(statuses))
        raise AssertionError(f"想定外の呼び出し: {joined}")
    monkeypatch.setattr(ar, "run", fake_run)


def pr_checks_of(*pairs):
    return [{"name": n, "bucket": b} for n, b in pairs]


def test_not_started_check_is_excused_by_local_success(monkeypatch):
    fake_github(monkeypatch,
                pr_checks_of(("test", "fail"), ("wiki-lint", "fail"), ("local/test", "pass"), ("local/wiki-lint", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"},
                      {"id": 2, "name": "wiki-lint", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED], 2: [NOT_STARTED]},
                statuses=[{"context": "local/test", "state": "success"}, {"context": "local/wiki-lint", "state": "success"}])
    green, detail = ar.ci_state(1, SHA)
    assert green is True and "ローカル CI" in detail


@pytest.mark.parametrize("statuses", [
    [],                                                  # ローカル CI を回していない
    [{"context": "local/test", "state": "failure"}],    # ローカルでも落ちた
    [{"context": "local/test", "state": "pending"}],    # まだ実行中
    [{"context": "test", "state": "success"}],          # local/ 接頭辞の無い status は代替にならない
])
def test_not_started_check_without_local_success_is_not_green(monkeypatch, statuses):
    fake_github(monkeypatch, pr_checks_of(("test", "fail")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED]}, statuses=statuses)
    assert ar.ci_state(1, SHA)[0] is False


def test_really_failed_check_is_not_excused(monkeypatch):
    """Actions で実際に動いて落ちたチェックは、ローカルが成功でも緑にしない。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"}],
                annotations={1: ["Process completed with exit code 1."]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert ar.ci_state(1, SHA)[0] is False


def test_same_name_with_one_real_failure_is_not_excused(monkeypatch):
    """push と pull_request で同名の実行が 2 つあり、片方は実際に落ちている。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"},
                      {"id": 2, "name": "test", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED], 2: ["Process completed with exit code 1."]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert ar.ci_state(1, SHA)[0] is False


def test_pending_check_is_not_excused(monkeypatch):
    """落ちたチェックと実行中のチェックが混ざっているなら、代替を考えずに緑にしない。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("wiki-lint", "pending"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert ar.ci_state(1, SHA)[0] is False


def test_without_sha_no_excuse_is_attempted(monkeypatch):
    """sha を渡さない呼び出し (従来どおり) では GitHub に問い合わせず、落ちたチェックは落ちたまま。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail")), runs=[], annotations={}, statuses=[])
    assert ar.ci_state(1)[0] is False


# --- check_merge: マージ可否 ---------------------------------------------------
def review(sha, verdict):
    return f"<!-- agy-review sha={sha} verdict={verdict} -->\n"


def triage(sha):
    return f"<!-- agy-review-triage sha={sha} -->\n"


def test_no_review_blocks():
    assert not ar.check_merge("", SHA)[0]


def test_review_of_old_head_blocks():
    assert not ar.check_merge(review(OTHER, "APPROVE"), SHA)[0]


def test_approve_passes():
    assert ar.check_merge(review(SHA, "APPROVE"), SHA)[0]


def test_changes_requested_without_triage_blocks():
    comments = review(SHA, "CHANGES_REQUESTED") + triage(OTHER)
    assert not ar.check_merge(comments, SHA)[0]


def test_changes_requested_with_triage_passes():
    assert ar.check_merge(review(SHA, "CHANGES_REQUESTED") + triage(SHA), SHA)[0]


def test_unverified_critical_blocks():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 未確認。本番でしか起きない → #61\n")
    ok, reason = ar.check_merge(comments, SHA)
    assert not ok and "未確認の CRITICAL" in reason


def test_unverified_warning_and_false_positive_critical_pass():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 誤検知。`app/routes.py:40` で検証済み → https://example.com/1\n"
                + "- 2. [WARNING] Podfile — 未確認。Ruby が無い → #61\n")
    assert ar.check_merge(comments, SHA)[0]


def test_unverified_in_reason_text_does_not_block():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 誤検知。未確認のユーザーは `app/routes.py:40` で弾いている → https://example.com/1\n")
    assert ar.check_merge(comments, SHA)[0]


def test_retriage_after_verifying_critical_passes():
    comments = (review(SHA, "CHANGES_REQUESTED")
                + triage(SHA) + "- 1. [CRITICAL] 認可漏れ — 未確認。本番でしか起きない → #61\n"
                + triage(SHA) + "- 1. [CRITICAL] 認可漏れ — 誤検知。`app/routes.py:40` で検証済み → https://example.com/1\n")
    assert ar.check_merge(comments, SHA)[0]


def test_unverified_critical_of_old_head_does_not_block():
    comments = (review(OTHER, "CHANGES_REQUESTED") + triage(OTHER)
                + "- 1. [CRITICAL] 認可漏れ — 未確認。 → #61\n"
                + review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [WARNING] N+1 — スコープ外 → #58\n")
    assert ar.check_merge(comments, SHA)[0]


# --- ツール拒否: 途中で終わったレビューを APPROVE にしない ----------------------
def test_denied_tool_with_empty_response_is_error():
    out = result_line(status="SUCCESS", response="", denied_actions=[{"action": "command"}])
    with pytest.raises(ar.ToolDeniedError, match="command") as e:
        ar.parse_output(out)
    assert e.value.denied == ["command"]


def denied_step(tool, params):
    return json.dumps({"event": "step_update", "step_update": {
        "state": "ERROR", "tool_name": tool,
        "tool_info": {"parameters": params, "error": {"message": "user denied permission to run command"}}}})


def test_denied_error_carries_conversation_and_calls():
    out = "\n".join([
        denied_step("run_command", {"CommandLine": 'python -c "print(1)"'}),
        denied_step("run_command", {"CommandLine": 'python -c "print(1)"'}),
        result_line(conversation_id="conv-1", status="SUCCESS", response="",
                    denied_actions=[{"action": "command"}]),
    ])
    with pytest.raises(ar.ToolDeniedError) as e:
        ar.parse_output(out)
    assert e.value.conversation == "conv-1"
    assert e.value.calls == ['run_command: python -c "print(1)"']


def denial(conversation="conv-1", calls=("run_command: python -c x",), raw="denied-raw"):
    e = ar.ToolDeniedError("denied", ["command"], conversation, calls)
    e.raw = raw
    return e


def test_retry_continues_denied_conversation_naming_the_call():
    sent = []

    def flaky(message, model, timeout, add_dir, conversation):
        sent.append((message, conversation))
        if len(sent) == 1:
            raise denial()
        return [], "raw"

    assert ar.call_agy_with_retry("P", "m", 1, "d", call=flaky) == ([], "denied-raw\nraw")
    assert sent[0] == ("P", None)
    message, conversation = sent[1]
    assert conversation == "conv-1"
    assert "python -c x" in message and not message.startswith("P")


def test_retry_without_conversation_restarts_with_note():
    sent = []

    def flaky(message, model, timeout, add_dir, conversation):
        sent.append((message, conversation))
        if len(sent) == 1:
            raise denial(conversation=None, calls=())
        return [], "raw"

    ar.call_agy_with_retry("P", "m", 1, "d", call=flaky)
    assert sent[1][1] is None
    assert sent[1][0].startswith("P\n\n") and "command" in sent[1][0]


def test_retry_note_accumulates_denied_calls():
    sent = []

    def denied_twice(message, model, timeout, add_dir, conversation):
        sent.append(message)
        if len(sent) <= 2:
            raise denial(calls=(f"run_command: cmd{len(sent)}",))
        return [], "raw"

    ar.call_agy_with_retry("P", "m", 1, "d", call=denied_twice)
    assert "cmd1" in sent[2] and "cmd2" in sent[2]


def test_retry_gives_up_and_other_errors_are_not_retried():
    calls = []

    def always_denied(*a):
        calls.append(1)
        raise denial()

    with pytest.raises(ar.ToolDeniedError):
        ar.call_agy_with_retry("P", "m", 1, "d", call=always_denied, retries=2)
    assert len(calls) == 3

    def broken(*a):
        calls.append(1)
        raise ar.ReviewError("empty")

    calls.clear()
    with pytest.raises(ar.ReviewError):
        ar.call_agy_with_retry("P", "m", 1, "d", call=broken)
    assert len(calls) == 1


def test_prompt_ends_with_environment_note(tmp_path):
    # 会話ログを読ませないための指示。長い入力では末尾の指示の方が守られるので末尾に置く。
    prompt = ar.build_prompt("diff --git a/x b/x", "general", tmp_path, [], ["x"])
    assert prompt.rstrip().endswith(ar.ENVIRONMENT_NOTE.rstrip())
    # 切り詰めは中間を落とすので、先頭にも置いてどちらかが必ず残るようにする。
    assert prompt.count(ar.ENVIRONMENT_NOTE) == 2
    assert prompt.index(ar.ENVIRONMENT_NOTE) < prompt.index("diff --git")
    assert "transcript_full.jsonl" in ar.TOOL_DENIED_NOTE


# --- スナップショットの展開 -----------------------------------------------------
def make_tar(entries):
    """entries: [(name, bytes | None, type)]。type は tarfile の型定数。"""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "../../outside"
            if data is not None:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                tar.addfile(info)
    return buf.getvalue()


def test_extract_snapshot_writes_regular_files_only(tmp_path):
    import tarfile
    data = make_tar([
        ("owner-repo-abc/", None, tarfile.DIRTYPE),
        ("owner-repo-abc/src/app.py", b"print(1)\n", tarfile.REGTYPE),
        ("owner-repo-abc/.env", b"TOKEN=x\n", tarfile.REGTYPE),
        ("owner-repo-abc/.env.example", b"TOKEN=\n", tarfile.REGTYPE),
        ("owner-repo-abc/keys/server.pem", b"key\n", tarfile.REGTYPE),
        ("owner-repo-abc/link", None, tarfile.SYMTYPE),
        ("owner-repo-abc/../escape.txt", b"x", tarfile.REGTYPE),
    ])
    dest = tmp_path / "snap"
    dest.mkdir()
    written, skipped = ar.extract_snapshot(data, dest)
    assert written == 2
    assert (dest / "src" / "app.py").read_bytes() == b"print(1)\n"
    assert (dest / ".env.example").exists()
    assert not (dest / ".env").exists()
    assert not (dest / "keys" / "server.pem").exists()
    assert not (dest / "link").exists()
    assert not (tmp_path / "escape.txt").exists()
    assert len(skipped) == 4


@pytest.mark.parametrize("name, secret", [
    (".env", True), (".env.local", True), ("id_rsa", True), ("server.key", True),
    (".env.example", False), ("app.py", False), ("keyboard.py", False),
])
def test_is_secret_name(name, secret):
    assert ar.is_secret_name(name) is secret


@pytest.mark.parametrize("returncode, stdout", [(1, b""), (0, b"")])
def test_fetch_tarball_failure_is_error(monkeypatch, returncode, stdout):
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: Proc(returncode, stdout, b"HTTP 404"))
    with pytest.raises(ar.ReviewError):
        ar.fetch_tarball(SHA)


def test_extract_snapshot_with_no_files_is_error(tmp_path):
    import tarfile
    with pytest.raises(ar.ReviewError):
        ar.extract_snapshot(make_tar([("owner-repo-abc/", None, tarfile.DIRTYPE)]), tmp_path)


# --- スナップショットの後片付け -------------------------------------------------
def test_snapshot_is_removed_even_on_error(tmp_path):
    with pytest.raises(RuntimeError):
        with ar.snapshot_dir(tmp_path) as root:
            readonly = root / "readonly.txt"
            readonly.write_text("x")
            readonly.chmod(0o444)
            raise RuntimeError("boom")
    assert not root.exists()


class RunningProc:
    """communicate が指定の例外を投げる agy のプロセス。"""
    pid = 999
    returncode = None

    def __init__(self, exc):
        self.exc = exc

    def communicate(self, *args, timeout=None):
        if timeout is not None:
            raise self.exc
        return b"", b""

    def poll(self):
        return None

    def kill(self):
        pass


@pytest.mark.parametrize("exc, expected", [
    (ar.subprocess.TimeoutExpired("agy", 60), ar.ReviewError),
    (KeyboardInterrupt(), KeyboardInterrupt),
])
def test_call_agy_kills_process_tree_when_interrupted(monkeypatch, tmp_path, exc, expected):
    # 子プロセスが残るとスナップショットのファイルを掴んだままになり、後片付けで消せない。
    monkeypatch.setattr(ar, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(ar.subprocess, "Popen", lambda *a, **k: RunningProc(exc))
    killed = []
    monkeypatch.setattr(ar, "kill_tree", lambda proc: killed.append(proc.pid))
    with pytest.raises(expected):
        ar.call_agy("prompt", "model", 1, tmp_path)
    assert killed == [999]


class FinishedProc:
    pid = 999
    returncode = 0

    def __init__(self, stdout):
        self.stdout = stdout

    def communicate(self, *args, timeout=None):
        return self.stdout.encode("utf-8"), b""

    def poll(self):
        return 0


def test_call_agy_continues_conversation_and_appends_dump(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, "WORK_DIR", tmp_path / "work")
    cmds = []
    denied = result_line(conversation_id="conv-1", status="SUCCESS", response="",
                         denied_actions=[{"action": "command"}])

    def popen(cmd, **k):
        cmds.append(cmd)
        return FinishedProc(denied if len(cmds) == 1 else "second")

    monkeypatch.setattr(ar.subprocess, "Popen", popen)
    with pytest.raises(ar.ToolDeniedError) as e:
        ar.call_agy("prompt", "model", 1, tmp_path)
    assert "--conversation" not in cmds[0] and e.value.raw == denied
    with pytest.raises(ar.ReviewError):
        ar.call_agy("note", "model", 1, tmp_path, conversation="conv-1")
    assert cmds[1][cmds[1].index("--conversation") + 1] == "conv-1"
    dump = (tmp_path / "work" / "agy_raw_output.txt").read_text(encoding="utf-8")
    assert dump.index(denied) < dump.index("second")


def test_kill_tree_uses_taskkill_on_windows(monkeypatch):
    monkeypatch.setattr(ar.os, "name", "nt")
    called = []
    monkeypatch.setattr(ar.subprocess, "run", lambda cmd, **k: called.append(cmd))
    ar.kill_tree(RunningProc(None))
    assert called == [["taskkill", "/PID", "999", "/T", "/F"]]


def test_kill_tree_kills_process_group_on_posix(monkeypatch):
    monkeypatch.setattr(ar.os, "name", "posix")
    monkeypatch.setattr(ar.signal, "SIGKILL", 9, raising=False)
    called = []
    monkeypatch.setattr(ar.os, "killpg", lambda pid, sig: called.append((pid, sig)), raising=False)
    ar.kill_tree(RunningProc(None))
    assert called == [(999, 9)]


def test_sweep_removes_only_stale_snapshots(tmp_path):
    import os
    old = tmp_path / f"{ar.SNAPSHOT_PREFIX}old"
    new = tmp_path / f"{ar.SNAPSHOT_PREFIX}new"
    other = tmp_path / "unrelated-old"
    for d in (old, new, other):
        d.mkdir()
    now = 10 * 86400
    os.utime(old, (now - 25 * 3600, now - 25 * 3600))
    os.utime(new, (now - 1 * 3600, now - 1 * 3600))
    os.utime(other, (now - 25 * 3600, now - 25 * 3600))
    removed = ar.sweep_stale_snapshots(tmp_path, now=now)
    assert removed == [old]
    assert new.exists() and other.exists()


# --- Wiki ページの選択 ----------------------------------------------------------
DIFF = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-a
+b
diff --git a/wiki/components/app.md b/wiki/components/app.md
"""


def test_changed_paths():
    assert ar.changed_paths(DIFF) == ["src/app.py", "wiki/components/app.md"]


def test_changed_paths_with_quoted_and_spaced_names():
    # 非 ASCII・特殊文字を含むパスは git が引用符で囲み、UTF-8 のバイトを 8 進でエスケープする。
    diff = "\n".join([
        'diff --git "a/docs/\\346\\227\\245\\346\\234\\254.md" "b/docs/\\346\\227\\245\\346\\234\\254.md"',
        "diff --git a/my dir/file.txt b/my dir/file.txt",
        'diff --git "a/q\\"uote.txt" "b/q\\"uote.txt"',
    ])
    assert ar.changed_paths(diff) == ["docs/日本.md", "my dir/file.txt", 'q"uote.txt']


def test_files_read_survives_unresolvable_path(monkeypatch, tmp_path):
    # agy が OS の扱えないパスで view_file を呼んでも、レビュー結果ごと失わない。
    real_resolve = ar.Path.resolve

    def resolve(self, *a, **k):
        if "bad" in str(self):
            raise OSError("invalid name")
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(ar.Path, "resolve", resolve)
    out = step("view_file", "DONE", AbsolutePath="bad<name>.py")
    assert ar.files_read(out, tmp_path) == ["bad<name>.py"]



def write_page(root, rel, body):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_select_wiki_pages_ranks_and_budgets(tmp_path):
    write_page(tmp_path, "wiki/overview.md", "overview")
    write_page(tmp_path, "wiki/index.md", "`src/app.py:1`")
    write_page(tmp_path, "wiki/components/_template.md", "`src/app.py:1`")
    write_page(tmp_path, "wiki/components/app.md", "changed page")
    write_page(tmp_path, "wiki/concepts/uses-app.md", "入口は `src/app.py:10 main()` にある")
    write_page(tmp_path, "wiki/concepts/example-only.md", "```\n`src/app.py:1`\n```")
    write_page(tmp_path, "wiki/concepts/unrelated.md", "`src/other.py:1`")
    write_page(tmp_path, "wiki/operations/huge.md", "`./src/app.py` " + "x" * 1000)

    included, omitted = ar.select_wiki_pages(tmp_path, ar.changed_paths(DIFF), budget=200)
    assert [rel for rel, _ in included] == ["wiki/overview.md", "wiki/components/app.md", "wiki/concepts/uses-app.md"]
    assert omitted == ["wiki/operations/huge.md"]


def test_select_wiki_pages_without_wiki(tmp_path):
    assert ar.select_wiki_pages(tmp_path, ["src/app.py"], budget=1000) == ([], [])


def test_code_refs_keeps_dotted_directories():
    assert ar.code_refs("`.github/workflows/test.yml:3`") == {".github/workflows/test.yml"}


# --- agy が読んだファイル -------------------------------------------------------
def step(tool, state, **params):
    return json.dumps({"event": "step_update", "step_update": {
        "tool_name": tool, "state": state, "tool_info": {"parameters": params}}})


def test_files_read(tmp_path):
    target = tmp_path / "src" / "app.py"
    out = "\n".join([
        step("view_file", "ACTIVE", AbsolutePath=str(target)),
        step("view_file", "DONE", AbsolutePath=str(target)),
        step("view_file", "DONE", AbsolutePath=str(target)),
        step("list_dir", "DONE", DirectoryPath=str(tmp_path)),
        step("view_file", "ERROR", AbsolutePath=str(tmp_path / "denied.py")),
    ])
    assert ar.files_read(out, tmp_path) == ["src/app.py"]


def test_render_lists_unread_changed_files():
    context = {"wiki_included": [], "wiki_omitted": ["wiki/x.md"], "files_read": ["a.py"],
               "changed_existing": ["a.py", "b.py"], "snapshot_skipped": []}
    report = ar.render([], SHA, "m", "general", context)
    assert "`b.py`" in report.split("開かれなかった変更ファイル")[1]
    assert "`wiki/x.md`" in report
    assert ar.REVIEW_MARKER_RE.search(report).groups() == (SHA, "APPROVE")


# --- call_agy: モデル不一致 (exit!=0・stderr 空・stdout はモデル一覧) を原因が分かる形で落とす ---
class _FakeProc:
    def __init__(self, out, err, code):
        self._r, self.returncode, self.pid = (out, err), code, 0

    def communicate(self, *_a, **_k):
        return self._r

    def poll(self):
        return self.returncode


@pytest.mark.parametrize("err,expect_hint", [(b"", True), (b"boom", False)])
def test_call_agy_model_mismatch_hint(monkeypatch, tmp_path, err, expect_hint):
    monkeypatch.setattr(ar, "WORK_DIR", tmp_path)
    monkeypatch.setattr(ar.subprocess, "Popen", lambda *a, **k: _FakeProc(b"Claude Sonnet 5.5 (Medium)\n", err, 1))
    with pytest.raises(ar.ReviewError) as e:
        ar.call_agy("P", "bad-model", 1, tmp_path)
    assert ("--model のモデル名" in str(e.value)) is expect_hint


def test_startup_failure_is_classified_and_success_runs_are_ignored(monkeypatch):
    """conclusion が startup_failure の実行も判定対象。成功した別の実行 (注記を読まない) は無視する。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("other", "pass"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "startup_failure"},
                      {"id": 2, "name": "other", "conclusion": "success"}],
                annotations={1: [NOT_STARTED], 2: ["読まれてはいけない"]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert ar.ci_state(1, SHA)[0] is True
