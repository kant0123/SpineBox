<#
  SpineBox の待ち受けポートを、LAN からだけ受け付ける Windows ファイアウォールの受信規則を追加・削除・確認する。

    .\firewall_rule.ps1 -Action Add -Port 8000
    .\firewall_rule.ps1 -Action Status
    .\firewall_rule.ps1 -Action Remove

  規則の中身: 受信・TCP・指定ポート・**プライベートプロファイルのみ**・接続元は同じサブネット(LocalSubnet)だけ。
  パブリック / ドメインのプロファイルでは開かない。ネットワークの種類がパブリックになっていると規則が効かない
  (Status が現在のネットワークの種類を表示する)。ルーターのポート開放はしない(インターネットへ公開しない)。

  Add / Remove は管理者権限の PowerShell が要る(システムの設定を変更するため)。Status は不要。
  `-WhatIf` を付けると、追加する内容を表示するだけで何も変更しない。
  ポートは設定ファイルの port と合わせる。

  **UTF-8 (BOM 付き) で保存すること**(BOM が無いと Windows PowerShell 5.1 が CP932 として読み、日本語が化ける)。
  手順の全体は wiki/operations/server-setup.md。
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Add", "Remove", "Status")]
    [string]$Action,
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$RuleName = "SpineBox (TCP)",
    # 接続元の制限。LocalSubnet 以外にするなら、LAN のアドレス範囲(例 192.168.0.0/24)を指定する
    [string]$RemoteAddress = "LocalSubnet"
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Show-Status {
    $rules = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)
    if ($rules.Count -eq 0) { Write-Host "規則 '$RuleName' はありません" }
    foreach ($r in $rules) {
        $port = $r | Get-NetFirewallPortFilter
        $addr = $r | Get-NetFirewallAddressFilter
        Write-Host ("規則 '{0}': 有効={1}, 方向={2}, 動作={3}, プロファイル={4}, プロトコル={5}, ポート={6}, 接続元={7}" -f `
            $r.DisplayName, $r.Enabled, $r.Direction, $r.Action, $r.Profile, $port.Protocol, $port.LocalPort, ($addr.RemoteAddress -join ","))
    }
    foreach ($n in @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
        Write-Host ("ネットワーク '{0}' ({1}) の種類: {2}" -f $n.Name, $n.InterfaceAlias, $n.NetworkCategory)
        if ($n.NetworkCategory -ne "Private") {
            Write-Host "  注意: Private ではないので、この規則はこのネットワークでは効きません"
        }
    }
}

switch ($Action) {
    "Add" {
        Write-Host "追加する規則: '$RuleName' 受信 TCP $Port、プロファイル Private のみ、接続元 $RemoteAddress"
        if (@(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue).Count -gt 0) {
            throw "規則 '$RuleName' は既にあります。作り直すなら先に -Action Remove を実行してください"
        }
        if (-not $PSCmdlet.ShouldProcess($RuleName, "ファイアウォールの受信規則を追加")) { return }
        if (-not (Test-Administrator)) { throw "管理者権限の PowerShell で実行してください" }
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
            -Profile Private -RemoteAddress $RemoteAddress -Description "SpineBox (LAN 内のみ。インターネットへ公開しない)" | Out-Null
        Write-Host "追加しました。-Action Status でネットワークの種類が Private か確認してください"
    }
    "Remove" {
        if (@(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue).Count -eq 0) {
            Write-Host "規則 '$RuleName' はありません"
            return
        }
        if (-not $PSCmdlet.ShouldProcess($RuleName, "ファイアウォールの受信規則を削除")) { return }
        if (-not (Test-Administrator)) { throw "管理者権限の PowerShell で実行してください" }
        Remove-NetFirewallRule -DisplayName $RuleName
        Write-Host "削除しました: $RuleName"
    }
    "Status" { Show-Status }
}
