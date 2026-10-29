param(
    [ValidateSet('install', 'uninstall', 'status', 'preflight', 'live', 'live-worker', 'reset')]
    [string]$Action = 'install',
    [string]$ReleaseName = 'automation-hub-local',
    [string]$Namespace = 'automation-hub',
    [ValidateSet('host', 'docker-executor')]
    [string]$Profile = 'host',
    [string]$ValuesFile = '',
    [string]$ApiBase,
    [ValidateSet('host', 'docker')]
    [string]$LiveWorkerExecutor = 'host',
    [switch]$DeleteNamespace
)

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Split-Path -Parent $scriptDir
$workspaceRoot = Split-Path -Parent $appRoot
$chartPath = Join-Path $appRoot 'deploy/charts/automation-hub'
$temporaryLiveTokenFile = Join-Path $appRoot '.tmp_k8s_local_live_token.txt'

function Resolve-ProfileValuesFile {
    if ($ValuesFile) {
        return $ValuesFile
    }

    switch ($Profile) {
        'docker-executor' {
            return 'deploy/charts/automation-hub/values-local-docker-desktop-docker-executor.yaml'
        }
        default {
            return 'deploy/charts/automation-hub/values-local-docker-desktop.yaml'
        }
    }
}

$selectedValuesFile = Resolve-ProfileValuesFile
$resolvedValuesFile = Join-Path $appRoot $selectedValuesFile

function Resolve-CommandPath {
    param(
        [string]$Name,
        [string]$Fallback = ''
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    if ($Fallback -and (Test-Path $Fallback)) {
        return $Fallback
    }
    throw "$Name not found"
}

$helmExe = Resolve-CommandPath -Name 'helm' -Fallback 'C:\Users\Administrator\AppData\Local\Programs\helm\helm.exe'
$kubectlExe = Resolve-CommandPath -Name 'kubectl'

function Resolve-PythonExecutable {
    $candidates = @(
        (Join-Path $workspaceRoot '.venv3.11\Scripts\python.exe'),
        (Join-Path $appRoot '.venv3.11\Scripts\python.exe')
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw 'python executable not found'
}

$pythonExe = Resolve-PythonExecutable

function Write-Info {
    param([string]$Message)
    Write-Output ("[k8s-local] {0}" -f $Message)
}

function Get-ProfileSummary {
    switch ($Profile) {
        'docker-executor' {
            return 'docker-executor profile: worker mounts docker.sock, runs privileged/root, and requires DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false'
        }
        default {
            return 'host profile: worker does not mount docker.sock and uses host fallback friendly defaults'
        }
    }
}

function Invoke-Helm {
    param([string[]]$CommandArgs)
    & $helmExe @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "helm command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Kubectl {
    param([string[]]$CommandArgs)
    & $kubectlExe @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl command failed with exit code $LASTEXITCODE"
    }
}

function Ensure-ReleaseNamespace {
    $namespaceJson = & $kubectlExe get namespace $Namespace -o json --ignore-not-found 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl get namespace failed with exit code $LASTEXITCODE"
    }

    if ($namespaceJson) {
        $namespaceResource = $namespaceJson | ConvertFrom-Json
        if ($namespaceResource.metadata.deletionTimestamp) {
            Write-Info 'namespace is terminating; waiting for deletion'
            & $kubectlExe wait --for=delete ("namespace/{0}" -f $Namespace) --timeout=180s 2>$null
            $namespaceJson = $null
        }
    }

    if (-not $namespaceJson) {
        Invoke-Kubectl -CommandArgs @('create', 'namespace', $Namespace) | Out-Null
    }
    Invoke-Kubectl -CommandArgs @('label', 'namespace', $Namespace, 'app.kubernetes.io/managed-by=Helm', '--overwrite') | Out-Null
    Invoke-Kubectl -CommandArgs @('annotate', 'namespace', $Namespace, "meta.helm.sh/release-name=$ReleaseName", '--overwrite') | Out-Null
    Invoke-Kubectl -CommandArgs @('annotate', 'namespace', $Namespace, "meta.helm.sh/release-namespace=$Namespace", '--overwrite') | Out-Null
}

function Test-ReleaseExists {
    $releaseFilter = ("^{0}$" -f $ReleaseName)
    $releaseJson = Invoke-Helm -CommandArgs @('list', '-n', $Namespace, '--filter', $releaseFilter, '-o', 'json')
    $releases = $releaseJson | ConvertFrom-Json
    return ($releases | Measure-Object).Count -gt 0
}

function Get-CurrentReleaseProfile {
    if (-not (Test-ReleaseExists)) {
        return 'not-installed'
    }

    $configJson = Invoke-Kubectl -CommandArgs @('get', 'configmap', ("{0}-config" -f $ReleaseName), '-n', $Namespace, '-o', 'json')
    $config = $configJson | ConvertFrom-Json
    $allowHostFallback = "$($config.data.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK)"

    $deploymentJson = Invoke-Kubectl -CommandArgs @('get', 'deployment', ("{0}-worker" -f $ReleaseName), '-n', $Namespace, '-o', 'json')
    $deployment = $deploymentJson | ConvertFrom-Json
    $workerContainer = $deployment.spec.template.spec.containers | Select-Object -First 1
    $volumeMountNames = @($workerContainer.volumeMounts | ForEach-Object { $_.name })
    $socketVolume = @($deployment.spec.template.spec.volumes | Where-Object { $_.name -eq 'docker-sock' }) | Select-Object -First 1

    if ($socketVolume -and $socketVolume.hostPath -and ($volumeMountNames -contains 'docker-sock') -and $allowHostFallback -eq 'false') {
        return 'docker-executor'
    }

    return 'host'
}

function Show-Status {
    Write-Output ("[k8s-local] selected profile: {0}" -f $Profile)
    Write-Output ("[k8s-local] current release profile: {0}" -f (Get-CurrentReleaseProfile))
    Write-Output '---'
    Invoke-Helm -CommandArgs @('list', '-n', $Namespace)
    Write-Output '---'
    Invoke-Kubectl -CommandArgs @('get', 'pods,svc,deploy,pvc', '-n', $Namespace, '-o', 'wide')

    $serviceJson = & $kubectlExe get svc $ReleaseName -n $Namespace -o json 2>$null
    if ($LASTEXITCODE -eq 0 -and $serviceJson) {
        $service = $serviceJson | ConvertFrom-Json
        if ($service.spec.type -eq 'NodePort' -and $service.spec.ports.Count -gt 0) {
            $nodePort = $service.spec.ports[0].nodePort
            if ($nodePort) {
                Write-Output '---'
                Write-Output ("[k8s-local] access url: http://127.0.0.1:{0}" -f $nodePort)
                Write-Output ("[k8s-local] health url: http://127.0.0.1:{0}/health" -f $nodePort)
            }
        }
    }
}

function Get-ApiBaseUrl {
    if ($ApiBase) {
        return $ApiBase.TrimEnd('/')
    }

    $serviceJson = Invoke-Kubectl -CommandArgs @('get', 'svc', $ReleaseName, '-n', $Namespace, '-o', 'json')
    $service = $serviceJson | ConvertFrom-Json

    if ($service.spec.type -eq 'NodePort' -and $service.spec.ports.Count -gt 0 -and $service.spec.ports[0].nodePort) {
        return ('http://127.0.0.1:{0}' -f $service.spec.ports[0].nodePort)
    }

    throw 'unable to resolve API base url; set -ApiBase explicitly'
}

function Wait-ForHealth {
    param([string]$ResolvedApiBase)

    $healthUrl = ('{0}/health' -f $ResolvedApiBase.TrimEnd('/'))
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                Write-Info ("health check passed: {0}" -f $healthUrl)
                return
            }
        }
        catch {
        }
        Start-Sleep -Seconds 2
    }

    throw "health check failed: $healthUrl"
}

function Get-ApiPodName {
    $podName = Invoke-Kubectl -CommandArgs @('get', 'pod', '-n', $Namespace, '-l', 'app=automation-hub,component=api', '-o', 'jsonpath={.items[0].metadata.name}')
    $resolved = "$podName".Trim()
    if (-not $resolved) {
        throw 'api pod not found'
    }
    return $resolved
}

function Assert-DockerWorkerPreconditions {
    if ($LiveWorkerExecutor -ne 'docker') {
        return
    }

    $configJson = Invoke-Kubectl -CommandArgs @('get', 'configmap', ("{0}-config" -f $ReleaseName), '-n', $Namespace, '-o', 'json')
    $config = $configJson | ConvertFrom-Json
    $allowHostFallback = "$($config.data.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK)"

    $deploymentJson = Invoke-Kubectl -CommandArgs @('get', 'deployment', ("{0}-worker" -f $ReleaseName), '-n', $Namespace, '-o', 'json')
    $deployment = $deploymentJson | ConvertFrom-Json
    $workerContainer = $deployment.spec.template.spec.containers | Select-Object -First 1
    $volumeMountNames = @($workerContainer.volumeMounts | ForEach-Object { $_.name })
    $socketVolume = @($deployment.spec.template.spec.volumes | Where-Object { $_.name -eq 'docker-sock' }) | Select-Object -First 1

    if ($allowHostFallback -ne 'false') {
        throw 'live-worker docker preflight failed: current release still allows host fallback. Reinstall with worker.dockerSocket.enabled=true and env.DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK="false", or use -LiveWorkerExecutor host.'
    }

    if (-not $socketVolume -or -not $socketVolume.hostPath -or -not ($volumeMountNames -contains 'docker-sock')) {
        throw 'live-worker docker preflight failed: worker deployment does not mount docker.sock. Reinstall with a docker-executor values file that enables worker.dockerSocket, or use -LiveWorkerExecutor host.'
    }
}

function Invoke-PodPython {
    param(
        [string]$PodName,
        [string]$Code
    )

    Invoke-Kubectl -CommandArgs @('exec', '-n', $Namespace, $PodName, '--', 'python', '-c', $Code)
}

function New-RandomHexToken {
    return ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
}

function Get-Sha256Hex {
    param([string]$Value)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))
        return ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function New-TemporaryAdminToken {
    param([string]$PodName)

    $rawToken = New-RandomHexToken
    $tokenId = [guid]::NewGuid().ToString()
    $userId = [guid]::NewGuid().ToString()
    $deviceId = [guid]::NewGuid().ToString()
    $createdAt = [DateTime]::UtcNow.ToString('o')
    $tokenHash = Get-Sha256Hex -Value $rawToken

    $code = @"
import json, sqlite3

db = '/data/automation_hub.sqlite3'
conn = sqlite3.connect(db)
cur = conn.cursor()
user = cur.execute('select id from users order by created_at limit 1').fetchone()
user_id = user[0] if user else '$userId'
if not user:
    cur.execute('insert into users(id,name,created_at) values(?,?,?)', (user_id, 'K8s Local Live', '$createdAt'))
device = cur.execute('select id from devices where user_id=? order by created_at limit 1', (user_id,)).fetchone()
device_id = device[0] if device else '$deviceId'
if not device:
    cur.execute('insert into devices(id,user_id,name,platform,created_at) values(?,?,?,?,?)', (device_id, user_id, 'K8s Local Live', 'windows', '$createdAt'))
scopes = ['tool:read', 'tool:write', 'tool:execute', 'approval:read', 'approval:decide', 'audit:read', 'proposal:read', 'proposal:write', 'repo:read', 'repo:write', 'agent:use', 'user:admin']
cur.execute(
    'insert into api_tokens(id,user_id,device_id,token_hash,scopes,expires_at,created_at) values(?,?,?,?,?,?,?)',
    ('$tokenId', user_id, device_id, '$tokenHash', json.dumps(scopes, ensure_ascii=False), None, '$createdAt')
)
conn.commit()
print(json.dumps({'token_id': '$tokenId', 'user_id': user_id, 'device_id': device_id}, ensure_ascii=False))
conn.close()
"@

    $result = Invoke-PodPython -PodName $PodName -Code $code
    $parsed = $result | ConvertFrom-Json

    Set-Content -Path $temporaryLiveTokenFile -Value $rawToken -Encoding ascii

    return @{
        TokenId = $parsed.token_id
        UserId = $parsed.user_id
        DeviceId = $parsed.device_id
        TokenFile = $temporaryLiveTokenFile
    }
}

function Remove-TemporaryAdminToken {
    param(
        [string]$PodName,
        [string]$TokenId
    )

    if ($TokenId) {
        $revokeAt = [DateTime]::UtcNow.ToString('o')
        $code = @"
import sqlite3

conn = sqlite3.connect('/data/automation_hub.sqlite3')
cur = conn.cursor()
cur.execute('update api_tokens set revoked_at=? where id=? and revoked_at is null', ('$revokeAt', '$TokenId'))
conn.commit()
remaining = cur.execute('select count(*) from api_tokens where revoked_at is null').fetchone()[0]
print('ACTIVE_REMAINING=' + str(remaining))
conn.close()
"@
        Invoke-PodPython -PodName $PodName -Code $code | Out-Null
    }

    if (Test-Path $temporaryLiveTokenFile) {
        Remove-Item $temporaryLiveTokenFile -Force
    }
}

function Invoke-InstallAction {
    Write-Info ("using profile {0} with values {1}" -f $Profile, $selectedValuesFile)
    Write-Info 'ensuring namespace ownership metadata'
    Ensure-ReleaseNamespace
    Write-Info 'installing or upgrading local Helm release'
    Invoke-Helm -CommandArgs @(
        'upgrade', '--install', $ReleaseName, $chartPath,
        '--namespace', $Namespace,
        '--values', $resolvedValuesFile
    )
    Write-Info 'waiting for all deployments to become available'
    Invoke-Kubectl -CommandArgs @('wait', '--namespace', $Namespace, '--for=condition=Available', 'deployment', '--all', '--timeout=240s')
    Show-Status
}

function Invoke-UninstallAction {
    if (Test-ReleaseExists) {
        Write-Info 'uninstalling local Helm release'
        Invoke-Helm -CommandArgs @('uninstall', $ReleaseName, '-n', $Namespace)
    }
    else {
        Write-Info 'release not found; skip uninstall'
    }

    if ($DeleteNamespace.IsPresent) {
        Write-Info 'deleting namespace'
        Invoke-Kubectl -CommandArgs @('delete', 'namespace', $Namespace, '--ignore-not-found=true')
        Write-Info 'waiting for namespace deletion'
        & $kubectlExe wait --for=delete namespace/$Namespace --timeout=180s 2>$null
    }
}

function Remove-PersistentData {
    if ($DeleteNamespace.IsPresent) {
        return
    }

    $pvcName = "{0}-data" -f $ReleaseName
    Write-Info ("deleting pvc {0}" -f $pvcName)
    Invoke-Kubectl -CommandArgs @('delete', 'pvc', $pvcName, '-n', $Namespace, '--ignore-not-found=true')
    Write-Info 'waiting for pvc deletion'
    & $kubectlExe wait '-n' $Namespace '--for=delete' ("pvc/{0}" -f $pvcName) '--timeout=180s' 2>$null
}

function Invoke-ResetAction {
    Write-Info 'resetting local Helm release and persistent data'
    Write-Info ("reset will reinstall with profile {0}" -f $Profile)
    Write-Info (Get-ProfileSummary)
    Write-Info 'reset is destructive for local PVC data, including SQLite state and run artifacts'
    if (Test-Path $temporaryLiveTokenFile) {
        Remove-Item $temporaryLiveTokenFile -Force
    }

    Invoke-UninstallAction
    Remove-PersistentData
    Invoke-InstallAction
    Wait-ForHealth -ResolvedApiBase (Get-ApiBaseUrl)
}

function Invoke-ReleaseCheck {
    param(
        [ValidateSet('live', 'live-worker')]
        [string]$Mode,
        [string]$ResolvedApiBase,
        [string]$TokenFilePath,
        [string]$WorkerExecutor = 'host'
    )

    $commandArgs = @(
        (Join-Path $scriptDir 'release_check.py'),
        '--mode', $Mode,
        '--live-api-base', $ResolvedApiBase,
        '--live-token-file', $TokenFilePath
    )

    if ($Mode -eq 'live-worker') {
        $commandArgs += @('--live-worker-executor', $WorkerExecutor)
    }

    Write-Info ("running {0} acceptance against {1}" -f $Mode, $ResolvedApiBase)
    & $pythonExe @commandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "release_check failed with exit code $LASTEXITCODE"
    }
}

function Invoke-LiveAction {
    param(
        [ValidateSet('live', 'live-worker')]
        [string]$Mode
    )

    $resolvedApiBase = Get-ApiBaseUrl
    $podName = Get-ApiPodName
    $temporaryToken = $null

    try {
        Assert-DockerWorkerPreconditions
        Write-Info ("preparing temporary admin token via pod {0}" -f $podName)
        $temporaryToken = New-TemporaryAdminToken -PodName $podName
        Invoke-ReleaseCheck -Mode $Mode -ResolvedApiBase $resolvedApiBase -TokenFilePath $temporaryToken.TokenFile -WorkerExecutor $LiveWorkerExecutor
    }
    finally {
        if ($temporaryToken) {
            Write-Info 'revoking temporary live token'
            Remove-TemporaryAdminToken -PodName $podName -TokenId $temporaryToken.TokenId
        }
    }
}

if (-not (Test-Path $resolvedValuesFile)) {
    throw "values file not found: $resolvedValuesFile"
}

switch ($Action) {
    'preflight' {
        Write-Info 'running cluster preflight'
        $preflightArgs = @{
            Namespace = $Namespace
            ValuesFile = $selectedValuesFile
            ExpectedSecretName = ''
        }
        if ($Profile -eq 'docker-executor') {
            $preflightArgs.RequireDockerExecutor = $true
        }
        & (Join-Path $scriptDir 'cluster_preflight.ps1') @preflightArgs
    }
    'install' {
        Invoke-InstallAction
    }
    'uninstall' {
        Invoke-UninstallAction
    }
    'status' {
        Write-Info 'showing release status'
        Show-Status
    }
    'live' {
        Invoke-LiveAction -Mode 'live'
    }
    'live-worker' {
        Invoke-LiveAction -Mode 'live-worker'
    }
    'reset' {
        Invoke-ResetAction
    }
}