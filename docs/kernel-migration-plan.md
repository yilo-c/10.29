# 思忆集 · 执行内核迁移施工手册（kernel-migration-plan）

> **性质**：活文档。每完成一项：勾选状态 + 在 `kernel-journal.md` 登记。粒度 = 接口级（签名 / 数据流 / 文件落点 / DoD / 回滚）。
> **路线**：A1（决议 D6）--butler+PydanticAI 整体换 DSH harness，换循环不换模型；前置 V6 三关实测闸门。
> 契约依据：`kernel-contract.md` v1.2（Zone 边界 §1.1 / 三级路由 §2.5 / prompt 组装 §2.6 / ADR §9 / OQ §10）。
> **执行手册**：[`kernel-execution-guide.md`](kernel-execution-guide.md)--自动驾驶级步骤（代码骨架/命令/DoD），本手册定接口与顺序，执行手册给每一步的敲法。
> **模型**：hy3（腾讯混元3 免费）可行性矩阵与接入见执行手册 §2；内核默认 deepseek-v4-flash（DSH 内置）。

---

## 0. 当前状态仪表盘

| 里程碑 | 内容 | 状态 | 依赖 |
|---|---|---|---|
| M1 | AgentGateway 端口 + 4 适配器 + factory | ✅ 完成（22 测试绿） | - |
| M3 | MCP Server 10 工具 + 生产 profile | ✅ 完成（16 测试绿 + 端到端） | - |
| **V6** | DSH 三关实测：流式 / 人设 / 审批 | ✅ 完成（2026-08-15，见 kernel-journal 第九轮） | - |
| **S1 稳定区加固** | cron_actions 迁移（调度层脱内核）+ M1.5 AiTextService | 🔓 已解锁（核心 §5.1/§5.2 已落地，余兼容分支 M5 清） | - |
| M2a | 协议 v2 + ConfirmationStore + siyi_confirm_* + 契约测试 | ✅ 完成（102 测试绿） | 契约 v1.2 |
| M-cron | cron `kernel.*` action 直走 gateway | ✅ 完成（4 测试绿） | M2a（部分）∥ S1 |
| M-mcp | MCP P0 工具 12 个补齐 | ✅ 完成（13 工具全注册，39 契约/回归测试绿） | M2a ∥ M-cron |
| M2b | gateway_path + 三级路由 + chat 灰度 | ✅ 完成（gateway_path + 三级路由 + chat.resume 网关续跑，37 测试绿） | V6 + M2a + M-mcp |
| 翻转 | chat_route 默认 gateway（代码已落），butler 降兜底；观察期前提见 §7 | ✅ 完成（2026-08-16，commit a39af4b5 已切 `chat_route=gateway`；**观察期已开启（2026-08-16 起）**，见 §7） | M2b |
| M4 | DSH 双 profile + 技能卡 + MCP P1（桌面/浏览器） | 🔓 进行中（2026-08-16：双 profile 已建 + settings→profile 映射已落地并测试；**技能卡 4 张已建并经 cron→kernel 接通（9 测试绿）**；MCP P1(OQ2 待决) 待续） | 翻转后 |
| M5 | assistant/ 退役 + orchestration 收敛 + 终态验收 | 🔓 进行中（2026-08-16：基线已测；核心退役待观察期 + S1；安全准备/标注类改动可执行） | S1 + 观察期 |
| **L1 词汇统一** | params.kernel 词汇一致性（契约 CT-13）：`fast/deep/shell/legacy` ↔ 前端选择器 ↔ 后端 `resolve_dsh_profile` | 🔴 **P0**（2026-08-20 核验登记：三套词汇互不匹配——契约 `fast/deep/shell/legacy` vs 前端 `auto/dsh/claude_code/codex`（ChatComposer.tsx:33-36）+ 深度模式布尔（ChatModule.tsx:439-446）vs 后端只认 fast/deep 落默认（routing.py:92-109）——**UI 选 Claude Code 实际跑 DSH，选项装饰性**；实现 per-request 内核选择或 UI 收敛） | M2b+翻转 |

并行关系：V6 最先（1 小时定生死）；随后 S1 / M2a / M-mcp / M-cron 四线并行（约 1.5 周）；然后 M2b。

---

## 1. V6 三关实测闸门（1 小时，决定 chat 切换形态）

| # | 验证 | 方法 | 通过标准 | 不通过则 |
|---|---|---|---|---|
| V6a | DSH headless **流式节奏** | 提交长回答任务（如"用 500 字讲 X"），观察 stdout 输出 | 逐行/逐段增量输出（非憋到最后一次性吐） | chat 手感差 -> 过渡方案：gateway 路径加"进度心跳"或暂缓翻转 |
| V6b | **中文人设注入** | prompt 前缀小问 persona + 情绪，跑 3 轮对话 | 风格保持、中文自然 | 人设块加模板强化 / 评估 shell 路径承担人设交互 |
| V6c | **审批闭环体验** | 内核任务触发 `siyi_confirm_request`（需 M2a 先行，可先用 mock store） | 用户批准后下次轮询可续 | 调整为"确认后自动重提交"语义 |

> V6 结果记 journal；三关全过 -> M2b 按计划；部分不过 -> 对应过渡方案，**cron/kernel 任务路径不受影响照常推进**。

---

## 2. S1 稳定区加固（"下次换内核零改动"的地基）

### 2.1 调度层脱内核（最高优先，审计发现的隐性依赖）

**现状（2026-08-15 实测代码路径）**：
```
cron 触发 -> PersistentTaskQueue（稳定✓）
  -> executor = AppContext._execute_cron_action（app_runtime/_context_cron.py L59）
  -> autonomous_assistant_manager.router._execute_cron_action（assistant/_router_schedule.py L241）
  -> capability_router.execute(PlannedStep)   ← 深嵌 assistant 内核！
```
另：`get_cron_service_status` 也经 `autonomous_assistant_manager.router`。

**改造**：新建 `services/cron_actions.py`（稳定区 action 注册表）：

```python
@dataclass
class CronActionSpec:
    action_id: str
    handler: Callable[[AppContext, dict], dict]
    description: str

def register_cron_action(spec: CronActionSpec) -> None: ...
def execute_cron_action(context, action_id: str, payload: dict) -> dict: ...
```

- `_context_cron.py::_execute_cron_action` 改为先查注册表，miss 才走旧 router（过渡期白名单）；
- 迁移清单（逐个从 capability_router 搬 handler，多数是薄封装）：`info.collect` / `proactive.tick` / `suggestion.daily` / `evolution.daily_scan` / `tech_watch.collect` / `tech_watch.judge` / `system.memory_health` / `system.auto_backup`（`system.self_maintenance` 已直处理✓）；
- 新增 `kernel.submit` action（见 M-cron）也注册在这里；
- `get_cron_service_status` 改直连 `context.cron_service`。

**DoD**：
- [ ] `_context_cron.py` grep 不到 `autonomous_assistant_manager`；
- [ ] 上述 9 个 action 经注册表执行，行为与旧路径一致（每个 action 一条回归验证）；
- [ ] 全量 cron RPC（cron.list/add/run_now）回归绿。
**回滚**：注册表 miss 自动落旧 router（保留一个版本周期后删）。

### 2.2 功能路径收口（M1.5 AiTextService）

- 新建 `services/ai_text_service.py`：`AiTextService` Protocol + `LegacyLlmTextService`（包装 `context.ai_client`，复用 ai_client_consumers Binding）；settings 键 `llm_text_service=legacy`（唯一实现；**模型链 v4-flash -> moonshot -> ollama 不动**，决议 D7）；
- 注入 `headless_main.py` 1.4d 邻位 `context.ai_text_service`；
- 消费者迁移顺序：`translate_service` -> `inbox_classifier/capture` -> `info_collector` -> `suggestion_engine` -> `feedback_service` -> `knowledge_organizer` -> `url_learning` -> handlers（`_word_extract/_reading/_url_learning`）。
- **DoD**：grep `chat_completion` 在 services/handlers 仅 ai_text_service.py 内部命中；关停内核进程后 translate/note auto_tag/inbox classify 正常。
- **回滚**：删 settings 键回默认；按文件粒度 revert。

### 2.3 数据层与前端协议（小项）

- 数据层：WAL + busy_timeout 已就绪（实测确认，无需改）；随 M-mcp 落 **OQ1 幂等**（写工具统一 upsert 或 client_op_id）与 **OQ3 审计**（SiyiStore 写路径记 audit，source=mcp）；
- 前端协议：冻结清单已入契约 §1.1-4；前端新增两个 UI 项（随 M2b）：深度模式入口（发 `params.kernel`）+ 内核状态徽章（gateway health / 当前降级级）。

---

## 3. M2a 协议 v2 + ConfirmationStore + 契约测试

| 文件 | 动作 |
|---|---|
| `services/agent_gateway/protocol.py` | 增 `AgentRunEvent`（六 kind，契约 §2.2 状态机）/ `submit_stream`（默认包装=submit 单事件，旧适配器零破坏）/ `resume`（默认退化 submit）/ `cancel`（默认 False）/ `raw["error_kind"]` 五类（契约 §2.3） |
| `services/agent_gateway/adapters.py` | DshGateway：stdout 行流->事件、session_ref、error_kind 归一化、usage（无输出时记字符估算，契约 §5.2）；ClaudeCode：`--output-format stream-json` 逐行、usage 字段映射；Codex：exec --json 事件行 |
| `services/agent_gateway/health.py`（新） | `GatewayHealthMonitor`：TTL 缓存 + degraded_after 连败标记 + 恢复探活；接口 `get(name)/report_failure(kind)/reset(name)` |
| `services/confirmation_store.py`（新） | 契约 §4.2 全部签名（create/resolve/get/list/purge_expired；纯状态，执行留调用方；条目形状兼容 butler；source 标记发起方） |
| `assistant/butler_agent.py` | 模块级 `_pending_confirmations/_confirm_lock/_purge_expired` 改转发 store（外部签名不变，过渡期） |
| `backend/handlers/_confirmations.py` | 改依赖 store；`confirmation.*` RPC 协议不变 |
| `mcp_server/tools.py` + `store.py` | 新增 `siyi_confirm_request`（RW，server 侧注入 source，内核不可伪造）/ `siyi_confirm_status`（RO）；门面方法 |
| `tests/test_agent_gateway_contract.py`（新） | CT-01~CT-10 参数化（契约 §7） |

**DoD**：CT 全绿 + 旧 22 用例不破坏；`_confirmations.py` grep 无 `assistant.butler_agent`；stdio 端到端：fake 内核 confirm_request -> confirmation.list 可见 -> resolve -> status 读到 approved。
**回滚**：store 转发层可一键还原；MCP 新工具不影响旧工具。

> ✅ **M2a 完成（2026-08-16）**：v2 端口（AgentRunEvent 状态机 / BaseGateway 默认实现 / error_kind 五类归一化）、ConfirmationStore（文件后端跨进程共享）、butler 转发层、MCP `siyi_confirm_request`(RW,source=`mcp:kernel`)/`siyi_confirm_status`(RO)、CT-01~CT-10 契约测试全绿。实测 6 文件 **102 passed**（contract 20 + gateway 22 + mcp_server 24 + butler_agent 16 + butler_tools_upgrade 17 + butler_resume_integration 3），lint 0。下一站 M-mcp（M2b 前置）∥ 或 M-cron 余量。

---

## 4. M-cron：cron `kernel.*` action（用户四需求的直接落地）

```python
# services/cron_actions.py 注册（依赖 S1 注册表）
register_cron_action(CronActionSpec(
    action_id="kernel.submit",
    handler=lambda ctx, p: {
        "run": ctx.agent_gateway.submit(p["prompt"], timeout_s=p.get("timeout_s", 240)).__dict__
    },
    description="提交一次性任务给执行内核（DSH/CC），结果经 MCP 回写",
))
```

- 任务模板存 cron job payload（prompt + 技能名 + timeout）；产出经 `siyi_news_save`/`siyi_notify` 回写；
- 失败语义：gateway failed/timeout -> PersistentTaskQueue 指数退避重试（已有）-> 死信告警（已有）；
- trace：`task_type="kernel:{name}"`（契约 §5.1）。
- **DoD**：一条真实 cron 任务（如每日简报 kernel 版）全链路：触发 -> 内核执行 -> MCP 回写 -> 前端可见 -> trace 可查；连续失败 3 次进死信且告警。
- **回滚**：cron 条目改回旧 action_id。

> ✅ **M-cron 完成（2026-08-16）**：`kernel.submit`（§5.3，经稳定区注册表提交任意已注册 action）已落地；新增 `kernel.cron_demo` 演示项，显式走 `context.agent_gateway.submit`（优先复用 headless 注入的网关实例，缺失时按默认配置 `build_gateway(None)` 重建），无真实内核时返回 `unavailable` 仍证明路径接通、不回退旧 router。实测 `test_cron_actions.py` **4 passed**（注册可见 + 经网关路径 + prompt 透传 + 未知 action 仍 None 的回归保护）。注：完整全链路 DoD（每日简报 kernel 版 + MCP 回写 + trace）属后续真实 cron 任务接入，本里程碑验证"路径接通"。

---

## 5. M-mcp：MCP P0 工具补齐（12 个，内核干活不瞎）

| 工具（级别） | 复用的壳内服务 | 备注 |
|---|---|---|
| `siyi_knowledge_query`(RO) | knowledge unified_query | "我之前记过 X" |
| `siyi_memory_recall`(RO) | memory search | 记忆召回 |
| `siyi_notes_get`(RO) / `siyi_notes_update`(RW) / `siyi_notes_tag`(RW) | notes repo | 补齐 CRUD 缺口 |
| `siyi_tasks_search`(RO) / `siyi_tasks_update`(RW) / `siyi_tasks_toggle`(RW) | tasks repo | 同上 |
| `siyi_inbox_classify`(RW) / `siyi_inbox_update`(RW) | inbox_classifier（AiTextService） | 自动分类 |
| `siyi_info_brief`(RO) | info_collector 缓存 | 读简报 |
| `siyi_vocab_review_status`(RO) / `siyi_vocab_quiz`(RW) | vocab FSRS | 词汇场景 |

- 全部走 `SiyiStore` 门面（禁 import assistant/backend）；写工具带幂等语义（OQ1）+ audit（OQ3）；
- **P1（M4 再做）**：browser.*（OQ2 待决）/ desktop.screenshot / clipboard / app.launch / transfer.send；
- file/shell/web 不做（DSH 自带，避免重复暴露攻击面）。
- **DoD**：每个工具单测 + tools/list 冒烟；`dsh --profile siyi` 端到端调 2 个新工具成功。

> ✅ **M-mcp 完成（2026-08-16）**：13 个 `siyi_*` P0 工具全部经 `SiyiStore` 门面注册，分两批落地：第一批（任务/笔记/收件箱 7 项）+ 第二批（`siyi_knowledge_query` / `siyi_memory_recall` / `siyi_info_brief` / `siyi_vocab_review_status` / `siyi_vocab_quiz` / `siyi_inbox_classify`）。实测 `test_mcp_p0_tools_batch2.py`(9) + `test_mcp_p0_tools.py`(6) + `test_mcp_server.py`(24) = **39 passed**，lint 0。
> - **隔离约束落地**：MCP 进程仅依赖 `data/` + `repositories/`，不 import assistant/backend；`knowledge/unified_service` 与 `memory` 的重服务需完整 app 上下文+AI，故 `knowledge_query`/`memory_recall` 采用 **repo 级跨源 RRF 融合**（笔记/任务/收件箱/资讯/词汇）的等效实现，`inbox_classify` 采用**无 AI 关键词启发式**（`inbox_classifier`/`AiTextService` 需 AI 客户端，MCP 进程无持有），均已在代码 docstring 与 journal 标注偏离原因。
> - 下一站 **M2b**（chat 切网关灰度，依赖 V6 + M2a + M-mcp，三者现已齐备）。

### 5.5 M-tools：工具能力层身体化（契约 §1.1-⑤，2026-08-15 决议）

**定位**：外部工具调用能力 = 身体（稳定区），不随内核更换而变。工具的注册/描述/审批/执行/审计全在壳，大脑只是消费方（"工具是身体的器官，内核是可换的脑子"）。

| # | 任务 | 量 | 说明 |
|---|---|---|---|
| 1 | **`siyi_tool_run` MCP 工具**（RW/DANGER 按 risk 分级） | ~1 天 | 内核经 MCP 调用工具注册表：`{tool_id, command, params}` -> risk 审批（high 强制 ConfirmationStore/sandbox）-> GenericToolBridge 代跑 -> 结果 JSON 回传；任何内核（DSH/claude/codebuddy）都能调任何注册工具 |
| 2 | codebuddy ToolSpec 注册 + CLI 无头探测（SOP S1） | 0.5 天 | 零代码：`risk=high + confirmation=every_time`（用户"审批同意后启动"语义现成） |
| 3 | `tool_spec.py`/`generic_tool_bridge.py` 迁 `services/tooling/` | ~0.5 天 | 原 M5 项，可提前（MCP 工具需要 import 它们，Zone 纪律要求不在 assistant/） |
| 4 | 工具执行 audit（source=tool/mcp 记录，OQ3 联动） | ~0.5 天 | 审计+回滚基础 |

**与 gateway 适配器的关系（两个消费层次，都要）**：
- 工具级（大脑干活中途调 codebuddy 写段代码）-> `siyi_tool_run`（身体能力，本节）；
- 任务级（整个编程任务委托 codebuddy 当大脑）-> RoutingGateway 适配器 + launch_approval（§M2b/M2.5）。

**DoD**：`dsh --profile siyi` 对话"用 gh 查一下这个仓库的 open issues" -> 内核调 `siyi_tool_run` -> 审批（若 high）-> 结果返回对话；前端 `tool.execute` 与 MCP `siyi_tool_run` 走同一注册表同一审批。

---

## 6. M2b：chat 切网关（灰度）

### 数据流（三级路由，契约 §2.5）

```
chat.send -> 解析 params.kernel（L1 用户指定）
          -> 无则 L2 轻量预判（fast/deep/shell）
          -> 无则 L3 settings chat_route
   gateway 路径：build prompt（契约 §2.6 四块结构）
     -> asyncio.to_thread(gateway.submit_stream)
     -> delta->chunk / step->状态chunk / confirm->ConfirmationStore+通知
     -> result->save_messages+done（存 session_ref 进 chat session metadata）
   失败降级链：deep -> fast -> legacy(butler) -> shell，每级发一行状态提示（D5）
chat.resume：有 session_ref -> gateway.resume；无 -> butler checkpoint（过渡期，M5 删）
```

### 落点

| 文件 | 动作 |
|---|---|
| `backend/handlers/chat_paths/gateway_path.py`（新） | `run_gateway_path / run_gateway_resume`；纯端口依赖（I1）；含 L2 预判器（关键词+长度启发式，规则可配置） |
| `backend/handlers/_chat_memory.py` | 分发插入：chat_route/params.kernel -> gateway（默认 legacy 灰度）-> butler 兜底 -> simple_stream |
| 前端 desk-notes | 深度模式入口（params.kernel）+ 内核状态徽章（gateway health/降级级展示） |

**DoD**：
- [ ] `chat_route=gateway`+`type=dsh`：流式对话可用、能调通 ≥1 个 siyi_* 新工具（端到端）；
- [ ] 三级路由：params.kernel=shell 时直答不经过内核；=deep 时走 siyi-deep profile；
- [x] 降级链（降级半·安全关键）：移除 dsh from PATH -> 2s 内降级 butler，无卡顿（A3-降级 ✅，单位证据已足）；
- [x] 降级链（正向半·端到端）：dsh 在 PATH -> 流式回复 + 调通 ≥1 siyi_* 工具（**A3-正向 ✅，2026-08-16 实证**：`dsh --profile headless` 调 `siyi_tasks_list` 返回真实生产数据）；
- [ ] 分级状态提示实测：显式降级（params.kernel 指定内核却落空）chat 内出现降级 notice；隐式降级（默认路由）仅 StatusBar 徽章变 degraded、无 chat notice（双断言，见四步流程第 3 步）；
- [ ] trace 出现 `kernel:*` 条目；usage.dashboard 出现 kernel 来源；
- [ ] confirm 流：内核 confirm_request -> 前端批准 -> 轮询读到 approved（集成测试）。
**回滚**：`chat_route=legacy` 一行配置回滚，无代码回滚。

> ✅ **M2b 完成（2026-08-16）**：新增 `backend/handlers/chat_paths/gateway_path.py`（`run_gateway_path` / `run_gateway_resume` / `_use_gateway_route` / `_resolve_chat_route`），并在 `backend/handlers/_chat_memory.py` 的 `_chat_send_inner` 与 `chat_resume` 中接入**三级路由**（L1 `params.kernel` 强制 / L2 `chat_route=gateway` 灰度 / L3 默认 legacy）。网关不可用或空响应时返回 `False`、dispatcher 自然回落到遗留管家路径（灰滚安全）。
> - 功能路径层只依赖 `protocol.AgentGateway` 抽象：`AgentRunEvent` 增量流经 `_emit_events` 映射为前端 chunk（文本 / 错误），`submit_stream` 阻塞调用经 `asyncio.to_thread` 包裹，内核异常归一为 error 事件不抛到事件循环。换内核零改动。
> - 实测 `test_chat_gateway_path.py`(10) + 回归 `test_handlers_chat.py` / `test_chat_memory_tool_required.py` = **37 passed**，lint 0。
> - **偏离/留白**：`chat_route` 设置键经 `settings_repository.get_setting` 动态读取，未新增专属 UI（灰滚期靠设置 API 切换；前端"深度模式入口 + 内核状态徽章" UI 项（计划 §1.1-4）留待翻转阶段）；DSH 端到端冒烟与降级链实测（计划 §6 DoD 列表）需真实内核环境，留待翻转阶段执行。
> - 下一站 **翻转**（chat_route 默认 gateway + butler 降兜底，观察 2-4 周）。

---

## 7. 翻转与观察期（2-4 周）

- settings 默认 `chat_route=gateway`；观察指标：chat 成功率（trace）、首字延迟、降级触发次数、日成本（usage）、confirm 平均等待；
- 退回条件：chat 成功率 < 95% 或降级触发 > 10%/天 -> 回 legacy 并按 error_kind 归因修复。
- ⚠️ **观察期开启前提**：`dsh` 必须在 PATH（或 `kernel_cli_path` 设置指向可用二进制）。否则 `chat_route=gateway` 下每个 chat 请求都走 butler 降级路径，观察样本全部失真——降级半虽安全，但无任何内核样本可观察，观察期无意义。修复 dsh 环境前**不开观察期**（配置锚点 `kernel_cli_path` 锁定 dsh 路径，不依赖 PATH，亦支持 A7 换内核演练指向任意路径）。
> ✅ **观察期已开启（2026-08-16 起）**：dsh 已在 PATH（`shutil.which("dsh")` 可解析，npm 装成 `dsh.cmd`）；`headless` profile 的 `cordis.patch.yml` 已挂 `mcp-siyi`（与 `siyi` profile 同款 venv python + `mcp_server/server.py` stdio），`--dump-config` 已确认组合成功；A3-正向实证（`dsh --profile headless` 调 `siyi_tasks_list` 返回真实生产数据，VERDICT: CONNECTED）已通过。
> - **tool_events 结构化（2026-08-16 补）**：`AgentRunEvent` 增 `tool`/`confirm` kind，`gateway_path._emit_events` 映射为 `chunk_type="tool"/"confirm"` chunk（前端可展示工具步骤/审批提示）；`CodexGateway.submit_stream` 解析 `codex exec --json` JSONL 产出结构化 `tool` 事件（`_parse_codex_events`，样本单测）。**DSH 不暴露结构化工具事件**（dsh 无事件/JSON 输出模式，journal 实证）——DSH 路径 `tool_events` 仍恒 0，前端工具步骤展示在使用 Codex 等结构化内核时可用；DSH 需内核侧支持或观察期后评估。审批闭环（V6c/A4）端到端测试已补（`test_approval_loop.py`：store 闭环 + gateway confirm/tool 映射 + confirmation RPC，11 用例）。
> - M2b §6 DoD 余下子项（三级路由 shell 直答 / 分级状态双断言 / trace `kernel:*` / confirm 流）仍需观察期内真实内核端到端实证补齐。

## 8. M4：双 profile + 技能卡 + MCP P1

- `~/.dsh/profiles/siyi-fast`（v4-flash）/ `siyi-deep`（reasoner），都挂 mcp-siyi；settings profiles 映射（契约 §2.4）；
- 技能卡 4 张：`daily-news-digest` / `knowledge-digest` / `inbox-triage` / `memory-anchor`（调 siyi_* 工具，cron kernel.submit 触发）：**✅ 已落地（2026-08-16）**——`data/skills/<id>.md`（front-matter + 正文，对齐 DSH 技能形态）+ `services/skill_cards.py` 加载器（front-matter 解析 + prompt 渲染 + kernel→dsh profile 映射）+ `cron_actions` 注册 4 个 action 经 `gateway.submit` 直走 dsh；仅依赖已挂 mcp-siyi 工具，不依赖 MCP P1（OQ2）。
- MCP P1（OQ2 已决议 2026-08-16）：**安全边界 = 审批门控 + 白名单**——`mcp_server/desktop.py` 新增 `siyi_desktop_screenshot` / `siyi_clipboard_read` / `siyi_clipboard_write` / `siyi_app_launch`（均 risk=high，先经 `siyi_confirm_request` 批准后重调才执行，内核不可自我批准）；`app_launch` 仅白名单应用；依赖缺失（PIL/pyperclip）优雅降级。**browser.* 暂缓**（攻击面最大、依赖重，需单独决议）；`transfer.send` 复用既有 transfer 服务路径，未新增 MCP 工具。测试 `test_mcp_desktop_p1.py`（11 用例）。
- AI 类 cron 全面改道 kernel.*（info.collect 的 AI 部分保留壳内 AiTextService 抓取+筛选，digest 生成交内核）。

## 9. M5：退役与终态验收

1. 分流执行：迁壳工具（`tool_spec`/`generic_tool_bridge`->`services/tooling/`、`context_compressor`->`services/`、`persona`->`services/persona/`、`trae_bridge`->`integrations/`；`triggers`/`autonomy` 触发判定->`services/proactive/`）；归档内核本体（butler_* / judge / react_engine / multi_agent_orchestrator / autonomy 编排段 / cc_bridge / codex.py / intent_router / task_manager / capability_router / _router_agent / smolagents_path / multi_agent_path）；保留 `trace_recorder`；
2. `_context_builder.py` 拆 assistant 构造段（含 autonomous_assistant_manager）；`orchestration/` 收敛（trae 迁出后归档）；
3. `assistant.resume_task` 标 deprecated；butler checkpoint 导出归档；`mcp/`（外部 client）归档；
4. 终态 grep（I1 + Zone 契约）：
```powershell
rg "^from assistant|^import assistant|from services.agent_gateway.adapters|smolagents" `
  思忆集app/backend 思忆集app/services 思忆集app/repositories 思忆集app/app_runtime
```
- [ ] 结果 = 0（迁移白名单全部清除）
- [ ] **action_guard 分级消费者处置清单（2026-08-20 补）**：MCP 风险分级自 `assistant/core/action_guard.py` 迁出（→ `services/tooling/`）后，列出全部消费者（mcp_server / tool_runner / ConfirmationStore / tool.execute 前端路径等）逐一核对处置，确保删除后数据写入防线不静默消失（2026-08-20 核验：分级当前挂在待退役路径，M5 删除即失防——与 B6 合并迁入 services/tooling）
- [ ] **ai_text 兼容分支清零（2026-08-20 补）**：AiTextService 收口后 grep `chat_completion` 在 services/handlers 仅 `services/ai_text_service.py` 内部命中；LegacyLlmTextService 兼容分支全部删除（关停内核进程后 translate/auto_tag/inbox classify 仍正常）
- [ ] **旧 router 白名单清零（2026-08-20 补）**：`_context_cron.py` 过渡期白名单（注册表 miss 落旧 router 的兜底）删除，grep `autonomous_assistant_manager` 在 `_context_cron.py` 为零
- [ ] 普通 PowerShell 全量回归 3450 基线（勿在 agent 沙箱跑，journal 注意 B）
- [ ] 换内核演练终版：dsh -> claude_code -> none 三态，chat/资讯/知识三路径行为一致
- [ ] journal 登记退役记录

### 9.1 M5 基线（2026-08-16 实测；观察期内仅准备不改兜底）

- **终态 grep（§9.4 命令）现状**：验收四区（backend/services/repositories/app_runtime）内、且非 `assistant/` 自身的命中 **仅 `app_runtime/ai_manager.py` 1 处**（L13-16 引 `assistant.cost_tracker/budget_manager/judge/local_router` = 实时 butler 兜底接线，**观察期内不可动**）；其余 60 处分布在 `assistant/` 自身（允许）、`ui/`（PyQt6 淘汰中）、`tests/`、`ai/`、`mcp/`、`memory/`、`workflow/`，均不在四区验收范围。→ Zone 解耦度已高，M5 step 1 实质 = 撤掉 `ai_manager.py` 这 1 处对 `assistant.*` 的 import（即退役 butler）。
- `assistant/` 现 50+ 模块；`orchestration/`（drivers/ 7 外部 Agent 驱动 + agent_directory/agent_driver/setup）壳内消费者仅 `_agent_tasks`(zombie S11)/`_remote`/`capability_router`(退役中)。
- **安全可先行（不碰兜底接线，属"迁移/修复类"）**：`assistant.resume_task` 标 deprecated（§9.3，已在 `backend/handlers/_trace.py:8,114` 标注 M5 弃用）；**换内核演练脚本已预置**（`scripts/kernel_swap_drill.py`，dsh→claude_code→codex→none 四态经统一 `build_gateway` 健康检查 + 可选 `--run` 真实 submit 冒烟，实测 2026-08-16 all_ok=True）。
- **阻断项（须观察期 + S1 满足后）**：butler 退役（撤 ai_manager import + 归档 assistant/ 本体 + 收敛 orchestration/ + mcp/ 外部 client 归档）—— 任一执行都改/撤实时兜底大脑，观察期内做会断降级链、失真观察样本，按 §0/§7 纪律暂缓。

---

## 10. 横切验收（A 系列，任何里程碑后可重复）

| # | Given/When/Then |
|---|---|
| A1 | Given type=none When chat.send Then 流式回复（butler/simple_stream）+ 数据 RPC 正常 |
| A2 | Given type=dsh + chat_route=gateway When "搜下我关于 X 的笔记" Then 流式回复含 siyi_notes_search step（trace 可查） |
| A3-降级 | Given 内核 CLI 移出 PATH When chat.send Then 2s 内降级 butler，无卡顿（安全关键） | ✅ 已签收（单位证据：`test_gateway_path_none_gateway_yields_nothing` 证明同步返回 False、不 spawn 子进程、无卡顿） |
| A3-正向 | Given dsh 在 PATH When chat.send Then 流式回复 + 调通 ≥1 siyi_* 工具（端到端实跑） | ✅ 已签收（2026-08-16 实证：`dsh --profile headless` 调 `siyi_tasks_list` 返回真实生产数据 VERDICT: CONNECTED；3 处启动/适配修复见 kernel-journal 同轮登记） |
| A4 | Given 内核发起 DANGER 审批 When 用户拒绝 Then 数据无变更 + trace 记 rejected |
| A5 | Given cron kernel.submit 任务 When 到点触发 Then 内核执行 -> siyi_news_save 回写 -> 前端可见（用户四需求验收） |
| A6 | Given 断网 When chat.send Then 降级链走完，收到明确错误 chunk（非挂起） |
| A7 | Given 换内核演练（dsh->claude_code）When 跑 A2/A5 Then **稳定区代码 diff = 0**（Zone 契约终验） |

---

## 11. 迁移窗口期功能路线（冻结令 + S/A/B 分级，2026-08-15 决议）

### 冻结令（至 M5 退役完成）

1. **`assistant/` 冻结**：不新增任何功能（A1 将退役该包，做了白做）；仅允许本计划的迁移/修复类改动；
2. 新功能一律落稳定区（`services/` / `repositories/` / `backend/handlers/` / 前端），且过"换内核零影响"筛子；
3. 依赖旧大脑（planner/judge/react_engine）语义的功能设计，推迟到 M2b 后按新内核事件流重新设计。

### S 级：与迁移同趟车（迁移窗口期内做）

| 功能 | 来源 | 搭车点 | 量 |
|---|---|---|---|
| 统一状态面板 | roadmap P4.4 | 承载 gateway 健康/降级徽章/usage kernel 来源/trace 成功率 = 迁移观察期仪表盘（`system_health` 聚合已在，缺前端） | ~1 周 |
| AI 模型指派 | roadmap P4.3 | 与 M2b 内核选择器 + 三级路由 L1 同根（ProviderConfig 扩展 + chat 参数 + 前端选择器） | ~1 周（M2b 摊掉一半） |
| 收件箱自动分类路由 | roadmap P3.3 | inbox_classifier 是 M1.5 样板消费者（执行手册 §5.2.3），迁移完顺手加"分类->路由 tasks.create" | +2 天 |

### A 级：迁移后按兴趣插队

| 功能 | 说明 | 量 |
|---|---|---|
| 记忆浮现组件（P3.1） | RPC `memory.surface_for_input` **已存在**，只缺前端侧边栏组件+防抖 | ~1 天 |
| 周报系统（P3.6） | cron_actions 注册 action + 内容生成交 kernel.submit（kernel 回写链路实战） | ~2 天 |
| 真人 TTS（edge-tts） | 独立小功能，免费微软语音 | ~2 天 |
| 遗忘曲线可视化 / 打卡成就 | 词汇模块增强，纯前端+数据 | 各 1-2 天 |

### B 级：暂缓

桌宠升级 / 手机传感 / 自适应 cron / 程序员词库预设（价值独立但与迁移无协同）。

### 明确不做在壳里（避免白做）

- **P3.4 Agent 执行沙盒预览**：依赖 assistant/planner（退役对象）；M2b 后由内核事件流（DSH plan/step 输出）天然提供；
- **P3.5 对话记忆锚点残项**：大半已实现（`_chat_context_anchor.py` + P2.8 偏好注入）；剩余随契约 §2.6 prompt 组装落地。

---

## 附录 A：内核选型参考（2026-08-15 时点；web 检索不可用，基于模型内置知识，装机前核对）

| Agent | 许可 | headless | MCP | 适配 | 定位 |
|---|---|---|---|---|---|
| **DSH (DeepSeek Harness)** | rc 阶段 | ✓ 已验证（V1 亚秒） | ✓ patch | ✓ 已有（**A1 默认**） | 换循环不换模型；v4-flash 成本不变 |
| **CodeBuddy Code (hy3)** | 免费额度 | **待探测（CLI 未装，本机仅 IDE 插件版）** | 待验证 | 快车道 ToolSpec -> 正道适配器（M2b） | **编程任务专用**（用户实测 hy3 ≥ v4-flash）；路由 "代码"->codebuddy；启动走 launch_approval 审批 |
| **Claude Code** | 商业 API | ✓ `-p --output-format stream-json` | ✓ 原生 | ✓ 已有 | 编排最强；降级为 A7 换内核演练对象 / codebuddy 不可用时 fallback |
| **OpenAI Codex CLI** | Apache-2.0 | ✓ `codex exec --json` | ✓ | ✓ 已有（本机损坏待修） | 开源可控备选 |
| Gemini CLI / Qwen Code | Apache-2.0 | ✓ | ✓ | 薄适配 ~40 行 | 免费备用档 |
| OpenHands / Goose / Aider | MIT/Apache | ✓ | 部分 | 重/中/不推荐 | 通用任务 / 自动化 / 场景错位 |

**CodeBuddy 接入两步走（2026-08-15 决议）**：① 快车道 = ToolSpec 动态工具（`risk=high, confirmation=every_time`，审批语义现成，验证 CLI 无头 + hy3 编程质量 + 免费额度）；② 正道 = `CodeBuddyGateway` 适配器 + 路由规则 + `launch_approval` 启动审批门（M2b/M2.5）；后期 M4 加 `siyi_code_delegate` MCP 工具（内核委托编程）。

**codex 修复**：`npm rm -g @openai/codex; npm i -g @openai/codex@latest; codex --version`。
