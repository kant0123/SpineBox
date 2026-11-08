"""deploy/ の PowerShell スクリプト(常駐起動・タスク登録・ファイアウォール)の検査。

タスクの登録・規則の追加はシステムの設定変更なので、テストでは実行しない(`-WhatIf` で内容の表示だけ確かめる)。
再起動ループはユーザー権限の通常プロセスとして、偽の `spinebox` パッケージ(すぐ終了する)で確かめる。
孤児の後始末のテストが止めるのは、テスト自身が起動したプロセスだけ(偽の `spinebox` が待ち受ける空きポートを使う)。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
SCRIPTS = ["start_spinebox.ps1", "install_task.ps1", "firewall_rule.ps1", "update_spinebox.ps1"]
BATCHES = {"restart_spinebox.bat": "Restart", "update_spinebox.bat": "Update"}
WINDOWS = sys.platform == "win32"
# 本番は Windows PowerShell 5.1(OS 標準)で動かすので、実行するテストは powershell.exe に限る
POWERSHELL = shutil.which("powershell")
PARSER_SHELL = POWERSHELL or shutil.which("pwsh")

needs_windows_ps = pytest.mark.skipif(not (WINDOWS and POWERSHELL), reason="Windows PowerShell が要る")


def run_ps(args, **kw):
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, timeout=120, **kw)


def both_decodings(data):
    # PowerShell の出力の文字コードは環境で変わる(cp932 / utf-8)ので、両方で読んで部分文字列を探す
    return data.decode("cp932", "replace") + data.decode("utf-8", "replace")


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_are_utf8_with_bom(name):
    # BOM が無いと Windows PowerShell 5.1 が CP932 として読み、日本語が化けてスクリプトが動かない
    assert (DEPLOY / name).read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(PARSER_SHELL is None, reason="PowerShell が無い")
@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_have_no_syntax_errors(name):
    path = str(DEPLOY / name).replace("'", "''")
    cmd = (f"$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$e); "
           "if ($e.Count -gt 0) { $e | ForEach-Object { $_.Message }; exit 1 }")
    r = subprocess.run([PARSER_SHELL, "-NoProfile", "-Command", cmd], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


def fake_repo(tmp_path, exit_code):
    repo = tmp_path / "repo"
    (repo / "spinebox").mkdir(parents=True)
    (repo / "spinebox" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "spinebox" / "__main__.py").write_text(f"import sys\nprint('起動した')\nsys.exit({exit_code})\n", encoding="utf-8")
    return repo


@needs_windows_ps
def test_restart_loop_restarts_and_logs_each_run(tmp_path):
    repo = fake_repo(tmp_path, 3)
    logs = tmp_path / "logs"
    r = run_ps(["-File", str(DEPLOY / "start_spinebox.ps1"), "-RepoPath", str(repo), "-PythonPath", sys.executable,
                "-LogDir", str(logs), "-MaxRuns", "3", "-MinDelaySeconds", "1", "-MaxDelaySeconds", "2"])
    assert r.returncode == 3, r.stderr.decode("utf-8", "replace")  # 最後に起動したプロセスの終了コード
    supervisor = (logs / "supervisor.log").read_text(encoding="utf-8")
    assert supervisor.count("起動:") == 3 and supervisor.count("終了: code=3") == 3
    assert supervisor.count("ポートの事前確認を省略します") == 3  # この偽の spinebox は --print-bind を話さない。確認できなくても起動は止めない
    # 間隔は 1 秒 → 2 秒(上限)と延びる。最後の 1 回のあとは待たない
    assert supervisor.count("1 秒後に再起動します") == 1 and supervisor.count("2 秒後に再起動します") == 1
    # 起動ごとに別のログファイル(前回のログを上書きしない)。標準出力は UTF-8 で残る
    outs = sorted(logs.glob("spinebox-*.out.log"))
    assert len(outs) == 3 and len(list(logs.glob("spinebox-*.err.log"))) == 3
    assert all("起動した" in p.read_text(encoding="utf-8") for p in outs)


@needs_windows_ps
def test_old_logs_are_pruned(tmp_path):
    repo = fake_repo(tmp_path, 0)
    logs = tmp_path / "logs"
    logs.mkdir()
    for day in range(1, 6):
        for kind in ("out", "err"):
            (logs / f"spinebox-2000010{day}-000000.{kind}.log").write_text("old", encoding="utf-8")
    r = run_ps(["-File", str(DEPLOY / "start_spinebox.ps1"), "-RepoPath", str(repo), "-PythonPath", sys.executable,
                "-LogDir", str(logs), "-MaxRuns", "1", "-KeepLogs", "2"])
    assert r.returncode == 0
    names = sorted(p.name for p in logs.glob("spinebox-*.log"))
    assert len(names) == 4 and names[0].startswith("spinebox-20000105")  # 新しい 2 回分(今回 + 20000105)だけ残る
    assert (logs / "supervisor.log").exists()


@needs_windows_ps
@pytest.mark.parametrize("extra", [
    ["-PythonPath", "no-such-python-xyz"],
    ["-ConfigPath", "no-such-config.toml"],
    ["-RepoPath", "no-such-repo"],
])
def test_invalid_arguments_exit_2_without_looping(tmp_path, extra):
    repo = fake_repo(tmp_path, 0)
    params = {"-RepoPath": str(repo), "-PythonPath": sys.executable, "-LogDir": str(tmp_path / "logs"), "-MaxRuns": "1"}
    params.update(dict(zip(extra[::2], extra[1::2])))  # 1 つだけ不正な値に差し替える
    r = run_ps(["-File", str(DEPLOY / "start_spinebox.ps1"), *[x for kv in params.items() for x in kv]])
    assert r.returncode == 2
    assert not list((tmp_path / "logs").glob("spinebox-*.log"))  # 一度も起動していない


@needs_windows_ps
def test_register_whatif_prints_the_plan_and_changes_nothing(tmp_path):
    name = "SpineBoxTest-" + uuid.uuid4().hex[:8]
    conf = tmp_path / "config.toml"
    conf.write_text("", encoding="utf-8")
    # 設定の host が 0.0.0.0 でも、稼働確認の URL は設定の port と 127.0.0.1 から決まる(#76)
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-TaskName", name,
                "-RepoPath", str(fake_bind_repo(tmp_path, "0.0.0.0 8123")), "-PythonPath", sys.executable,
                "-PythonArguments", "-B", "-ConfigPath", str(conf)])
    out = r.stdout.decode("cp932", "replace") + r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert name in out and "start_spinebox.ps1" in out and "-ConfigPath" in out
    assert "http://127.0.0.1:8123/healthz" in out
    after = run_ps(["-Command", f"(Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue | Measure-Object).Count"])
    assert after.stdout.decode("ascii", "replace").strip() == "0"


@needs_windows_ps
def test_register_rejects_a_missing_repo():
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-RepoPath", "no-such-repo"])
    assert r.returncode != 0


@needs_windows_ps
def test_register_stops_before_registering_when_python_is_missing_or_cannot_read_the_config(tmp_path):
    # 解決できない Python・設定を読めない Python は登録前に止め、案内(フルパスを渡す)を出す
    name = "SpineBoxTest-" + uuid.uuid4().hex[:8]
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-TaskName", name,
                "-RepoPath", str(fake_bind_repo(tmp_path / "a", "0.0.0.0 8123")), "-PythonPath", "no-such-python-xyz"])
    assert r.returncode != 0 and "-PythonPath に python.exe のフルパス" in both_decodings(r.stdout + r.stderr)
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-TaskName", name,
                "-RepoPath", str(fake_repo(tmp_path / "b", 3)), "-PythonPath", sys.executable, "-PythonArguments", "-B"])
    assert r.returncode != 0 and "設定を読めません" in both_decodings(r.stdout + r.stderr)


@needs_windows_ps
def test_status_derives_the_health_url_from_the_config(tmp_path):
    # -HealthUrl を省くと設定の host / port から決める。0.0.0.0 は 127.0.0.1 で確かめる。タスク未登録でも確かめる
    name = "SpineBoxTest-" + uuid.uuid4().hex[:8]
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Status", "-TaskName", name,
                "-RepoPath", str(fake_bind_repo(tmp_path, "0.0.0.0 8123")), "-PythonPath", sys.executable,
                "-PythonArguments", "-B"])
    out = r.stdout.decode("utf-8", "replace") + r.stdout.decode("cp932", "replace")
    assert r.returncode == 0, out
    assert "http://127.0.0.1:8123/healthz" in out


@needs_windows_ps
def test_status_reports_when_the_health_url_cannot_be_decided(tmp_path):
    # 設定を読めないときは Status を落とさず、URL を決められないと表示する
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Status", "-TaskName", "SpineBoxTest-" + uuid.uuid4().hex[:8],
                "-RepoPath", str(fake_repo(tmp_path, 3)), "-PythonPath", sys.executable, "-PythonArguments", "-B"])
    assert r.returncode == 0 and "稼働確認の URL を決められません" in both_decodings(r.stdout)


@needs_windows_ps
def test_firewall_add_whatif_changes_nothing_and_rejects_bad_port():
    name = "SpineBoxTest-" + uuid.uuid4().hex[:8]
    r = run_ps(["-File", str(DEPLOY / "firewall_rule.ps1"), "-Action", "Add", "-WhatIf", "-Port", "18765", "-RuleName", name])
    out = r.stdout.decode("cp932", "replace") + r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0 and name in out and "Private" in out and "18765" in out
    after = run_ps(["-Command", f"(Get-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue | Measure-Object).Count"])
    assert after.stdout.decode("ascii", "replace").strip() == "0"
    assert run_ps(["-File", str(DEPLOY / "firewall_rule.ps1"), "-Action", "Add", "-WhatIf", "-Port", "99999"]).returncode != 0


# --- 子プロセスの後始末(#23) ---

FAKE_SERVER = """import os, socket, sys, time
port = int(os.environ["FAKE_PORT"])
if "--print-bind" in sys.argv:
    print("127.0.0.1 %d" % port)
    sys.exit(0)
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    print("bind 失敗", flush=True)
    sys.exit(98)
s.listen()
open(os.environ["FAKE_PIDFILE"], "w").write(str(os.getpid()))
time.sleep(float(os.environ.get("FAKE_LIFETIME", "600")))
"""

# 親(この -c のプロセス)をすぐ終わらせて、子の `-m spinebox` を孤児にする
ORPHAN_SPAWNER = ("import subprocess, sys; subprocess.Popen([sys.executable, '-m', 'spinebox'], "
                  "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")


def fake_server_repo(tmp_path):
    """指定ポートで待ち受け、FAKE_LIFETIME 秒で終わる偽の spinebox。--print-bind も話す。"""
    repo = tmp_path / "repo"
    (repo / "spinebox").mkdir(parents=True)
    (repo / "spinebox" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "spinebox" / "__main__.py").write_text(FAKE_SERVER, encoding="utf-8")
    return repo


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pid_alive(pid):
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def wait_for(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def kill_own(pid):
    """テスト自身が起動したプロセスの後片付け(PID 指定)。"""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)


def read_pid(path):
    try:
        return int(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def spawn_orphan(repo, env, pidfile):
    subprocess.run([sys.executable, "-c", ORPHAN_SPAWNER], cwd=repo, env=env, timeout=60, check=True)
    assert wait_for(lambda: read_pid(pidfile) is not None), "孤児が起動しなかった"
    return read_pid(pidfile)


def run_wrapper_once(tmp_path, repo, env, *extra):
    """偽の spinebox を 1 回だけ起動するラッパーを回す(自分の偽 spinebox は 1 秒で終わる)。"""
    logs = tmp_path / "logs"
    r = run_ps(["-File", str(DEPLOY / "start_spinebox.ps1"), "-RepoPath", str(repo), "-PythonPath", sys.executable,
                "-LogDir", str(logs), "-MaxRuns", "1", *extra],
               env={**env, "FAKE_PIDFILE": str(tmp_path / "new.pid"), "FAKE_LIFETIME": "1"})
    return r, (logs / "supervisor.log").read_text(encoding="utf-8")


@needs_windows_ps
def test_killing_the_wrapper_also_stops_the_python_child(tmp_path):
    # 受け入れ基準: ラッパーを強制終了しても python が孤児として残らない(Job Object)
    repo = fake_server_repo(tmp_path)
    pidfile = tmp_path / "child.pid"
    env = {**os.environ, "FAKE_PORT": str(free_port()), "FAKE_PIDFILE": str(pidfile)}
    wrapper = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(DEPLOY / "start_spinebox.ps1"),
         "-RepoPath", str(repo), "-PythonPath", sys.executable, "-LogDir", str(tmp_path / "logs")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child = None
    try:
        assert wait_for(lambda: read_pid(pidfile) is not None), "python の子が起動しなかった"
        child = read_pid(pidfile)
        assert pid_alive(child)
        wrapper.kill()  # TerminateProcess。finally も走らない強制終了
        wrapper.wait(timeout=30)
        assert wait_for(lambda: not pid_alive(child), timeout=15), "ラッパーを終了しても python が残った"
        assert "Job Object)を有効にしました" in (tmp_path / "logs" / "supervisor.log").read_text(encoding="utf-8")
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
        if child and pid_alive(child):
            kill_own(child)


@needs_windows_ps
def test_orphan_holding_the_port_is_stopped_before_start(tmp_path):
    # 受け入れ基準: 前回の孤児(親が既に居ない `-m spinebox`)がポートを握っていたら、検出してログに出し、止めて起動する
    repo = fake_server_repo(tmp_path)
    port = free_port()
    env = {**os.environ, "FAKE_PORT": str(port), "FAKE_PIDFILE": str(tmp_path / "orphan.pid")}
    orphan = spawn_orphan(repo, env, tmp_path / "orphan.pid")
    try:
        assert pid_alive(orphan)
        r, supervisor = run_wrapper_once(tmp_path, repo, env)
        assert r.returncode == 0, supervisor  # 孤児を止めたあとで bind できた(失敗なら 98)
        assert f"ポート {port} を前回の孤児が握っているため止めます: PID={orphan}" in supervisor
        assert not pid_alive(orphan)
    finally:
        if pid_alive(orphan):
            kill_own(orphan)


@needs_windows_ps
def test_orphan_is_only_logged_with_no_reap_orphan(tmp_path):
    repo = fake_server_repo(tmp_path)
    env = {**os.environ, "FAKE_PORT": str(free_port()), "FAKE_PIDFILE": str(tmp_path / "orphan.pid")}
    orphan = spawn_orphan(repo, env, tmp_path / "orphan.pid")
    try:
        r, supervisor = run_wrapper_once(tmp_path, repo, env, "-NoReapOrphan")
        assert r.returncode == 98  # bind に失敗して終了コードが残る(孤児には触らない)
        assert f"-NoReapOrphan のため止めません): PID={orphan}" in supervisor
        assert pid_alive(orphan)
    finally:
        if pid_alive(orphan):
            kill_own(orphan)


@needs_windows_ps
def test_process_with_a_live_parent_on_the_port_is_not_stopped(tmp_path):
    # 手で起動した SpineBox(親のシェルが生きている)や別のプログラムは、孤児ではないので止めない
    repo = fake_server_repo(tmp_path)
    env = {**os.environ, "FAKE_PORT": str(free_port()), "FAKE_PIDFILE": str(tmp_path / "manual.pid")}
    manual = subprocess.Popen([sys.executable, "-m", "spinebox"], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # 親 = このテストのプロセス
    try:
        assert wait_for(lambda: read_pid(tmp_path / "manual.pid") is not None), "偽の spinebox が起動しなかった"
        manual_pid = read_pid(tmp_path / "manual.pid")
        r, supervisor = run_wrapper_once(tmp_path, repo, env)
        assert r.returncode == 98
        assert f"孤児ではないので止めません): PID={manual_pid}" in supervisor
        assert pid_alive(manual_pid)
    finally:
        kill_own(manual.pid)


@needs_windows_ps
def test_stop_action_without_a_registered_task_stops_nothing():
    name = "SpineBoxTest-" + uuid.uuid4().hex[:8]
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Stop", "-TaskName", name])
    out = r.stdout.decode("cp932", "replace") + r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0 and "登録されていません" in out


@needs_windows_ps
def test_free_port_logs_nothing_about_conflicts(tmp_path):
    # ポートが空いているとき(通常の起動)は、事前確認が黙って通る。確認を省略した旨のログも出ない
    repo = fake_server_repo(tmp_path)
    env = {**os.environ, "FAKE_PORT": str(free_port()), "FAKE_PIDFILE": str(tmp_path / "x.pid")}
    r, supervisor = run_wrapper_once(tmp_path, repo, env)
    assert r.returncode == 0, supervisor
    assert "警告" not in supervisor and "省略" not in supervisor


# --- 再起動・更新のバッチ(#96) -------------------------------------------------------


@pytest.mark.parametrize("name,mode", BATCHES.items())
def test_batches_are_ascii_crlf_and_call_the_script(name, mode):
    # cmd はバッチを OEM の文字コードで読むので、日本語を入れると化ける。改行は CRLF でないと行の解釈がずれることがある
    data = (DEPLOY / name).read_bytes()
    assert data.isascii()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    text = data.decode("ascii")
    assert "update_spinebox.ps1" in text and f"-Mode {mode}" in text and "-Verb RunAs" in text


def fake_bind_repo(tmp_path, bind):
    repo = tmp_path / "repo"
    (repo / "spinebox").mkdir(parents=True)
    (repo / "spinebox" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "spinebox" / "__main__.py").write_text(
        f"import sys\nif '--print-bind' in sys.argv:\n    print({bind!r})\n    sys.exit(0)\nsys.exit(3)\n", encoding="utf-8")
    return repo


def snapshot(root):
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*") if "__pycache__" not in p.parts)


@needs_windows_ps
@pytest.mark.parametrize("mode", ["Restart", "Update"])
def test_update_dry_run_shows_the_steps_and_changes_nothing(tmp_path, mode):
    repo = fake_bind_repo(tmp_path, "0.0.0.0 8123")
    before = snapshot(repo)
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-Mode", mode, "-DryRun", "-RepoPath", str(repo),
                "-PythonPath", sys.executable, "-PythonArguments", "-B", "-TaskName", f"SpineBoxTest-{uuid.uuid4().hex[:8]}"])
    out = r.stdout.decode("utf-8", "replace") + r.stdout.decode("cp932", "replace")
    assert r.returncode == 0, out
    # 0.0.0.0 で待ち受ける設定は、この機械の 127.0.0.1 で応答を確かめる
    assert "http://127.0.0.1:8123/healthz" in out
    assert ("git pull" in out) == (mode == "Update")
    assert snapshot(repo) == before


@needs_windows_ps
def test_update_rejects_a_missing_repo_and_unreadable_config(tmp_path):
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-DryRun", "-RepoPath", str(tmp_path / "no-such")])
    assert r.returncode == 2
    repo = fake_bind_repo(tmp_path, "garbage")
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-DryRun", "-RepoPath", str(repo),
                "-PythonPath", sys.executable, "-PythonArguments", "-B"])
    assert r.returncode == 2


def fake_noisy_repo(tmp_path, bind):
    """--print-bind の前に stderr へ警告を出す偽の spinebox(古い設定のキーを無視したときの警告など)。"""
    repo = tmp_path / "repo"
    (repo / "spinebox").mkdir(parents=True)
    (repo / "spinebox" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "spinebox" / "__main__.py").write_text(
        "import sys\n"
        "print('WARNING:spinebox.config:old key ignored', file=sys.stderr)\n"
        f"print({bind!r})\n", encoding="utf-8")
    return repo


@needs_windows_ps
def test_update_reads_the_bind_line_even_with_warnings_on_stderr(tmp_path):
    repo = fake_noisy_repo(tmp_path, "192.168.0.5 8123")
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-DryRun", "-RepoPath", str(repo),
                "-PythonPath", sys.executable, "-PythonArguments", "-B"])
    out = r.stdout.decode("utf-8", "replace") + r.stdout.decode("cp932", "replace")
    assert r.returncode == 0, out
    assert "http://192.168.0.5:8123/healthz" in out


def fake_runner(tmp_path, stop_exit=0):
    """止める・起動するの代わりに呼ばれる偽の実行役。呼ばれた順を calls.txt に残す。"""
    log = tmp_path / "calls.txt"
    runner = tmp_path / "runner.ps1"
    runner.write_bytes(b"\xef\xbb\xbf" + (
        "param([string]$Action)\r\n"
        f"Add-Content -LiteralPath '{log}' -Value $Action\r\n"
        f"if ($Action -eq 'Stop') {{ exit {stop_exit} }}\r\n"
        "exit 0\r\n").encode("utf-8"))
    return runner, log


@needs_windows_ps
def test_update_starts_again_even_when_a_step_fails(tmp_path):
    # git リポジトリではないので git pull が失敗する。それでも最後に起動し直し、止めたままにしない
    repo = fake_bind_repo(tmp_path, f"127.0.0.1 {free_port()}")
    runner, log = fake_runner(tmp_path)
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-Mode", "Update", "-RepoPath", str(repo),
                "-PythonPath", sys.executable, "-PythonArguments", "-B", "-TaskRunner", str(runner), "-TimeoutSeconds", "1"])
    assert r.returncode == 1
    assert log.read_text(encoding="utf-8").split() == ["Stop", "Start"]


@needs_windows_ps
def test_update_does_nothing_more_when_stopping_fails(tmp_path):
    repo = fake_bind_repo(tmp_path, f"127.0.0.1 {free_port()}")
    runner, log = fake_runner(tmp_path, stop_exit=1)
    r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-Mode", "Update", "-RepoPath", str(repo),
                "-PythonPath", sys.executable, "-PythonArguments", "-B", "-TaskRunner", str(runner), "-TimeoutSeconds", "1"])
    assert r.returncode == 1
    assert log.read_text(encoding="utf-8").split() == ["Stop"]  # 更新も起動もしない(何も変えていない)


def health_server(tmp_path, body):
    """/healthz に body を返す小さなサーバーを別プロセスで立てる。(プロセス, ポート)を返す。"""
    port = free_port()
    script = tmp_path / "health.py"
    script.write_text(
        "import http.server, json, sys\n"
        f"BODY = json.dumps({body!r}).encode()\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()\n"
        "        self.wfile.write(BODY)\n"
        "    def log_message(self, *a): pass\n"
        f"http.server.HTTPServer(('127.0.0.1', {port}), H).serve_forever()\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    return proc, port


@needs_windows_ps
@pytest.mark.parametrize("commit", [None, "abc1234"])
def test_restart_succeeds_when_healthz_answers_with_or_without_a_commit(tmp_path, commit):
    # git が見えない環境ではアプリの /healthz が commit: null を返す。それでも正常に起動したと判定する
    proc, port = health_server(tmp_path, {"ok": True, "commit": commit})
    try:
        repo = fake_bind_repo(tmp_path, f"127.0.0.1 {port}")
        runner, log = fake_runner(tmp_path)
        r = run_ps(["-File", str(DEPLOY / "update_spinebox.ps1"), "-Mode", "Restart", "-RepoPath", str(repo),
                    "-PythonPath", sys.executable, "-PythonArguments", "-B", "-TaskRunner", str(runner), "-TimeoutSeconds", "10"])
        assert r.returncode == 0, r.stdout.decode("cp932", "replace")
        assert log.read_text(encoding="utf-8").split() == ["Stop", "Start"]
    finally:
        proc.kill()



# --- worktree-cleanup.ps1 (#121) -----------------------------------------------------

WORKTREE_CLEANUP = ROOT / "scripts" / "worktree-cleanup.ps1"


def test_worktree_cleanup_is_utf8_with_bom():
    assert WORKTREE_CLEANUP.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.skipif(PARSER_SHELL is None, reason="PowerShell が無い")
def test_worktree_cleanup_has_no_syntax_errors():
    path = str(WORKTREE_CLEANUP).replace("'", "''")
    cmd = (f"$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$e); "
           "if ($e.Count -gt 0) { $e | ForEach-Object { $_.Message }; exit 1 }")
    r = subprocess.run([PARSER_SHELL, "-NoProfile", "-Command", cmd], capture_output=True, timeout=60)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace")


def init_git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "spinebox").mkdir(parents=True)
    (repo / "frontend").mkdir(parents=True)
    (repo / ".gitignore").write_text("frontend/node_modules/\nfrontend/dist/\n", encoding="utf-8")
    (repo / "spinebox" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "frontend" / "package.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", ".gitignore", "spinebox/__init__.py", "frontend/package.json"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def make_junction(link: Path, target: Path):
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


@needs_windows_ps
def test_worktree_cleanup_removes_live_worktree_with_junction_and_readonly(tmp_path):
    repo = init_git_repo(tmp_path)
    shared_nm = tmp_path / "shared_node_modules"
    shared_nm.mkdir()
    pkg_file = shared_nm / "pkg.js"
    pkg_file.write_text("keep-me", encoding="utf-8")

    wt = tmp_path / "repo-wt1"
    subprocess.run(["git", "worktree", "add", str(wt), "-b", "feature/wt1"], cwd=repo, check=True, capture_output=True)
    (wt / "committed.txt").write_text("work", encoding="utf-8")
    subprocess.run(["git", "add", "committed.txt"], cwd=wt, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: work on branch"], cwd=wt, check=True, capture_output=True)
    make_junction(wt / "frontend" / "node_modules", shared_nm)
    # ReadOnly 属性付きのファイル、.gitignore 対象の成果物、.gitignore 外の未追跡ファイルを置く
    ro_file = wt / "spinebox" / "__init__.py"
    os.chmod(ro_file, 0o444)
    (wt / "frontend" / "dist").mkdir(parents=True)
    (wt / "frontend" / "dist" / "bundle.js").write_text("built", encoding="utf-8")
    (wt / "untracked.txt").write_text("untracked", encoding="utf-8")

    r = run_ps(["-File", str(WORKTREE_CLEANUP), "-Remove", str(wt), "-RemoveDirs"], cwd=repo)
    out = r.stdout.decode("cp932", "replace") + r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, out + r.stderr.decode("cp932", "replace")
    assert not wt.exists(), "worktree ディレクトリが残った"
    assert pkg_file.read_text(encoding="utf-8") == "keep-me", "ジャンクションのリンク先が壊れた"
    # .git/worktrees/<id> メタデータも消え、コミットを持つ作業ブランチの git branch -D が成功することを確認する
    r_branch = subprocess.run(["git", "branch", "-D", "feature/wt1"], cwd=repo, capture_output=True)
    assert r_branch.returncode == 0, "worktree メタデータが残りブランチ削除に失敗した: " + r_branch.stderr.decode("utf-8", "replace")


@needs_windows_ps
def test_worktree_cleanup_detects_and_removes_stale_dirs_without_dotgit(tmp_path):
    repo = init_git_repo(tmp_path)
    shared_nm = tmp_path / "shared_node_modules"
    shared_nm.mkdir()
    pkg_file = shared_nm / "pkg.js"
    pkg_file.write_text("keep-me", encoding="utf-8")

    # 1. .git ファイルが残っている孤立ディレクトリ
    stale_with_git = tmp_path / "repo-stale-git"
    stale_with_git.mkdir()
    (stale_with_git / ".git").write_text("gitdir: C:/nonexistent", encoding="utf-8")

    # 2. .git ファイルが消え、frontend/node_modules ジャンクションだけ残った残骸ディレクトリ
    stale_no_git = tmp_path / "repo-stale-nogit"
    make_junction(stale_no_git / "frontend" / "node_modules", shared_nm)

    # 3. .git ファイルが消えて空になった残骸ディレクトリ
    stale_empty = tmp_path / "repo-stale-empty"
    stale_empty.mkdir()

    # 4. 無関係な同名 prefix の手動ディレクトリ(メインツリー直下の項目名を含まない)
    unrelated = tmp_path / "repo-notes"
    unrelated.mkdir()
    (unrelated / "memo.txt").write_text("keep", encoding="utf-8")

    # 5. .git がディレクトリである独立した通常の git クローン
    clone_dir = tmp_path / "repo-clone"
    subprocess.run(["git", "clone", str(repo), str(clone_dir)], check=True, capture_output=True)

    # -RemoveDirs 無し: 1, 2, 3 だけが残骸候補として表示され、4, 5 は出ず、どれも削除されない
    r1 = run_ps(["-File", str(WORKTREE_CLEANUP)], cwd=repo)
    out1 = r1.stdout.decode("cp932", "replace") + r1.stdout.decode("utf-8", "replace")
    assert r1.returncode == 0, out1
    assert "repo-stale-git" in out1 and "repo-stale-nogit" in out1 and "repo-stale-empty" in out1
    assert "repo-notes" not in out1 and "repo-clone" not in out1
    assert stale_with_git.exists() and stale_no_git.exists() and stale_empty.exists()

    # -RemoveDirs 付き: 1, 2, 3 が削除され、ジャンクションのリンク先と 4, 5 は無傷で残る
    r2 = run_ps(["-File", str(WORKTREE_CLEANUP), "-RemoveDirs"], cwd=repo)
    out2 = r2.stdout.decode("cp932", "replace") + r2.stdout.decode("utf-8", "replace")
    assert r2.returncode == 0, out2
    assert not stale_with_git.exists() and not stale_no_git.exists() and not stale_empty.exists()
    assert pkg_file.read_text(encoding="utf-8") == "keep-me"
    assert (unrelated / "memo.txt").read_text(encoding="utf-8") == "keep"
    assert (clone_dir / ".git").is_dir()


@needs_windows_ps
def test_worktree_cleanup_remove_handles_already_detached_dir_with_removedirs(tmp_path):
    repo = init_git_repo(tmp_path)
    detached = tmp_path / "repo-detached"
    (detached / "spinebox").mkdir(parents=True)

    # -RemoveDirs 無しで既に git worktree list から外れたディレクトリを -Remove に渡すとエラー(exit 1)
    r1 = run_ps(["-File", str(WORKTREE_CLEANUP), "-Remove", str(detached)], cwd=repo)
    assert r1.returncode != 0
    assert detached.exists()

    # -Remove と -RemoveDirs を併せて渡せば、既に外れていても残骸掃除で削除される
    r2 = run_ps(["-File", str(WORKTREE_CLEANUP), "-Remove", str(detached), "-RemoveDirs"], cwd=repo)
    assert r2.returncode == 0
    assert not detached.exists()
