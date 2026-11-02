<#
  サーバー機の SpineBox を再起動する / 更新して再起動する。restart_spinebox.bat / update_spinebox.bat から呼ばれる。

    .\update_spinebox.ps1 -Mode Restart     # 止めて起動し直す
    .\update_spinebox.ps1 -Mode Update      # 止める → git pull → pip → npm ci / build → 起動
    .\update_spinebox.ps1 -Mode Update -DryRun   # 何も変えずに、やることだけ表示する

  前提: 常駐タスクが install_task.ps1 -Action Register で登録済みであること。止める・起動するのに管理者権限が要る
  (-DryRun は不要)。止めるのは install_task.ps1 -Action Stop(python を孤児にしない)、起動は Start-ScheduledTask。
  起動後は設定ファイルの host / port(python -m spinebox --print-bind)から /healthz を決めて応答を待つ。

  Update で途中の手順が失敗しても、最後に起動し直す(止めたまま放置しない)。git pull が失敗したならコードは前の版のまま。
  終了コード: 0 = 成功 / 1 = 手順か起動の確認に失敗 / 2 = 引数・前提の誤り。

  **UTF-8 (BOM 付き) で保存すること**(BOM が無いと Windows PowerShell 5.1 が CP932 として読み、日本語が化ける)。
  手順の全体は wiki/operations/server-setup.md。
#>
param(
    [ValidateSet("Restart", "Update")]
    [string]$Mode = "Restart",
    [string]$TaskName = "SpineBox",
    # SpineBox のリポジトリ。既定はこのスクリプトの 1 つ上
    [string]$RepoPath = "",
    # 設定の確認(--print-bind)と pip に使う Python。既定は py ランチャーの 3.12
    [string]$PythonPath = "py",
    [string]$PythonArguments = "-3.12",
    # 起動後に /healthz の応答を待つ秒数
    [int]$TimeoutSeconds = 90,
    # テスト用: 止める・起動するを、このスクリプトに `-Action Stop / Start` で任せる(管理者・タスクの確認もしない)
    [string]$TaskRunner = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }

function Stop-Invalid([string]$Message) {
    Write-Host "エラー: $Message" -ForegroundColor Red
    exit 2
}

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-PythonArgs([string[]]$Rest) {
    $pre = @()
    if ($PythonArguments) { $pre = $PythonArguments -split "\s+" | Where-Object { $_ } }
    return @($pre) + @($Rest)
}

if (-not (Test-Path -LiteralPath (Join-Path $RepoPath "spinebox"))) {
    Stop-Invalid "SpineBox のリポジトリが見つかりません: $RepoPath"
}
if ($TimeoutSeconds -lt 1) { Stop-Invalid "-TimeoutSeconds は 1 以上にしてください" }
$RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path
$installTask = Join-Path $PSScriptRoot "install_task.ps1"

# 設定の host / port から /healthz の URL を決める(0.0.0.0 / :: はこの機械の 127.0.0.1 / ::1 で確かめる)
function Get-HealthUrl {
    # 標準出力だけを読む。標準エラーには設定の警告(使われなくなったキーなど)が出ることがあり、混ぜると host を読み違える
    # Windows PowerShell 5.1 は、Stop のまま外部コマンドの stderr を受けると 1 行でも例外にする。成否は終了コードで見る
    $ErrorActionPreference = "Continue"
    $errFile = [System.IO.Path]::GetTempFileName()
    Push-Location $RepoPath
    try {
        $out = @(& $PythonPath @(Get-PythonArgs @("-m", "spinebox", "--print-bind")) 2>$errFile)
        $code = $LASTEXITCODE
        $err = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
    } finally {
        Pop-Location
        Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
    }
    if ($code -ne 0) { throw "設定を読めません(python -m spinebox --print-bind の終了コード $code): $out $err" }
    $line = @($out | Where-Object { "$_" -match '^\s*(\S+)\s+(\d+)\s*$' })
    if ($line.Count -eq 0) { throw "--print-bind の出力を解釈できません: $out" }
    [void]("$($line[-1])" -match '^\s*(\S+)\s+(\d+)\s*$')
    $h = $Matches[1]; $p = $Matches[2]
    if ($h -eq "0.0.0.0") { $h = "127.0.0.1" }
    elseif ($h -eq "::") { $h = "::1" }
    if ($h -like "*:*") { $h = "[$h]" }
    return "http://${h}:${p}/healthz"
}

function Invoke-Step([string]$Title, [scriptblock]$Body) {
    Write-Host "== $Title"
    if ($DryRun) { Write-Host "   (DryRun: 実行しない)"; return $true }
    # git・npm は進み具合や警告を stderr に出す。Stop のままだと Windows PowerShell 5.1 はそれを失敗扱いにするので、
    # 手順の成否は終了コードで判定する
    $ErrorActionPreference = "Continue"
    try {
        # 手順の出力は画面へ流す(関数の戻り値に混ぜると、成否の判定が出力で真になってしまう)
        $global:LASTEXITCODE = 0
        & $Body | Out-Host
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "終了コード $LASTEXITCODE" }
        return $true
    } catch {
        Write-Host "   失敗: $_" -ForegroundColor Red
        return $false
    }
}

function Wait-Healthy([string]$Url, [string]$ExpectCommit) {
    Write-Host "== 応答を待つ: $Url(最大 $TimeoutSeconds 秒)"
    if ($DryRun) { Write-Host "   (DryRun: 確かめない)"; return $true }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Uri $Url -TimeoutSec 5
            if ($r.ok) {
                Write-Host "   応答あり: commit $($r.commit)" -ForegroundColor Green
                # 短縮の長さが違っても同じコミットなら一致とみなす(アプリは 7 文字、git は環境で長くなりうる)。
                # git が見えない環境ではアプリが commit: null を返すので、そのときは照合しない
                if ($ExpectCommit -and $r.commit) {
                    $running = "$($r.commit)"
                    if (-not ($running.StartsWith($ExpectCommit) -or $ExpectCommit.StartsWith($running))) {
                        Write-Host "   注意: 稼働中のコミット $running が、リポジトリの $ExpectCommit と違います" -ForegroundColor Yellow
                        return $false
                    }
                } elseif ($ExpectCommit) {
                    Write-Host "   注意: アプリが稼働中のコミットを返さないので、新しい版かどうかは確かめていません" -ForegroundColor Yellow
                }
                return $true
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-Host "   $TimeoutSeconds 秒以内に応答がありませんでした。logs\supervisor.log と logs\spinebox-*.err.log を見てください" -ForegroundColor Red
    return $false
}

Write-Host "SpineBox: $Mode(リポジトリ $RepoPath、タスク $TaskName)$(if ($DryRun) { ' [DryRun]' })"
if ($TaskRunner -and -not (Test-Path -LiteralPath $TaskRunner)) { Stop-Invalid "-TaskRunner が見つかりません: $TaskRunner" }
if (-not $DryRun -and -not $TaskRunner -and -not (Test-Administrator)) {
    Stop-Invalid "管理者権限の PowerShell で実行してください(バッチから起動すると自動で昇格を求めます)"
}
if (-not $DryRun -and -not $TaskRunner -and -not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Stop-Invalid "タスク $TaskName が登録されていません。先に deploy\install_task.ps1 -Action Register で登録してください"
}

try { $healthUrl = Get-HealthUrl } catch { Stop-Invalid "$_" }

$stopTask = {
    if ($TaskRunner) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TaskRunner -Action Stop }
    else { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installTask -Action Stop -TaskName $TaskName }
}
$startTask = {
    if ($TaskRunner) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TaskRunner -Action Start }
    else { Start-ScheduledTask -TaskName $TaskName }
}

$ok = $true
$ok = (Invoke-Step "止める" $stopTask) -and $ok
if (-not $ok) {
    Write-Host "止められなかったので、ここで中断します(何も更新していません)" -ForegroundColor Red
    exit 1
}

if ($Mode -eq "Update") {
    Push-Location $RepoPath
    try {
        $updated = (Invoke-Step "git pull --ff-only" { & git pull --ff-only }) `
            -and (Invoke-Step "Python の依存を入れる" { & $PythonPath @(Get-PythonArgs @("-m", "pip", "install", "-r", "requirements.txt")) }) `
            -and (Invoke-Step "フロントの依存を入れる(npm ci)" { & npm --prefix frontend ci }) `
            -and (Invoke-Step "フロントをビルドする" { & npm --prefix frontend run build })
    } finally { Pop-Location }
    if (-not $updated) {
        Write-Host "更新の途中で失敗しました。起動し直します(git pull が失敗したならコードは前の版のまま)" -ForegroundColor Yellow
        $ok = $false
    }
}

$expect = ""
if ($Mode -eq "Update" -and -not $DryRun) {
    # アプリ(spinebox/buildinfo.py)と同じ 7 文字にそろえる。取れなければ照合しない
    $ErrorActionPreference = "Continue"   # git が無い・リポジトリでないときも起動まで進める
    $expect = "$(& git -C $RepoPath rev-parse --short=7 HEAD 2>$null)".Trim()
    if ($LASTEXITCODE -ne 0) { $expect = "" }
    $ErrorActionPreference = "Stop"
}
$started = Invoke-Step "起動する" $startTask
$healthy = $started -and (Wait-Healthy $healthUrl $expect)
$ok = $ok -and $healthy

if ($ok) {
    Write-Host "完了しました" -ForegroundColor Green
    exit 0
}
Write-Host "問題がありました。上のメッセージと logs\ を確認してください" -ForegroundColor Red
exit 1
