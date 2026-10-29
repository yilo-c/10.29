<#
Windows 防火墙与监听端口检查脚本
输出：docs/M0-firewall-check.json
#>
$timestamp = Get-Date -Format o

# 仓库根目录
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$outFile = Join-Path $repoRoot 'docs\M0-firewall-check.json'

$result = [ordered]@{
    metadata = [ordered]@{ host = $env:COMPUTERNAME; generated = $timestamp }
    profiles = @()
    rules = @()
    listening = @()
    notes = @()
}

try {
    $profiles = Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction,AllowInboundRules,AllowLocalFirewallRules
    $result.profiles = $profiles
} catch {
    $result.notes += "Get-NetFirewallProfile failed: $($_.Exception.Message)"
}

try {
    $rules = Get-NetFirewallRule -PolicyStore ActiveStore -ErrorAction Stop | Select-Object Name,DisplayName,Direction,Action,Enabled,Profile,Platform,Group,Description
    $result.rules = $rules
} catch {
    $result.notes += "Get-NetFirewallRule failed: $($_.Exception.Message)"
}

try {
    $tcp = Get-NetTCPConnection -State Listen -ErrorAction Stop | Select-Object LocalAddress,LocalPort,OwningProcess
    $list = @()
    foreach ($c in $tcp) {
        $procName = $null
        try { $p = Get-Process -Id $c.OwningProcess -ErrorAction Stop; $procName = $p.ProcessName } catch { $procName = $null }
        $list += [ordered]@{ localAddress = $c.LocalAddress; localPort = $c.LocalPort; pid = $c.OwningProcess; process = $procName }
    }
    $result.listening = $list
} catch {
    $result.notes += "Get-NetTCPConnection failed: $($_.Exception.Message)"
}

try {
    $json = $result | ConvertTo-Json -Depth 6
    $dir = Split-Path $outFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $json | Out-File -FilePath $outFile -Encoding utf8
    Write-Output "Firewall check complete. Results written to $outFile"
} catch {
    Write-Output "Failed to write firewall check results: $($_.Exception.Message)"
}
