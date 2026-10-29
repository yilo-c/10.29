# 执行内核分层 · 全面核验与深度分析报告（2026-08-20）

> **性质**：时点性审计 + 建议核验。对照 `kernel-contract.md` / `kernel-migration-plan.md` / `agent改进方案-借鉴ClaudeCode与DSH.md` 的文档声称，逐条用**代码事实**（`file:line`）重新核验上一轮 11 条建议，并补充三个上轮未发现的问题。
> **方法**：3 个并行代码核验（后端内核层 / 前端 / 功能路径）+ 施工仪表盘核对 + 5 处关键疑点亲核（L1 词汇表、技能卡形态、get_gateway、外部论断×2）。
> **登记**：见 `kernel-journal.md` 2026-08-20 条目。
> **结论预览**：上轮 11 条建议中 **7 条成立、4 条修正、1 条撤回**；新发现 3 个上轮不可见的问题（观察期盲飞、L1 词汇表欺骗性、M5 阻塞链）才是当前真正该先做的。

---

## 一、进度重估：项目比上轮假设更快（关键修正）

上一轮依据 `kernel-layering-design-2026-08-15.md` 快照（"⬜ 待做：M2a/M2b/翻转"）排的路线图**已过时**。施工仪表盘（`kernel-migration-plan.md` §0）与代码证实：

| 里程碑 | 状态 | 证据 |
|---|---|---|
| **M2a** | ✅ 已完成（102 测试绿） | v2 协议完整落地：`submit_stream/resume/cancel` + `AgentRunEvent` 六事件（`protocol.py:121-148`）；error_kind 五类归一化（`adapters.py:31-42`）；ConfirmationStore + `siyi_confirm_*`；CT-01~10 契约测试（`test_agent_gateway_contract.py`）全在 |
| **M2b + 翻转** | ✅ 已完成（2026-08-16，commit a39af4b5） | chat_route 默认 **gateway**，观察期已开启；降级 notice 已实现（`_chat_memory.py:50-65`） |
| **M4** | 🔶 进行中 | 双 profile 已建，4 张技能卡已接通（cron→kernel，9 测试绿） |

→ **上一轮建议"尽快落地 v2 resume/cancel"撤回**。当前真实位置是：翻转后的观察期 + M4/M5 收尾阶段。

---

## 二、逐条核验上一轮 11 条建议

### Harness 层（6 条）

| # | 上轮建议 | 核验结论 | 证据与修正 |
|---|---|---|---|
| 1 | B6 审批 UX（理由框/权限档） | ✅ 成立，**紧迫性上调** | 前端确认卡片有批准/拒绝但**全库无理由输入框**。更深：契约 §3.3 `mcp_tool_policy` 在 `mcp_server` **完全未实现**（全仓 grep 无匹配），实际风险分级在 `assistant/core/action_guard.py:271-277`（**butler 路径**）——M5 退役 assistant 时分级随之消失。**这是 M5 的硬前置，非 UX 优化** |
| 2 | PlanCard 接 v2 事件 | ✅ 成立 | planner 仅只读时间线（`agentEvents.ts:14`），无批准/拒绝/编辑。v2 `confirm` 事件已就绪且前端 confirmation 卡片已能渲染——**纯前端工作** |
| 3 | v2 resume/cancel | ❌ 撤回 | 已完成（见 §一） |
| 4 | 写 DSH 技能卡 | ⚠️ 修正 | 已有 4 张（`daily-news-digest` / `knowledge-digest` / `inbox-triage` / `memory-anchor`），形态为壳侧 `data/skills/*.md` + `skill_cards.py` + cron→gateway（`$DSH_HOME/skills` 不存在，未用 DSH 原生技能）。缺口：**无 chat 触发**——用户在聊天说"整理笔记"不会命中技能卡（无 trigger_patterns 匹配注入）。补这个，而非补卡数量 |
| 5 | SubAgentPanel | ✅ 成立 | 仅事件时间线（`agentEvents.ts:17-18`），无专属面板 |
| 6 | 成本卡片 | ⚠️ 重大修正 | 前端 `UsageDashboardModule` + `AIActivityPanel` **已存在**——上轮"可观测性未在 UI 暴露"是错的。真缺口在后端：`usage_tracker` 无 source 参数（`usage_tracker.py:219`），`trace_recorder` 无 `kernel:*` 支持 → **内核调用在 TracePanel 和用量面板里不可见** |

### 产品层（5 条）

| # | 上轮建议 | 核验结论 | 证据与修正 |
|---|---|---|---|
| 7 | 数据所有权导出 | ⚠️ 修正 | **大部分已存在**：`note_exporter`（MD/HTML/TXT/PDF）+ `backup_service`（JSON.gz 覆盖 notes/tasks/settings/memories）+ 前端 `exportToMarkdown/JSON`。修正：不新建，**整合成显式入口**，补记忆/词库开放格式导出（JSON.gz 普通用户打不开）。工作量下调 |
| 8 | 记忆可编辑 | ✅ 成立且更具体 | `structured_store.py` 有 `update/delete_entry`（**数据层已支持**！），但 `memory.update/delete` RPC 不存在（`_chat_memory.py` 仅 search/recent/related/save_anchor），UI 只有只读浮现。→ **低垂果实：2 个薄 RPC + 1 个面板** |
| 9 | 晨报反馈闭环 | ✅ 成立 | `MorningBriefCard` 仅关闭按钮（L36），无 thumbs。外部对标已二次验证（见 §四） |
| 10 | FSRS 咬死 | ✅ 确认 | `_fsrs_scheduler.py`（fsrs>=4.0 双写）+ 听写 + 测验。维持现状 |
| 11 | 云同步边界 | ⚠️ 修正 | 核验确认**记忆不入云**（好）；但 notes/tasks/词库/翻译入云且有 token 派生密钥加密——比上轮表述更乐观。建议 UI 明示同步范围 |

---

## 三、核验暴露的新问题（上轮未发现）

### 问题 1 — L1 kernel 参数三套词汇表互不匹配（最严重，UI 具有欺骗性）

- 契约 §2.5 定义：`fast/deep/shell/legacy`
- 前端选择器发送：`auto/dsh/claude_code/codex`（`ChatComposer.tsx:33-36`）；"深度模式"发送 `kernel: true`（`ChatModule.tsx:439-446`，布尔值）
- 后端实际：`resolve_dsh_profile` 只认 fast/deep→profile 映射（`routing.py:92-109`，其他值全部落默认）；`get_gateway` 只返回 settings 配置的**单一网关**（`routing.py:36-43`）

**后果：用户在 UI 选"Claude Code"，实际跑的是 settings 里配置的内核（DSH）——选项是装饰性的。**
修复二选一：(a) 实现 per-request 内核选择（`get_gateway(context, params)` 按值临时构建，4 适配器注册表已就绪，符合契约 L1 本义）；(b) UI 收敛为 fast/deep/auto。建议 (a)。

### 问题 2 — 文档漂移

契约说 MCP 10 工具（实际 20）、cron 9+1 action（实际 17，含 4 技能卡 + knowledge.* 等）。建议 `kernel-contract.md` 采用"工具清单以 `tools.py` 为源真值"的引用纪律（同 `CLAUDE.md` 方法表既有规则）。

### 问题 3 — `tests/e2e/` 目录为空

273 个测试文件全是 mock 单元/集成测试，"新闻摘要全链路"级别验证为零——上轮排第一的 golden-path E2E 缺口**坐实且加重**。

### 问题 4 — audit 落独立 `audit_mcp.db`

（`tool_runner.py:68-87`）而非统一审计链——OQ3 只算部分解决。

---

## 四、外部论断抽查（均属实）

- **Limitless/Meta**：属实（2025-12-05 收购、Rewind 2025-12-19 停用、7 个地区用户数据限期删除）。教训比上轮说的更硬：用户被迫在截止日前抢救自己的数据——**"数据所有权必须架构落地"成立**。
- **ChatGPT Pulse**：属实（2025-09-25 Pro 预览）。三个值得直接抄的细节：每日**有限卡片**（5-10 张，非信息流）、**thumbs + curate** 反馈闭环、**卡片短时效**（当日过期，防噪音堆积）。晨报建议据此细化。

---

## 五、深度分析：三个系统性判断

**判断 1 — "契约先行"的债务开始显形。**
`kernel-contract.md` 质量很高，但核验显示 §3.3（工具分级）与 §5（可观测）是**纸面能力**——契约与实现已经漂移（另有 L1 三套词汇、工具数漂移）。建议把漂移变成红灯：契约测试扩 CT-11（`mcp_tool_policy` 拦截）+ CT-12（usage source 归因）+ CT-13（`params.kernel` 词汇表一致性，前后端协议级断言）。

**判断 2 — 观察期正在盲飞。**
翻转已发生（chat 默认走 DSH），但内核执行不进 trace、不进用量面板（`usage_tracker` 无 source 列）。观察期的目的是验证"DSH 能否当主大脑"——没有观测数据就无法得出任何结论。**这是当前第一优先，且工作量很小**（一个 schema 迁移 + `gateway_path` 两行埋点）。

**判断 3 — M5 有一条未登记的阻塞链。**
MCP 风险分级在 `assistant/core/action_guard`（待退役区），M5 删除时数据写入的防线会静默消失。要么先迁入 `services/tooling/`（与 B6 合并做），要么把此项补进施工手册 M5 的 DoD。

---

## 六、修正后优先级路线图 v2

| 级 | 事项 | 理由（核验证据） | 工作量 |
|---|---|---|---|
| **P0** | 内核可观测收口：`usage_records` 加 `source` 列 + gateway 执行 `start_run("kernel:{name}")` | 观察期盲飞；契约 §5 唯一零实现段 | 小 |
| **P0** | L1 词汇统一：per-request 内核选择（或 UI 收敛） | UI 选 Claude Code 实际跑 DSH | 小-中 |
| **P0** | golden-path E2E：先做"每日新闻摘要"一条全链路 | `tests/e2e/` 为空 | 中 |
| **P1** | MCP 工具策略层：分级迁入 `services/tooling` + 审批理由框 | M5 硬前置；分级现在挂在待退役路径上 | 中 |
| **P1** | PlanCard + chat 侧技能触发（trigger_patterns 注入 gateway prompt） | v2 事件已就绪；技能卡无 chat 入口 | 中 |
| **P1** | `memory.update/delete` RPC + Memory Panel | 数据层已支持，只差两层薄壳 | 小 |
| **P2** | 晨报 thumbs/curate + 短时效卡片（学 Pulse）；SubAgentPanel；导出整合为"数据所有权"入口；同步范围 UI 明示 | 体验增强 | 各小-中 |
| **P2** | M5 按计划 + 新增 DoD（action_guard 分级迁移、ai_text 兼容分支清理、旧 router 清零） | 收尾 | 按手册 |

---

## 七、与既有文档的关系

- 本报告的 CT-11/12/13 建议应在 `kernel-contract.md` §7 契约测试清单中追加条目。
- 问题 1（L1 词汇表）、问题 2（文档漂移）、判断 3（M5 阻塞链）应在 `kernel-migration-plan.md` 补登记：L1 词汇统一进入 P0；M5 加 DoD 子项。
- `agent改进方案-借鉴ClaudeCode与DSH.md` 的"Python 重造 harness 内部件"条目（A1 内核钩子、A3 git 快照、A5 压缩、B1 持久子代理、B3 agent 会话 goal、B4 SkillRegistry、B5 插件运行时）已被 DSH 原生覆盖，按 `dsh-integration-report-2026-08-15.md` 结论 **supersede**；仅保留壳侧条目（B6 审批 UX、D3 人格注入、记忆/Goal 作为个人数据层、可观测 UI、Plan UX 接线）。本报告不修改该文档，仅在此标注作废范围，待用户统一确认后由施工手册落实。

---

## 附：核验证据索引（file:line）

| 主题 | 证据位置 |
|---|---|
| v2 协议 / AgentRunEvent | `思忆集app/services/agent_gateway/protocol.py:121-148` |
| error_kind 五类 | `思忆集app/services/agent_gateway/adapters.py:31-42` |
| get_gateway 单网关 | `思忆集app/services/agent_gateway/routing.py:36-43` |
| resolve_dsh_profile 映射 | `思忆集app/services/agent_gateway/routing.py:92-109` |
| 降级 notice | `思忆集app/backend/handlers/_chat_memory.py:50-65` |
| 前端内核选择器 | `desk-notes/packages/desktop/src/components/chat/ChatComposer.tsx:33-36` |
| 深度模式布尔值 | `desk-notes/packages/desktop/src/components/Modules/ChatModule.tsx:439-446` |
| 前端只读时间线 | `desk-notes/packages/desktop/src/.../agentEvents.ts:14,17-18` |
| 技能卡形态 | `思忆集app/services/skill_cards.py` + `思忆集app/data/skills/*.md` |
| 风险分级（待退役） | `思忆集app/assistant/core/action_guard.py:271-277` |
| 记忆数据层 update/delete | `思忆集app/memory/structured_store.py`（`update_entry`/`delete_entry`） |
| 记忆 RPC 缺口 | `思忆集app/backend/handlers/_chat_memory.py`（仅 search/recent/related/save_anchor） |
| usage_tracker 无 source | `思忆集app/.../usage_tracker.py:219` |
| 独立 audit db | `思忆集app/mcp_server/tool_runner.py:68-87` |
