# 思忆集：功能路径 × 执行内核 分层（总览）

> **日期**：2026-08-15 建立（同日完成 M1/M3 与两轮验证，文档拆分为四份）
> **目标**：功能路径（壳）与执行内核（agent）彻底解耦--换内核（DSH -> Claude Code -> Codex -> 任何新 agent）、升级版本、换模型，均**不影响功能路径层**。

---

## 0. 文档地图（本主题共四份，各司其职）

| 文档 | 性质 | 回答什么问题 | 改动频率 |
|---|---|---|---|
| **本文**（总览） | 导航入口 | 架构是什么、去哪找细节、现在什么状态 | 低 |
| [`kernel-contract.md`](kernel-contract.md) | **契约**（稳定） | 端口签名、错误分类学、MCP 工具规范、安全审批密钥、可观测、新内核接入 SOP、契约测试 | 低（变更需登记） |
| [`kernel-migration-plan.md`](kernel-migration-plan.md) | **施工手册**（活） | M1.5~M5 每步的接口落点、DoD、回滚开关 | 随施工更新 |
| [`kernel-journal.md`](kernel-journal.md) | **日志**（只追加） | 验证记录（V0-V5）、落地记录、回归、审计、选型登记 | 每次动作追加 |

相关（不在本主题四件套内）：[`dsh-integration-report-2026-08-15.md`](dsh-integration-report-2026-08-15.md)--第一轮 DSH 选型分析（历史存档）。

---

## 1. 一句话架构

**壳只认两个端口（`AiTextService` 内嵌小调用 / `AgentGateway` 执行内核）和一批 `siyi_*` MCP 工具名；内核只是端口的某个适配器实现 + 一个 MCP client。换内核 = 一个适配器类 + `agent_kernel.type` 一行配置。**

```
┌────────────────────────────────────────────────────────────────┐
│ 功能路径层（壳）- 思忆集 backend + Electron + 手机端            │
│  services/ domain/ repositories/ 337 RPC handlers                │
│   ┌─────────────────────┐      ┌──────────────────────────┐     │
│   │ AiTextService(端口) │      │ AgentGateway(端口)       │     │
│   │  翻译/分类/摘要/嵌入 │      │  submit / submit_stream  │     │
│   │  (内核无关)         │      │  resume / health         │     │
│   └──────────┬──────────┘      └───────────┬──────────────┘     │
└──────────────┼─────────────────────────────┼────────────────────┘
               │                             │
   ┌───────────▼──────────┐    ┌─────────────▼──────────────┐
   │ LLM Provider 实现    │    │ Agent 适配器注册表          │
   │ (DeepSeek/Ollama 链) │    │  dsh / claude_code / codex │
   │  → AiTextService 实现 │    │  / none(降级) / routing    │
   └──────────────────────┘    │  → 未来任意成熟 agent      │
                               └─────────────┬──────────────┘
                                             │ MCP client（标准协议）
               ┌─────────────────────────────▼────────────────────┐
               │ 思忆集 MCP Server（mcp_server/，纯标准库）        │
               │  siyi_* 工具（RO/RW/DANGER 分级）+ confirm 闭环   │
               └──────────────────────────────────────────────────┘
```

**六条分层不变量**（反向依赖 / 数据经 MCP / 内嵌 AI 与内核分离 / 配置即切换 / 单向事件 / 降级不崩溃）--定义与验证方法见契约 §1。

---

## 2. 替换矩阵（怎么换、影响面）

| 你想做的事 | 改哪里 | 功能路径受影响？ |
|---|---|---|
| 换内核：DSH -> Claude Code | `agent_kernel.type=claude_code`（+ 装 CLI） | 无 |
| 多内核并存 + 按任务路由 | `type=routing` + routing 规则（M2.5） | 无 |
| 升级内核版本 | 内核自带更新（独立进程/CLI） | 无 |
| 换模型 | 内核自身配置（profile/settings） | 无 |
| 内嵌 AI 换 provider | `llm_text_service` 实现（M1.5 后） | 无 |
| 内核挂了/未装 | 自动降级链（butler -> simple_stream）或 `type=none` | 无崩溃，数据照常 |
| 接入全新 agent | 按契约 §6 SOP 八步（约一周） | 无 |

---

## 3. 当前状态（活状态以 [`kernel-migration-plan.md`](kernel-migration-plan.md) §0 仪表盘为准）

- ✅ M1 端口落地（4 适配器，22 测试绿）/ ✅ M3 MCP Server（10 工具，16 测试绿）/ ✅ V0-V5 替换验证
- **路线已定：A1**（决议 D6）--butler+PydanticAI 整体换 DSH harness，**换循环不换模型**（v4-flash 成本不变）
- **模型**：hy3（腾讯混元3 免费）接入与可行性见 [`kernel-execution-guide.md`](kernel-execution-guide.md) §2；执行步骤（代码级）以执行手册为准
- ⬜ 待做：**V6 三关实测**（流式/人设/审批，1 小时闸门）-> 四线并行（S1 稳定区加固 / M2a / M-cron / M-mcp，约 1.5 周）-> M2b chat 灰度 -> 翻转观察 -> M4 双 profile+技能卡 -> M5 退役
- 稳定区审计发现：cron 执行分发深嵌 assistant（必须先迁 `services/cron_actions.py`）；SQLite WAL 多进程并发已就绪
- 内核 CLI 实测：dsh ✓、claude 2.1.170 ✓、codex 已装待修（`npm i -g @openai/codex@latest`）

---

## 4. 已定关键决策（完整 ADR 见契约 §9，D1-D8）

1. **稳定区/可换区边界固化（D8/契约 §1.1）**：数据层+功能路径+调度层+前端协议 = 稳定区（换内核零改动）；大脑循环（gateway 适配器）+ 手（MCP 工具面）= 可换区；
2. **壳保留轻量 AI（D1/D7）**：翻译/分类/摘要留壳，模型链 v4-flash 不动，不随内核更换而改变；
3. **A1 换循环不换模型（D6）**：DSH harness 替代 butler 循环，DSH 内配 v4-flash；前置 V6 三关实测；
4. **chat 三级路由（D2）**：用户指定（params.kernel/深度模式）> 壳自动分流（fast/deep/shell）> 默认 gateway；降级链 deep->fast->legacy->shell + 状态提示（D5）；
5. **人设壳注入（D3）+ 触发留壳执行交内核（D4）**：persona/情绪数据永存壳内，渲染进内核 prompt；triggers/autonomy 是数据感知，留壳；
6. 数据边界 = MCP（纯标准库，2025-06-18）；审批双层 + ConfirmationStore 防自我批准；密钥不跨层；事件单向；可观测统一（trace/usage/health）。

---

## 5. 历史说明

- 2026-08-15 第一轮：本文为单一设计文档，完成 V0-V5 验证与 M1/M3 落地；
- 2026-08-15 第二轮：全量回归尝试 + 现状审计（发现双抽象并行 / 协议过瘦 / 残余耦合 8 文件），文档重组为四份（本文改写为总览），历史内容全文移入 `kernel-journal.md`；
- 2026-08-15 第三、四轮：需求五项确认 -> 路线三轮辩论（工具增强 vs 双轨 vs 直接换）-> 终决 A1 + 稳定区审计（cron 隐性依赖发现）-> 契约升 v1.2（Zone 契约 / 三级路由 / ADR / OQ），施工手册重写为 A1 版。
