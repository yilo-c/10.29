$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoRoot '.venv3.11\Scripts\pythonw.exe'
$python = Join-Path $repoRoot '.venv3.11\Scripts\python.exe'

$entry = Get-ChildItem -Path $repoRoot -File -Recurse -Filter '*test1.py' |
    Where-Object { $_.Name -notlike '*backup*' } |
    Sort-Object FullName |
    Select-Object -First 1 -ExpandProperty FullName

if (-not $entry -or -not (Test-Path -LiteralPath $entry)) {
    throw 'Desktop entrypoint not found under repository root.'
}

$interpreter = if (Test-Path -LiteralPath $pythonw) {
    $pythonw
} elseif (Test-Path -LiteralPath $python) {
    $python
} else {
    throw 'Virtual environment interpreter not found under .venv3.11\Scripts.'
}

$env:SIYI_LAUNCH_SOURCE = 'windows-launcher'
$autoExitMs = 0
[void][int]::TryParse("$env:SIYI_AUTO_EXIT_MS", [ref]$autoExitMs)

$process = Start-Process -FilePath $interpreter -ArgumentList @($entry) -WorkingDirectory $repoRoot -PassThru
if ($autoExitMs -gt 0) {
    $process.WaitForExit()
    exit $process.ExitCode
}

exit 0