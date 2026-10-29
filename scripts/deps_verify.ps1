<#
依赖验证（非侵入性）
输出：docs/M0-deps-verify.json

作用：
- 搜索仓库中的 docker-compose 文件
- 列出本地运行的容器（docker ps），并收集每个容器的基本信息与最近若干行日志（非阻塞）
- 不会尝试启动/停止容器或修改系统
#>

$timestamp = Get-Date -Format o
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$outFile = Join-Path $repoRoot 'docs\M0-deps-verify.json'

$result = [ordered]@{
    metadata = [ordered]@{ host = $env:COMPUTERNAME; generated = $timestamp }
    composeFiles = @()
    containers = @()
    notes = @()
}

try {
    $compose = Get-ChildItem -Path $repoRoot -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'docker-compose.*\.ya?ml' -or $_.Name -eq 'docker-compose.yml' }
    foreach ($f in $compose) { $result.composeFiles += @{ path = $f.FullName; name = $f.Name; length = $f.Length } }
} catch { $result.notes += "compose search failed: $($_.Exception.Message)" }

try {
    $ps = & docker ps --no-trunc --format '{{json .}}' 2>$null
    if ($LASTEXITCODE -ne 0) { $result.notes += 'docker ps failed or docker unavailable' } else {
        foreach ($line in $ps -split "`n") {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                try {
                    $obj = $line | ConvertFrom-Json
                    $cid = $obj.ID
                    $cinfo = [ordered]@{ id=$cid; image=$obj.Image; names=$obj.Names; status=$obj.Status }
                    # inspect for more detail
                    try { $inspect = & docker inspect --format '{{json .}}' $cid 2>$null; $cinfo.inspect = $inspect } catch {}
                    # collect short logs (tail 50)
                    try { $logs = & docker logs --tail 50 $cid 2>$null; $cinfo.logs = ($logs -split "`n")[-50..-1] -replace "`r","" } catch { $cinfo.logs = @("logs unavailable") }
                    $result.containers += $cinfo
                } catch { $result.notes += "docker ps parse error: $($_.Exception.Message)" }
            }
        }
    }
} catch { $result.notes += "docker queries failed: $($_.Exception.Message)" }

# write output
try {
    $json = $result | ConvertTo-Json -Depth 8
    $dir = Split-Path $outFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $json | Out-File -FilePath $outFile -Encoding utf8
    Write-Output "Deps verify written to $outFile"
} catch { Write-Output "Failed to write deps verify: $($_.Exception.Message)" }
