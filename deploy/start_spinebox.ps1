<#
  SpineBox の常駐起動スクリプト(再起動ループつき)。タスクスケジューラから呼ぶ想定。

  `python -m spinebox` を起動し、終了したら間隔をあけて起動し直す。ログは LogDir に起動ごとのファイルで残す。
    spinebox-<日時>.out.log / .err.log   アプリの標準出力 / 標準エラー(uvicorn のアクセスログはエラー側に出る)
    supervisor.log                       このスクリプト自身の記録(起動・終了コード・待ち時間)

  再起動の間隔: すぐ落ち続けるときに高負荷にならないよう、2 秒から倍々で最大 60 秒まで延ばす。
  StableSeconds 以上動いてから落ちたときは、間隔を最小に戻す。

  引数が不正(リポジトリ・Python・設定ファイルが見つからない)なときは、再起動ループに入らず exit 2 で終わる。

  **UTF-8 (BOM 付き) で保存すること**(BOM が無いと Windows PowerShell 5.1 が CP932 として読み、日本語が化ける)。
  手順の全体は wiki/operations/server-setup.md。
#>
[CmdletBinding()]
param(
    # SpineBox のリポジトリ(spinebox/ があるフォルダ)。既定はこのスクリプトの 1 つ上
    [string]$RepoPath = "",
    # Python の実行ファイル。py ランチャーなら -PythonPath py -PythonArguments "-3.12"
    [string]$PythonPath = "python",
    # Python の前に付ける引数(空白区切り)
    [string]$PythonArguments = "",
    # 設定ファイル(環境変数 SPINEBOX_CONFIG に入れる)。空なら RepoPath の config.local.toml が使われる
    [string]$ConfigPath = "",
    # ログの置き場。空なら RepoPath\logs
    [string]$LogDir = "",
    [int]$MinDelaySeconds = 2,
    [int]$MaxDelaySeconds = 60,
    [int]$StableSeconds = 60,
    # 起動回数の上限。0 なら無制限(試験用に小さい値を指定する)
    [int]$MaxRuns = 0,
    # 残すログの起動回数
    [int]$KeepLogs = 30
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$SupervisorMaxBytes = 5MB

function Write-SupervisorLog {
    param([string]$LogDirectory, [string]$Message)
    $line = "{0} {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    $path = Join-Path $LogDirectory "supervisor.log"
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Length -gt $SupervisorMaxBytes)) {
        Move-Item -LiteralPath $path -Destination (Join-Path $LogDirectory "supervisor.old.log") -Force
    }
    [System.IO.File]::AppendAllText($path, $line + "`r`n", $Utf8NoBom)
}

function Remove-OldLogs {
    param([string]$LogDirectory, [int]$Keep)
    # 起動ごとに out / err の 2 ファイル。日時順の名前なので、名前の降順で新しい方から Keep 回分を残す
    Get-ChildItem -LiteralPath $LogDirectory -Filter "spinebox-*.log" -File |
        Sort-Object Name -Descending |
        Select-Object -Skip ($Keep * 2) |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

# 戻り値: 最後に起動したプロセスの終了コード(MaxRuns に達したとき)。無制限なら返らない。
function Invoke-SpineBoxLoop {
    param(
        [string]$Repo, [string]$Python, [string[]]$PythonArgList, [string]$LogDirectory,
        [int]$MinDelay, [int]$MaxDelay, [int]$Stable, [int]$Runs, [int]$Keep
    )
    $delay = $MinDelay
    $count = 0
    while ($true) {
        $started = Get-Date
        $stamp = $started.ToString("yyyyMMdd-HHmmss")
        $outLog = Join-Path $LogDirectory ("spinebox-{0}.out.log" -f $stamp)
        $errLog = Join-Path $LogDirectory ("spinebox-{0}.err.log" -f $stamp)
        $argList = @($PythonArgList) + @("-m", "spinebox")
        Write-SupervisorLog $LogDirectory ("起動: {0} {1} (cwd={2})" -f $Python, ($argList -join " "), $Repo)
        $p = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $Repo -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $code = $p.ExitCode
        $ran = [int]((Get-Date) - $started).TotalSeconds
        $count++
        Write-SupervisorLog $LogDirectory ("終了: code={0}, 稼働 {1} 秒 (ログ: {2})" -f $code, $ran, $errLog)
        Remove-OldLogs $LogDirectory $Keep
        if ($Runs -gt 0 -and $count -ge $Runs) { return $code }
        # 安定して動いたあとの終了は最小の間隔で、すぐ落ちたときは前回の倍で再起動する
        if ($ran -ge $Stable) { $delay = $MinDelay }
        Write-SupervisorLog $LogDirectory ("{0} 秒後に再起動します" -f $delay)
        Start-Sleep -Seconds $delay
        if ($ran -lt $Stable) { $delay = [Math]::Min($delay * 2, $MaxDelay) }
    }
}

# 不正な引数は再起動しても直らないので、ループに入らず exit 2 で終わる
function Stop-Invalid {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
    exit 2
}

# --- 引数の検証(不正なら再起動ループに入らない) ---
if (-not $RepoPath) { $RepoPath = Split-Path -Parent $PSScriptRoot }
$givenRepoPath = $RepoPath
$RepoPath = (Resolve-Path -LiteralPath $RepoPath -ErrorAction SilentlyContinue).Path
if (-not $RepoPath -or -not (Test-Path -LiteralPath (Join-Path $RepoPath "spinebox\__main__.py"))) {
    Stop-Invalid "RepoPath に spinebox\__main__.py が見つかりません: $givenRepoPath"
}
if (-not (Get-Command $PythonPath -ErrorAction SilentlyContinue)) {
    Stop-Invalid "Python が見つかりません: $PythonPath"
}
if ($ConfigPath) {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        Stop-Invalid "設定ファイルが見つかりません: $ConfigPath"
    }
    $env:SPINEBOX_CONFIG = (Resolve-Path -LiteralPath $ConfigPath).Path
}
if (-not $LogDir) { $LogDir = Join-Path $RepoPath "logs" }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogDir = (Resolve-Path -LiteralPath $LogDir).Path

# ログを UTF-8 にする(既定だとコンソールのコードページ = CP932 で書かれ、ログを開いたときに化ける)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$pythonArgList = @($PythonArguments -split "\s+" | Where-Object { $_ })
$exitCode = Invoke-SpineBoxLoop -Repo $RepoPath -Python $PythonPath -PythonArgList $pythonArgList -LogDirectory $LogDir `
    -MinDelay $MinDelaySeconds -MaxDelay $MaxDelaySeconds -Stable $StableSeconds -Runs $MaxRuns -Keep $KeepLogs
exit $exitCode
