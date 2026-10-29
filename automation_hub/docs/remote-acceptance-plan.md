<!-- 模块说明：真实部署环境 live / live-worker 验收计划。 -->

# 真实部署环境验收计划

本文档用于把根 README 中“真实部署环境 live / live-worker 还未完成”两项待办，收敛成一份可执行的远端环境验收计划。

如果当前需要按步骤执行而不是阅读说明，优先使用 [remote-acceptance-checklist.md](remote-acceptance-checklist.md)。

目标不是替代 [live-acceptance-template.md](live-acceptance-template.md) 和 [worker-live-acceptance-template.md](worker-live-acceptance-template.md)，而是明确：

1. 什么时候可以开始远端验收。
2. 远端验收前要准备什么。
3. live 和 live-worker 分别怎么执行。
4. 验收结束后必须沉淀哪些留档。

## 验收目标

本阶段要补齐两条真实部署环境证据：

1. 一次 `release_check.py --mode live` 的成功记录。
2. 一次 `release_check.py --mode live-worker --live-worker-executor docker` 的成功记录。

只有这两条都留档，根 README 里的两项待办才可以关闭。

## 启动条件

满足以下条件后，再启动真实部署环境验收：

1. 目标环境 API 已经稳定可访问，`GET /health` 返回 200。
2. 目标环境 Worker 已运行，且 Redis 队列可用。
3. 已确认目标集群的 worker 节点确实支持本次执行器类型。
4. 已准备一枚具备管理员能力的 token。
5. 已确认当前验收是针对哪一个环境，例如 `staging`、`pre-prod` 或正式生产前灰度环境。

## 执行前准备

### 环境信息

先固定以下信息，避免验收后无法追溯：

- 验收环境名称
- API Base URL
- 执行时间窗口
- 执行人
- 目标 Helm values 文件
- 当前 release / chart 版本

### Token

二选一：

1. 命令行执行：准备本地 token 文件。
2. GitHub Actions 执行：提前配置仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN`。

无论哪种方式，都要先验证：

- `GET /auth/me` 返回 200
- token 具备管理员能力
- token 未过期

### 执行器确认

如果计划跑 `live-worker --live-worker-executor docker`，先确认：

1. 目标 worker 节点运行时支持 `docker.sock` hostPath。
2. 实际部署 values 已关闭 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK`。
3. worker 启动时未因 docker CLI/socket/image 预检失败退出。

## 验收顺序

推荐顺序固定为：

1. 先跑 `live`
2. 再跑 `live-worker --live-worker-executor docker`

不要反过来执行。`live` 先通过，能先证明 API、token、基础资源和主链路是健康的；再跑 worker live，定位会更清晰。

## Live 验收

### 命令行入口

```bash
python automation-hub/scripts/release_check.py --mode live --live-api-base <目标地址> --live-token-file <token文件>
```

### GitHub Actions 入口

1. 打开 [automation-hub/.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
2. 选择 `workflow_dispatch`
3. 设置 `mode=live`
4. 填写 `live_api_base`
5. 确认仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

### 通过标准

至少同时满足：

1. 输出包含 `[release-check] live preflight passed`
2. 输出包含 `[release-check] all checks passed`
3. `verify_system.py` 汇总失败数为 0

### 验收留档

执行完后，必须复制一份模板并填写本次实际结果：

- 模板： [live-acceptance-template.md](live-acceptance-template.md)
- 建议命名： `live-acceptance-YYYY-MM-DD-<env>.md`

建议至少记录：

- 目标环境
- API 地址
- 实际执行命令或 workflow_dispatch 参数
- preflight 关键输出
- 最终结果判定

## Worker Live 验收

### 命令行入口

```bash
python automation-hub/scripts/release_check.py --mode live-worker --live-api-base <目标地址> --live-token-file <token文件> --live-worker-executor docker
```

### GitHub Actions 入口

1. 打开 [automation-hub/.github/workflows/ci.yaml](../.github/workflows/ci.yaml)
2. 选择 `workflow_dispatch`
3. 设置 `mode=live-worker`
4. 填写 `live_api_base`
5. 设置 `live_worker_executor=docker`
6. 确认仓库 Secret `AUTOMATION_HUB_LIVE_TOKEN` 已存在

### 通过标准

至少同时满足：

1. 低风险 tool-run 进入 `succeeded`
2. 高风险 tool-run 在审批通过后进入 `succeeded`
3. 输出包含 `[release-check] all checks passed`

### 验收留档

执行完后，必须复制一份模板并填写本次实际结果：

- 模板： [worker-live-acceptance-template.md](worker-live-acceptance-template.md)
- 建议命名： `worker-live-acceptance-YYYY-MM-DD-<env>.md`

建议至少记录：

- 执行器类型
- 低风险 run 结果
- 高风险 run + 审批结果
- 审计与 stdout_tail 的关键信息
- 是否允许继续发布

## 产出要求

本阶段最少需要产出两份文件：

1. 一份 live 验收记录
2. 一份 worker live 验收记录

建议都放在 [automation-hub/docs](.) 下，便于后续从 README 和部署清单直接引用。

## 失败处理

如果 `live` 失败：

1. 先不要继续跑 `live-worker`
2. 优先排查 `/health`、`/auth/me`、token、API 路由和配置问题
3. 在留档里记录失败现象、初步原因和是否阻塞发布

如果 `live-worker` 失败：

1. 区分是低风险 run 未消费，还是高风险审批后未消费
2. 优先排查 Redis、worker 日志、docker executor 预检和 `DOCKER_EXECUTOR_ALLOW_HOST_FALLBACK`
3. 在留档里记录失败现象、初步原因和是否阻塞发布

## 完成定义

以下条件全部满足，才算真实部署环境验收阶段完成：

1. `live` 真实环境通过一次并有留档
2. `live-worker --live-worker-executor docker` 真实环境通过一次并有留档
3. 根 README 的两项未完成待办可以改为已完成或替换为下一阶段任务

执行时建议同步对照 [remote-acceptance-checklist.md](remote-acceptance-checklist.md)，逐项记录环境信息、命令输出和留档回填状态。
