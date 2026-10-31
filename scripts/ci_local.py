"""CI と同じ検査を手元で実行し、結果を HEAD のコミットに commit status として載せる。

GitHub Actions が課金・上限で起動しないとき (注記「The job was not started」) の代替。
`.github/workflows/test.yml` もこのスクリプトの `--job test --no-report` を呼ぶので、
検査の中身はここが正になる。

    python scripts/ci_local.py               # 全ジョブを実行し、local/<ジョブ名> を載せる
    python scripts/ci_local.py --no-report   # 実行だけ (GitHub に載せない)
    python scripts/ci_local.py --job test    # 1 ジョブだけ

終了コード: 0 = 全ジョブ成功 / 1 = 失敗したジョブがある / 2 = 前提を満たさない (結果を載せられない)
"""
import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_PREFIX = "local/"  # review/agy_review.py の LOCAL_STATUS_PREFIX と揃える


def jobs(root: Path = ROOT) -> dict:
    """ジョブ名 → 実行するコマンドの列。名前は Actions のジョブ名 (`jobs:` 直下のキー) と同じにする。"""
    pytest_targets = ["review/tests"] + (["tests"] if (root / "tests").is_dir() else [])
    node_tests = sorted(str(p.relative_to(root)).replace("\\", "/")
                        for d in (".claude/hooks/test", "scripts/test") for p in (root / d).glob("*.test.mjs"))
    test = [[sys.executable, "-m", "pytest", *pytest_targets, "-v"]]
    if node_tests:
        test.append(["node", "--test", *node_tests])
    test += frontend_commands(root)
    return {"test": test, "wiki-lint": [["node", "scripts/wiki-lint.js"]]}


def frontend_commands(root: Path) -> list:
    """frontend/ の単体テスト。frontend/ が無ければ何もしない。

    node_modules が無いときは飛ばさずに `npm ci` から始める (飛ばすと「検査していないのに成功」が
    載る)。npm は Windows では npm.cmd なので、名前のままでは subprocess が見つけられない。
    """
    if not (root / "frontend" / "package.json").is_file():
        return []
    npm = shutil.which("npm") or "npm"
    cmds = []
    if not (root / "frontend" / "node_modules").is_dir():
        cmds.append([npm, "ci", "--prefix", "frontend"])
    cmds.append([npm, "--prefix", "frontend", "test"])
    return cmds


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", errors="replace", **kw)


def precondition_error() -> str | None:
    """結果を HEAD のものとして載せられないなら、その理由。"""
    dirty = run(["git", "status", "--porcelain"], capture_output=True)
    if dirty.returncode != 0:
        return f"git status が失敗しました: {dirty.stderr.strip()}"
    if dirty.stdout.strip():
        return "未コミットの変更か未追跡のファイルがあります。結果が HEAD のものだと言えないので載せません"
    remote = run(["git", "branch", "-r", "--contains", "HEAD"], capture_output=True)
    if remote.returncode != 0 or not remote.stdout.strip():
        return "HEAD がまだ push されていません。push してから実行してください"
    return None


def post_status(sha: str, job: str, state: str, description: str) -> bool:
    proc = run(["gh", "api", "-X", "POST", f"repos/{{owner}}/{{repo}}/statuses/{sha}",
                "-f", f"state={state}", "-f", f"context={STATUS_PREFIX}{job}",
                "-f", f"description={description[:140]}"], capture_output=True)
    if proc.returncode != 0:
        print(f"[ci_local] status を載せられませんでした ({job}): {proc.stderr.strip()}", file=sys.stderr)
    return proc.returncode == 0


def run_job(name: str, commands: list) -> tuple[bool, float]:
    start = time.monotonic()
    for cmd in commands:
        print(f"\n[ci_local] {name}: {' '.join(cmd)}", flush=True)
        try:
            if run(cmd).returncode != 0:
                return False, time.monotonic() - start
        except FileNotFoundError:
            print(f"[ci_local] コマンドが見つかりません: {cmd[0]}", file=sys.stderr)
            return False, time.monotonic() - start
    return True, time.monotonic() - start


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-report", action="store_true", help="GitHub に commit status を載せない")
    ap.add_argument("--job", action="append", help="実行するジョブ (複数可。既定は全ジョブ)")
    args = ap.parse_args(argv)

    all_jobs = jobs()
    names = args.job or list(all_jobs)
    unknown = [n for n in names if n not in all_jobs]
    if unknown:
        print(f"[ci_local] 不明なジョブ: {', '.join(unknown)} (あるのは {', '.join(all_jobs)})", file=sys.stderr)
        return 2

    sha = None
    if not args.no_report:
        err = precondition_error()
        if err:
            print(f"[ci_local] {err}", file=sys.stderr)
            return 2
        sha = run(["git", "rev-parse", "HEAD"], capture_output=True).stdout.strip()
        for n in names:
            post_status(sha, n, "pending", "ローカルで実行中")

    host = socket.gethostname()
    failed = []
    for n in names:
        ok, sec = run_job(n, all_jobs[n])
        if not ok:
            failed.append(n)
        if sha:
            post_status(sha, n, "success" if ok else "failure", f"{host} で{'成功' if ok else '失敗'} ({sec:.0f} 秒)")

    print(f"\n[ci_local] 結果: " + ", ".join(f"{n}={'失敗' if n in failed else '成功'}" for n in names)
          + (f" / {sha[:7]} に local/* として記録" if sha else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
