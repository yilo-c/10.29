param(
    [string]$Namespace = 'automation-hub',
    [string]$ValuesFile = 'deploy/charts/automation-hub/values-prod-external-redis.yaml',
    [string]$ExpectedSecretName = 'automation-hub-prod-secret',
    [string]$ExpectedStorageClass = '',
    [string]$ExpectedRedisHost = '',
    [switch]$RequireDockerExecutor
)

$ErrorActionPreference = 'Stop'

function Test-CommandAvailable {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Write-Check {
    param(
        [string]$Status,
        [string]$Message
    )
    Write-Output ("[{0}] {1}" -f $Status, $Message)
}

function Get-ValuesYamlField {
    param(
        [string]$FilePath,
        [string]$Pattern
    )

    $line = Select-String -Path $FilePath -Pattern $Pattern | Select-Object -First 1
    if (-not $line) {
        return ''
    }

    $parts = $line.Line.Split(':', 2)
    if ($parts.Count -lt 2) {
        return ''
    }

    return $parts[1].Trim().Trim('"')
}

function Get-ValuesYamlBool {
    param(
        [string]$FilePath,
        [string]$Pattern
    )

    $value = Get-ValuesYamlField -FilePath $FilePath -Pattern $Pattern
    return $value -eq 'true'
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appRoot = Split-Path -Parent $scriptDir
$resolvedValuesFile = Join-Path $appRoot $ValuesFile

if (-not (Test-CommandAvailable -Name 'kubectl')) {
    Write-Check -Status 'FAIL' -Message 'kubectl not found in PATH'
    exit 1
}

Write-Check -Status 'OK' -Message 'kubectl found'

if (-not (Test-Path $resolvedValuesFile)) {
    Write-Check -Status 'FAIL' -Message ("values file not found: {0}" -f $resolvedValuesFile)
    exit 1
}

Write-Check -Status 'OK' -Message ("values file found: {0}" -f $resolvedValuesFile)

$currentContext = ''
try {
    $currentContext = (kubectl config current-context).Trim()
} catch {
    Write-Check -Status 'FAIL' -Message 'kubectl current-context is not set'
    exit 1
}

if (-not $currentContext) {
    Write-Check -Status 'FAIL' -Message 'kubectl current-context is empty'
    exit 1
}

Write-Check -Status 'OK' -Message ("kubectl context: {0}" -f $currentContext)

try {
    kubectl cluster-info | Out-Null
    Write-Check -Status 'OK' -Message 'cluster-info reachable'
} catch {
    Write-Check -Status 'FAIL' -Message 'kubectl cannot reach the current cluster'
    exit 1
}

$namespaceExists = $false
$namespaceLookup = kubectl get namespace $Namespace -o name --ignore-not-found 2>$null
if ($LASTEXITCODE -eq 0 -and $namespaceLookup) {
    $namespaceExists = $true
}

if ($namespaceExists) {
    Write-Check -Status 'OK' -Message ("namespace exists: {0}" -f $Namespace)
} else {
    Write-Check -Status 'WARN' -Message ("namespace does not exist yet: {0}" -f $Namespace)
}

$secretName = $ExpectedSecretName
if (-not $secretName) {
    $secretName = Get-ValuesYamlField -FilePath $resolvedValuesFile -Pattern '^  existingSecretName:'
}

$secretCreateEnabled = Get-ValuesYamlBool -FilePath $resolvedValuesFile -Pattern '^  create:'

if ($secretName) {
    $secretExists = $false
    if ($namespaceExists) {
        $secretLookup = kubectl -n $Namespace get secret $secretName -o name --ignore-not-found 2>$null
        if ($LASTEXITCODE -eq 0 -and $secretLookup) {
            $secretExists = $true
        }
    }

    if ($secretExists) {
        Write-Check -Status 'OK' -Message ("secret exists: {0}" -f $secretName)
    } else {
        Write-Check -Status 'WARN' -Message ("secret not found in namespace {0}: {1}" -f $Namespace, $secretName)
    }
} elseif ($secretCreateEnabled) {
    Write-Check -Status 'INFO' -Message 'chart-managed secret.create=true; no pre-existing secret is required'
} else {
    Write-Check -Status 'INFO' -Message 'no external secret expected from values file'
}

$storageClassName = $ExpectedStorageClass
if (-not $storageClassName) {
    $storageClassName = Get-ValuesYamlField -FilePath $resolvedValuesFile -Pattern '^  storageClassName:'
}

if ($storageClassName) {
    $storageClassLookup = kubectl get storageclass $storageClassName -o name --ignore-not-found 2>$null
    if ($LASTEXITCODE -eq 0 -and $storageClassLookup) {
        Write-Check -Status 'OK' -Message ("storageClass exists: {0}" -f $storageClassName)
    } else {
        Write-Check -Status 'WARN' -Message ("storageClass not found: {0}" -f $storageClassName)
    }
} else {
    Write-Check -Status 'INFO' -Message 'storageClass not pinned in values; cluster default storageClass will be used'
}

$redisHost = $ExpectedRedisHost
$redisEnabled = Get-ValuesYamlBool -FilePath $resolvedValuesFile -Pattern '^  enabled:'
if (-not $redisHost) {
    $redisUrl = Get-ValuesYamlField -FilePath $resolvedValuesFile -Pattern '^  REDIS_URL:'
    if ($redisUrl -match '^redis://([^:/]+)') {
        $redisHost = $Matches[1]
    }
}

if ($redisHost) {
    Write-Check -Status 'INFO' -Message ("expected Redis host: {0}" -f $redisHost)
} elseif ($redisEnabled) {
    Write-Check -Status 'INFO' -Message 'chart-managed Redis is enabled in values file'
} else {
    Write-Check -Status 'WARN' -Message 'Redis host could not be derived from values file'
}

if ($RequireDockerExecutor.IsPresent) {
    $dockerSocketEnabled = Get-ValuesYamlField -FilePath $resolvedValuesFile -Pattern '^    enabled:'
    if ($dockerSocketEnabled -eq 'true') {
        Write-Check -Status 'OK' -Message 'docker executor socket mount is enabled in values file'
    } else {
        Write-Check -Status 'WARN' -Message 'docker executor requested, but values file does not appear to enable dockerSocket'
    }
}

Write-Check -Status 'DONE' -Message 'cluster preflight completed'