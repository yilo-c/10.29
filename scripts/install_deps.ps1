<#
PowerShell: 依赖安装脚本模板（Windows）
用途：在目标主机上安装/验证部署所需依赖。请根据实际需求修改包清单。
#>

Write-Output "Start dependency installation checks..."

# 示例：检查并安装 chocolatey（可选）
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Output "chocolatey not found — skipping auto-install. Please install manually or enable script to bootstrap choco."
} else {
    Write-Output "chocolatey found"
}

# 示例：检查 Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Output "Python not found — please install Python 3.11+ or ensure PATH contains python.exe"
} else {
    python --version
}

# 示例：检查 Docker
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Output "Docker CLI not found — skip. If container deployment expected, install Docker Desktop or Docker Engine."
} else {
    docker --version
}

# 记录已安装项
Get-Command python,docker -ErrorAction SilentlyContinue | Select-Object Name,Source | ConvertTo-Json | Out-File -FilePath .\docs\M0-deps-check.json -Encoding utf8
Write-Output "Dependency check written to .\docs\M0-deps-check.json"

exit 0
