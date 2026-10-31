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
SCRIPTS = ["start_spinebox.ps1", "install_task.ps1", "firewall_rule.ps1"]
WINDOWS = sys.platform == "win32"
# 本番は Windows PowerShell 5.1(OS 標準)で動かすので、実行するテストは powershell.exe に限る
POWERSHELL = shutil.which("powershell")
PARSER_SHELL = POWERSHELL or shutil.which("pwsh")

needs_windows_ps = pytest.mark.skipif(not (WINDOWS and POWERSHELL), reason="Windows PowerShell が要る")


def run_ps(args, **kw):
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, timeout=120, **kw)


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
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-TaskName", name,
                "-RepoPath", str(fake_repo(tmp_path, 0)), "-PythonPath", sys.executable, "-ConfigPath", str(conf)])
    out = r.stdout.decode("cp932", "replace") + r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert name in out and "start_spinebox.ps1" in out and "-ConfigPath" in out
    after = run_ps(["-Command", f"(Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue | Measure-Object).Count"])
    assert after.stdout.decode("ascii", "replace").strip() == "0"


@needs_windows_ps
def test_register_rejects_a_missing_repo():
    r = run_ps(["-File", str(DEPLOY / "install_task.ps1"), "-Action", "Register", "-WhatIf", "-RepoPath", "no-such-repo"])
    assert r.returncode != 0


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
