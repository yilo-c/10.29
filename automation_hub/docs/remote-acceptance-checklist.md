<!-- 模块说明：阶段 0 真实部署环境验收执行清单。 -->

# 阶段 0 真实部署环境验收执行清单

本文档是 [remote-acceptance-plan.md](remote-acceptance-plan.md) 的执行版清单，用于把“远端 live / live-worker 还未完成”收敛成一组可以逐项打勾的动作。

适用目标：

- 一次真实部署环境 `live`
- 一次真实部署环境 `live-worker --live-worker-executor docker`

只有这两条都完成并留档，阶段 0 才算关闭。

## 使用方式

执行人不需要先阅读全部 README。推荐顺序：

1. 先确认本清单中的环境信息与前置条件。
2. 按顺序执行 `live`。
3. 回填 live 留档。
4. 再执行 `live-worker --live-worker-executor docker`。
5. 回填 worker live 留档。
6. 最后更新根 README 中的待办状态。

## A. 环境信息准备

- [ ] 明确验收环境名称，例如 `staging`、`pre-prod` 或灰度环境
- [ ] 明确 `live_api_base`
- [ ] 明确执行时间窗口
- [ ] 明确执行人
- [ ] 明确当前 release 名称
- [ ] 明确当前 chart 版本或对应提交
- [ ] 明确目标 Helm values 文件

建议先把上述信息直接写入以下两份待回填文件：

- [live-acceptance-2026-03-11-remote.md](live-acceptance-2026-03-11-remote.md)
- [worker-live-acceptance-2026-03-11-remote.md](worker-live-acceptance-2026-03-11-remote.md)

## B. Token 与入口准备

二选一即可：

- [ ] 已准备本地 token 文件，供命令行执行使用
- [ ] 已在 GitHub Actions 中配置 `AUTOMATION_HUB_LIVE_TOKEN`

无论采用哪种方式，都先确认：

- [ ] token 对目标环境 `GET /auth/me` 返回 200
- [ ] token 具备管理员能力
- [ ] token 未过期

命令行模式下，建议先在工作区根目录准备 token 文件路径，例如：

```powershell
$env:AUTOMATION_HUB_REMOTE_TOKEN_FILE = "automation-hub/.tmp_remote_live_token.txt"
```

## C. Live 前置检查

在真正执行 `release_check.py --mode live` 之前，先确认：

- [ ] 目标环境 API 已启动
- [ ] 目标环境 Worker 已启动
- [ ] `GET <live_api_base>/health` 返回 200
- [ ] `GET <live_api_base>/auth/me` 使用管理员 token 返回 200
- [ ] 当前网络路径可以从执行位置直达目标 API

PowerShell 预检示例：

```powershell
$apiBase = "https://<your-api-base>"
$token = Get-Content automation-hub/.tmp_remote_live_token.txt -Raw

Invoke-RestMethod -Method Get -Uri "$apiBase/health"
Invoke-RestMethod -Method Get -Uri "$apiBase/auth/me" -Headers @{ Authorization = "Bearer $($token.Trim())" }
```

## D. 执行远端 Live

命令行执行模板：

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base <目标地址> --live-token-file <token文件>
```

示例：

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live --live-api-base https://staging.example.com --live-token-file automation-hub/.tmp_remote_live_token.txt
```

GitHub Actions 执行模板：

- [ ] 打开 [../.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
- [ ] 选择 `workflow_dispatch`
- [ ] 设置 `mode=live`
- [ ] 填写 `live_api_base`
- [ ] 确认 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

Live 通过标准：

- [ ] 输出包含 `[release-check] live preflight passed`
- [ ] 输出包含 `[release-check] all checks passed`
- [ ] `verify_system.py` 汇总失败数为 0

需要保存的关键证据：

- [ ] 实际执行命令或 workflow_dispatch 参数
- [ ] preflight 输出摘要
- [ ] 最终通过输出摘要
- [ ] 若失败，记录失败现象和处置

## E. 回填 Live 留档

执行结束后，回填 [live-acceptance-2026-03-11-remote.md](live-acceptance-2026-03-11-remote.md)：

- [ ] 验收环境
- [ ] API 地址
- [ ] 执行时间
- [ ] 执行人
- [ ] token 来源
- [ ] 目标 values 文件
- [ ] release / chart 版本
- [ ] 本次实际执行内容
- [ ] 预检结果与关键输出摘要
- [ ] 最终结果判定
- [ ] 若失败，记录失败原因、处置、是否阻塞发布

## F. Worker Live 前置检查

只有在 Live 通过后，再继续执行 worker live。

- [ ] API 仍然可达
- [ ] Worker 正常运行
- [ ] Redis 队列可用
- [ ] worker 节点支持 `docker.sock` hostPath
- [ ] 部署 values 已关闭 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK`
- [ ] 当前部署的执行器前提与目标环境一致

如果以上任一项无法确认，不要直接跑 `live-worker --live-worker-executor docker`，应先补齐环境证据。

## G. 执行远端 Worker Live

命令行执行模板：

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base <目标地址> --live-token-file <token文件> --live-worker-executor docker
```

示例：

```powershell
D:/BaiduSyncdisk/个人/思忆集/思集/运维/.venv3.11/Scripts/python.exe automation-hub/scripts/release_check.py --mode live-worker --live-api-base https://staging.example.com --live-token-file automation-hub/.tmp_remote_live_token.txt --live-worker-executor docker
```

GitHub Actions 执行模板：

- [ ] 打开 [../.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
- [ ] 选择 `workflow_dispatch`
- [ ] 设置 `mode=live-worker`
- [ ] 填写 `live_api_base`
- [ ] 设置 `live_worker_executor=docker`
- [ ] 确认 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

Worker live 通过标准：

- [ ] 低风险 tool-run 进入 `succeeded`
- [ ] 高风险 tool-run 在审批通过后进入 `succeeded`
- [ ] `stdout_tail` 含预期标记
- [ ] 存在 `approval.decided` 审计
- [ ] 存在 `run.executed` 审计
- [ ] 输出包含 `[release-check] all checks passed`

需要保存的关键证据：

- [ ] 低风险 run_id 与终态
- [ ] 高风险 run_id、approval_id 与终态
- [ ] `stdout_tail` 关键摘要
- [ ] 审计事件摘要
- [ ] 若失败，记录失败现象和处置

## H. 回填 Worker Live 留档

执行结束后，回填 [worker-live-acceptance-2026-03-11-remote.md](worker-live-acceptance-2026-03-11-remote.md)：

- [ ] 验收环境
- [ ] API 地址
- [ ] 执行时间
- [ ] 执行人
- [ ] 执行器类型 `docker`
- [ ] token 来源
- [ ] 目标 values 文件
- [ ] release / chart 版本
- [ ] 本次实际执行内容
- [ ] 低风险与高风险 run 的关键证据
- [ ] 审计摘要
- [ ] 最终结果判定
- [ ] 若失败，记录失败原因、处置、是否阻塞发布

## I. 关闭阶段 0 的最终条件

以下条件全部满足后，阶段 0 才算完成：

- [ ] [live-acceptance-2026-03-11-remote.md](live-acceptance-2026-03-11-remote.md) 已从阻塞状态改为通过或失败并补齐真实输出
- [ ] [worker-live-acceptance-2026-03-11-remote.md](worker-live-acceptance-2026-03-11-remote.md) 已从阻塞状态改为通过或失败并补齐真实输出
- [ ] 根目录 [../../README.md](../../README.md) 中两项远端待办已更新
- [ ] 若两项均通过，允许进入下一阶段生产基线固化

## J. 常见阻塞与处理顺序

### 1. 没有远端 API 地址

- 不要猜测地址
- 先向环境负责人确认 `live_api_base`
- 地址未确认前，不要回填“通过”或“失败”

### 2. 没有管理员 token

- 优先补一枚真实环境管理员 token
- 若走 GitHub Actions，则确认 `AUTOMATION_HUB_LIVE_TOKEN` 已配置
- token 不可用时，不要跳过 `/auth/me` 预检

### 3. Worker live 前提不明确

- 先确认目标 worker 节点运行时
- 再确认是否真的支持 `docker.sock` hostPath
- 最后确认 values 中 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK=false`

### 4. 当前只有本机或自部署结果

- 本机 Windows、Docker Desktop、本机自部署 Docker 三容器结果都只能作为本地复验
- 这些记录不能关闭远端待办
- 远端证据必须来自真实部署环境