<#
网络连通性检查脚本
输出：docs/M0-network-check.json
#>
$timestamp = Get-Date -Format o

# 解析仓库根目录
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$outFile = Join-Path $repoRoot 'docs\M0-network-check.json'

$targets = @('google.com','github.com','pypi.org','registry-1.docker.io')
$ports = @(80,443)

$results = @()

function Test-TcpFallback {
    param($host, $port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect($host, $port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne(3000)
        if ($success) { $tcp.EndConnect($iar); $tcp.Close(); return $true } else { return $false }
    } catch { return $false }
}

foreach ($t in $targets) {
    $entry = [ordered]@{ target = $t; timestamp = $timestamp; dns = @(); checks = @(); errors = @() }
    try {
        $dns = Resolve-DnsName -Name $t -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -ExpandProperty IPAddress -ErrorAction SilentlyContinue
        if ($dns) { $entry.dns = $dns }
    } catch {
        $entry.errors += "DNS: $($_.Exception.Message)"
    }

    foreach ($p in $ports) {
        $check = [ordered]@{ port = $p; testedWith = $null; tcp = $false; ping = $null; remoteAddress = $null }
        if (Get-Command Test-NetConnection -ErrorAction SilentlyContinue) {
            try {
                $r = Test-NetConnection -ComputerName $t -Port $p -InformationLevel Detailed -WarningAction SilentlyContinue
                $check.testedWith = 'Test-NetConnection'
                $check.tcp = [bool]$r.TcpTestSucceeded
                $check.ping = if ($r.PingSucceeded) { $r.PingReplyDetails.RoundtripTime } else { $null }
                $check.remoteAddress = $r.RemoteAddress
            } catch {
                $entry.errors += "Test-NetConnection error on $($t):$($p) - $($_.Exception.Message)"
            }
        } else {
            $check.testedWith = 'TcpClient'
            try {
                $ok = Test-TcpFallback -host $t -port $p
                $check.tcp = [bool]$ok
            } catch {
                $entry.errors += "TcpClient error on $($t):$($p) - $($_.Exception.Message)"
            }
        }
        $entry.checks += $check
    }

    $results += $entry
}

$output = [ordered]@{
    metadata = [ordered]@{ host = $env:COMPUTERNAME; generated = $timestamp }
    results = $results
}

try {
    $json = $output | ConvertTo-Json -Depth 6
    $dir = Split-Path $outFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $json | Out-File -FilePath $outFile -Encoding utf8
    Write-Output "Network check complete. Results written to $outFile"
} catch {
    Write-Output "Failed to write network check results: $($_.Exception.Message)"
}
