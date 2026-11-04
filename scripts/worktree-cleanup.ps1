<#
.SYNOPSIS
  worktree を安全に削除し、過去に失敗して残った孤立エントリを掃除する（Windows 専用）。

.DESCRIPTION
  ディレクトリに ReadOnly 属性が付いていると、Windows の RemoveDirectory は ACCESS_DENIED を返し、
  `git worktree remove` が Permission denied で失敗する。失敗すると git は管理情報の切り離しにだけ
  成功するので、.git/worktrees/<id> と作業ディレクトリがディスク上に取り残される。しかも残った
  メタデータにも ReadOnly が付いていると、以後 `git worktree prune` も同じ場所で失敗し続ける。

  また、worktree 内に Windows ジャンクション（`frontend/node_modules` 等の ReparsePoint）があると、
  `git worktree remove` はエラーを出さず ExitCode 0 で終了しながらジャンクションと親ディレクトリを
  ディスク上に残す。さらに `git worktree remove` が途中で失敗すると `.git` ファイルだけが先に消え、
  `.git` ファイルの有無だけを見る残骸検出からは見えなくなる。

  -Remove を付けると、ジャンクション（ReparsePoint）をリンク先の中身を辿らずに解除し、ReadOnly を
  外してから `git worktree remove` を実行する。未追跡ファイル等でディレクトリが残った場合も、
  -RemoveDirs を併せて付けておけば後段の残骸掃除でディレクトリごと削除し、孤立メタデータも prune する。
  引数なしで実行すると、既に取り残されている孤立エントリの掃除と残骸ディレクトリの検出を行う。

  このファイルは UTF-8 (BOM 付き) で保存すること。BOM が無いと Windows PowerShell 5.1 が
  CP932 として読み、日本語が化けてパースエラーで 1 行も実行されない。

.PARAMETER Remove
  削除する worktree のパス。ReparsePoint の解除と ReadOnly の解除を行ってから `git worktree remove` を実行する。

.PARAMETER RemoveDirs
  git が把握していない作業ディレクトリの残骸も削除する。未コミットの変更ごと消えるので、
  中身を確認してから（またはマージ完了後の後始末で -Remove と併せて）付けること。

.PARAMETER DryRun
  対象を表示するだけで、実際には何も変更しない。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\worktree-cleanup.ps1 -Remove ..\<repo>-<作業名> -RemoveDirs

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\worktree-cleanup.ps1 -RemoveDirs
#>

param(
  [string]$Remove,
  [switch]$RemoveDirs,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ReadOnly を再帰的に外す。これが付いている限り git 側の rmdir は必ず EACCES になる。
# ReparsePoint（ジャンクション等）の下には潜らない（リンク先を触らないため）。
function Clear-ReadOnlyRecurse {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $stack = New-Object System.Collections.Generic.Stack[System.IO.FileSystemInfo]
  $stack.Push((Get-Item -LiteralPath $Path -Force))
  while ($stack.Count -gt 0) {
    $item = $stack.Pop()
    $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    if ($item.Attributes -band [IO.FileAttributes]::ReadOnly) {
      $item.Attributes = $item.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    }
    if ($item.PSIsContainer -and -not $isReparse) {
      foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
        $stack.Push($child)
      }
    }
  }
}

# ジャンクション・シンボリックリンク(ReparsePoint)をリンク先の中身を辿らずに解除する。
# git worktree remove はジャンクションがあると ExitCode 0 のまま親ディレクトリを残すため、
# 削除前に解除する。また Remove-Item -Recurse がリンク先を巻き込むのも防ぐ。
function Remove-ReparsePointsRecurse {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $stack = New-Object System.Collections.Generic.Stack[System.IO.FileSystemInfo]
  $stack.Push((Get-Item -LiteralPath $Path -Force))
  while ($stack.Count -gt 0) {
    $item = $stack.Pop()
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      if ($item.Attributes -band [IO.FileAttributes]::ReadOnly) {
        $item.Attributes = $item.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
      }
      if ($item.PSIsContainer) {
        [IO.Directory]::Delete($item.FullName)
      } else {
        [IO.File]::Delete($item.FullName)
      }
      continue
    }
    if ($item.PSIsContainer) {
      foreach ($child in Get-ChildItem -LiteralPath $item.FullName -Force) {
        $stack.Push($child)
      }
    }
  }
}

# --- リポジトリの位置を掴む -------------------------------------------------
# worktree の中から実行されてもメインの .git を指すよう --git-common-dir を使う。
$commonDir = & git rev-parse --path-format=absolute --git-common-dir
if ($LASTEXITCODE -ne 0) {
  Write-Error 'git リポジトリの中で実行してください。'
  exit 1
}
$commonDir = ($commonDir | Select-Object -First 1).Trim()
$worktreesDir = Join-Path $commonDir 'worktrees'

# 稼働中の worktree（メインツリーを含む）。孤立判定はここに載っていないことが前提になる。
$live = @()
$mainRoot = $null
foreach ($line in (& git worktree list --porcelain)) {
  if ($line -match '^worktree (.+)$') {
    $resolved = Resolve-Path -LiteralPath $Matches[1] -ErrorAction SilentlyContinue
    if ($resolved) {
      $path = $resolved.Path
      if (-not $mainRoot) { $mainRoot = $path }
      $live += $path
    }
  }
}
if (-not $mainRoot) {
  Write-Error 'メインツリーの位置を特定できません。'
  exit 1
}

$failed = $false

# --- 0. -Remove: ReparsePoint と ReadOnly を外してから git worktree remove ---
if ($Remove) {
  Write-Host "== worktree の削除: $Remove =="
  if (-not (Test-Path -LiteralPath $Remove)) {
    Write-Error "$Remove が見つかりません。"
    exit 1
  }
  $target = (Resolve-Path -LiteralPath $Remove).Path
  if ($target -eq $mainRoot) {
    Write-Error 'メインツリーは削除できません。'
    exit 1
  }
  if ($live -notcontains $target) {
    if (-not $RemoveDirs) {
      Write-Error "$target は稼働中の worktree ではありません（git worktree list に無い）。残骸の削除は -RemoveDirs を付けて実行する。"
      exit 1
    }
    Write-Host "  $target は既に git worktree list から外れているため、下の残骸掃除で削除する。"
  } else {
    # .git は `gitdir: <メタデータのパス>` の 1 行。メタデータ側にも ReadOnly が付く。
    $metaDir = $null
    $dotGitPath = Join-Path $target '.git'
    if (Test-Path -LiteralPath $dotGitPath -PathType Leaf) {
      $dotGit = (Get-Content -LiteralPath $dotGitPath -Raw).Trim()
      if ($dotGit -match '^gitdir:\s*(.+)$') { $metaDir = $Matches[1].Trim() }
    }

    if ($DryRun) {
      Write-Host "  (DryRun) ReparsePoint と ReadOnly を外して git worktree remove する: $target"
    } else {
      Remove-ReparsePointsRecurse -Path $target
      Clear-ReadOnlyRecurse -Path $target
      if ($metaDir) { Clear-ReadOnlyRecurse -Path $metaDir }
      $removeArgs = @('worktree', 'remove')
      if ($RemoveDirs) { $removeArgs += '--force' }
      $removeArgs += $target
      & git @removeArgs
      $live = @($live | Where-Object { $_ -ne $target })
      if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $target)) {
        Write-Warning 'git worktree remove でディレクトリが残った。残骸は下の掃除で拾う。'
        if (-not $RemoveDirs) { $failed = $true }
      } else {
        Write-Host "  削除した: $target"
      }
    }
  }
  Write-Host ''
}

# --- 1. 作業ディレクトリ側の残骸 --------------------------------------------
# 先に残骸ディレクトリを掃除してから .git/worktrees/ の孤立エントリを prune する
# (.git ファイルが残っていた残骸を -RemoveDirs で消したとき、そのメタデータも続けて prune できるようにするため)。
Write-Host '== git が把握していない作業ディレクトリの残骸 =='

# メインツリー直下の項目名（.git を除く）。git worktree remove が途中で失敗して
# .git ファイルだけ先に消えた残骸を、無関係な同名 prefix の手動フォルダと区別するために使う。
$mainEntryNames = @{}
foreach ($item in Get-ChildItem -LiteralPath $mainRoot -Force) {
  if ($item.Name -ne '.git') { $mainEntryNames[$item.Name] = $true }
}

function Test-StaleWorktreeDir {
  param([Parameter(Mandatory = $true)][System.IO.DirectoryInfo]$Dir)
  $dotGit = Join-Path $Dir.FullName '.git'
  # worktree のチェックアウトは .git が「ファイル」
  if (Test-Path -LiteralPath $dotGit -PathType Leaf) { return $true }
  # .git が「ディレクトリ」なら独立した通常のクローンなので絶対に巻き込まない
  if (Test-Path -LiteralPath $dotGit -PathType Container) { return $false }
  # .git が無い場合（削除途中で .git ファイルだけ先に消えた残骸）：
  # 空ディレクトリ、またはメインツリー直下と同じ名前の項目を 1 つ以上含んでいれば残骸と判定する
  $children = @(Get-ChildItem -LiteralPath $Dir.FullName -Force)
  if ($children.Count -eq 0) { return $true }
  foreach ($child in $children) {
    if ($mainEntryNames.ContainsKey($child.Name)) { return $true }
  }
  return $false
}

$prefix = (Split-Path -Leaf $mainRoot) + '-'
$stale = @()
foreach ($dir in Get-ChildItem -LiteralPath (Split-Path -Parent $mainRoot) -Force -Directory) {
  if (-not $dir.Name.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { continue }
  if ($live -contains $dir.FullName) { continue }
  if (-not (Test-StaleWorktreeDir -Dir $dir)) { continue }
  $stale += $dir
}

if ($stale.Count -eq 0) {
  Write-Host '  なし'
} elseif (-not $RemoveDirs) {
  foreach ($dir in $stale) { Write-Host "  $($dir.FullName)" }
  Write-Host '  未コミットの変更が残っている可能性があるため既定では削除しない。'
  Write-Host '  中身を確認して問題なければ -RemoveDirs を付けて再実行する。'
} else {
  foreach ($dir in $stale) {
    if ($DryRun) {
      Write-Host "  (DryRun) 削除する: $($dir.FullName)"
      continue
    }
    try {
      Remove-ReparsePointsRecurse -Path $dir.FullName
      Clear-ReadOnlyRecurse -Path $dir.FullName
      Remove-Item -LiteralPath $dir.FullName -Recurse -Force
    } catch {
      Write-Warning "削除中にエラーが発生した: $($dir.FullName) ($_)"
      $failed = $true
    }
    if (Test-Path -LiteralPath $dir.FullName) {
      Write-Warning "削除後も残ったディレクトリ: $($dir.FullName)"
      $failed = $true
    } else {
      Write-Host "  削除した: $($dir.FullName)"
    }
  }
}

# --- 2. .git/worktrees/ の孤立エントリ --------------------------------------
Write-Host ''
Write-Host '== .git/worktrees/ の孤立エントリ =='
$orphans = @()
if (Test-Path -LiteralPath $worktreesDir) {
  foreach ($entry in Get-ChildItem -LiteralPath $worktreesDir -Force -Directory) {
    $gitdirFile = Join-Path $entry.FullName 'gitdir'
    $target = ''
    if (Test-Path -LiteralPath $gitdirFile) { $target = (Get-Content -LiteralPath $gitdirFile -Raw).Trim() }
    # gitdir が無い / 指す先が無い = git prune が消したがるエントリ。判定条件は git 本体と同じ。
    if (-not $target -or -not (Test-Path -LiteralPath $target)) { $orphans += $entry }
  }
}

if ($orphans.Count -eq 0) {
  Write-Host '  なし'
} else {
  foreach ($entry in $orphans) { Write-Host "  $($entry.Name)" }
  if ($DryRun) {
    Write-Host '  (DryRun) ReadOnly の解除と prune は行わない'
  } else {
    foreach ($entry in $orphans) { Clear-ReadOnlyRecurse -Path $entry.FullName }
    & git worktree prune -v
    $left = @()
    if (Test-Path -LiteralPath $worktreesDir) {
      $left = @(Get-ChildItem -LiteralPath $worktreesDir -Force -Directory |
        Where-Object { $orphans.Name -contains $_.Name })
    }
    if ($left.Count -gt 0) {
      Write-Warning "prune 後も残ったエントリ: $($left.Name -join ', ')"
      $failed = $true
    } else {
      Write-Host "  $($orphans.Count) 件を削除した"
    }
  }
}

if ($failed) { exit 1 }
