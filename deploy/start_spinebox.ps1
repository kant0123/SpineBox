<#
  SpineBox の常駐起動スクリプト(再起動ループつき)。タスクスケジューラから呼ぶ想定。

  `python -m spinebox` を起動し、終了したら間隔をあけて起動し直す。ログは LogDir に起動ごとのファイルで残す。
    spinebox-<日時>.out.log / .err.log   アプリの標準出力 / 標準エラー(uvicorn のアクセスログはエラー側に出る)
    supervisor.log                       このスクリプト自身の記録(起動・終了コード・待ち時間)

  再起動の間隔: すぐ落ち続けるときに高負荷にならないよう、2 秒から倍々で最大 60 秒まで延ばす。
  StableSeconds 以上動いてから落ちたときは、間隔を最小に戻す。

  引数が不正(リポジトリ・Python・設定ファイルが見つからない)なときは、再起動ループに入らず exit 2 で終わる。

  子プロセスの後始末(python を孤児として残さない):
    - このスクリプト自身を Job Object(ハンドルが閉じたら中のプロセスを全部終了する設定)に入れる。python は子として同じ Job に
      入るので、このスクリプトを強制終了しても、タスクスケジューラが止めても python は一緒に終わる。
      Job を作れないときは警告をログに出して続ける
    - 起動の前に、設定のポートを握っているプロセスを調べる。`-m spinebox` のプロセスで親が既に居ない(前回の孤児)なら
      止めてログに残す。親が生きている(手で起動した)プロセスや別のプログラムは止めず、ログに残して起動を試みる
      (bind に失敗して終了し、通常の再起動の間隔で待つ)。-NoReapOrphan で、止めずに記録だけにできる

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
    [int]$KeepLogs = 30,
    # 前回の孤児がポートを握っていても止めず、ログに残すだけにする
    [switch]$NoReapOrphan
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

# このプロセスを「ハンドルが閉じたら中のプロセスを全部終了する」Job Object に入れる。
# 戻り値: 成功なら空文字、失敗ならその理由。
# 子(python)は起動時に同じ Job へ入るので、このスクリプトが強制終了されても python が孤児として残らない。
# Job のハンドルは閉じずに持ち続ける(閉じるのはプロセス終了時だけ、というのが狙い)。
function Enable-KillChildrenOnExit {
    try {
        if (-not ([System.Management.Automation.PSTypeName]'SpineBoxJob').Type) {
            Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class SpineBoxJob
{
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimit
    {
        public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong A, B, C, D, E, F; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimit
    {
        public BasicLimit Basic; public IoCounters Io;
        public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

    static IntPtr job = IntPtr.Zero;

    // 成功なら空文字、失敗ならその理由を返す
    public static string KillChildrenOnExit(IntPtr process)
    {
        if (job != IntPtr.Zero) return "";
        IntPtr h = CreateJobObject(IntPtr.Zero, null);
        if (h == IntPtr.Zero) return "CreateJobObject: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
        ExtendedLimit info = new ExtendedLimit();
        info.Basic.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        int size = Marshal.SizeOf(typeof(ExtendedLimit));
        IntPtr buf = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(info, buf, false);
            if (!SetInformationJobObject(h, 9, buf, (uint)size)) // JobObjectExtendedLimitInformation
                return "SetInformationJobObject: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
        }
        finally { Marshal.FreeHGlobal(buf); }
        if (!AssignProcessToJobObject(h, process))
            return "AssignProcessToJobObject: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
        job = h;
        return "";
    }
}
"@
        }
        return [SpineBoxJob]::KillChildrenOnExit([System.Diagnostics.Process]::GetCurrentProcess().Handle)
    }
    catch {
        return $_.Exception.Message
    }
}

function Get-ProcessInfo {
    param([int]$ProcessId)
    return Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction SilentlyContinue
}

# 指定プロセスとその子孫を、子から先に終了する
function Stop-ProcessTree {
    param([int]$RootId)
    $root = Get-ProcessInfo $RootId
    if ($root) {
        foreach ($child in @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = {0}" -f $RootId) -ErrorAction SilentlyContinue)) {
            # PID は再利用される。親より新しく始まったものだけを子として辿る
            if ($child.ProcessId -ne $RootId -and $child.CreationDate -ge $root.CreationDate) {
                Stop-ProcessTree $child.ProcessId
            }
        }
    }
    Stop-Process -Id $RootId -Force -ErrorAction SilentlyContinue
}

# 設定の host / port を python に尋ねる(TOML を PowerShell で読み直さない)。戻り値: ポート番号、分からなければ 0
function Get-ConfiguredPort {
    param([string]$Repo, [string]$Python, [string[]]$PythonArgList)
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("spinebox-bind-{0}.txt" -f [guid]::NewGuid().ToString("N"))
    $tmpErr = $tmp + ".err"
    try {
        $argList = @($PythonArgList) + @("-m", "spinebox", "--print-bind")
        $p = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $Repo -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $tmp -RedirectStandardError $tmpErr
        if ($p.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $tmp)) { return 0 }
        $text = ([System.IO.File]::ReadAllText($tmp)).Trim()
        if ($text -match "^\S+\s+(\d+)$") { return [int]$Matches[1] }
        return 0
    }
    catch { return 0 }
    finally { Remove-Item -LiteralPath $tmp, $tmpErr -Force -ErrorAction SilentlyContinue }
}

# ポートを待ち受けているプロセス(Win32_Process)を返す。呼び出し側は @() で受ける(0 件のとき何も返らない)
function Get-PortHolders {
    param([int]$Port)
    $ids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { $_.OwningProcess } | Sort-Object -Unique)
    return @($ids | ForEach-Object { Get-ProcessInfo $_ } | Where-Object { $_ })
}

# `-m spinebox` のプロセスが孤児(祖先をたどって親が居ない)なら、その一番上のプロセス(Win32_Process)を返す。
# 親が生きている別のプロセス(手で起動した・別のプログラム)なら $null。
# `py -3.12 -m spinebox` だと py.exe → python.exe と並ぶので、`-m spinebox` の親は同じ仲間として辿る。
function Find-OrphanRoot {
    param($Holder)
    if (-not $Holder.CommandLine -or $Holder.CommandLine -notmatch "-m\s+spinebox") { return $null }
    $top = $Holder
    while ($true) {
        $parent = Get-ProcessInfo $top.ParentProcessId
        # PID は再利用される。親が子より後に始まっているなら、本物の親ではない(= 親は既に居ない)
        if (-not $parent -or $parent.CreationDate -gt $top.CreationDate) { return $top }
        if ($parent.CommandLine -and $parent.CommandLine -match "-m\s+spinebox") { $top = $parent; continue }
        return $null
    }
}

# 起動の前に、前回の孤児がポートを握っていないか調べる。例外は投げず、結果をログに残すだけにする
function Resolve-PortConflict {
    param([string]$Repo, [string]$Python, [string[]]$PythonArgList, [string]$LogDirectory, [bool]$Reap)
    $port = Get-ConfiguredPort $Repo $Python $PythonArgList
    if ($port -le 0) {
        Write-SupervisorLog $LogDirectory "ポートの事前確認を省略します(設定のポートを取得できませんでした。設定エラーなら起動時に分かります)"
        return
    }
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        Write-SupervisorLog $LogDirectory "ポートの事前確認を省略します(Get-NetTCPConnection が使えません)"
        return
    }
    $holders = @(Get-PortHolders $port)
    $reaped = $false
    foreach ($h in $holders) {
        $root = Find-OrphanRoot $h
        if (-not $root) {
            Write-SupervisorLog $LogDirectory ("警告: ポート {0} は別のプロセスが使用中です(孤児ではないので止めません): PID={1} {2}" -f $port, $h.ProcessId, $h.CommandLine)
        }
        elseif (-not $Reap) {
            Write-SupervisorLog $LogDirectory ("警告: ポート {0} を前回の孤児が握っています(-NoReapOrphan のため止めません): PID={1} {2}" -f $port, $root.ProcessId, $root.CommandLine)
        }
        else {
            Write-SupervisorLog $LogDirectory ("警告: ポート {0} を前回の孤児が握っているため止めます: PID={1} {2}" -f $port, $root.ProcessId, $root.CommandLine)
            Stop-ProcessTree $root.ProcessId
            $reaped = $true
        }
    }
    if ($reaped) {
        # 終了してポートが解放されるのを少し待つ
        for ($i = 0; $i -lt 10; $i++) {
            if (@(Get-PortHolders $port).Count -eq 0) { break }
            Start-Sleep -Milliseconds 500
        }
    }
}

# 戻り値: 最後に起動したプロセスの終了コード(MaxRuns に達したとき)。無制限なら返らない。
function Invoke-SpineBoxLoop {
    param(
        [string]$Repo, [string]$Python, [string[]]$PythonArgList, [string]$LogDirectory,
        [int]$MinDelay, [int]$MaxDelay, [int]$Stable, [int]$Runs, [int]$Keep, [bool]$Reap
    )
    $delay = $MinDelay
    $count = 0
    while ($true) {
        $started = Get-Date
        $stamp = $started.ToString("yyyyMMdd-HHmmss")
        $outLog = Join-Path $LogDirectory ("spinebox-{0}.out.log" -f $stamp)
        $errLog = Join-Path $LogDirectory ("spinebox-{0}.err.log" -f $stamp)
        Resolve-PortConflict -Repo $Repo -Python $Python -PythonArgList $PythonArgList -LogDirectory $LogDirectory -Reap $Reap
        $argList = @($PythonArgList) + @("-m", "spinebox")
        Write-SupervisorLog $LogDirectory ("起動: {0} {1} (cwd={2})" -f $Python, ($argList -join " "), $Repo)
        $p = Start-Process -FilePath $Python -ArgumentList $argList -WorkingDirectory $Repo -NoNewWindow -PassThru `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $null = $p.Handle   # 終了コードを読めるよう、ハンドルを先に掴んでおく
        try { $p.WaitForExit() }
        finally {
            # Ctrl+C などで待ちを中断されたときに python を残さない(強制終了のときは Job Object が止める)
            if (-not $p.HasExited) { Stop-ProcessTree $p.Id }
        }
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

# python が孤児として残らないよう、このプロセスを Job Object に入れる(以降に起動する子は同じ Job に入る)
$jobError = Enable-KillChildrenOnExit
if ($jobError) {
    Write-SupervisorLog $LogDir ("警告: 子プロセスの一括終了(Job Object)を有効にできませんでした: {0}。このスクリプトを強制終了すると python が孤児として残ります" -f $jobError)
}
else {
    Write-SupervisorLog $LogDir "子プロセスの一括終了(Job Object)を有効にしました"
}

$pythonArgList = @($PythonArguments -split "\s+" | Where-Object { $_ })
$exitCode = Invoke-SpineBoxLoop -Repo $RepoPath -Python $PythonPath -PythonArgList $pythonArgList -LogDirectory $LogDir `
    -MinDelay $MinDelaySeconds -MaxDelay $MaxDelaySeconds -Stable $StableSeconds -Runs $MaxRuns -Keep $KeepLogs `
    -Reap (-not $NoReapOrphan)
exit $exitCode
