<#
PowerShell: 主机检查脚本模板
用法示例：在目标主机上以管理员身份运行，输出结果可保存为 `host_check_result.json`。
#>

$result = [ordered]@{}

# 基本信息
$result.Hostname = $env:COMPUTERNAME
$result.OS = (Get-CimInstance -ClassName CIM_OperatingSystem).Caption
$result.OSVersion = (Get-CimInstance -ClassName CIM_OperatingSystem).Version
$result.TimeZone = (Get-TimeZone).Id

# 用户与权限检查（示例）
$adminUsers = Get-LocalGroupMember -Group "Administrators" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
$result.Administrators = $adminUsers

# 时钟同步
$ntp = w32tm /query /status 2>$null
$result.NtpStatus = if ($LASTEXITCODE -eq 0) { 'ok' } else { 'unknown' }

# 安装软件版本示例（Python/Docker）
$result.Python = @{ installed = $false; version = $null }
if (Get-Command python -ErrorAction SilentlyContinue) {
    $p = python --version 2>&1
    $result.Python.installed = $true
    $result.Python.version = $p
}

$result.Docker = @{ installed = $false; version = $null }
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $d = docker --version 2>$null
    $result.Docker.installed = $true
    $result.Docker.version = $d
}

# 输出为 JSON
$json = $result | ConvertTo-Json -Depth 5
$json | Out-File -FilePath .\docs\M0-host-check-result.json -Encoding utf8
Write-Output "Host check written to .\docs\M0-host-check-result.json"

exit 0
