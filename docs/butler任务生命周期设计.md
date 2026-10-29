# Butler 任务生命周期设计：断点续跑 + 审批恢复

> 状态：设计稿 v1 · 2026-08-15
> 背景：用户反馈"任务周期太短，返回第一段信息后就不再继续，后续只能自己慢慢追问；批准的命令还是被拦截"。
> 问题 2（批准被拦截）已单独修复（见文末）。本文解决问题 1：**任务断链**。

---

## 1. 现状与根因

当前 `chat.send` → `run_pydantic_agent_path`（`backend/handlers/chat_paths/pydantic_agent_path.py:67`）执行**一次性事务**：

```
用户消息 → agent.run_stream_events()（最多 MAX_MODEL_REQUESTS=12 次模型请求）
  → 遇到 CONFIRM 工具 → _request_confirmation 返回 "pending_confirmation" 文本
  → 模型把该文本当作"结果"，停止调用工具 → 汇报 → 任务 completed
用户批准 → resolve_confirmation 只执行工具 + 发事件 → 原任务已结束，无人续跑
```

**断链点**：`pending_confirmation` 被当作普通工具结果喂回模型，任务正常结束；批准事件不关联任何运行中的任务，也没有恢复入口。用户只能发下一条消息开启新回合，再靠 `confirmation.status` 查询结果——体验就是"一次只做一步"。

## 2. 参考的成熟设计

| 来源 | 借鉴点 |
|------|--------|
| **LangGraph interrupt()** | 图执行到外部输入点时挂起并保存 checkpoint；resume 时从断点继续，状态零丢失 |
| **Claude Code** | 权限请求是"暂停 → 批准 → 自动继续"；一次会话内任务持续推进直到完成或用户喊停 |
| **DSH harness** | goal 对象（pause/resume/blocked 跨轮次）；审批异步恢复；subagent durable id |
| **ChatGPT 任务 / Cline** | 后台任务队列 + 任务状态卡 UI（执行中/等待批准/已完成） |
| **PydanticAI** | `message_history` 可序列化保存，`run_stream_events` 可随时重新发起续跑 |

共性结论：**任务 ≠ 一次 RPC 流**。任务是有生命周期的实体，审批是任务内的一个"暂停点"，批准后任务继续跑，直到完成。

## 3. 设计

> **关键前提（2026-08-15 复核）**：项目已有 P1-1/P1-2 断点续跑基础设施——
> `assistant/checkpoint.py`（CheckpointManager，LangGraph 式 checkpoint 存储于
> data_store `assistant_checkpoints`）、`assistant/task_manager.py:recover_task`、
> RPC `assistant.unfinished_tasks` / `assistant.resume_task`（`backend/handlers/_trace.py`）、
> 启动扫描事件 `task.resumable`（`backend/headless_main.py:284`）。
> **但该机制只服务 planner 步骤模型（AgentTask + steps + execute_plan），
> butler PydanticAI 路径完全不接入。**
> 本设计**复用既有 checkpoint 存储/扫描/事件/RPC 形态**，仅新增 butler 专用的
> 断点写入与流式恢复执行器，避免平行造轮子。

### 3.1 任务状态机（会话级）

```
                    chat.send（新消息）
 idle ───────────────────────────────▶ running
                                          │  CONFIRM 工具触发审批
                                          ▼
                                   waiting_approval ──批准──▶ running（从断点恢复）
                                          │
                                          ├──拒绝 ──▶ running（注入"用户拒绝"结果，
                                          │             模型调整方案后继续其他步骤；
                                          │             无剩余步骤则自然结束）
                                          └──超时(300s) ──▶ expired（可重新发起）
                                          │
                                          ▼
                         模型输出完成 ──▶ completed / failed
```

约束（v1 串行版）：
- 同一 `session_id` 同一时刻最多一个活跃任务（`running` 或 `waiting_approval`）。
- 任务在 `waiting_approval` 时释放全部运行资源（asyncio 任务退出，只留 checkpoint 数据）。

### 3.2 数据模型：复用 CheckpointManager（`assistant_checkpoints`）

不扩 `task_runs` 表。butler 挂起时调用既有
`CheckpointManager.save_checkpoint(task_id, state=..., context_snapshot=...)`：

| checkpoint 字段 | 值 | 说明 |
|------|------|------|
| `task_id` | `chat-<ts>`（与 trace 一致） | butler 任务唯一 ID |
| `state` | `"waiting_approval"` | 非终端状态；`scan_unfinished_tasks` 天然纳入续跑扫描 |
| `task_type` | `"pydantic_agent"` | 新增约定键，恢复时与 planner 类型分流 |
| `context_snapshot.message_history` | PydanticAI 序列化消息列表 | 断点核心：模型对话状态 |
| `context_snapshot.remaining_requests` | int | 模型请求预算余量（12 - 已用） |
| `context_snapshot.session_id` | str | 归属会话 |
| `context_snapshot.pending_injections` | list | 待注入的已批准工具结果（`{tool, result}`） |

`assistant.unfinished_tasks` / `task.resumable`（启动扫描）无需改动即可覆盖
butler 挂起任务——checkpoint 状态非终端即被扫描到。

### 3.3 断点（interrupt）实现

改动点：`backend/handlers/chat_paths/pydantic_agent_path.py` + `assistant/butler_agent.py`

1. 工具结果事件里识别 `pending_confirmation`（`FunctionToolResultEvent` 的 content 含 `"pending_confirmation": true`）。
2. **不把该文本喂回模型继续循环**，而是：
   - 从 `events` 流取当前 `message_history`（PydanticAI 内部状态），连同 `UsageLimits` 剩余预算、会话 ID、确认 ID 序列化，写入
     `CheckpointManager.save_checkpoint(state="waiting_approval", task_type="pydantic_agent", context_snapshot=...)`。
     CheckpointManager 经 `context.automation.autonomous_assistant_manager.task_manager.checkpoint_manager` 获取（与 planner 路径同源）。
   - 发事件 `task.waiting_approval`（payload: session_id, task_id, confirmation_id, action_id, args 摘要）→ EventBridge 推前端。
   - 正常退出 `run_stream_events`（asyncio 任务结束，资源释放），**不 yield 最终汇报**——任务未完成。
3. `resolve_confirmation`（butler_agent.py:233）执行工具成功后，把结果（批准的实际结果 / 拒绝标记）追加到 checkpoint 的 `context_snapshot.pending_injections`（写回同一 task_id 的新版本 checkpoint），对应 `confirmations` 项标 `approved/rejected`。

### 3.4 恢复（resume）实现：butler 流式 + planner 同步双轨

**planner 任务**（既有）：`assistant.resume_task(task_id)` → `TaskManager.recover_task` → `execute_plan(remaining_steps)`，同步执行返回结果。**不改动。**

**butler 任务**（新增 RPC：**`chat.resume`**，`params: {session_id, task_id?}`）：

```
前端批准按钮回调：
  1. confirmation.resolve(confirmation_id, approved=true)   ← 已有 RPC（M1 已修复拦截）
  2. chat.resume(session_id)                                 ← 新增，复用 chat.send 流式通道
```

后端 `chat.resume` 流程：
1. 从 `assistant_checkpoints` 取该 session 最新 `state=waiting_approval` 且 `task_type=pydantic_agent` 的 checkpoint；无则 `{resumed: false, reason: no_pending_task}`；仍有未处理的确认则 `confirmation_pending`。
2. 反序列化 `message_history`，把最后一条 ModelRequest 中的 tool-return 占位文本**替换为实际结果**（批准 → 真实工具结果；拒绝 → "用户拒绝"标记），模型看到的是真实结果而非 pending 占位（LangGraph interrupt 语义）。
3. 以**剩余预算**重新 `run_stream_events`，chunk 走 chat.send 同一流式管道（前端视角：同一对话继续输出）。
4. `state=running`；再次遇 CONFIRM → 再次挂起（多级审批天然支持）；模型完成 → checkpoint 标 `completed`，yield 最终汇报。

并发保护：`session_id` 级 asyncio 锁；`chat.send` 检测到存在 `running/waiting_approval` 任务时先提示或排队。

### 3.5 前端改动（desk-notes）

- `ChatModule` / `FloatingChatApp`：
  - 订阅 `task.waiting_approval` 事件 → 消息区渲染"任务挂起卡"（tool 名 + 参数摘要 + 批准/拒绝按钮 + 超时倒计时）。
  - 批准按钮点击 → `confirmation.resolve` → 成功后自动 `chat.resume(session_id)`（无需用户再输入）。
  - 拒绝 → `confirmation.resolve(rejected)`，卡片显示"已取消"。
  - 任务状态徽标：执行中 → 等待批准 → 已完成（复用 tool_progress 卡片样式）。
- 任务列表（可选 M3）：复用 `assistant.unfinished_tasks` 渲染历史可续跑任务，支持点选恢复。

### 3.6 边界与降级

| 场景 | 行为 |
|------|------|
| 审批超时（300s，`_CONFIRM_TTL_SECONDS`） | checkpoint 标 `expired`；前端卡片显示"已过期"，可点"重新发起"（重发原消息） |
| 拒绝一次确认 | 注入"用户拒绝"结果，模型调整方案后继续其他步骤（多确认场景不中断整个任务） |
| 后端重启 | checkpoint 已在 SQLite，`chat.resume` / 启动 `task.resumable` 扫描均可恢复（P1-1 既有目标） |
| 模型请求预算耗尽 | 续跑预算 = 12 - 已用；耗尽则 `failed` 并提示"任务未完成，可继续追问" |
| 多窗口同一会话 | 锁 + 唯一活跃任务约束，后到者返回冲突提示 |
| `chat.send` 新消息覆盖挂起任务 | 允许（用户主动开新任务），旧 checkpoint 标 `cancelled` 并保留 |

## 4. 阶段计划

| 阶段 | 内容 | 落点 |
|------|------|------|
| **M1（已完成）** | 批准后被拦截修复 + `file.delete` 走能力域 | `core/action_guard.py`、`assistant/action_broker.py`、`assistant/capability_router.py`、`assistant/butler_agent.py`、`assistant/capability/_file_ops.py` |
| **M2（已完成）** | 断点续跑 v1：butler 挂起写 checkpoint + `chat.resume` 流式恢复 + 前端批准后自动续跑 | `assistant/butler_checkpoint.py`（新）、`backend/handlers/chat_paths/pydantic_agent_path.py`（挂起/恢复/共享循环）、`assistant/butler_agent.py`（确认结果记录）、`backend/handlers/_chat_memory.py`（注册 `chat.resume`）、`desk-notes`：`useChatStream.ts`（resumeChatStream）、`ChatView.tsx`（批准→自动 resume）、`shared/ws-client/types.ts`（chat_resume_status） |
| **M3** | 并行任务树 + 任务列表 UI（`assistant.unfinished_tasks`）+ 暂停/取消 | `desk-notes` 任务面板、`_trace.py` 扩展 |
| **M4** | goal 语义对齐（任务级 pause/resume/blocked）、工作流脚本、子代理树 | 与 DSH 对齐层 |

## 5. 验收标准（M2）

1. 对管家说"删除桌面上所有 0 字节空文件"：
   - agent 用 `file.read` 找空文件 → 列出 → 发起 `file.delete`（弹确认）
   - 点批准 → **不追问**，agent 自动继续删除全部 → 最终汇报结果
2. 多级审批：任务连续触发两次确认，两次批准后任务继续到完成。
3. 拒绝一次确认 → 任务取消，其余步骤不再执行。
4. 审批超时 → 卡片显示过期；重启后端后 `chat.resume` 仍可恢复。

---

## 附：M1 修复记录（问题 2）

- `core/action_guard.py`：`validate(..., approved=False)`——`approved=True` 时跳过 LLM 高危拦截（人工确认是最高授权），记录 `assistant.action.human_approved` 审计事件。
- `assistant/action_broker.py`：`ActionRequest` 新增 `approved` 字段并透传 guard；`needs_confirmation` 对 `approved` 请求跳过。
- `assistant/capability_router.py`：`execute_direct(..., approved=False)` 透传；`execute()` 构造请求时读取。
- `assistant/butler_agent.py`：`resolve_confirmation` 批准后 `execute_direct(..., approved=True)`；`SAFE_TOOLS` 新增 `file.delete`。
- `assistant/capability/_file_ops.py`：新增 `file_delete`（路径白名单 `_resolve_read_path` 保护，非空目录拒绝，写审计快照）。
- 测试：`test_action_guard.py` +2、`test_butler_tools_upgrade.py` +3，全量相关 117 passed。
