<#
.SYNOPSIS
  worktree を安全に削除し、過去に失敗して残った孤立エントリと残骸を掃除する（Windows 専用）。

.DESCRIPTION
  素の `git worktree remove` が Windows で worktree を消し切れない典型は 2 つある。

  1. ディレクトリに ReadOnly 属性が付いている。RemoveDirectory が ACCESS_DENIED を返し、
     `git worktree remove` が Permission denied で失敗する。git は管理情報の切り離しにだけ成功するので、
     .git/worktrees/<id> と作業ディレクトリが取り残され、以後 `git worktree prune` も同じ場所で失敗し続ける。
     ReadOnly を付けてくる典型はクラウドストレージのミラー同期（Google ドライブ / OneDrive 等）や
     バックアップツール。
  2. worktree の中にジャンクション / シンボリックリンク（ReparsePoint。共有の node_modules 等）がある。
     `git worktree remove` は追跡ファイル・.git ファイル・管理情報だけを消して **exit 0 で終わり**、
     リンクと親ディレクトリが残る。成功したように見えるので気付きにくい。

  -Remove <path> は、リンクを外し ReadOnly を外してから `git worktree remove` を実行し、
  それでも残ったディレクトリを消す（= 1 回で消し切る）。後始末の標準手順はこれ。
  引数なしで実行すると、既に取り残されている孤立エントリの掃除と残骸の列挙だけを行う。

  ReparsePoint はリンク先を辿らずにリンクそのものだけを外す（[IO.Directory]::Delete /
  [IO.File]::Delete）。走査もリンクの下には潜らない。リンク先はメインツリー側の共有ディレクトリ
  であることが多く、巻き込むと他の worktree まで壊すため。

  このファイルは UTF-8 (BOM 付き) で保存すること。BOM が無いと Windows PowerShell 5.1 が
  CP932 として読み、日本語が化けてパースエラーで 1 行も実行されない。

.PARAMETER Remove
  削除する worktree のパス。稼働中なら上記の下ごしらえをして `git worktree remove` を実行し、
  残ったディレクトリも消す。既に git の管理から外れた残骸（下の -RemoveDirs と同じ判定）を
  指したときは、その 1 件だけを消す。他の残骸には触らない。

.PARAMETER RemoveDirs
  git が把握していない作業ディレクトリの残骸を、-Remove の対象以外も含めて**すべて**削除する。
  他セッションの残骸の未コミットの変更ごと消えるので、引数なしで列挙して中身を確認してから付けること。

.PARAMETER DryRun
  対象を表示するだけで、実際には何も変更しない。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\worktree-cleanup.ps1 -Remove ..\<repo>-<作業名>

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\worktree-cleanup.ps1 -RemoveDirs
#>

param(
  [string]$Remove,
  [switch]$RemoveDirs,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# git の出力を受け取る。git は UTF-8 で書くが、Windows PowerShell 5.1 はコンソールのコードページ
# (日本語環境なら CP932)で読むので、非 ASCII のパスが化けて 1 件も処理できなくなる。
# [Console]::OutputEncoding はコンソールを共有する親シェルにも残るので、呼ぶたびに戻す。
function Get-GitOutput {
  $saved = [Console]::OutputEncoding
  try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
    return @(& git @args)
  } finally {
    [Console]::OutputEncoding = $saved
  }
}

function Test-ReparsePoint {
  param([Parameter(Mandatory = $true)]$Item)
  return [bool]($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

# $Path 配下の項目を列挙する。ReparsePoint 自体は返すが、その下には潜らない。
# Get-ChildItem -Recurse の振る舞いは PowerShell のバージョンで違うので自前で辿る。
function Get-ItemsNoFollow {
  param([Parameter(Mandatory = $true)][string]$Path)
  $result = New-Object System.Collections.Generic.List[object]
  $stack = New-Object System.Collections.Generic.Stack[string]
  $stack.Push($Path)
  while ($stack.Count -gt 0) {
    foreach ($child in @(Get-ChildItem -LiteralPath $stack.Pop() -Force)) {
      $result.Add($child)
      if ($child.PSIsContainer -and -not (Test-ReparsePoint $child)) { $stack.Push($child.FullName) }
    }
  }
  return $result
}

# 配下の ReparsePoint を、リンク先の中身に触れずにリンクだけ外す。
function Remove-ReparsePoints {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $links = @(Get-ItemsNoFollow -Path $Path | Where-Object { Test-ReparsePoint $_ })
  foreach ($link in $links) {
    # リンク自身に ReadOnly が付いていると外せない。
    $link.Attributes = $link.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    if ($link.PSIsContainer) {
      [IO.Directory]::Delete($link.FullName)   # 非再帰。ジャンクション / ディレクトリリンクだけが消える
    } else {
      [IO.File]::Delete($link.FullName)
    }
    Write-Host "  リンクを外した: $($link.FullName)"
  }
}

# ReadOnly を再帰的に外す（リンクの下には潜らない）。これが付いている限り rmdir は必ず EACCES になる。
function Clear-ReadOnlyRecurse {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $root = Get-Item -LiteralPath $Path -Force
  $items = @($root)
  if (-not (Test-ReparsePoint $root)) { $items += @(Get-ItemsNoFollow -Path $Path) }
  foreach ($item in $items) {
    if ($item.Attributes -band [IO.FileAttributes]::ReadOnly) {
      $item.Attributes = $item.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    }
  }
}

# 残骸ディレクトリを消す。リンクを先に外すので Remove-Item がリンク先に触れることはない。
function Remove-ResidueDir {
  param([Parameter(Mandatory = $true)][string]$Path)
  Remove-ReparsePoints -Path $Path
  Clear-ReadOnlyRecurse -Path $Path
  Remove-Item -LiteralPath $Path -Recurse -Force
}

# --- リポジトリの位置を掴む -------------------------------------------------
# worktree の中から実行されてもメインの .git を指すよう --git-common-dir を使う。
$commonDir = Get-GitOutput rev-parse --path-format=absolute --git-common-dir
if ($LASTEXITCODE -ne 0) {
  Write-Error 'git リポジトリの中で実行してください。'
  exit 1
}
$commonDir = ($commonDir | Select-Object -First 1).Trim()
$worktreesDir = Join-Path $commonDir 'worktrees'

# パスを比較できる形にそろえる。ネットワークドライブ（G: → \\host\share）上のリポジトリでは、
# git は UNC で、引数やカレントディレクトリはドライブ文字で表すので、そのままでは一致しない。
# ディスクに無いもの（prunable）は Resolve-Path できないので、文字列のまま正規化する。
function Get-CanonicalPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (Test-Path -LiteralPath $Path) {
    $full = (Resolve-Path -LiteralPath $Path).ProviderPath   # .Path は UNC にプロバイダ名が前置される
  } else {
    $full = [IO.Path]::GetFullPath($Path)
  }
  # git は UNC を //host/share と書き、Resolve-Path はそれを区切り文字ごと保つ。\\host\share にそろえる。
  $full = [IO.Path]::GetFullPath($full)
  if ($full -match '^([A-Za-z]):(.*)$') {
    $rest = $Matches[2]
    $drive = Get-PSDrive -Name $Matches[1] -PSProvider FileSystem -ErrorAction SilentlyContinue
    if ($drive -and $drive.DisplayRoot -and $drive.DisplayRoot.StartsWith('\\')) {
      $full = $drive.DisplayRoot.TrimEnd('\') + $rest
    }
  }
  return $full
}

# 稼働中の worktree（メインツリーを含む）。孤立・残骸の判定はここに載っていないことが前提になる。
$live = @()
$mainRoot = $null
foreach ($line in (Get-GitOutput worktree list --porcelain)) {
  if ($line -match '^worktree (.+)$') {
    $p = Get-CanonicalPath $Matches[1]
    if (-not $mainRoot) { $mainRoot = $p }
    $live += $p
  }
}
$siblingsDir = Split-Path -Parent $mainRoot
$prefix = (Split-Path -Leaf $mainRoot) + '-'
$mainNames = @(Get-ChildItem -LiteralPath $mainRoot -Force | ForEach-Object { $_.Name } | Where-Object { $_ -ne '.git' })

# git が把握していない worktree の残骸か。
#   - メインツリーの兄弟で、名前が `<repo>-` で始まり、git worktree list に無い
#   - .git がディレクトリなら独立したクローンなので対象外
#   - .git がファイル（worktree のチェックアウト）なら残骸
#   - .git が既に無い（git worktree remove が途中まで消した）なら、リンクを除いて空か、
#     メインツリー直下と同じ名前の項目を 1 つ以上持つものだけを残骸とみなす（無関係な兄弟を巻き込まない）
function Test-StaleWorktreeDir {
  param([Parameter(Mandatory = $true)][IO.DirectoryInfo]$Dir)
  $full = Get-CanonicalPath $Dir.FullName
  if ((Split-Path -Parent $full) -ne $siblingsDir) { return $false }
  if (-not $Dir.Name.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { return $false }
  if ($live -contains $full) { return $false }
  $dotGit = Join-Path $Dir.FullName '.git'
  if (Test-Path -LiteralPath $dotGit -PathType Container) { return $false }
  if (Test-Path -LiteralPath $dotGit -PathType Leaf) { return $true }
  $children = @(Get-ChildItem -LiteralPath $Dir.FullName -Force)
  if (@($children | Where-Object { -not (Test-ReparsePoint $_) }).Count -eq 0) { return $true }
  return @($children | Where-Object { $mainNames -contains $_.Name }).Count -gt 0
}

$failed = $false

# --- 0. -Remove: 下ごしらえ → git worktree remove → 残ったディレクトリの削除 ----
if ($Remove) {
  Write-Host "== worktree の削除: $Remove =="
  if (-not (Test-Path -LiteralPath $Remove)) {
    Write-Error "$Remove が見つかりません。"
    exit 1
  }
  $target = Get-CanonicalPath $Remove
  if ($target -eq $mainRoot) {
    Write-Error 'メインツリーは削除できません。'
    exit 1
  }

  if ($live -contains $target) {
    # .git は `gitdir: <メタデータのパス>` の 1 行。メタデータ側にも ReadOnly が付く。
    $metaDir = $null
    $dotGitFile = Join-Path $target '.git'
    if (Test-Path -LiteralPath $dotGitFile -PathType Leaf) {
      $dotGit = (Get-Content -LiteralPath $dotGitFile -Raw -Encoding UTF8).Trim()
      if ($dotGit -match '^gitdir:\s*(.+)$') { $metaDir = $Matches[1].Trim() }
    }

    # 未追跡・変更ありのファイルがあると git worktree remove は断る。断られる worktree のリンクを
    # 先に外すと稼働中の worktree を壊すだけなので、何も触らずに止める。
    $dirty = @(Get-GitOutput -C $target status --porcelain)
    $statusOk = ($LASTEXITCODE -eq 0)
    if (-not $statusOk -or $dirty.Count -gt 0) {
      if ($statusOk) { $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "  $_" } }
      Write-Warning "未コミットの変更か未追跡ファイルがあるため削除しない（git status を確認する）: $target"
      $failed = $true
    } elseif ($DryRun) {
      Write-Host "  (DryRun) リンクと ReadOnly を外して git worktree remove し、残ったディレクトリを消す: $target"
    } else {
      Remove-ReparsePoints -Path $target
      Clear-ReadOnlyRecurse -Path $target
      if ($metaDir) { Clear-ReadOnlyRecurse -Path $metaDir }
      & git worktree remove $target
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "git worktree remove が失敗した: $target"
        $failed = $true
      } else {
        $live = @($live | Where-Object { $_ -ne $target })
        if (Test-Path -LiteralPath $target) {
          # git は exit 0 でも、消し切れなかった項目とディレクトリを黙って残すことがある。
          Write-Host '  git worktree remove の後もディレクトリが残ったので消す'
          Remove-ResidueDir -Path $target
        }
        Write-Host "  削除した: $target"
      }
    }
  } elseif (Test-StaleWorktreeDir -Dir (Get-Item -LiteralPath $target -Force)) {
    # 既に git の管理から外れた残骸。明示的に指定された 1 件だけを消す。
    if ($DryRun) {
      Write-Host "  (DryRun) git の管理外の残骸として削除する: $target"
    } else {
      Remove-ResidueDir -Path $target
      Write-Host "  git の管理外の残骸を削除した: $target"
    }
  } else {
    Write-Error "$target は稼働中の worktree でも、worktree の残骸でもありません。"
    exit 1
  }
  Write-Host ''
}

# --- 1. .git/worktrees/ の孤立エントリ --------------------------------------
Write-Host '== .git/worktrees/ の孤立エントリ =='
$orphans = @()
if (Test-Path -LiteralPath $worktreesDir) {
  foreach ($entry in Get-ChildItem -LiteralPath $worktreesDir -Force -Directory) {
    $gitdirFile = Join-Path $entry.FullName 'gitdir'
    $t = ''
    if (Test-Path -LiteralPath $gitdirFile) { $t = (Get-Content -LiteralPath $gitdirFile -Raw -Encoding UTF8).Trim() }
    # gitdir が無い / 指す先が無い = git prune が消したがるエントリ。判定条件は git 本体と同じ。
    if (-not $t -or -not (Test-Path -LiteralPath $t)) { $orphans += $entry }
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

# --- 2. 作業ディレクトリ側の残骸 --------------------------------------------
Write-Host ''
Write-Host '== git が把握していない作業ディレクトリの残骸 =='
$stale = @(Get-ChildItem -LiteralPath $siblingsDir -Force -Directory |
  Where-Object { Test-StaleWorktreeDir -Dir $_ })

if ($stale.Count -eq 0) {
  Write-Host '  なし'
} elseif (-not $RemoveDirs) {
  foreach ($dir in $stale) { Write-Host "  $($dir.FullName)" }
  Write-Host '  未コミットの変更が残っている可能性があるため既定では削除しない。'
  Write-Host '  1 件だけ消すなら -Remove <パス>、中身を確認してすべて消すなら -RemoveDirs を付けて再実行する。'
} else {
  foreach ($dir in $stale) {
    if ($DryRun) {
      Write-Host "  (DryRun) 削除する: $($dir.FullName)"
      continue
    }
    Remove-ResidueDir -Path $dir.FullName
    Write-Host "  削除した: $($dir.FullName)"
  }
}

if ($failed) { exit 1 }
