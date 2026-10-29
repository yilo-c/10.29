param(
    [ValidateSet('up', 'down', 'status')]
    [string]$Action = 'up',
    [string]$ImageTag = 'automation-hub:docker-local',
    [string]$ApiPort = '8010'
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExe) {
    $dockerPath = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
    if (-not (Test-Path $dockerPath)) {
        throw 'docker.exe not found. Install Docker Desktop or add docker to PATH.'
    }
    $dockerExe = @{ Source = $dockerPath }
}

$networkName = 'automation-hub-docker-net'
$dataVolume = 'automation-hub-docker-data'
$workspaceVolume = 'automation-hub-docker-workspace'
$redisContainer = 'automation-hub-docker-redis'
$apiContainer = 'automation-hub-docker-api'
$workerContainer = 'automation-hub-docker-worker'
$statusFormat = "table {{.Names}}`t{{.Status}}`t{{.Ports}}"

function Initialize-DockerObject {
    param(
        [ValidateSet('network', 'volume')]
        [string]$Kind,
        [string]$Name
    )

    $exists = & $dockerExe.Source $Kind 'ls' '--format' '{{.Name}}' | Where-Object { $_ -eq $Name }
    if (-not $exists) {
        & $dockerExe.Source $Kind 'create' $Name | Out-Null
    }
}

function Remove-ContainerIfExists {
    param([string]$Name)
    $exists = & $dockerExe.Source 'ps' '-a' '--filter' "name=^/${Name}$" '--format' '{{.Names}}' | Where-Object { $_ -eq $Name }
    if ($exists) {
        & $dockerExe.Source 'rm' '-f' $Name | Out-Null
    }
}

switch ($Action) {
    'down' {
        Remove-ContainerIfExists -Name $workerContainer
        Remove-ContainerIfExists -Name $apiContainer
        Remove-ContainerIfExists -Name $redisContainer
        Write-Output 'docker local stack stopped'
        break
    }
    'status' {
        & $dockerExe.Source 'ps' '--filter' 'name=automation-hub-docker' '--format' $statusFormat
        break
    }
    'up' {
        Initialize-DockerObject -Kind network -Name $networkName
        Initialize-DockerObject -Kind volume -Name $dataVolume
        Initialize-DockerObject -Kind volume -Name $workspaceVolume

        Remove-ContainerIfExists -Name $workerContainer
        Remove-ContainerIfExists -Name $apiContainer
        Remove-ContainerIfExists -Name $redisContainer

        & $dockerExe.Source @(
            'run', '-d',
            '--name', $redisContainer,
            '--network', $networkName,
            'redis:7-alpine'
        ) | Out-Null

        & $dockerExe.Source @(
            'run', '-d',
            '--name', $apiContainer,
            '--network', $networkName,
            '-p', "127.0.0.1:${ApiPort}:8000",
            '-e', 'REDIS_URL=redis://automation-hub-docker-redis:6379/0',
            '-e', 'DATABASE_PATH=/data/automation_hub.sqlite3',
            '-e', 'DATA_DIR=/data',
            '-e', 'RUNS_DIR=/data/runs',
            '-e', 'LOGS_DIR=/data/logs',
            '-e', 'WORKSPACE_DIR=/workspace',
            '-e', 'SCRIPTS_MANIFEST=/home/appuser/scripts/manifest.json',
            '-v', "${dataVolume}:/data",
            '-v', "${workspaceVolume}:/workspace",
            $ImageTag
        ) | Out-Null

        & $dockerExe.Source @(
            'run', '-d',
            '--name', $workerContainer,
            '--network', $networkName,
            '--user', '0:0',
            '-e', 'REDIS_URL=redis://automation-hub-docker-redis:6379/0',
            '-e', 'DATABASE_PATH=/data/automation_hub.sqlite3',
            '-e', 'DATA_DIR=/data',
            '-e', 'RUNS_DIR=/data/runs',
            '-e', 'LOGS_DIR=/data/logs',
            '-e', 'WORKSPACE_DIR=/workspace',
            '-e', 'SCRIPTS_MANIFEST=/home/appuser/scripts/manifest.json',
            '-e', 'DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false',
            '-e', "DOCKER_EXECUTOR_IMAGE=${ImageTag}",
            '-v', "${dataVolume}:/data",
            '-v', "${workspaceVolume}:/workspace",
            '-v', '/var/run/docker.sock:/var/run/docker.sock',
            $ImageTag,
            'python', '-m', 'worker.worker'
        ) | Out-Null

        Write-Output "docker local stack started on http://127.0.0.1:${ApiPort}"
        & $dockerExe.Source 'ps' '--filter' 'name=automation-hub-docker' '--format' $statusFormat
    }
}