<#
Service health check (non-invasive)
Writes: docs/M0-service-health.json

Checks:
- Docker daemon availability
- Python process presence (looks for repo scripts)
- Local listening ports (common set + from firewall_check)
- Test local loopback TCP connect on expected ports
#>

$timestamp = Get-Date -Format o
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$outFile = Join-Path $repoRoot 'docs\M0-service-health.json'

$result = [ordered]@{
    metadata = [ordered]@{ host = $env:COMPUTERNAME; generated = $timestamp }
    docker = $null
    pythonApps = @()
    listening = @()
    portChecks = @()
    notes = @()
}

# Check Docker availability
try {
    $dockerInfo = & docker info --format '{{json .}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $dockerInfo) {
        $result.docker = @{ available = $true; info = $dockerInfo }
    } else {
        $result.docker = @{ available = $false; info = $dockerInfo }
    }
} catch {
    $result.docker = @{ available = $false; error = $_.Exception.Message }
}

# Detect python processes that reference the repo
try {
    $pyProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*思忆集*' -or $_.CommandLine -like '*test1*') }
    foreach ($p in $pyProcs) {
        $result.pythonApps += [ordered]@{ pid = $p.ProcessId; cmd = $p.CommandLine }
    }
} catch {
    $result.notes += "python process scan failed: $($_.Exception.Message)"
}

# Gather listening ports (from firewall_check if exists)
$fwFile = Join-Path $repoRoot 'docs\M0-firewall-check.json'
if (Test-Path $fwFile) {
    try {
        $fw = Get-Content $fwFile -Raw | ConvertFrom-Json
        if ($fw.listening) {
            foreach ($l in $fw.listening) { $result.listening += $l }
        }
    } catch { $result.notes += "Failed to read firewall_check: $($_.Exception.Message)" }
}

# Common ports to check
$ports = @(80,443,8000,8080,5432,6379)
foreach ($p in $ports) { $result.portChecks += [ordered]@{ port = $p; localTcp = $false; testLoopback = $null } }

# Include ports from listening entries
foreach ($l in $result.listening) {
    if ($l.localPort) {
        $found = $false
        for ($j=0; $j -lt $result.portChecks.Count; $j++) {
            if ($result.portChecks[$j].port -eq $l.localPort) { $result.portChecks[$j].localTcp = $true; $found = $true; break }
        }
        if (-not $found) {
            $result.portChecks += [ordered]@{ port = $l.localPort; localTcp = $true; testLoopback = $null }
        }
    }
}

function Test-LocalPort($port) {
    try {
        $r = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue
        return @{ success = $r.TcpTestSucceeded; r = $r }
    } catch { return @{ success = $false; error = $_.Exception.Message } }
}

for ($i=0; $i -lt $result.portChecks.Count; $i++) {
    $p = $result.portChecks[$i].port
    $t = Test-LocalPort -port $p
    $result.portChecks[$i].testLoopback = $t
}

# Write output
try {
    $json = $result | ConvertTo-Json -Depth 6
    $dir = Split-Path $outFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $json | Out-File -FilePath $outFile -Encoding utf8
    Write-Output "Service health check complete. Results written to $outFile"
} catch {
    Write-Output "Failed to write service health: $($_.Exception.Message)"
}
