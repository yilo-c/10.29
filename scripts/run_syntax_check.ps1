$files = @('.\\scripts\\check_host.ps1', '.\\scripts\\install_deps.ps1')
$results = @()
foreach ($f in $files) {
    try {
        [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$null)
        $results += [PSCustomObject]@{Script = $f; SyntaxOk = $true; Error = $null}
    } catch {
        $results += [PSCustomObject]@{Script = $f; SyntaxOk = $false; Error = $_.Exception.Message}
    }
}
$results | ConvertTo-Json -Depth 5 | Out-File .\docs\M0-lint-results.json -Encoding utf8
Write-Output 'Syntax check complete; PSScriptAnalyzer unavailable.'
