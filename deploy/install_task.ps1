<#
  SpineBox の常駐起動をタスクスケジューラへ登録・解除・確認する。

    .\install_task.ps1 -Action Register   -PythonPath C:\Python312\python.exe -ConfigPath D:\SpineBox\config.local.toml
    .\install_task.ps1 -Action Status
    .\install_task.ps1 -Action Stop
    .\install_task.ps1 -Action Unregister

  Register / Stop / Unregister は管理者権限の PowerShell が要る(システムの設定・他ユーザーのプロセスを触るため)。Status は不要。
  Stop は、タスクを止めたうえで python(-m spinebox)やラッパーが残っていないかを確かめ、残っていれば PID を指定して止める。
  更新手順では Stop-ScheduledTask の代わりにこれを使う(python が孤児として残るとポートを握ったままになる)。
  `-WhatIf` を付けると、登録する内容を表示するだけで何も変更しない(管理者でなくてもよい)。

  登録するタスク: OS の起動 30 秒後に start_spinebox.ps1 を実行する。ログオン不要。
    - 実行ユーザー: 既定は SYSTEM。蔵書や Python がユーザーのフォルダにある場合は -UserName を指定する
      (パスワードの入力を求める。そのユーザーがログオンしていなくても動く)
    - start_spinebox.ps1 が落ちたアプリを再起動する。スクリプト自身が落ちたときのため、タスク側にも
      1 分間隔の再試行を設定する。実行時間の上限は無し、二重起動はしない

  リポジトリ・Python・設定ファイルのパスは引数で受け取る(開発機のドライブ文字・ユーザー名を前提にしない)。

  **UTF-8 (BOM 付き) で保存すること**(BOM が無いと Windows PowerShell 5.1 が CP932 として読み、日本語が化ける)。
  手順の全体は wiki/operations/server-setup.md。
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Register", "Unregister", "Status", "Stop")]
    [string]$Action,
    [string]$TaskName = "SpineBox",
    # SpineBox のリポジトリ。既定はこのスクリプトの 1 つ上
    [string]$RepoPath = "",
    [string]$PythonPath = "python",
    # Python の前に付ける引数(空白区切り)。py ランチャーなら -PythonPath py -PythonArguments "-3.12"
    [string]$PythonArguments = "",
    [string]$ConfigPath = "",
    [string]$LogDir = "",
    # 空なら SYSTEM。指定するとパスワードを尋ねる
    [string]$UserName = "",
    # Status が応答を確かめる URL(設定の host / port に合わせる)
    [string]$HealthUrl = "http://127.0.0.1:8000/healthz",
    # Register 後すぐにタスクを開始する
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Format-Quoted {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

# タスクが実行する powershell.exe の引数を組み立てる(空の任意引数は渡さない)
function Get-TaskArguments {
    param([string]$Script, [string]$Repo, [string]$Python, [string]$PythonArgs, [string]$Config, [string]$Logs)
    $parts = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", (Format-Quoted $Script),
               "-RepoPath", (Format-Quoted $Repo), "-PythonPath", (Format-Quoted $Python))
    if ($PythonArgs) { $parts += @("-PythonArguments", (Format-Quoted $PythonArgs)) }
    if ($Config) { $parts += @("-ConfigPath", (Format-Quoted $Config)) }
    if ($Logs) { $parts += @("-LogDir", (Format-Quoted $Logs)) }
    return ($parts -join " ")
}

function Get-ExistingTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

# SpineBox のプロセス: ラッパー(start_spinebox.ps1 を動かしている PowerShell)と、`python -m spinebox`(py ランチャー含む)。
# ラッパーを先に並べる(python だけ止めてもラッパーが起動し直すため、止める順序に使う)
function Get-SpineBoxProcesses {
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $wrappers = @($all | Where-Object { $_.Name -match "^(powershell|pwsh)\.exe$" -and $_.CommandLine -match "start_spinebox\.ps1" })
    $pythons = @($all | Where-Object { $_.Name -match "^(python|pythonw|py)[0-9.]*\.exe$" -and $_.CommandLine -match "-m\s+spinebox" })
    return @($wrappers) + @($pythons)
}

function Show-Status {
    $task = Get-ExistingTask
    if (-not $task) {
        Write-Host "タスク '$TaskName' は登録されていません"
    }
    else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host ("タスク '{0}': 状態={1}, 実行ユーザー={2}" -f $TaskName, $task.State, $task.Principal.UserId)
        Write-Host ("  最終実行={0}, 最終結果={1}, 次回実行={2}" -f $info.LastRunTime, $info.LastTaskResult, $info.NextRunTime)
        Write-Host ("  実行内容: {0} {1}" -f $task.Actions[0].Execute, $task.Actions[0].Arguments)
    }
    # タスクを止めても残っていないかの確認用
    $procs = @(Get-SpineBoxProcesses)
    if ($procs.Count -eq 0) { Write-Host "SpineBox のプロセス: なし" }
    foreach ($p in $procs) { Write-Host ("SpineBox のプロセス: PID={0} {1}" -f $p.ProcessId, $p.CommandLine) }
    try {
        $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        Write-Host ("{0} -> {1} {2}" -f $HealthUrl, [int]$resp.StatusCode, $resp.Content)
    }
    catch {
        Write-Host ("{0} -> 応答なし ({1})" -f $HealthUrl, $_.Exception.Message)
    }
}

function Register-SpineBoxTask {
    $startScript = Join-Path $PSScriptRoot "start_spinebox.ps1"
    if (-not (Test-Path -LiteralPath $startScript)) { throw "start_spinebox.ps1 が見つかりません: $startScript" }
    $repo = (Resolve-Path -LiteralPath $RepoPath -ErrorAction SilentlyContinue).Path
    if (-not $repo -or -not (Test-Path -LiteralPath (Join-Path $repo "spinebox\__main__.py"))) {
        throw "RepoPath に spinebox\__main__.py が見つかりません: $RepoPath"
    }
    if (-not (Get-Command $PythonPath -ErrorAction SilentlyContinue)) { throw "Python が見つかりません: $PythonPath" }
    $config = ""
    if ($ConfigPath) {
        if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw "設定ファイルが見つかりません: $ConfigPath" }
        $config = (Resolve-Path -LiteralPath $ConfigPath).Path
    }
    # 登録時のカレントやユーザーの PATH に依存させないため、Python は絶対パスにして渡す
    $python = (Get-Command $PythonPath).Source
    $arguments = Get-TaskArguments -Script $startScript -Repo $repo -Python $python -PythonArgs $PythonArguments -Config $config -Logs $LogDir

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = "PT30S"   # ドライブやネットワークが揃うのを待つ
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

    Write-Host "登録する内容:"
    Write-Host "  タスク名: $TaskName"
    Write-Host ("  実行ユーザー: {0}" -f $(if ($UserName) { $UserName } else { "SYSTEM" }))
    Write-Host "  トリガー: OS の起動 30 秒後(ログオン不要)"
    Write-Host "  実行: powershell.exe $arguments"

    if ((Get-ExistingTask) -and -not $WhatIfPreference) { Write-Host "  (既存のタスクを置き換えます)" }
    if (-not $PSCmdlet.ShouldProcess($TaskName, "タスクスケジューラへ登録")) { return }
    if (-not (Test-Administrator)) { throw "管理者権限の PowerShell で実行してください" }

    if ($UserName) {
        $cred = Get-Credential -UserName $UserName -Message "タスクを実行するユーザーのパスワード"
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
    }
    else {
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    }
    Write-Host "登録しました: $TaskName"
    if ($StartNow) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "開始しました。数秒後に -Action Status で応答を確認してください"
    }
}

# タスクの停止後に、ラッパーと python が残っていれば PID を指定して止める。
# 通常はラッパーが Job Object で python を道連れにするので、何も残らない。残っていたときだけここで止める。
function Stop-LeftoverProcesses {
    $left = @()
    for ($i = 0; $i -lt 20; $i++) {
        $left = @(Get-SpineBoxProcesses)
        if ($left.Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    }
    foreach ($p in $left) {
        Write-Host ("残っていたので止めます: PID={0} {1}" -f $p.ProcessId, $p.CommandLine)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    $still = @(Get-SpineBoxProcesses)
    if ($still.Count -gt 0) { throw ("止められないプロセスが残っています: PID=" + (($still | ForEach-Object { $_.ProcessId }) -join ",")) }
}

function Stop-SpineBoxTask {
    if (-not (Get-ExistingTask)) {
        Write-Host "タスク '$TaskName' は登録されていません(何も止めません)"
        return
    }
    if (-not $PSCmdlet.ShouldProcess($TaskName, "タスクを停止し、残った SpineBox のプロセスを止める")) { return }
    if (-not (Test-Administrator)) { throw "管理者権限の PowerShell で実行してください" }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-LeftoverProcesses
    Write-Host "停止しました: $TaskName"
}

function Unregister-SpineBoxTask {
    if (-not (Get-ExistingTask)) { Write-Host "タスク '$TaskName' は登録されていません"; return }
    if (-not $PSCmdlet.ShouldProcess($TaskName, "停止して登録を解除")) { return }
    if (-not (Test-Administrator)) { throw "管理者権限の PowerShell で実行してください" }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Stop-LeftoverProcesses
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "解除しました: $TaskName"
}

switch ($Action) {
    "Register" { Register-SpineBoxTask }
    "Unregister" { Unregister-SpineBoxTask }
    "Stop" { Stop-SpineBoxTask }
    "Status" { Show-Status }
}
