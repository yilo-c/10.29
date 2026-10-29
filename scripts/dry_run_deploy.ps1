# Dry-run 部署执行脚本：将脚本复制到临时目录执行，并把输出收集为 dryrun 文件
param()

$timestamp = (Get-Date -Format 'yyyyMMdd_HHmmss')
# 计算仓库根目录：脚本位于 repo/scripts，所以 repoRoot 为上一级
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$base = Join-Path $repoRoot "build\m0_dryrun_$timestamp"
 $dirs = @($base, (Join-Path $base 'scripts'), (Join-Path $base 'docs'))
foreach ($d in $dirs) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null } }

# 复制脚本（从仓库的 scripts 目录复制）
Copy-Item -Path (Join-Path $repoRoot 'scripts\*.ps1') -Destination (Join-Path $base 'scripts') -Force

# 进入临时脚本目录并执行每个脚本
Push-Location (Join-Path $base 'scripts')
$log = Join-Path $base 'docs\dryrun_execution.log'
"Dry-run start: $timestamp" | Out-File -FilePath $log -Encoding utf8
foreach ($s in Get-ChildItem -Filter *.ps1) {
    "--- Running $($s.Name) ---" | Out-File -FilePath $log -Append -Encoding utf8
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $s.FullName *>&1 | Tee-Object -FilePath (Join-Path $base "docs\$($s.BaseName)-stdout.txt") >> $log
        "Exit: success" | Out-File -FilePath $log -Append -Encoding utf8
    } catch {
        "Exit: failed - $($_.Exception.Message)" | Out-File -FilePath $log -Append -Encoding utf8
    }
}

Pop-Location

# 将临时 docs 中的 json 结果复制回仓库的 docs，标注为 dryrun
$files = Get-ChildItem -Path (Join-Path $base 'docs') -File -Filter *.json -ErrorAction SilentlyContinue
foreach ($f in $files) {
    $dest = Join-Path $repoRoot "docs\$($f.BaseName)-dryrun.json"
    Copy-Item -Path $f.FullName -Destination $dest -Force
}

# 生成摘要并写入仓库 docs
$summary = @()
$summary += "Dry-run summary: $timestamp"
$summary += "Temp dir: $base"
if ($files) { foreach ($f in $files) { $summary += "Collected: $($f.Name) -> docs/$($f.BaseName)-dryrun.json" } } else { $summary += "No json results produced in temp docs." }
$summary | Out-File -FilePath (Join-Path $repoRoot 'docs\M0-dryrun-summary.md') -Encoding utf8

Write-Output "Dry-run complete. Summary written to docs/M0-dryrun-summary.md and files copied to docs/*-dryrun.json"
exit 0
