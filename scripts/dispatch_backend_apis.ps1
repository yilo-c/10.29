param(
    [string]$RepoRoot = "D:\h\3.25\思忆集"
)

$LogDir = Join-Path $RepoRoot "scripts\dispatch_logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Run-ClaudeTask {
    param($Name, $PromptFile)
    $logFile = Join-Path $LogDir "${Name}.log"
    $prompt = Get-Content $PromptFile -Raw -Encoding utf8
    Write-Host "[${Name}] 启动..."
    $result = claude -p $prompt --print 2>&1
    $result | Out-File -FilePath $logFile -Encoding utf8
    Write-Host "[${Name}] 完成"
    return $result
}

# 先创建 prompt 文件
$PromptDir = Join-Path $RepoRoot "scripts\dispatch_prompts"
New-Item -ItemType Directory -Path $PromptDir -Force | Out-Null

$tasks = @(
    @{Name = "web_search"; File = "web_search_prompt.txt"}
    @{Name = "cron"; File = "cron_prompt.txt"}
    @{Name = "plugins"; File = "plugins_prompt.txt"}
    @{Name = "evolution"; File = "evolution_prompt.txt"}
    @{Name = "workflows"; File = "workflows_prompt.txt"}
    @{Name = "knowledge_base"; File = "knowledge_base_prompt.txt"}
)

# === Batch 1: web_search, cron, plugins ===
Write-Host "=== Batch 1: 启动 3 个任务 ==="
$job1 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "web_search",  (Join-Path $PromptDir "web_search_prompt.txt")
$job2 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "cron",       (Join-Path $PromptDir "cron_prompt.txt")
$job3 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "plugins",    (Join-Path $PromptDir "plugins_prompt.txt")

Write-Host "等待 Batch 1 完成..."
Wait-Job $job1, $job2, $job3 | Out-Null
Receive-Job $job1, $job2, $job3
Remove-Job $job1, $job2, $job3
Write-Host "=== Batch 1 全部完成 ==="

# === Batch 2: evolution, workflows, knowledge_base ===
Write-Host "=== Batch 2: 启动 3 个任务 ==="
$job4 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "evolution",      (Join-Path $PromptDir "evolution_prompt.txt")
$job5 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "workflows",      (Join-Path $PromptDir "workflows_prompt.txt")
$job6 = Start-Job -ScriptBlock ${function:Run-ClaudeTask} -ArgumentList "knowledge_base", (Join-Path $PromptDir "knowledge_base_prompt.txt")

Write-Host "等待 Batch 2 完成..."
Wait-Job $job4, $job5, $job6 | Out-Null
Receive-Job $job4, $job5, $job6
Remove-Job $job4, $job5, $job6
Write-Host "=== Batch 2 全部完成 ==="

# 汇总
Write-Host "`n===== 所有任务完成 ====="
Get-ChildItem $LogDir\*.log | ForEach-Object {
    Write-Host "`n--- $($_.BaseName) ---"
    Get-Content $_.FullName -Tail 3 -Encoding utf8 | ForEach-Object { Write-Host "  $_" }
}
