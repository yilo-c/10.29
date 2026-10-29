# Agent 改进方案：借鉴 Claude Code（单循环体验）与 DSH（编排深度）

> 目标：补齐思忆集 butler 单循环的交互精度，同时把 multi_agent_orchestrator 升级到持久化编排深度。
> 原则：所有新服务走 P3.2 DI 模式（`headless_main.py` 显式注入 + AppContext property 槽位，禁止 handler 懒构建）；执行一律经 `ActionBroker` 闸门；事件一律经 EventBus；同步 IO 在异步 handler 中包 `asyncio.to_thread`；每个里程碑回归测试基线（3450 用例）。

---

## 总体策略：两条主线

| 主线 | 内容 | 对应条目 | 目标 |
|---|---|---|---|
| A 线：单循环体验 | 安全与交互精度 | A1 hooks、A2 路径权限、A3 git 快照、A4 plan UX、A5 分层压缩 | butler 单循环达到 Claude Code 的成品度 |
| B 线：编排深度 | 持久化与可组合 | B1 持久子代理、B2 脚本工作流、B3 goal、B4 技能目录、B5 双端插件、B6 沙箱 UX | orchestrator 达到 DSH 的运行时深度 |

依赖关系：A1 与 A2 共享规则匹配引擎 → 先做；B1 是 B2 的执行底座 → B1 先于 B2；A3 依赖 A1 的 post_tool 钩子做自动记录 → A1 先于 A3。

---

## A 线：单循环体验

### A1. 声明式生命周期钩子（优先级：P0，工作量：中）

**现状**：`assistant/action_broker.py` 的 `execute()` 是固定闸门链（CapabilityRegistry → Invocation → ActionGuard → Executor → Receipt），无用户可配置钩子；audit 在回执层、事件走 EventBus，但均不可插拔。

**设计**：新建 `services/hook_registry.py` — `HookRegistry`：

- 钩子定义（JSON 配置，风格对齐 `data/tool_specs.json`，存储于 settings key `agent_hooks`）：
```json
[
  {"id": "h1", "event": "pre_tool",  "action": "deny",  "pattern": "shell.run", "source": "llm", "reason": "LLM 不得直跑 shell"},
  {"id": "h2", "event": "on_error",  "action": "retry", "pattern": "web.fetch", "max_retries": 2, "backoff_seconds": 1.5},
  {"id": "h3", "event": "post_tool", "action": "notify", "pattern": "notes.delete", "channel": "toast"}
]
```
- 事件：`pre_tool`（可阻断/改参）、`post_tool`（记档/通知）、`on_error`（重试/告警）。
- 动作：`allow / deny / retry / audit / notify`，短路语义 `deny > allow > audit`，同优先级按配置顺序。
- 接口：`evaluate(event, request, result) -> list[HookDecision]`；`dry_run(request)` 供前端预览。
- 安全约束：`retry` 只对幂等/重试安全的 capability 生效（白名单校验，仿 `SAFE_TOOLS`）；retry 次数计入 butler 的 `MAX_MODEL_REQUESTS` 预算，防无限循环。

**接入点**：`ActionBroker.execute()` 在 `_prepare_and_validate()` 前插入 pre_tool 求值；executor 完成后调 post_tool；`except` 路径调 on_error。注入方式：`headless_main.py` 构造并 `context.hook_registry = ...`，ActionBroker 经 `getattr(context, "hook_registry", None)` 读取。

**RPC**：`backend/handlers/_hooks.py` → `hooks.list / hooks.set / hooks.test`（dry-run 评估，不实际执行）。

**测试**：`services/tests/test_hook_registry.py`（短路语义、retry 上限、pattern 匹配）+ 与 ActionBroker 的集成用例。

---

### A2. 路径级权限规则（优先级：P0，工作量：中）

**现状**：`services/sandbox_manager.py` 只有"批准/拒绝"二元 pending 队列 + 超时策略（P1-5）；capability 有风险分级白名单，但无"按文件路径放行/拦截"的规则。

**设计**：新建 `services/path_policy.py` — `PathPolicyEngine`：

- 规则（settings key `path_policies`，提供 `data/path_policies.sample.json`）：
```json
[
  {"id": "p1", "path_glob": "思忆集app/**",        "tools": ["file.edit", "file.write"], "mode": "allow", "source": "llm"},
  {"id": "p2", "path_glob": "**/*.env",           "tools": ["file.read"],               "mode": "block", "source": "llm"},
  {"id": "p3", "path_glob": "data/**",            "tools": ["notes.delete"],            "mode": "confirm", "source": "user"}
]
```
- 匹配：`fnmatch` glob（支持 `**`），从 `request.input` 提取文件字段（`file_path / note_path / path`）做路径解析；`block > confirm > allow`；未命中回退现有 RiskLevel 逻辑。
- 接入位置：`ActionBroker` 的 ActionGuard 之后、sandbox 之前；LLM 来源的 `file.*` 命中 `allow` 则跳过沙箱直接放行（大幅降低审批噪音）。

**RPC**：`sandbox.policies.list / sandbox.policies.set`（挂 `_sandbox.py` 或独立 `_path_policy.py`）。

**注意**：A2 与 A1 是"专用规则引擎 vs 通用钩子"，保留两个引擎但共享匹配/求值工具函数（放 `services/rule_engine.py` 或 `hook_registry` 内公共函数），避免重复实现。

---

### A3. 任务级 git 快照回退（优先级：P1，工作量：中）

**现状**：`assistant/cc_bridge.py` 执行 claude.exe 前后无快照；`core/audit_rollback.py` 是 RPC 级回滚（不适合代码文件的批量改动）。

**设计**：新建 `services/git_snapshot.py` — `GitSnapshotService`：

- `snapshot(repo_path, label) -> snapshot_id`：检测 git 仓库 → `git stash create` 或 `commit-tree`（不动工作区），把 commit SHA + label + repo 写入 SQLite 表 `snapshots`（复用 trace_recorder 的 db 或新 db `data/snapshots.db`）。
- `restore(snapshot_id)`：`git checkout <sha>` 恢复工作区（**破坏性操作，必须走 sandbox 审批**）。
- `list() / prune(older_than_days)`；非 git 目录降级返回 `available=False`（P1 不做 zip 快照）。

**接入**：
1. `cc_bridge.execute()` 启动子进程前自动 `snapshot()`，结果写入返回结构；
2. 通过 A1 的 post_tool 钩子对 `file.edit/file.write` 批量会话按"任务粒度"打点（默认只对 cc_bridge / codex 自动开启，普通 butler 会话需配置开启，避免快照噪音）。

**RPC**：`snapshot.list / snapshot.create / snapshot.restore`（`backend/handlers/_snapshot.py`）。

---

### A4. Plan 模式 UX 闭环（优先级：P1，工作量：大，前后端）

**现状**：`assistant/execution_plan.py` 已有 `ExecutionPlan / ExecutionPlanStep / PlanStatus`（pending/approved/rejected/edited/expired）与风险分级、耗时估算，但**无 RPC 暴露、无前端交互**。

**设计**：
1. **后端**：新建 `backend/handlers/_plan.py` → `plan.create / plan.list / plan.get / plan.approve / plan.reject / plan.edit`；计划状态变化发事件 `plan.created / plan.approved / plan.rejected`（EventBridge 自动转发）。
2. **前端（desk-notes）**：新组件 `PlanCard`：步骤列表（风险标签 + 预估耗时 + 影响说明 + 每步 enable 开关）→ 全部批准 / 逐条编辑 / 拒绝；挂在 chat 消息流，订阅 `plan.*` 事件。
3. **接入**：Judge 判定 complex → 调 `plan.create`（已有 `execution_plan` 生成逻辑）→ 前端卡片 → 批准后按步骤执行（每步仍走 ActionBroker，保留单步审批语义）。

**与 butler confirmation 队列的边界**：plan 是多步计划的整体确认；confirmation 是单操作的实时确认，二者并存、互不替代。

---

### A5. 分层上下文压缩（优先级：P1，工作量：中）

**现状**：`assistant/context_compressor.py` 只有 `compress_text_head_tail`（头尾保留）与 `TokenEstimator`；`chat.compress_context` RPC 已存在；多 agent 编排用头尾压缩兜底。

**设计**：在 `context_compressor.py` 新增 `compress_conversation_tiered(messages, token_budget)`：

- **Tier 1**：单条超长消息 → 现有头尾压缩。
- **Tier 2**：多轮早期消息 → 生成 AI 摘要（优先本地 qwen，规则兜底——与 `_chat_context_anchor.py::_preference_summary()` 的聚合策略一致），摘要 + 最近 N 条原文。
- **Tier 3**：会话级 → 结构化要点抽取（按域：目标 / 已做 / 结果 / 待办，供 resume 使用）。
- 分层策略：优先 Tier 2 摘要，仍超预算再 Tier 1 裁剪；全程记录 `token_estimate` 与节省统计（复用现有压缩统计口径）。

**接入**：butler 循环内超预算时触发；`chat.compress_context` 改用新策略；`multi_agent_orchestrator._compress_output` 保留但作为 Tier 1 兜底。

---

## B 线：编排深度

### B1. 可续聊的持久子代理树（优先级：P0，工作量：大）

**现状**：`assistant/multi_agent_orchestrator.py` 的 SubAgent 是进程内对象（`_agents` 字典 + 内存结果缓存 P3-11）；`trace_recorder.py` 已用 SQLite 记录 `task_runs + exec_steps`；butler 已有 `subagent.spawn / subagent.list` 工具。

**设计**：
1. **持久化**：在 trace_recorder 的 db 中新增表 `subagent_runs`：
```sql
CREATE TABLE subagent_runs (
  subagent_id TEXT PRIMARY KEY,
  parent_id   TEXT,           -- 父代理（可空 = 根）
  session_id  TEXT,
  agent_name  TEXT,           -- browser/coder/researcher/自定义
  task        TEXT,
  status      TEXT,           -- running/idle/completed/failed
  result      TEXT,           -- 最终输出（completed 后写入）
  error       TEXT,
  continuation TEXT,          -- 追加消息列表（JSON）
  created_at  REAL, finished_at REAL
);
```
2. **接口扩展**（`MultiAgentOrchestrator`）：
   - `spawn(config, task, parent_id=None) -> subagent_id`（写库 + 启动执行）
   - `send_message(subagent_id, msg)` — 从库恢复 run 上下文 → 追加消息 → 再执行一轮（等价 DSH `send_message` 续聊）
   - `list(scope="children"|"descendants") -> 子树枚举`；`get(subagent_id)`；`interrupt(subagent_id)`
3. **执行路径**：SubAgent 仍复用 `make_react_executor` / `stream_chat_with_tools`；执行期间每步经 `exec.step` 事件上报（trace_recorder 已订阅），`task_id = subagent_id` 即得到完整步骤历史，无需新观测通道。
4. **并发控制**：新增进程内 asyncio 任务注册表（`_running: dict[subagent_id, asyncio.Task]`），持久化与并发分离：内存管"正在跑"，SQLite 管"可续聊"。

**RPC**：扩展 `backend/handlers/_agent_tasks.py` 或独立 `_subagent.py` → `subagent.spawn / subagent.send / subagent.list / subagent.get / subagent.interrupt`（保持与 butler 工具同名兼容）。

**测试**：持久化往返（spawn → 进程重启 → send_message 续跑）、子树枚举、interrupt 语义。

---

### B2. 脚本化工作流 + Schema 校验（优先级：P1，工作量：大）

**现状**：`workflow/execution_engine.py` 是 DAG 画布模型（trigger/ai_call/tool_action/condition/delivery，拓扑排序并行）；无脚本化编排、无结构化输入输出校验。

**设计**：新建 `workflow/script_engine.py` — `run_script(script, args)`，执行底座复用 B1 的 subagent 执行器：

- 脚本定义（JSON，可存 `data/workflow_scripts/`）：
```json
{
  "name": "audit-files",
  "stages": [
    {"title": "扫描", "prompt": "列出 {path} 下的可疑文件", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}, "output_schema": {"type": "array", "items": {"type": "string"}}},
    {"title": "复核", "prompt": "对 {items} 逐项复核", "input_schema": {"$ref": "prev"}, "output_schema": {"type": "object"}}
  ],
  "max_concurrency": 4
}
```
- 语义：阶段间默认串行（下一阶段输入取上一阶段输出），支持 fan-out（对数组逐项派并发 subagent，等全部完成后进入下一阶段——barrier）；每个阶段输出按 `output_schema` 校验，不合法重试一次，仍失败则该 item 置 null 不阻断（对齐 DSH workflow 的容错语义）。
- **与 DAG 的关系**：DAG 是可视化配置、script 是代码化编排，两者共用 B1 执行器层与 trace 记录；`WorkflowNode` 新增 `node_type="script_stage"` 可把脚本挂进画布。

**RPC**：`workflow.run_script / workflow.script.validate / workflow.scripts.list`。

---

### B3. 持久目标对象 GoalService（优先级：P1，工作量：中）

**现状**：有 `agent_tasks`（单次任务）+ `assistant.resume_task`（checkpoint 断点续跑）与 `checkpoint.py`；无"目标级"持久对象（修订号、pause/resume/blocked 语义）。

**设计**：新建 `services/goal_service.py` — `GoalService`（SQLite 表 `goals`）：

```sql
CREATE TABLE goals (
  goal_id TEXT PRIMARY KEY, objective TEXT,
  status TEXT,                -- active/paused/completed/blocked
  revision INTEGER DEFAULT 1,
  phase TEXT,                 -- 当前阶段描述
  max_rounds INTEGER, completed_rounds INTEGER DEFAULT 0,
  blocked_reason TEXT DEFAULT '',
  created_at REAL, updated_at REAL
);
```
- API：`create / get / update(action=edit|pause|resume|complete|blocked, ...)`；blocked 需连续 3 轮同因阻塞（对齐 DSH goal 语义），blocked_reason 必填具体条件。
- **与 agent_tasks 的边界**：goal 是"跨轮次目标"，内部驱动多个 agent_tasks / subagent 轮次；`resume_task` 是单任务的断点续跑，goal 复用其 checkpoint 机制做每轮推进。
- **接入**：常驻管家（`assistant/autonomy.py`）循环扫描 active goals，每轮推进一次并 bump revision；headless 启动时扫描 active goals（对齐现有 `task.resumable` 事件模式，发 `goal.resumable` 事件）。

**RPC**：`goal.create / goal.get / goal.list / goal.update`。

---

### B4. 技能目录 SkillRegistry（优先级：P0，工作量：中 + 内容分批）

**现状**：`assistant/persona/`（persona_definition + persona_injector）是静态人格注入；`capability_registry` 是能力注册；butler 的 system prompt 是硬编码单块文本。

**设计**：新建 `services/skill_registry.py` — `SkillRegistry`：

- Skill 定义（`data/skills/<skill_id>.md`，front-matter + 正文，对齐 DSH 技能形态）：
```markdown
---
skill_id: notes-organize
name: 笔记整理
description: 批量整理笔记、打标签、去重、归档。用户提到"整理笔记/归档/打标签"时使用。
trigger_patterns: [整理笔记, 归档, 打标签, 批量, 去重]
tools: [notes.list, notes.update, notes.tag, notes.batch_delete]
---
指令正文（分节：目标 / 流程 / 边界 / 示例）
```
- 匹配：用户输入或 Judge 意图 → 按 `description` + `trigger_patterns` 打分 → 选中技能在 persona 之后注入 system prompt（复用 `persona_injector` 的注入通道，扩展为 persona + skills 两段）。
- 技能可声明依赖 capability（不满足则提示而非注入）；与 hook（A1）联动：技能可自带默认钩子。
- 内容：首批 5-8 个高频域技能卡（笔记整理 / 任务管理 / 资讯简报 / 翻译学习 / 文件操作 / 收件箱处理），后续按 46 个 handler 域滚动补齐。

**RPC**：`skill.list / skill.get / skill.reload`。

---

### B5. 双端插件运行时 + 热更新（优先级：P2，工作量：很大，分 3 个里程碑）

**现状**：`plugins/` 已有相当基础 —— `manifest.py`（PluginManifest + 校验）、`permissions.py`（PermissionManager）、`registry.py`（install/enable/disable/uninstall 生命周期 + 与 CapabilityRegistry 集成）、`protocol.py`（ExternalCapabilityProvider / CardPluginSpec 等）。缺口：Host/Client 双端、UI 插槽、不可变版本、热更新。

**设计（分阶段）**：
1. **M5-1 后端版本化 + 热更新**：PluginRegistry 增加"不可变版本"语义（`plugin.packages` 版本列表，安装新版本不覆盖旧版本，可回滚——对齐 DSH cordis_define 的 immutable package）；enable/disable/update 不重启进程（registry.reload + 注销旧 side effect，复用 EventBus 的订阅/退订模式）；新增 `plugin.packages.list / plugin.update / plugin.rollback` RPC。
2. **M5-2 前端插槽**：在 `CardPluginSpec` 基础上定义统一 UI 插槽注册表（chat_toolbar / workbench_card / floating_assistant / settings_panel）；Electron 端启动时经 WS 拉取插件 manifest + 插槽清单，渲染注册的 React 组件（对齐 DSH Slot 注册模型）；桌面↔后端经现有 WS 桥 + `plugin.*` RPC 通信。
3. **M5-3 跨端统一注册表**：automation_hub 作为授权/审计层（已有 approvals/audit/security），插件 manifest 数据模型与 hub 对齐（共享 schema），桌面/手机端从 hub 同步插件配置。

**约束**：`ExternalCapabilityProvider` 协议保持向后兼容，新运行时在其上叠加。

---

### B6. 沙箱升级 UX（优先级：P2，工作量：中）

**现状**：`_sandbox.py` 已有 `sandbox.preview/approve/reject/list_pending/batch_approve`；pending 条目用 secrets ID；超时策略 P1-5 已有。

**设计**：
1. **最窄放宽 + 一次性理由**：pending 条目新增 `escalation` 字段（本次操作所需的最窄沙箱模式，如 file.edit → workspace-write）；审批接口 `sandbox.approve(pending_id, reason="")` 的 reason 必填（写入审计）；等价 DSH 的"一次性 escalation，理由随行"。
2. **失败即停**：同一 capability+目标被拒绝后，30 分钟内 LLM 侧不自动重试同类操作（sandbox 内记录拒绝指纹）；防 AI 反复试探。
3. **前端**：审批卡片显示"建议模式 + 理由输入框 + 拒绝原因选项"；已由 `sandbox.timeout_warning/timeout` 事件覆盖的到期提示保留。

**与 A2 的关系**：A2 的 path_policy `allow` 先过滤大部分低危路径操作（减少审批噪音），B6 优化真正需要审批的交互本身。

---

## 实施路线图

| 里程碑 | 内容 | 依赖 | 预估 |
|---|---|---|---|
| M1（安全层） | A1 hooks + A2 path_policy + B4 skill 框架 | 无 | 2 周 |
| M2（单循环） | A3 快照 + A5 压缩升级 + B3 goal | M1（A3 用 post_tool 钩子） | 2-3 周 |
| M3（编排核心） | B1 持久子代理 + B2 脚本工作流 | M2（trace 扩展先行） | 3-4 周 |
| M4（交互闭环） | A4 plan UX（前端）+ B6 沙箱 UX（前端） | M1 | 2-3 周 |
| M5（插件运行时） | B5 双端插件（3 个子里程碑滚动） | M3 后端能力 | 独立演进 |

**每个里程碑的验收**：
- 根级 pytest 全绿（3450 基线），新增模块配套单测；
- 新服务遵循 P3.2 DI（`headless_main.py` 注入 + AppContext 槽位），无 handler 懒构建新增；
- RPC 方法注册进 `method_registry` 并更新 AGENTS.md 方法表；
- WS 冒烟套件覆盖新增只读方法。

## 关键风险

1. **retry 与预算叠加**：A1 的 on_error retry 必须计入 butler `MAX_MODEL_REQUESTS`（当前 12），否则无限循环；实现时在 HookDecision 中带回 `budget_cost`。
2. **B1 并发执行**：现 orchestrator 是进程内 await 语义，持久化改造需引入 asyncio 任务注册表；多轮 send_message 需处理"上一轮仍在跑"的排队（对齐 DSH 的 parked message 语义）。
3. **A3 快照噪音**：git stash create 对高频 file.edit 会话开销大，默认只对 cc_bridge/codex 自动开启。
4. **A2 路径解析**：需统一提取工具输入中的路径字段（file_path/note_path/path），与 `url_learning` 的路径遍历防护（basename + `..` 检查）保持一致口径。
5. **B4 注入长度**：技能指令注入会增加 prompt 预算，单技能正文限长 + 最多注入 2 个技能（打分最高两个），超限走压缩。
