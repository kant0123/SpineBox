"""deploy/ の PowerShell スクリプト(常駐起動・タスク登録・ファイアウォール)の検査。

タスクの登録・規則の追加はシステムの設定変更なので、テストでは実行しない(`-WhatIf` で内容の表示だけ確かめる)。
再起動ループはユーザー権限の通常プロセスとして、偽の `spinebox` パッケージ(すぐ終了する)で確かめる。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
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
