<!-- 状态：✅ 现行（纠正说明，照此维护） -->
# 执行相关模块真实分层（纠正说明）

> **状态：✅ 现行**
> **日期：** 2026-08-23
> **性质：** 对外部评估/报告中"四套执行引擎并存、应标记 deprecated"论断的代码级纠正。
> **目的：** 记录执行相关模块的真实角色与依赖，避免后续 AI 协作者或重构会话重蹈"把活跃模块当死代码"的误判。

---

## 0. 背景

深度评估报告将"执行引擎四套并存无收口"列为 P1 结构性债务，并建议把
`assistant/react_engine.py` 与 `assistant/multi_agent_orchestrator.py` 标记 deprecated。
经 2026-08-23 对仓库实际代码的核实，**该论断的前提有误**——这四份文件并非四份
冗余副本，而是**三种不同角色 + 一个设计内 shim**，各自接在不同调用链上。
直接标记 deprecated 会切断运行链路。

---

## 1. 真实地图：三种角色 + 一个 shim

| 文件 | 行数 | 真实角色 | 活跃调用方（grep 核实） |
|---|---|---|---|
| `services/react_executor.py` | 1076 | **ReAct 执行器**：真正执行工具循环、被 sandbox / 多 agent 调用 | `backend/handlers/_sandbox.py`、`backend/react_executor.py`(shim)、`services/sandbox_manager.py`、`services/react_tool_catalog.py`、多份测试 |
| `backend/react_executor.py` | 31 | **设计内 shim**（P0-12）：re-export `services.react_executor` 等以兼容历史调用方，**不可立即删** | 历史 `backend/*` 与测试的兼容导入 |
| `assistant/react_engine.py` | 343 | **聊天流 ReAct 循环层**：在聊天流中启发式判断是否走工具循环（与 executor 不是同一层） | `assistant/execution_router.py`、`assistant/autonomy.py`、`app_runtime/ai_client_consumers.py`、`autonomy_components/lifecycle_builder.py` |
| `assistant/multi_agent_orchestrator.py` | 926 | **多 agent 编排器**：协调子 agent / 黑板 | `app_runtime/_context_builder.py`、`backend/handlers/_chat_memory.py`、`services/react_tool_catalog.py` |

**关键结论：** 后两个是活模块，承载执行路由与上下文构建，绝非死代码。四份文件并存是
设计内分层（executor / engine / orchestrator / shim），不是需要"收口删除"的重复实现。

---

## 2. 收口结论（修订版，替代报告原 W1）

- **不**将 `react_engine.py` / `multi_agent_orchestrator.py` 标 deprecated——会崩。
- `backend/react_executor.py` 作为 shim 可保留；若未来要删，须先迁移其约 10 个调用方
  （主要是 `backend/*` 与测试）到 `services.*` 直引，再删除。属低优先级清理。
- "四套并存"的**真正**待办是：确认 `react_engine`（聊天流拦截）与 `services/react_executor`
  （执行器）之间是否仍存在行为重叠；若有，做**行为合并**而非简单删文件。当前代码未见重叠证据。

---

## 3. automation_hub 裁剪的隐藏依赖（重要）

`docs/automation-hub-disposition.md` 将 `worker/` 列入删除清单（"无当前消费方"），
但 **hub 自身的 API 仍在 import `worker`**，删除会导致 hub 启动即 `ImportError`：

| 引用点 | 导入 |
|---|---|
| `automation_hub/api/approvals/router.py:10` | `from automation_hub.worker.arq_settings import enqueue_job_sync` |
| `automation_hub/api/proposals/router.py:36-37` | `worker.jobs_v2` / `worker.policy_enforce` |
| `automation_hub/api/routes/agent.py:37` | `worker.arq_settings.enqueue_job` |
| `automation_hub/api/routes/metrics.py:16` | `worker.arq_settings.queue_length` |
| `automation_hub/api/routes/mobile_ws.py:285` | `worker.arq_settings.enqueue_job_sync` |
| `automation_hub/api/routes/runs.py:29` | `worker.jobs.execute_script_job` 等 |
| `automation_hub/api/routes/tool_runs.py:37` | `worker.arq_settings.enqueue_job_sync` |

**正确顺序（修订版 W2）：**
1. 先把 `api/approvals`、`api/proposals`、`api/routes/{agent,metrics,mobile_ws,runs,tool_runs}`
   从 `worker` 解耦（或将 `worker` 的核心作业执行逻辑下沉为 hub 内部服务）；
2. 解耦完成后再裁 `deploy/` `intel/` `repos/` 等无引用目录；
3. `worker/` **暂留**——它实际撑着审批/提议/agent 执行核心，属 ADR 声称要"保留"的价值核。

> 这也意味着原报告的"W2 删 worker/intel/repos"若照做会破坏 hub。裁剪前务必先跑
> `grep -rn "automation_hub.worker" automation_hub/api` 确认依赖已清零。

---

## 4. 已完成的收口（避免重复劳动）

`思忆集app/integrations/automation_hub_client.py`（365 行）**已是干净泛型实现**：
- 仅 `_ENTITY_ENDPOINTS` 单一定义源（6 类：`note/task/news/translation/wordbook/word`）；
- 无 `__getattr__` 动态生成的 ~100 行具名方法死代码；
- 对外仅 `pull/pull_all/upsert/delete` 泛型方法（兼容别名 `_pull/_upsert/_delete` 仅委托）。

即 ADR `automation-hub-disposition.md` 阶段 A 的"客户端收口 / 修 9:6 契约漂移 / 乱码修复"
任务**已完成**，无需重做。新协作者勿再按 ADR 旧描述去"清理"该文件。

---

## 5. 行为测试安全网（拆分大文件前的护栏）

阶段二 W3 计划拆分 `sync/cloud_sync_client.py`(1415) 与 `services/info_collector_service.py`(1382)。
按"拆前先补行为测试"原则，已为两文件的纯函数建立测试：

- `tests/test_cloud_sync_client_crypto.py` — 加解密往返、v4 同组跨设备可互解 / 跨组·错 token 必失败、
  `_detect_conflict` 真冲突判据、`_normalize_ts` 多格式归一（19 项，全绿）。
- `tests/test_info_collector_dedup.py` — `dedup_items` 的 URL 归一 / 标题相似度去重 / 无 url 回退 id、
  `title_similarity` / `_strip_html` 边界（12 项，全绿）。

拆分这两个大文件时，先确保上述测试继续通过，再迁移逻辑。

---

## 6. 维护提醒

- 任何把"执行引擎"当单一概念、建议"合并/删除其中 N 个"的提案，先回来核对第 1 节的真实地图。
- 任何 automation_hub 删除提案，先核对第 3 节的 `worker` 依赖清单。
- 本文档随代码变更同步修订（例如某日 `react_engine` 与 `react_executor` 真合并时，更新第 1 节）。
