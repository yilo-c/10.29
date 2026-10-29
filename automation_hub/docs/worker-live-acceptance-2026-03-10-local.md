# Worker 端到端验收记录 2026-03-10（本地）

## 基本信息

- 验收环境：本地开发环境（Windows）
- API 地址：http://127.0.0.1:8766
- 执行时间：2026-03-10
- 执行人：GitHub Copilot
- 验收目标：验证 worker 对 Redis 队列的真实消费执行链路

## 覆盖范围

- API 创建 tool-run
- Redis 入队
- worker 消费任务
- host executor 执行本机命令
- tool-run 状态回写
- 审批通过后的高风险 run 重新入队与执行
- 审计事件写入

## 验收方法

使用隔离的临时数据库、数据目录和 workspace，分别启动本地 API 与 worker，然后执行两条链路：

1. 低风险工具直接执行
2. 高风险工具先进入 pending_approval，再 approve，随后由 worker 消费执行

执行脚本会校验：

- low/high run 最终状态均为 succeeded
- stdout_tail 分别包含 `worker-live-low` 和 `worker-live-high`
- workspace 下对应标记文件存在
- 审计中存在 approval.decided 与 run.executed

## 验收结果

结果判定：通过

关键结果摘要：

```text
low_run.status = succeeded
low_run.exit_code = 0
low_run.stdout_tail = worker-live-low

high_run.status = succeeded
high_run.exit_code = 0
high_run.stdout_tail = worker-live-high
high_run.approval_id = f2e5bcef-894f-42ff-84c1-5952d86b393e

low_marker_exists = true
high_marker_exists = true
approval_event_present = true
```

## 暴露并修复的问题

### 1. Windows worker 不能使用默认 RQ Worker

- 现象：启动后在 `os.fork` 处失败
- 根因：默认 `Worker` 依赖 Unix fork
- 修复：在 [worker/worker.py](../worker/worker.py) 中，无 fork 环境自动切换到 `SimpleWorker`

### 2. Windows worker 执行 job 时默认超时实现不可用

- 现象：执行 job 时在 `signal.SIGALRM` 处失败
- 根因：默认 death penalty 使用 Unix signal
- 修复：在 [worker/worker.py](../worker/worker.py) 中，为 `SimpleWorker` 显式设置 `TimerDeathPenalty`

### 3. 审批通过后高风险 run 未重新入队

- 现象：高风险 run 在 approve 后长期停留在 `pending_approval`
- 根因：审批路由重构时丢失了批准后 `run` 重新入队逻辑
- 修复：在 [api/approvals/router.py](../api/approvals/router.py) 恢复对 run 型审批的 `queued + enqueue` 行为

### 4. 自定义数据库路径场景未自动建目录

- 现象：隔离 API 使用嵌套 SQLite 路径启动时报 `unable to open database file`
- 根因：应用启动时缺少 `settings.ensure_dirs()`
- 修复：在 [api/main.py](../api/main.py) 恢复启动前目录初始化

## 回归结果

- worker 相关专项回归：6 passed
- execution flow 专项回归：9 passed
- 全量 regression：40 passed

## 结论

- 本地 Windows 环境下，worker 的真实消费执行链路已通过验收
- 低风险直执行与高风险审批后执行均已闭环
- 后续若进入真实部署环境，建议再补一轮容器内 worker 验收，重点覆盖 Docker executor 与多进程部署行为

补充：同日已完成 Docker Desktop 本地栈下的 host/docker 双执行器闭环，记录见 [worker-live-acceptance-2026-03-10-docker-local.md](worker-live-acceptance-2026-03-10-docker-local.md)

## 补充记录：主库环境 live-worker 复验

- 验收环境：本地开发环境（Windows，默认 Redis + 主数据库）
- API 地址：http://127.0.0.1:8000
- 执行时间：2026-03-10
- 执行人：GitHub Copilot
- 执行器模式：host

执行命令：

```bash
python scripts/release_check.py --mode live-worker --live-api-base http://127.0.0.1:8000 --live-token-file .tmp_live_token.txt --live-worker-executor host
```

结果摘要：

- [x] live preflight 通过
- [x] 低风险 tool-run 真实消费执行通过
- [x] 高风险 tool-run 审批后执行通过
- [x] worker 验收脚本 2 项全部通过
- [x] release-check 返回 all checks passed

补充结论：当前默认本地环境已经完成 API、Redis、worker 三段链路闭环，后续本地联调可直接复用这一套启动方式。