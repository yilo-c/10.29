# automation_hub：API 与 Worker 软解耦（阶段二 W2 收口）

> 状态：✅ 现行（2026-08-23 完成）
> 关联：[automation-hub-disposition.md](automation-hub-disposition.md)（hub 处置 ADR）、[architecture-execution-engines.md](architecture-execution-engines.md)（引擎分层纠正）

## 背景：原 ADR 与现实代码冲突

`docs/automation-hub-disposition.md` 判定 `worker/` "无消费方，可删"。但实测
推翻了这一判断——**hub 自身 API 仍在顶层 import worker**：

| 文件 | 顶层 import | 用途 |
|---|---|---|
| `api/approvals/router.py` | `worker.arq_settings.enqueue_job_sync` | 审批决策后入队通知 |
| `api/proposals/router.py` | `worker.jobs_v2.run_tool_job` / `worker.policy_enforce.is_proposal_approved` | 提议执行 / 策略判定 |
| `api/routes/runs.py` | `worker.jobs.execute_script_job` / `load_scripts_manifest` | 脚本执行 / 清单加载 |
| `api/routes/tool_runs.py` | `worker.arq_settings.enqueue_job_sync` | 工具运行入队 |
| `api/routes/metrics.py` | `worker.arq_settings.queue_length` | 队列长度指标 |
| `api/routes/agent.py` | `worker.arq_settings.enqueue_job` | agent 任务入队 |

而 `worker.arq_settings` 顶层 import `arq` + `redis`，worker 链又反向依赖
`worker.jobs_v2`。结果是：只要 Redis/arq/worker 链任一缺失，`import
api.main` 立即 `ImportError`，**Hub 无法独立启动**，ADR 里判定可删的
`deploy/intel/repos` 也因此无法干净裁掉（会牵连 `api.main` 导入）。

## 收口动作（可逆，不删业务代码）

新增 `api/_worker_bridge.py`：把上述 6 处"顶层硬 import"改为"调用时延迟
import"。worker 本体**暂不删除**（它仍撑着审批/提议的 ARQ 入队核心）。

桥接语义：
- worker 在场 → 一切照旧（函数转发）；
- worker 不在场（`ImportError`）→ 抛出 `WorkerUnavailableError`（明确报错），
  而非静默返回 `None`，避免掩盖部署缺失。

改动文件：
- 新增 `api/_worker_bridge.py`
- 改 `api/approvals/router.py`、`api/proposals/router.py`、`api/routes/runs.py`、
  `api/routes/tool_runs.py`、`api/routes/metrics.py`、`api/routes/agent.py`
  的顶层 import 指向 bridge

新增回归测试 `tests/test_worker_bridge.py`（3 项）：
1. 模拟 worker 缺失时 `import api.main` 仍成功；
2. worker 在场时桥接函数可加载；
3. worker 缺失时桥接抛 `WorkerUnavailableError`。

## 验证

- 模拟 worker 缺失：`mv worker _worker_disabled && python -c "import api.main"` → OK，已还原
- hub 全量测试：`185 passed`（含本次新增 3 项，含 28 项直接相关的审批/提议/指标路由测试）

## 后续

1. **现在可安全裁 `deploy/intel/repos`**：因 `api.main` 不再顶层依赖 worker，
   删这些目录不会破坏 hub 启动（仍需确认它们自身不反向被 worker 引用）。
2. worker 本体去留：待审批/提议的 ARQ 入队机制确认可由 app 侧
   `integrations/automation_hub_client.py` 接管或废弃后再决定。
