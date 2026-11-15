"""gemini_review.py のうち、壊れると「レビューしていないのに通る」部分の回帰テスト。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gemini_review as gr  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40


def result_line(**result):
    return json.dumps({"event": "result", "result": result}, ensure_ascii=False)


# --- verdict / render ----------------------------------------------------------
def test_verdict():
    assert gr.verdict_of([]) == "APPROVE"
    assert gr.verdict_of([{"severity": "NITPICK"}]) == "APPROVE"
    assert gr.verdict_of([{"severity": "WARNING"}]) == "CHANGES_REQUESTED"


def test_render_marker_is_recognized_by_check():
    report = gr.render([{"severity": "WARNING", "fix_code": "```\nx\n```"}], SHA, "m", "general")
    assert gr.REVIEW_MARKER_RE.search(report).groups() == (SHA, "CHANGES_REQUESTED")
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
    monkeypatch.setattr(gr, "run", lambda *a, **k: proc)
    assert gr.ci_state(1)[0] is green


@pytest.mark.parametrize("proc", [
    Proc(1, "", "HTTP 502"),
    Proc(0, "not json"),
])
def test_ci_state_error_is_not_green(monkeypatch, proc):
    monkeypatch.setattr(gr, "run", lambda *a, **k: proc)
    with pytest.raises(gr.ReviewError):
        gr.ci_state(1)


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
    monkeypatch.setattr(gr, "run", fake_run)


def pr_checks_of(*pairs):
    return [{"name": n, "bucket": b} for n, b in pairs]


def test_not_started_check_is_excused_by_local_success(monkeypatch):
    fake_github(monkeypatch,
                pr_checks_of(("test", "fail"), ("wiki-lint", "fail"), ("local/test", "pass"), ("local/wiki-lint", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"},
                      {"id": 2, "name": "wiki-lint", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED], 2: [NOT_STARTED]},
                statuses=[{"context": "local/test", "state": "success"}, {"context": "local/wiki-lint", "state": "success"}])
    green, detail = gr.ci_state(1, SHA)
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
    assert gr.ci_state(1, SHA)[0] is False


def test_really_failed_check_is_not_excused(monkeypatch):
    """Actions で実際に動いて落ちたチェックは、ローカルが成功でも緑にしない。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"}],
                annotations={1: ["Process completed with exit code 1."]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert gr.ci_state(1, SHA)[0] is False


def test_same_name_with_one_real_failure_is_not_excused(monkeypatch):
    """push と pull_request で同名の実行が 2 つあり、片方は実際に落ちている。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"},
                      {"id": 2, "name": "test", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED], 2: ["Process completed with exit code 1."]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert gr.ci_state(1, SHA)[0] is False


def test_pending_check_is_not_excused(monkeypatch):
    """落ちたチェックと実行中のチェックが混ざっているなら、代替を考えずに緑にしない。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("wiki-lint", "pending"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "failure"}],
                annotations={1: [NOT_STARTED]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert gr.ci_state(1, SHA)[0] is False


def test_without_sha_no_excuse_is_attempted(monkeypatch):
    """sha を渡さない呼び出し (従来どおり) では GitHub に問い合わせず、落ちたチェックは落ちたまま。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail")), runs=[], annotations={}, statuses=[])
    assert gr.ci_state(1)[0] is False


# --- check_merge: マージ可否 ---------------------------------------------------
def review(sha, verdict):
    return f"<!-- gemini-review sha={sha} verdict={verdict} -->\n"


def triage(sha):
    return f"<!-- gemini-review-triage sha={sha} -->\n"


def test_no_review_blocks():
    assert not gr.check_merge("", SHA)[0]


def test_review_of_old_head_blocks():
    assert not gr.check_merge(review(OTHER, "APPROVE"), SHA)[0]


def test_approve_passes():
    assert gr.check_merge(review(SHA, "APPROVE"), SHA)[0]


def test_changes_requested_without_triage_blocks():
    comments = review(SHA, "CHANGES_REQUESTED") + triage(OTHER)
    assert not gr.check_merge(comments, SHA)[0]


def test_changes_requested_with_triage_passes():
    assert gr.check_merge(review(SHA, "CHANGES_REQUESTED") + triage(SHA), SHA)[0]


def test_unverified_critical_blocks():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 未確認。本番でしか起きない → #61\n")
    ok, reason = gr.check_merge(comments, SHA)
    assert not ok and "未確認の CRITICAL" in reason


def test_unverified_warning_and_false_positive_critical_pass():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 誤検知。`app/routes.py:40` で検証済み → https://example.com/1\n"
                + "- 2. [WARNING] Podfile — 未確認。Ruby が無い → #61\n")
    assert gr.check_merge(comments, SHA)[0]


def test_unverified_in_reason_text_does_not_block():
    comments = (review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [CRITICAL] 認可漏れ — 誤検知。未確認のユーザーは `app/routes.py:40` で弾いている → https://example.com/1\n")
    assert gr.check_merge(comments, SHA)[0]


def test_retriage_after_verifying_critical_passes():
    comments = (review(SHA, "CHANGES_REQUESTED")
                + triage(SHA) + "- 1. [CRITICAL] 認可漏れ — 未確認。本番でしか起きない → #61\n"
                + triage(SHA) + "- 1. [CRITICAL] 認可漏れ — 誤検知。`app/routes.py:40` で検証済み → https://example.com/1\n")
    assert gr.check_merge(comments, SHA)[0]


def test_unverified_critical_of_old_head_does_not_block():
    comments = (review(OTHER, "CHANGES_REQUESTED") + triage(OTHER)
                + "- 1. [CRITICAL] 認可漏れ — 未確認。 → #61\n"
                + review(SHA, "CHANGES_REQUESTED") + triage(SHA)
                + "- 1. [WARNING] N+1 — スコープ外 → #58\n")
    assert gr.check_merge(comments, SHA)[0]


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
    written, skipped = gr.extract_snapshot(data, dest)
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
    assert gr.is_secret_name(name) is secret


@pytest.mark.parametrize("returncode, stdout", [(1, b""), (0, b"")])
def test_fetch_tarball_failure_is_error(monkeypatch, returncode, stdout):
    monkeypatch.setattr(gr.subprocess, "run", lambda *a, **k: Proc(returncode, stdout, b"HTTP 404"))
    with pytest.raises(gr.ReviewError):
        gr.fetch_tarball(SHA)


def test_extract_snapshot_with_no_files_is_error(tmp_path):
    import tarfile
    with pytest.raises(gr.ReviewError):
        gr.extract_snapshot(make_tar([("owner-repo-abc/", None, tarfile.DIRTYPE)]), tmp_path)


# --- スナップショットの後片付け -------------------------------------------------
def test_snapshot_is_removed_even_on_error(tmp_path):
    with pytest.raises(RuntimeError):
        with gr.snapshot_dir(tmp_path) as root:
            readonly = root / "readonly.txt"
            readonly.write_text("x")
            readonly.chmod(0o444)
            raise RuntimeError("boom")
    assert not root.exists()


def test_sweep_removes_only_stale_snapshots(tmp_path):
    import os
    old = tmp_path / f"{gr.SNAPSHOT_PREFIX}old"
    new = tmp_path / f"{gr.SNAPSHOT_PREFIX}new"
    other = tmp_path / "unrelated-old"
    for d in (old, new, other):
        d.mkdir()
    now = 10 * 86400
    os.utime(old, (now - 25 * 3600, now - 25 * 3600))
    os.utime(new, (now - 1 * 3600, now - 1 * 3600))
    os.utime(other, (now - 25 * 3600, now - 25 * 3600))
    removed = gr.sweep_stale_snapshots(tmp_path, now=now)
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
    assert gr.changed_paths(DIFF) == ["src/app.py", "wiki/components/app.md"]


def test_changed_paths_with_quoted_and_spaced_names():
    # 非 ASCII・特殊文字を含むパスは git が引用符で囲み、UTF-8 のバイトを 8 進でエスケープする。
    diff = "\n".join([
        'diff --git "a/docs/\\346\\227\\245\\346\\234\\254.md" "b/docs/\\346\\227\\245\\346\\234\\254.md"',
        "diff --git a/my dir/file.txt b/my dir/file.txt",
        'diff --git "a/q\\"uote.txt" "b/q\\"uote.txt"',
    ])
    assert gr.changed_paths(diff) == ["docs/日本.md", "my dir/file.txt", 'q"uote.txt']


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

    included, omitted = gr.select_wiki_pages(tmp_path, gr.changed_paths(DIFF), budget=200)
    assert [rel for rel, _ in included] == ["wiki/overview.md", "wiki/components/app.md", "wiki/concepts/uses-app.md"]
    assert omitted == ["wiki/operations/huge.md"]


def test_select_wiki_pages_without_wiki(tmp_path):
    assert gr.select_wiki_pages(tmp_path, ["src/app.py"], budget=1000) == ([], [])


def test_code_refs_keeps_dotted_directories():
    assert gr.code_refs("`.github/workflows/test.yml:3`") == {".github/workflows/test.yml"}


def test_startup_failure_is_classified_and_success_runs_are_ignored(monkeypatch):
    """conclusion が startup_failure の実行も判定対象。成功した別の実行 (注記を読まない) は無視する。"""
    fake_github(monkeypatch, pr_checks_of(("test", "fail"), ("other", "pass"), ("local/test", "pass")),
                runs=[{"id": 1, "name": "test", "conclusion": "startup_failure"},
                      {"id": 2, "name": "other", "conclusion": "success"}],
                annotations={1: [NOT_STARTED], 2: ["読まれてはいけない"]},
                statuses=[{"context": "local/test", "state": "success"}])
    assert gr.ci_state(1, SHA)[0] is True


# --- 旧マーカー (agy-review) の互換 ---------------------------------------------
def legacy_review(sha, verdict):
    return f"<!-- agy-review sha={sha} verdict={verdict} -->\n"


def legacy_triage(sha):
    return f"<!-- agy-review-triage sha={sha} -->\n"


def test_legacy_markers_are_still_recognized():
    assert gr.check_merge(legacy_review(SHA, "APPROVE"), SHA)[0]
    assert not gr.check_merge(legacy_review(SHA, "CHANGES_REQUESTED"), SHA)[0]
    assert gr.check_merge(legacy_review(SHA, "CHANGES_REQUESTED") + legacy_triage(SHA), SHA)[0]


def test_legacy_and_new_markers_mix():
    # agy の時代の差し戻しに、新しい名前で評価を記録しても通る。未確認の CRITICAL は名前によらず止める。
    comments = legacy_review(SHA, "CHANGES_REQUESTED") + triage(SHA) + "- 1. [WARNING] N+1 — スコープ外 → #58\n"
    assert gr.check_merge(comments, SHA)[0]
    blocked = (review(SHA, "CHANGES_REQUESTED") + legacy_triage(SHA)
               + "- 1. [CRITICAL] 認可漏れ — 未確認。本番でしか起きない → #61\n")
    assert not gr.check_merge(blocked, SHA)[0]


def test_triage_body_ends_at_next_marker_of_either_name():
    comments = (triage(SHA) + "- 1. [CRITICAL] x — 未確認。\n"
                + legacy_review(OTHER, "APPROVE") + triage(SHA) + "- 1. [CRITICAL] x — 誤検知。\n"
                + review(OTHER, "APPROVE") + "- 9. [CRITICAL] 別の話 — 未確認。\n")
    bodies = gr.triage_bodies(comments, SHA)
    assert len(bodies) == 2
    assert "誤検知" in bodies[-1] and "別の話" not in bodies[-1]


# --- parse_response: 空・途中で切れた応答を APPROVE にしない ----------------------
def api_response(text=None, finish="STOP", **extra):
    parts = [] if text is None else [{"text": text}]
    return json.dumps({"candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}],
                       "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}, **extra},
                      ensure_ascii=False)


FINDING = {"severity": "WARNING", "category": "c", "location": "a.py:1", "issue": "i", "impact": "m"}


@pytest.mark.parametrize("raw", [
    "",                                                       # 本文なし
    "not json",
    "[]",                                                     # オブジェクトでない
    "{}",                                                     # candidates なし
    json.dumps({"candidates": []}),
    json.dumps({"candidates": ["x"]}),
    json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}),  # 入力が弾かれた
    api_response(None),                                       # parts が空
    api_response("   "),
    api_response("レビューしました"),                          # JSON でない
    api_response('{"result": "ok"}'),                         # findings が無い
    api_response('{"findings": "none"}'),
    api_response('{"findings": ["x"]}'),
    api_response('{"findings": [{"severity": "INFO"}]}'),     # 未知の severity
])
def test_unusable_response_is_error(raw):
    with pytest.raises(gr.ReviewError):
        gr.parse_response(raw)


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "RECITATION", None])
def test_unfinished_response_is_error_even_if_json_is_valid(finish):
    # 途中で切れた応答がたまたま閉じた JSON でも、指摘の一部だけを読んで APPROVE にしない。
    with pytest.raises(gr.ReviewError, match="finishReason"):
        gr.parse_response(api_response('{"findings": []}', finish=finish))


def test_response_is_parsed_and_severity_normalized():
    findings, usage = gr.parse_response(api_response(json.dumps({"findings": [{**FINDING, "severity": "critical"}]})))
    assert findings[0]["severity"] == "CRITICAL"
    assert usage["promptTokenCount"] == 10


def test_empty_findings_is_approve_only_when_finished():
    findings, _ = gr.parse_response(api_response('{"findings": []}'))
    assert findings == [] and gr.verdict_of(findings) == "APPROVE"


def test_fenced_response_and_thought_parts():
    body = '```json\n{"findings": [{"severity": "NITPICK", "fix_code": "```py\\nx\\n```"}]}\n```'
    assert gr.parse_response(api_response(body))[0][0]["severity"] == "NITPICK"
    # 思考の要約 (thought: true) は本文に混ぜない。
    raw = json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "考え中 {", "thought": True}, {"text": '{"findings": []}'}]}}]})
    assert gr.parse_response(raw)[0] == []


# --- call_gemini: 何を送るか・失敗をどう扱うか ------------------------------------
class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body.encode("utf-8")


def http_error(code, message="boom"):
    import io
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    return gr.urllib.error.HTTPError("https://example.invalid", code, "err", {}, io.BytesIO(body))


class Opener:
    """呼ばれるたびに results を先頭から 1 つ返す (例外なら投げる)。受け取ったリクエストを記録する。"""

    def __init__(self, *results):
        self.results, self.requests, self.timeouts = list(results), [], []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(result)


@pytest.fixture
def api_key(monkeypatch, tmp_path):
    monkeypatch.setenv(gr.API_KEY_ENV, "test-key-123")
    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "work")
    return "test-key-123"


def test_call_gemini_sends_key_in_header_not_url(api_key):
    opener = Opener(api_response('{"findings": []}'))
    findings, usage = gr.call_gemini("PROMPT 日本語", "gemini-4-hydrogen", "low", 2, opener=opener)
    assert findings == [] and usage["candidatesTokenCount"] == 5
    request = opener.requests[0]
    assert request.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-hydrogen:generateContent"
    assert api_key not in request.full_url
    assert request.get_header("X-goog-api-key") == api_key
    assert request.get_method() == "POST"
    assert opener.timeouts == [120]
    body = json.loads(request.data.decode("utf-8"))
    assert body["contents"][0]["parts"][0]["text"] == "PROMPT 日本語"
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == gr.SCHEMA
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_call_gemini_without_key_is_error_and_sends_nothing(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(gr.API_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(gr.API_KEY_ENV, value)
    opener = Opener()
    with pytest.raises(gr.ReviewError, match=gr.API_KEY_ENV):
        gr.call_gemini("P", "m", "low", 1, opener=opener)
    assert opener.requests == []


@pytest.mark.parametrize("code, hint", [
    (429, "利用上限"), (404, "モデル ID"), (401, "API キー"), (403, "API キー"), (400, "--thinking"), (418, None),
])
def test_call_gemini_http_error_is_not_retried(api_key, code, hint):
    opener, slept = Opener(http_error(code, "detail-text")), []
    with pytest.raises(gr.ReviewError) as e:
        gr.call_gemini("P", "bad-model", "low", 1, opener=opener, sleep=slept.append)
    message = str(e.value)
    assert f"HTTP {code}" in message and "detail-text" in message
    assert (hint in message) if hint else ("[" not in message)
    assert api_key not in message
    assert len(opener.requests) == 1 and slept == []


def test_call_gemini_retries_transient_errors_then_succeeds(api_key):
    opener, slept = Opener(http_error(503), http_error(500), api_response(json.dumps({"findings": [FINDING]}))), []
    findings, _ = gr.call_gemini("P", "m", "low", 1, opener=opener, sleep=slept.append)
    assert findings[0]["severity"] == "WARNING"
    assert len(opener.requests) == 3 and slept == list(gr.RETRY_WAITS)


def test_call_gemini_gives_up_after_retries(api_key):
    opener, slept = Opener(*[http_error(503) for _ in range(len(gr.RETRY_WAITS) + 1)]), []
    with pytest.raises(gr.ReviewError, match="HTTP 503"):
        gr.call_gemini("P", "m", "low", 1, opener=opener, sleep=slept.append)
    assert len(opener.requests) == len(gr.RETRY_WAITS) + 1 and slept == list(gr.RETRY_WAITS)


@pytest.mark.parametrize("exc", [
    gr.urllib.error.URLError("getaddrinfo failed"), TimeoutError("timed out"), ConnectionResetError("reset"),
])
def test_call_gemini_connection_failure_is_error(api_key, exc):
    opener = Opener(exc)
    with pytest.raises(gr.ReviewError, match="接続できません"):
        gr.call_gemini("P", "m", "low", 1, opener=opener)
    assert len(opener.requests) == 1


def test_call_gemini_non_json_error_body(api_key):
    import io
    err = gr.urllib.error.HTTPError("https://example.invalid", 502, "err", {}, io.BytesIO(b"<html>Bad Gateway</html>"))
    with pytest.raises(gr.ReviewError, match="Bad Gateway"):
        gr.call_gemini("P", "m", "low", 1, opener=Opener(err))


def test_call_gemini_dumps_unusable_response(api_key):
    raw = api_response('{"findings": []}', finish="MAX_TOKENS")
    with pytest.raises(gr.ReviewError) as e:
        gr.call_gemini("P", "m", "low", 1, opener=Opener(raw))
    dump = gr.WORK_DIR / "gemini_raw_output.txt"
    assert dump.read_text(encoding="utf-8") == raw
    assert str(dump) in str(e.value)


# --- 変更ファイルの全文 ---------------------------------------------------------
def write_bytes(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_read_changed_files_selects_in_diff_order(tmp_path):
    write_bytes(tmp_path, "src/app.py", "print('日本語')\n".encode("utf-8"))
    write_bytes(tmp_path, "src/big.py", b"x" * 500)
    write_bytes(tmp_path, "img/logo.png", b"\x89PNG\0\0")
    write_bytes(tmp_path, "legacy/sjis.txt", "日本語".encode("cp932"))
    write_bytes(tmp_path, "wiki/components/app.md", b"page")
    write_bytes(tmp_path, "src/small.py", b"ok\n")
    changed = ["src/app.py", "src/big.py", "img/logo.png", "legacy/sjis.txt", "wiki/components/app.md",
               "src/deleted.py", ".env", "src/small.py"]
    included, omitted = gr.read_changed_files(tmp_path, changed, budget=100,
                                              skip=["wiki/components/app.md"], excluded=[".env"])
    assert included == [("src/app.py", "print('日本語')\n"), ("src/small.py", "ok\n")]
    assert omitted == [("src/big.py", "予算超過"), ("img/logo.png", "バイナリ"),
                       ("legacy/sjis.txt", "UTF-8 でない"), (".env", "スナップショットから除外")]


def test_read_changed_files_survives_unreadable_path(monkeypatch, tmp_path):
    real = gr.Path.is_file

    def is_file(self):
        if "bad" in self.name:
            raise OSError("invalid name")
        return real(self)

    monkeypatch.setattr(gr.Path, "is_file", is_file)
    write_bytes(tmp_path, "ok.py", b"ok\n")
    included, omitted = gr.read_changed_files(tmp_path, ["bad<name>.py", "ok.py"], budget=100)
    assert included == [("ok.py", "ok\n")] and omitted == [("bad<name>.py", "読めない")]


# --- プロンプトとレポート -------------------------------------------------------
def test_prompt_order_is_materials_diff_then_instructions():
    prompt = gr.build_prompt("diff --git a/x b/x", "general", [("src/x.py", "BODY ``` fence")], [("wiki/overview.md", "WIKI")])
    order = [prompt.index(s) for s in ("変更されたファイルの全文", "## src/x.py", "関連する Wiki ページ",
                                       "diff --git a/x b/x", "# Role: Pull Request Reviewer", "ドメイン不変条件")]
    assert order == sorted(order)
    # 本文の中のフェンスで、埋め込みの囲みが途中で閉じない。
    assert "````\nBODY ``` fence\n````" in prompt


def test_prompt_omits_empty_sections_and_rejects_unknown_domain():
    prompt = gr.build_prompt("diff --git a/x b/x", "general")
    assert "参考資料 1" not in prompt and "参考資料 2" not in prompt
    with pytest.raises(gr.ReviewError):
        gr.build_prompt("d", "no-such-domain")


def test_prompt_md_does_not_promise_tools():
    # API はファイルを開けない。開けると書くと、見ていないコードを見た前提の指摘が出る。
    text = gr.PROMPT_FILE.read_text(encoding="utf-8")
    for word in ("view_file", "grep_search", "list_dir", "スナップショット"):
        assert word not in text


def test_render_lists_omitted_changed_files():
    context = {"wiki_included": [], "wiki_omitted": ["wiki/x.md"], "files_included": ["a.py"],
               "files_omitted": [("b.py", "予算超過")], "snapshot_skipped": [".env"],
               "usage": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 7}}
    report = gr.render([], SHA, "m (low)", "general", context)
    assert "`b.py`(予算超過)" in report.split("埋め込めなかった変更ファイル")[1]
    assert "(1 件): `a.py`" in report and "`wiki/x.md`" in report
    assert "入力 100 / 出力 20 / 思考 7" in report
    assert report.startswith(f"<!-- gemini-review sha={SHA} verdict=APPROVE -->")
    assert gr.REVIEW_MARKER_RE.search(report).groups() == (SHA, "APPROVE")


def test_sweep_removes_stale_legacy_snapshots(tmp_path):
    import os
    legacy = tmp_path / f"{gr.LEGACY_SNAPSHOT_PREFIXES[0]}old"
    legacy.mkdir()
    now = 10 * 86400
    os.utime(legacy, (now - 25 * 3600, now - 25 * 3600))
    assert gr.sweep_stale_snapshots(tmp_path, now=now) == [legacy]


# --- cmd_review: 全体の流れ -----------------------------------------------------
class Args:
    pr, domain, model, thinking, timeout = 7, "general", "gemini-4-hydrogen", "low", 1
    max_chars, wiki_chars, files_chars, no_post = 1000, 1000, 1000, False


def wire_review(monkeypatch, tmp_path, findings=None, error=None, heads=(SHA, SHA), green=True):
    """外部呼び出しをすべて差し替え、(送ったプロンプト, gh の呼び出し) を記録する。"""
    import tarfile
    heads, prompts, gh_calls = list(heads), [], []
    monkeypatch.setattr(gr, "WORK_DIR", tmp_path / "work")
    monkeypatch.setattr(gr, "pr_info", lambda pr: {"number": 7, "headRefOid": heads.pop(0), "state": "OPEN"})
    monkeypatch.setattr(gr, "ci_state", lambda pr, sha=None: (green, "1 件すべて成功" if green else "test=fail"))
    monkeypatch.setattr(gr, "fetch_tarball", lambda sha: make_tar([
        ("o-r-abc/src/app.py", b"FULL_TEXT_OF_APP\n", tarfile.REGTYPE),
        ("o-r-abc/wiki/overview.md", b"OVERVIEW_PAGE\n", tarfile.REGTYPE),
        ("o-r-abc/wiki/components/app.md", b"APP_PAGE\n", tarfile.REGTYPE),
    ]))

    def fake_gh(*args, **kw):
        gh_calls.append(args)
        return DIFF if args[:2] == ("pr", "diff") else ""

    def fake_call(prompt, model, thinking, timeout):
        prompts.append((prompt, model, thinking, timeout))
        if error:
            raise error
        return findings or [], {"promptTokenCount": 1}

    monkeypatch.setattr(gr, "gh", fake_gh)
    monkeypatch.setattr(gr, "call_gemini", fake_call)
    monkeypatch.setattr(gr.tempfile, "tempdir", str(tmp_path))
    return prompts, gh_calls


def comment_calls(gh_calls):
    return [c for c in gh_calls if c[:2] == ("pr", "comment")]


def test_cmd_review_embeds_changed_files_and_posts(monkeypatch, tmp_path, capsys):
    prompts, gh_calls = wire_review(monkeypatch, tmp_path)
    assert gr.cmd_review(Args) == 0
    prompt, model, thinking, timeout = prompts[0]
    assert (model, thinking, timeout) == ("gemini-4-hydrogen", "low", 1)
    assert "FULL_TEXT_OF_APP" in prompt and "OVERVIEW_PAGE" in prompt
    # 差分で変更された Wiki ページは Wiki として 1 回だけ埋め込む。
    assert prompt.count("APP_PAGE") == 1
    assert len(comment_calls(gh_calls)) == 1
    report = (tmp_path / "work" / f"pr-7-{SHA[:7]}.md").read_text(encoding="utf-8")
    assert f"<!-- gemini-review sha={SHA} verdict=APPROVE -->" in report
    assert "`gemini-4-hydrogen (low)`" in report and "(1 件): `src/app.py`" in report
    # スナップショットは残さない。
    assert not list(tmp_path.glob(f"{gr.SNAPSHOT_PREFIX}*"))


def test_cmd_review_changes_requested_exits_3(monkeypatch, tmp_path, capsys):
    _, gh_calls = wire_review(monkeypatch, tmp_path, findings=[FINDING])
    assert gr.cmd_review(Args) == 3
    assert len(comment_calls(gh_calls)) == 1


def test_cmd_review_api_failure_posts_nothing(monkeypatch, tmp_path, capsys):
    _, gh_calls = wire_review(monkeypatch, tmp_path, error=gr.ReviewError("HTTP 429"))
    with pytest.raises(gr.ReviewError):
        gr.cmd_review(Args)
    assert comment_calls(gh_calls) == []
    assert not list(tmp_path.glob(f"{gr.SNAPSHOT_PREFIX}*"))


def test_cmd_review_refuses_when_ci_is_not_green(monkeypatch, tmp_path, capsys):
    prompts, gh_calls = wire_review(monkeypatch, tmp_path, green=False)
    assert gr.cmd_review(Args) == 2
    assert prompts == [] and gh_calls == []


def test_cmd_review_head_moved_during_review_posts_nothing(monkeypatch, tmp_path, capsys):
    _, gh_calls = wire_review(monkeypatch, tmp_path, heads=(SHA, OTHER))
    with pytest.raises(gr.ReviewError, match="head が変わりました"):
        gr.cmd_review(Args)
    assert comment_calls(gh_calls) == []


def test_cmd_review_no_post(monkeypatch, tmp_path, capsys):
    class NoPost(Args):
        no_post = True
    _, gh_calls = wire_review(monkeypatch, tmp_path)
    assert gr.cmd_review(NoPost) == 0
    assert comment_calls(gh_calls) == []


# --- resolve_budgets: 思考レベル別の参照予算 (SpineBox 独自) -----------------------------
def test_default_budgets():
    assert gr.resolve_budgets("medium") == (400_000, 120_000)
    assert gr.resolve_budgets("low") == (400_000, 120_000)
    assert gr.resolve_budgets("high") == (600_000, 300_000)
    assert gr.resolve_budgets("xhigh") == (600_000, 300_000)


def test_resolve_budgets_respects_explicit_overrides():
    assert gr.resolve_budgets("high", max_chars=250_000) == (250_000, 300_000)
    assert gr.resolve_budgets("high", wiki_chars=50_000) == (600_000, 50_000)
    assert gr.resolve_budgets("medium", max_chars=0, wiki_chars=0) == (0, 0)
