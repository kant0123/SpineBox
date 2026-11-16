#!/usr/bin/env python3
"""PR の差分を Gemini API でレビューさせ、結果を PR コメントに残す。

使い方 (手順全体は gemini-review スキル):

  python review/gemini_review.py [--pr N]                 CI 緑を確認してレビューし、PR にコメントする
  python review/gemini_review.py --triage FILE [--pr N]   差し戻しに対する評価を PR にコメントする
  python review/gemini_review.py --check [--pr N]         マージしてよいかを判定する (merge hook 用)

終了コード:
  0  APPROVE / 評価を投稿した / マージしてよい
  1  実行時エラー (API・gh の失敗、応答を解釈できない)。レビュー結果は投稿しない
  2  前提を満たさない (CI が緑でない / レビュー未実施 / 差し戻しが未評価)
  3  CHANGES_REQUESTED (CRITICAL か WARNING がある)

判定の記録は PR コメントの先頭に置く HTML コメントのマーカーだけで行う。
**head SHA ごと**に記録するので、修正を push すれば自動的に「未レビュー」に戻る。

API キーは環境変数 GEMINI_API_KEY から読む (Google AI Studio で作る)。
レビューでは差分・関連 Wiki ページ・変更されたファイルの全文をプロンプトに埋め込み、API を 1 回呼ぶ。
Wiki とファイルの全文は head 時点のスナップショットから読み、スナップショットは実行後に消す。
"""

import argparse
import codecs
import contextlib
import fnmatch
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

REVIEW_DIR = Path(__file__).resolve().parent
PROMPT_FILE = REVIEW_DIR / "prompt.md"
INVARIANTS_FILE = REVIEW_DIR / "domain_invariants.json"
WORK_DIR = REVIEW_DIR / "work"

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
API_KEY_ENV = "GEMINI_API_KEY"

# 実装は Claude 系で行う前提なので、レビューは別系統のモデルに担当させる
# (自分が書いたコードを自分で採点させない)。名前は Gemini API のモデル ID。
# agy の時代: gemini-3.1-pro-high → (使えなくなった #40) gemini-3.8-flash-high → (#61) gemini-4-hydrogen-low。
# API への切り替え (#67) で、モデル ID と思考レベルを分けて指定する形になった。
DEFAULT_MODEL = os.environ.get("REVIEW_MODEL", "gemini-4-hydrogen")
THINKING_LEVELS = ("low", "medium", "high", "xhigh")
DEFAULT_THINKING = os.environ.get("REVIEW_THINKING", "medium")
DEFAULT_DOMAIN = os.environ.get("REVIEW_DOMAIN", "spinebox")
DEFAULT_TIMEOUT_MIN = 15
# これを超える差分は 1 回のレビューで精度が落ちる。PR を分けるのが本筋。
DEFAULT_MAX_CHARS = 400_000
# 埋め込む Wiki ページの合計文字数。主要な設計判断・過去指摘台帳が予算超過で落ちにくい量。
DEFAULT_WIKI_CHARS = 120_000
# 大きい変更で使う上位の思考レベルの (max_chars, wiki_chars)。--max-chars / --wiki-chars 未指定時に適用する (SpineBox #322・#336)。
THINKING_BUDGETS = {
    "high": (600_000, 300_000),
    "xhigh": (600_000, 300_000),
}
# 埋め込む変更ファイル (全文) の合計文字数。API はファイルを開けないので、差分の前後を見せる手段はこれだけ。
DEFAULT_FILES_CHARS = 200_000
# 一時的な障害 (過負荷・内部エラー) だけ、この秒数を置いて再試行する。429 (上限超過) は待っても通らないので再試行しない。
RETRY_STATUS = (500, 503)
RETRY_WAITS = (10, 30)

# スナップショットの一時ディレクトリ。プロセスごと強制終了されると finally が走らず残るので、
# 次回の起動時にこの接頭辞で古いものを掃除する。agy の時代の接頭辞で残ったものも掃除する。
SNAPSHOT_PREFIX = "gemini-review-snapshot-"
LEGACY_SNAPSHOT_PREFIXES = ("agy-review-snapshot-",)
STALE_SNAPSHOT_HOURS = 24
# 差分に載っていない秘匿情報を、変更ファイルの全文として Gemini (= Google) に送らないための除外。
# コミットされていなければ tarball に入らないが、誤ってコミットされた場合の保険。
SECRET_NAME_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ecdsa*", "id_ed25519*")
SECRET_NAME_ALLOW = ("*.example", "*.sample", "*.template")
# Wiki の中で、関連ページ選びの対象にしないもの (カタログ・雛形)。
WIKI_SKIP = ("index.md", "_template.md")
# wiki-lint.js の CODE_REF と同じ記法 (`path/to/file.py:123 記号名`)。行番号なしのパスも拾う。
CODE_REF_RE = re.compile(r"^([\w./-]+\.[a-zA-Z0-9]{1,5})(?::\d+)?(?:\s+.+)?$")

SEVERITIES = ("CRITICAL", "WARNING", "NITPICK")
BLOCKING = ("CRITICAL", "WARNING")

# 書くのは gemini-review。agy の時代に書かれた agy-review も読む (導入済みプロジェクトの過去の記録を無効にしない)。
MARKER_NAME = "gemini-review"
MARKER_NAMES_RE = r"(?:gemini|agy)-review"
REVIEW_MARKER_RE = re.compile(rf"<!-- {MARKER_NAMES_RE} sha=([0-9a-f]{{40}}) verdict=(APPROVE|CHANGES_REQUESTED) -->")
TRIAGE_MARKER_RE = re.compile(rf"<!-- {MARKER_NAMES_RE}-triage sha=([0-9a-f]{{40}}) -->")
ANY_MARKER_RE = re.compile(rf"<!-- {MARKER_NAMES_RE}")

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "category": {"type": "string"},
                    "location": {"type": "string"},
                    "issue": {"type": "string"},
                    "impact": {"type": "string"},
                    "fix_code": {"type": "string"},
                },
                "required": ["severity", "category", "location", "issue", "impact"],
            },
        },
    },
    "required": ["findings"],
}


class ReviewError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 外部コマンド
# ---------------------------------------------------------------------------
def run(cmd, stdin=None, timeout=120, cwd=None):
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        raise ReviewError(f"コマンドが見つかりません: {cmd[0]}")
    except subprocess.TimeoutExpired:
        raise ReviewError(f"タイムアウトしました ({timeout} 秒): {' '.join(cmd[:3])}")
    return proc


def gh(*args, timeout=120):
    proc = run(["gh", *args], timeout=timeout)
    if proc.returncode != 0:
        raise ReviewError(f"gh {' '.join(args[:3])} が失敗しました: {proc.stderr.strip()}")
    return proc.stdout


def pr_info(pr):
    args = ["pr", "view"] + ([str(pr)] if pr else []) + ["--json", "number,headRefOid,state"]
    return json.loads(gh(*args))


NOT_STARTED_MARK = "The job was not started"  # Actions が課金・上限でジョブを起動しなかったときの注記
LOCAL_STATUS_PREFIX = "local/"  # scripts/ci_local.py が載せる commit status の接頭辞


def excused_by_local_ci(sha):
    """Actions で起動しなかったチェックのうち、同じコミットのローカル CI が成功しているものの名前。

    **実際に動いて落ちたチェックはローカルの成功で上書きしない。** 同じ名前の実行が複数あり
    (push と pull_request)、1 つでも「起動しなかった」以外の理由で落ちていれば除外しない。
    """
    runs = json.loads(gh("api", f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100",
                         "--jq", "[.check_runs[] | {id, name, conclusion}]") or "[]")
    not_started, really_failed = set(), set()
    for r in runs:
        if r["conclusion"] not in ("failure", "startup_failure"):
            continue
        notes = gh("api", f"repos/{{owner}}/{{repo}}/check-runs/{r['id']}/annotations", "--jq", ".[].message")
        (not_started if NOT_STARTED_MARK in notes else really_failed).add(r["name"])
    statuses = json.loads(gh("api", f"repos/{{owner}}/{{repo}}/commits/{sha}/status",
                             "--jq", "[.statuses[] | {context, state}]") or "[]")
    local_ok = {s["context"][len(LOCAL_STATUS_PREFIX):] for s in statuses
                if s["context"].startswith(LOCAL_STATUS_PREFIX) and s["state"] == "success"}
    return (not_started - really_failed) & local_ok


def ci_state(pr, sha=None):
    """(緑か, 説明) を返す。skip されたチェックは成功扱い。

    **チェックが 1 件も無い状態を緑にしない。** push 直後はチェックがまだ登録されておらず、
    それを緑と読むと CI を待たずにレビューへ進んでしまう。gh の失敗 (出力が空) も同様。

    sha を渡すと、Actions が起動しなかったチェックを同名のローカル CI (`local/<名前>`) の
    成功で代替する (`excused_by_local_ci()`)。
    """
    proc = run(["gh", "pr", "checks", str(pr), "--json", "name,bucket"])
    if not proc.stdout.strip():
        if "no checks reported" in proc.stderr:
            return False, "チェックがまだ登録されていません"
        raise ReviewError(f"CI の状態を取得できませんでした (exit={proc.returncode}): {proc.stderr.strip()}")
    try:
        checks = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ReviewError(f"gh pr checks の出力を解釈できませんでした: {proc.stdout[:200]}")
    if not checks:
        return False, "チェックがまだ登録されていません"
    bad_checks = [c for c in checks if c["bucket"] not in ("pass", "skipping")]
    excused = set()
    if bad_checks and sha and all(c["bucket"] == "fail" for c in bad_checks):
        excused = excused_by_local_ci(sha)
    bad = [f"{c['name']}={c['bucket']}" for c in bad_checks if c["name"] not in excused]
    if bad:
        return False, ", ".join(bad)
    if excused:
        return True, (f"{len(checks)} 件のうち Actions が起動しなかった {', '.join(sorted(excused))} を"
                      "ローカル CI の成功で代替、ほかはすべて成功")
    return True, f"{len(checks)} 件すべて成功"


def pr_comments(pr):
    out = gh("api", "--paginate", f"repos/{{owner}}/{{repo}}/issues/{pr}/comments", "--jq", ".[].body")
    return out


# ---------------------------------------------------------------------------
# スナップショット
# ---------------------------------------------------------------------------
def fetch_tarball(sha, timeout=300):
    """head 時点の tarball を bytes で返す。テキストで受けると壊れるので run() は使わない。

    認証はスクリプト側の gh が持つので private リポジトリでも取れる。
    """
    cmd = ["gh", "api", f"repos/{{owner}}/{{repo}}/tarball/{sha}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise ReviewError("コマンドが見つかりません: gh")
    except subprocess.TimeoutExpired:
        raise ReviewError(f"tarball の取得がタイムアウトしました ({timeout} 秒)")
    if proc.returncode != 0 or not proc.stdout:
        raise ReviewError(f"tarball を取得できませんでした: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def is_secret_name(name):
    if any(fnmatch.fnmatch(name, p) for p in SECRET_NAME_ALLOW):
        return False
    return any(fnmatch.fnmatch(name, p) for p in SECRET_NAME_PATTERNS)


def safe_relpath(member_name):
    """tarball の要素名から、先頭の `<owner>-<repo>-<sha>/` を外した安全な相対パスを返す。

    絶対パス・`..`・ドライブ指定を含むものは None (展開しない)。tarfile.extract は使わない —
    Python 3.9 には展開先の外に書かせない filter が無いため、自前で検査して書き出す。
    """
    parts = PurePosixPath(member_name.replace("\\", "/")).parts
    if len(parts) < 2 or member_name.startswith(("/", "\\")):
        return None
    rel = parts[1:]
    if any(p in ("", ".", "..") or ":" in p for p in rel):
        return None
    return PurePosixPath(*rel)


def extract_snapshot(data, dest):
    """通常ファイルだけを dest に書き出す。(書き出した数, 除外したパスの一覧) を返す。

    リンク・デバイスは展開しない (展開先の外を指せるため)。秘匿情報らしい名前も除外する。
    """
    written, skipped = 0, []
    dest = Path(dest)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if member.isdir():
                continue
            rel = safe_relpath(member.name)
            if rel is None:
                skipped.append(member.name)
                continue
            if not member.isfile() or is_secret_name(rel.name):
                skipped.append(str(rel))
                continue
            target = dest.joinpath(*rel.parts)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            except OSError:
                # Windows で使えないファイル名など。レビューを止めるほどではない。
                skipped.append(str(rel))
                continue
            written += 1
    if written == 0:
        raise ReviewError("tarball から展開できたファイルが 0 件です。")
    return written, skipped


def remove_tree(path):
    """読み取り専用属性を外しながら消す。消せたら True。"""
    def retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)
    return not Path(path).exists()


def sweep_stale_snapshots(tmp_root=None, now=None, hours=STALE_SNAPSHOT_HOURS):
    """強制終了で残ったスナップショットのうち、古いものだけを消す。消したパスを返す。

    実行中の別のレビューのスナップショットを消さないよう、時間で区切る。
    """
    root = Path(tmp_root or tempfile.gettempdir())
    now = now if now is not None else time.time()
    removed = []
    entries = [e for prefix in (SNAPSHOT_PREFIX, *LEGACY_SNAPSHOT_PREFIXES) for e in root.glob(f"{prefix}*")]
    for entry in entries:
        try:
            if not entry.is_dir() or now - entry.stat().st_mtime < hours * 3600:
                continue
        except OSError:
            continue
        if remove_tree(entry):
            removed.append(entry)
    return removed


@contextlib.contextmanager
def snapshot_dir(tmp_root=None):
    """スナップショット用の一時ディレクトリ。例外・Ctrl+C でも抜けるときに消す。"""
    sweep_stale_snapshots(tmp_root)
    path = Path(tempfile.mkdtemp(prefix=SNAPSHOT_PREFIX, dir=tmp_root))
    try:
        yield path
    finally:
        if not remove_tree(path):
            print(f"[WARN] スナップショットを削除できませんでした: {path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Wiki
# ---------------------------------------------------------------------------
def _unquote_git_path(path):
    """git が引用符で囲んだパス (`"b/\\346\\227\\245.md"`) を戻す。非 ASCII や特殊文字を含むと囲まれる。"""
    if not (len(path) >= 2 and path[0] == path[-1] == '"'):
        return path
    raw = codecs.escape_decode(path[1:-1].encode("utf-8"))[0]
    return raw.decode("utf-8", "replace")


def changed_paths(diff):
    """差分に載っているファイルのパス (変更後の名前) を順序つきで返す。"""
    paths = []
    for m in re.finditer(r'^diff --git (?:"a/(?:[^"\\]|\\.)*"|a/.+?) ("b/(?:[^"\\]|\\.)*"|b/.+)$', diff, re.MULTILINE):
        path = _unquote_git_path(m.group(1))
        path = path[2:] if path.startswith("b/") else path
        if path not in paths:
            paths.append(path)
    return paths


def code_refs(text):
    """ページ本文のコードスパンから参照しているパスを集める (フェンスの中は記法の例なので除く)。"""
    body = re.sub(r"```[\s\S]*?```", "", text)
    refs = set()
    for span in re.findall(r"`([^`\n]+)`", body):
        m = CODE_REF_RE.match(span.strip())
        if m:
            path = m.group(1)
            refs.add(path[2:] if path.startswith("./") else path)
    return refs


def select_wiki_pages(root, changed, budget):
    """埋め込む Wiki ページを選ぶ。(採用 [(相対パス, 本文)], 予算で外した相対パス) を返す。

    候補は overview.md・差分で変更されたページ・変更ファイルを参照するページ。
    overview → 変更されたページ → 参照している変更ファイルの多い順に、予算に収まるものを採る。
    """
    wiki = Path(root) / "wiki"
    if not wiki.is_dir():
        return [], []
    changed = set(changed)
    candidates = []
    for page in sorted(wiki.rglob("*.md")):
        rel = page.relative_to(root).as_posix()
        if page.name in WIKI_SKIP:
            continue
        text = page.read_text(encoding="utf-8", errors="replace")
        if rel == "wiki/overview.md":
            rank = (0, 0)
        elif rel in changed:
            rank = (1, 0)
        else:
            hits = len(code_refs(text) & changed)
            if not hits:
                continue
            rank = (2, -hits)
        candidates.append((rank, rel, text))

    included, omitted, used = [], [], 0
    for _, rel, text in sorted(candidates):
        if used + len(text) > budget:
            omitted.append(rel)
            continue
        included.append((rel, text))
        used += len(text)
    return included, omitted


# ---------------------------------------------------------------------------
# 変更ファイルの全文
# ---------------------------------------------------------------------------
def read_changed_files(root, changed, budget, skip=(), excluded=()):
    """埋め込む変更ファイルをスナップショットから読む。(採用 [(相対パス, 本文)], 外した [(相対パス, 理由)]) を返す。

    差分の順に、予算に収まるものを採る。skip は別の形で既に埋め込んだパス (Wiki ページ)、
    excluded はスナップショットに展開しなかったパス (秘匿情報らしい名前・リンクなど)。
    スナップショットに無いパスは削除されたファイルなので、黙って飛ばす。
    """
    skip, excluded = set(skip), set(excluded)
    included, omitted, used = [], [], 0
    for rel in changed:
        if rel in skip:
            continue
        if rel in excluded:
            omitted.append((rel, "スナップショットから除外"))
            continue
        try:
            path = Path(root).joinpath(*PurePosixPath(rel).parts)
            if not path.is_file():
                continue
            data = path.read_bytes()
        except (OSError, ValueError):
            # OS が扱えないパス。レビューを止めるほどではないが、見せていないことは残す。
            omitted.append((rel, "読めない"))
            continue
        if b"\0" in data:
            omitted.append((rel, "バイナリ"))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            omitted.append((rel, "UTF-8 でない"))
            continue
        if used + len(text) > budget:
            omitted.append((rel, "予算超過"))
            continue
        included.append((rel, text))
        used += len(text)
    return included, omitted


# ---------------------------------------------------------------------------
# プロンプトと Gemini API
# ---------------------------------------------------------------------------
def build_prompt(diff, domain, files=(), wiki_pages=()):
    """参考資料 → 差分 → 指示の順に並べる。長い入力では末尾の指示の方が守られやすいため。"""
    invariants = json.loads(INVARIANTS_FILE.read_text(encoding="utf-8"))
    if domain not in invariants:
        raise ReviewError(f"未知のドメインです: {domain} (選択肢: {', '.join(invariants)})")
    info = invariants[domain]
    rules = "\n".join(f"- {r}" for r in info["critical_rules"])

    parts = ["以下の参考資料と差分を読んだ上で、末尾の「Role」以降の指示に従って差分をレビューしてください。", ""]
    if files:
        parts += ["# 参考資料 1: 変更されたファイルの全文 (PR の head 時点)", ""]
        for rel, text in files:
            parts += [f"## {rel}", "", fence(text), ""]
    if wiki_pages:
        parts += ["# 参考資料 2: 関連する Wiki ページ", ""]
        for rel, text in wiki_pages:
            parts += [f"## {rel}", "", fence(text), ""]
    parts += [
        "# レビュー対象の差分",
        "",
        diff,
        "",
        PROMPT_FILE.read_text(encoding="utf-8"),
        "",
        f"## ドメイン不変条件: {info['name']}",
        "",
        rules,
        "",
    ]
    return "\n".join(parts)


def parse_response(raw):
    """generateContent の応答から (findings, usageMetadata) を取り出す。

    **応答が空・途中で切れた・解釈不能のときは絶対に「指摘 0 件」として扱わない。**
    それを APPROVE として記録すると、レビューしていないのにレビュー済みの記録だけが残る。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ReviewError("Gemini API の応答を JSON として解釈できませんでした。")
    if not isinstance(data, dict):
        raise ReviewError("Gemini API の応答がオブジェクトではありません。")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        # 入力が安全フィルタなどで弾かれると candidates が無く、promptFeedback に理由が入る。
        raise ReviewError(f"Gemini API の応答に候補がありません (promptFeedback={data.get('promptFeedback')!r})。")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish != "STOP":
        # MAX_TOKENS で切れた JSON は、たまたま閉じていると指摘の一部だけを読んでしまう。
        raise ReviewError(f"Gemini の応答が最後まで生成されませんでした (finishReason={finish!r})。")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
    if not text:
        raise ReviewError("Gemini の応答が空です。")
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text[:text.rfind("```")] if "```" in text else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise ReviewError("Gemini の応答から JSON を取り出せませんでした。")
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list):
        raise ReviewError("Gemini の応答に findings 配列がありません。")

    normalized = []
    for f in findings:
        if not isinstance(f, dict):
            raise ReviewError(f"findings の要素が不正です: {f!r}")
        severity = str(f.get("severity", "")).upper()
        if severity not in SEVERITIES:
            raise ReviewError(f"未知の severity です: {f.get('severity')!r}")
        normalized.append({**f, "severity": severity})
    usage = data.get("usageMetadata")
    return normalized, usage if isinstance(usage, dict) else {}


def api_error_message(body):
    """エラー応答の本文から、人が読むメッセージだけを取り出す。"""
    try:
        return str(json.loads(body)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return body.strip()[:500]


def http_error_text(code, detail, model):
    hint = {
        400: "リクエストが受け付けられませんでした。API キーが無効か、--thinking の値をモデルが受け付けない可能性があります",
        401: f"API キーが無効です。環境変数 {API_KEY_ENV} を確認してください",
        403: f"API キーにこのモデルを使う権限がありません。環境変数 {API_KEY_ENV} を確認してください",
        404: (f"モデル `{model}` が見つかりません。--model / REVIEW_MODEL には Gemini API のモデル ID を指定します"
              " (例: gemini-4-hydrogen。agy の時代の `gemini-4-hydrogen-low` のような名前は使えません)"),
        429: "利用上限を超えました (無料枠の 1 日・1 分あたりの回数など)。時間を置くか、上限を確認してください",
    }.get(code, "")
    return f"Gemini API がエラーを返しました (HTTP {code}): {detail}" + (f" [{hint}]" if hint else "")


def call_gemini(prompt, model, thinking, timeout_min, opener=None, sleep=time.sleep):
    """(findings, usageMetadata) を返す。API キーは URL に載せず、ヘッダーで渡す (URL はログに残りやすい)。"""
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise ReviewError(f"環境変数 {API_KEY_ENV} が設定されていません。Google AI Studio で API キーを作って設定してください。")
    opener = opener or urllib.request.urlopen
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            # enum を書かないと severity が小文字で返ってくることがある。
            "responseJsonSchema": SCHEMA,
            "thinkingConfig": {"thinkingLevel": thinking},
        },
    }
    request = urllib.request.Request(
        API_URL.format(model=model),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "x-goog-api-key": api_key},
        method="POST",
    )
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            with opener(request, timeout=timeout_min * 60) as response:
                raw = response.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            detail = api_error_message(e.read().decode("utf-8", "replace"))
            if e.code in RETRY_STATUS and attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                print(f"[WARN] Gemini API が一時的なエラーを返しました (HTTP {e.code}): {detail} — "
                      f"{wait} 秒後に再試行します ({attempt + 1}/{len(RETRY_WAITS)})", file=sys.stderr)
                sleep(wait)
                continue
            raise ReviewError(http_error_text(e.code, detail, model))
        except OSError as e:
            # URLError (名前解決・接続の失敗) とタイムアウト。どちらも OSError の下にある。
            raise ReviewError(f"Gemini API に接続できませんでした ({timeout_min} 分でタイムアウト): {getattr(e, 'reason', e)}")
    try:
        return parse_response(raw)
    except ReviewError as e:
        WORK_DIR.mkdir(exist_ok=True)
        dump = WORK_DIR / "gemini_raw_output.txt"
        dump.write_text(raw or "(出力なし)", encoding="utf-8")
        e.args = (f"{e} 生出力: {dump}",)
        raise


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------
def verdict_of(findings):
    return "CHANGES_REQUESTED" if any(f["severity"] in BLOCKING for f in findings) else "APPROVE"


def fence(code):
    ticks = max([len(m) for m in re.findall(r"`+", code)] + [2]) + 1
    return f"{'`' * ticks}\n{code.rstrip()}\n{'`' * ticks}"


def render_context(context):
    """何を見せたか。省いたことに気づかないと「全部見た上での APPROVE」と読み違えるため。"""
    embedded = context.get("files_included", [])
    lines = ["<details><summary>参照した資料</summary>", ""]
    lines.append("- 埋め込んだ Wiki: " + (", ".join(f"`{p}`" for p in context.get("wiki_included", [])) or "なし"))
    if context.get("wiki_omitted"):
        lines.append("- 予算超過で外した Wiki: " + ", ".join(f"`{p}`" for p in context["wiki_omitted"]))
    lines.append(f"- 全文を埋め込んだ変更ファイル ({len(embedded)} 件): " + (", ".join(f"`{p}`" for p in embedded) or "なし"))
    if context.get("files_omitted"):
        lines.append("- **埋め込めなかった変更ファイル:** "
                     + ", ".join(f"`{p}`({why})" for p, why in context["files_omitted"]))
    lines.append("- 変更していないファイル(呼び出し元など)は Gemini に見せていません。")
    if context.get("snapshot_skipped"):
        lines.append(f"- スナップショットから除外したファイル: {len(context['snapshot_skipped'])} 件"
                     "(リンク・秘匿情報らしい名前・書き出せない名前)")
    usage = context.get("usage") or {}
    if usage:
        lines.append(f"- トークン: 入力 {usage.get('promptTokenCount', '?')} / 出力 {usage.get('candidatesTokenCount', '?')}"
                     f" / 思考 {usage.get('thoughtsTokenCount', 0)}")
    lines += ["", "</details>", ""]
    return lines


def render(findings, sha, model, domain, context=None):
    verdict = verdict_of(findings)
    icon = "✅" if verdict == "APPROVE" else "🔁"
    counts = " / ".join(f"{s} {sum(f['severity'] == s for f in findings)}" for s in SEVERITIES)
    lines = [
        f"<!-- {MARKER_NAME} sha={sha} verdict={verdict} -->",
        f"## {icon} Gemini レビュー: {verdict}",
        "",
        f"`{sha[:7]}` / `{model}` / ドメイン `{domain}` / {counts}",
        "",
    ]
    if context is not None:
        lines += render_context(context)
    if not findings:
        lines.append("指摘はありません。")
    for i, f in enumerate(sorted(findings, key=lambda f: SEVERITIES.index(f["severity"])), 1):
        lines += [
            f"### {i}. [{f['severity']}] {f.get('category', '')}",
            "",
            f"**場所:** `{f.get('location', '')}`",
            "",
            f.get("issue", ""),
            "",
            f"**影響:** {f.get('impact', '')}",
            "",
        ]
        if f.get("fix_code"):
            lines += [fence(f["fix_code"]), ""]
    if verdict == "CHANGES_REQUESTED":
        lines += [
            "---",
            "依頼元は CRITICAL / WARNING を 1 件ずつ評価し、`python review/gemini_review.py --triage <file>` で",
            "評価を記録してください(修正する場合は push 後に再レビュー)。",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------
def resolve_budgets(thinking, max_chars=None, wiki_chars=None):
    """思考レベルに応じた (max_chars, wiki_chars) を返す。明示指定 (None 以外) があればそちらを優先する。"""
    default_max, default_wiki = THINKING_BUDGETS.get(thinking, (DEFAULT_MAX_CHARS, DEFAULT_WIKI_CHARS))
    return (
        default_max if max_chars is None else max_chars,
        default_wiki if wiki_chars is None else wiki_chars,
    )


def cmd_review(args):
    info = pr_info(args.pr)
    pr, sha = info["number"], info["headRefOid"]
    if info["state"] != "OPEN":
        raise ReviewError(f"PR #{pr} は {info['state']} です。")

    green, detail = ci_state(pr, sha)
    if not green:
        print(f"PR #{pr} の CI が緑ではありません ({detail})。CI が通ってからレビューしてください。")
        return 2
    print(f"PR #{pr} `{sha[:7]}`: CI {detail}")

    diff = gh("pr", "diff", str(pr))
    if not diff.strip():
        raise ReviewError(f"PR #{pr} の差分が空です。")
    max_chars, wiki_chars = resolve_budgets(args.thinking, args.max_chars, args.wiki_chars)
    if len(diff) > max_chars:
        raise ReviewError(f"差分が {len(diff)} 文字あり、上限 {max_chars} を超えています。"
                          "PR を分けるか、--max-chars で上限を上げてください。")
    changed = changed_paths(diff)

    with snapshot_dir() as root:
        written, skipped = extract_snapshot(fetch_tarball(sha), root)
        wiki_pages, wiki_omitted = select_wiki_pages(root, changed, wiki_chars)
        files, files_omitted = read_changed_files(root, changed, args.files_chars,
                                                  skip=[rel for rel, _ in wiki_pages], excluded=skipped)
    # ここから先はスナップショットを使わない (本文は読み終えている)。
    print(f"スナップショット {written} ファイル / Wiki {len(wiki_pages)} ページ"
          f"{f' (予算超過で {len(wiki_omitted)} ページ除外)' if wiki_omitted else ''}"
          f" / 変更ファイルの全文 {len(files)} 件"
          f"{f' ({len(files_omitted)} 件は埋め込めず)' if files_omitted else ''}")
    prompt = build_prompt(diff, args.domain, files, wiki_pages)
    print(f"Gemini API ({args.model} / 思考 {args.thinking}) でレビュー中... プロンプト {len(prompt)} 文字")
    findings, usage = call_gemini(prompt, args.model, args.thinking, args.timeout)
    context = {
        "wiki_included": [rel for rel, _ in wiki_pages],
        "wiki_omitted": wiki_omitted,
        "files_included": [rel for rel, _ in files],
        "files_omitted": files_omitted,
        "snapshot_skipped": skipped,
        "usage": usage,
    }

    # レビュー中に push されていたら、古い差分の結果を新しい head に紐付けない。
    if pr_info(pr)["headRefOid"] != sha:
        raise ReviewError("レビュー中に PR の head が変わりました。再実行してください。")

    report = render(findings, sha, f"{args.model} ({args.thinking})", args.domain, context)
    WORK_DIR.mkdir(exist_ok=True)
    out = WORK_DIR / f"pr-{pr}-{sha[:7]}.md"
    out.write_text(report, encoding="utf-8")
    if not args.no_post:
        gh("pr", "comment", str(pr), "--body-file", str(out))
    print(report)
    print(f"(レポート: {out}{'' if args.no_post else ' / PR にコメントしました'})")
    return 0 if verdict_of(findings) == "APPROVE" else 3


def cmd_triage(args):
    info = pr_info(args.pr)
    pr, sha = info["number"], info["headRefOid"]
    reviews = {s: v for s, v in REVIEW_MARKER_RE.findall(pr_comments(pr))}
    if reviews.get(sha) != "CHANGES_REQUESTED":
        print(f"`{sha[:7]}` に対する差し戻しのレビューがありません。評価は差し戻しに対してだけ記録します。")
        return 2
    body = Path(args.triage).read_text(encoding="utf-8").strip()
    if not body:
        raise ReviewError("評価の本文が空です。")
    comment = f"<!-- {MARKER_NAME}-triage sha={sha} -->\n## Gemini レビューの評価 (`{sha[:7]}`)\n\n{body}\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
        f.write(comment)
    try:
        gh("pr", "comment", str(pr), "--body-file", f.name)
    finally:
        os.unlink(f.name)
    print(f"PR #{pr} に評価を記録しました。")
    return 0


def check_merge(comments, sha):
    """(マージしてよいか, 理由)。merge hook から使う。"""
    reviews = {s: v for s, v in REVIEW_MARKER_RE.findall(comments)}
    triaged = set(TRIAGE_MARKER_RE.findall(comments))
    verdict = reviews.get(sha)
    if verdict is None:
        return False, (f"head `{sha[:7]}` に対する Gemini レビューがありません。"
                       "CI が緑になってから `python review/gemini_review.py` を実行してください。")
    if verdict == "CHANGES_REQUESTED" and sha not in triaged:
        return False, (f"head `{sha[:7]}` のレビューは差し戻しで、評価が記録されていません。"
                       "指摘を評価し、的外れなら上流に、確かめられないならこのリポジトリに起票した上で "
                       "`python review/gemini_review.py --triage <file>` を実行してください。"
                       "成立を確かめた指摘があるなら修正して push してください。")
    if verdict == "CHANGES_REQUESTED" and has_unverified_critical(triage_bodies(comments, sha)):
        return False, (f"head `{sha[:7]}` の評価に未確認の CRITICAL があります。"
                       "マージせず、状況を人間に報告してください。")
    return True, f"head `{sha[:7]}` は Gemini レビュー済みです ({verdict})。"


def triage_bodies(comments, sha):
    """sha に対する評価コメントの本文。次のマーカーまでを 1 件とみなす(コメントの境界は取れないため)。"""
    marker = re.compile(rf"<!-- {MARKER_NAMES_RE}-triage sha={re.escape(sha)} -->")
    return [ANY_MARKER_RE.split(part, maxsplit=1)[0] for part in marker.split(comments)[1:]]


# 判定は最初の `—` の直後の語。理由文に「未確認」が出てくるだけでは止めない。
UNVERIFIED_CRITICAL_RE = re.compile(r"^\s*-[^\n]*\[CRITICAL\][^—\n]*—\s*未確認", re.MULTILINE)


def has_unverified_critical(bodies):
    """最新の評価に `- N. [CRITICAL] <題> — 未確認。...` の行があるか。

    未確認を確かめた後に評価を記録し直せるよう、古い評価は見ない。
    """
    return bool(bodies) and bool(UNVERIFIED_CRITICAL_RE.search(bodies[-1]))


def cmd_check(args):
    info = pr_info(args.pr)
    ok, reason = check_merge(pr_comments(info["number"]), info["headRefOid"])
    print(f"PR #{info['number']}: {reason}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 2


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pr", type=int, help="PR 番号。省略時は現在のブランチの PR")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--triage", metavar="FILE", help="差し戻しに対する評価 (Markdown) を PR に記録する")
    mode.add_argument("--check", action="store_true", help="マージしてよいかを判定する (merge hook 用)")
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help=f"ドメイン不変条件 (既定: {DEFAULT_DOMAIN})")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini API のモデル ID (既定: {DEFAULT_MODEL})")
    p.add_argument("--thinking", default=DEFAULT_THINKING, choices=THINKING_LEVELS,
                   help=f"思考レベル (既定: {DEFAULT_THINKING})")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_MIN, help="API 呼び出しのタイムアウト (分)")
    p.add_argument("--max-chars", type=int, default=None,
                   help=f"差分の上限文字数 (既定: {DEFAULT_MAX_CHARS}、--thinking high / xhigh 指定時は {THINKING_BUDGETS['high'][0]})")
    p.add_argument("--wiki-chars", type=int, default=None,
                   help=f"埋め込む Wiki ページの合計文字数 (既定: {DEFAULT_WIKI_CHARS}、--thinking high / xhigh 指定時は {THINKING_BUDGETS['high'][1]})")
    p.add_argument("--files-chars", type=int, default=DEFAULT_FILES_CHARS, help="埋め込む変更ファイル (全文) の合計文字数")
    p.add_argument("--no-post", action="store_true", help="PR にコメントせずローカルにだけ出力する")
    args = p.parse_args()
    try:
        if args.triage:
            return cmd_triage(args)
        if args.check:
            return cmd_check(args)
        return cmd_review(args)
    except ReviewError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
