# 思忆集 × DeepSeek Harness（DSH）结合分析报告

> 日期：2026-08-15
> 目的：评估「将思忆集自建 agent 内核替换为 DeepSeek Harness（DSH）」的可行性与结合方式，并给出推荐结论与落地路线。
> 调研方法：本机 DSH 安装源码（`@deepseek-ai/dsh@0.1.0-rc.6`，npm 缓存路径）一手阅读 + 思忆集仓库源码/文档/提交审计。**外部 web 搜索在本环境不可用（API key 无效），第八节的生态信息基于既有知识，标注了"需线上核实"项。**

---

## 一、结论先行（TL;DR）

**推荐结合，方向正确；但换的是「内核」，不是「壳」。** 具体三条判断：

1. **"玩具感"的主要来源是自建 agent 内核**（butler/judge/react_engine/多 agent 编排/自研工具闸门），而不是数据功能缺失。你已经写好的资讯收集、分类、翻译、记忆等数据服务反而是项目里最成熟、最值得保留的部分。
2. **DSH 的能力矩阵几乎逐条覆盖你缺的东西**（持久会话、goal、子代理、工作流、调度、技能、MCP、沙箱审批、Web UI），而且 DSH 自带 **codex / claude-code 子代理 provider**（dsh-base 默认休眠加载、agent preset 可启用）——"执行内核可替换为各大成熟 agent"是它设计好的能力，不是需要你造的。
3. **推荐的结合方式 = 思忆集瘦身为"个人数据 + 功能路径"层并暴露 MCP Server，DSH 作为独立执行内核进程，二者用 MCP + HTTP/API 打通**；UI 先保留现有 Electron，验证稳定后再评估是否迁移到 DSH Web + 自定义插件。

附带建议：
- **搁置** `docs/agent改进方案-借鉴ClaudeCode与DSH.md`：它的 A 线（hooks/路径权限/git 快照/plan UX/分层压缩）和 B 线（持久子代理/脚本工作流/goal/技能/插件/沙箱 UX）几乎就是 DSH 的现成功能清单，按该文档执行等于在 Python 里重造一个 DSH。
- **砍面**：`automation_hub`（README 已自认"已弱化"）、PyQt6 桌面端、桌宠、浏览器扩展、云同步 Worker 等边缘面冻结或归档，把维护预算集中到"数据服务 + 3~5 条功能路径 + 与 DSH 的对接"。

---

## 二、现状诊断：为什么"还是玩具"（证据）

### 1. 面铺得太宽，注意力被稀释

仓库根目录 8+ 个子项目并存：PyQt6 传统端、Electron 端（desk-notes）、手机端（Capacitor）、automation_hub（README 标注"已弱化"）、浏览器扩展、Cloudflare Worker 同步、桌宠、web-ui/、dist/ 残留目录。`README.md` 的关键目录表里 automation_hub 已标注"**已弱化**，新功能不走这里"——说明项目自己都在收缩，但历史包袱（崩溃日志、残留目录、多个入口脚本）仍在。

### 2. 内核自建且多轨并行

- `思忆集app/assistant/` 有 60+ 模块：butler_agent（704 行）、judge、react_engine、multi_agent_orchestrator（926 行）、autonomy、planner、cc_bridge（Claude Code CLI 适配，437 行）、codex.py（Codex 编排，429 行）…加上 `backend/handlers/chat_paths/` 下 pydantic_agent / smolagents / stream_tools / simple_stream / aggregate 等多条执行路径。
- 提交 `6cf44e20` 的体检结论原话："'五轨'实为两个产品面"，`assistant/agent_loop.py` **生产零引用**，已随测试一起归档至 `archive/dead-exec-paths-20260809/`。
- 结论：执行内核在反复推倒重来，这是"写了很多但用不起来"的典型形态。

### 3. 缺的不是"功能"，是"工程化交互层"

`docs/agent改进方案-借鉴ClaudeCode与DSH.md` 列的 A1–A5 / B1–B6 全部是 DSH 已经做好的东西：

| 文档条目 | 内容 | DSH 对应物 |
|---|---|---|
| A1 声明式钩子 | pre_tool/post_tool/on_error | dsh-tools 工具管线 + dsh-user-approval/sandbox |
| A2 路径级权限 | 按路径放行/拦截 | dsh-fs-sandbox + dsh-permission-presets + dsh-sandbox-policy |
| A3 git 快照 | 任务级回退 | dsh-session（持久化）+ dsh-session-checkpoint-policy |
| A4 plan UX | 计划审批闭环 | dsh-plan-mode + ui-plan（Web 已有计划座位） |
| A5 分层压缩 | 上下文压缩 | dsh-compaction-basic + dsh-compaction-tool-result-pruner + dsh-token-meter |
| B1 持久子代理 | 可续聊子代理树 | dsh-subagent（spawn/fork/continuable，冷恢复） |
| B2 脚本工作流 | 阶段化编排 | dsh-workflow + dsh-workflow-worker-thread |
| B3 持久 goal | 跨轮次目标 | dsh-goal + dsh-goal-round-driver |
| B4 技能目录 | 技能卡注入 | dsh-skill + dsh-skill-filesystem（`$DSH_HOME/skills`） |
| B5 双端插件+热更新 | 插件运行时 | dsh 的 cordis 插件体系 + client HMR |
| B6 沙箱 UX | 最窄放宽+审批 | dsh-sandbox 三档 + dsh-user-approval + ui-permission-presets |

### 4. 稳定性信号

- 仓库根目录 8 月初有 **20+ 份 `siyi_desktop_crash_*.log`**（8/3 一天 18 份），另有 `siyi_desktop_debug_crash_*` 与 `backend.dev.log` / `electron.dev.log`。
- 测试绿 ≠ 产品可用：后端 3450 测试通过、前端 514 用例全绿，但用户体感"无法可用"——测试覆盖的是管道（RPC/存储/组件），不是端到端的功能路径（"今天帮我总结新闻"能否稳定跑通）。

---

## 三、DSH 能力盘点（基于本机安装源码 0.1.0-rc.6）

调研对象：`C:\Users\Administrator\AppData\Local\npm-cache\_npx\1e7f6d9597241db0\node_modules\@deepseek-ai\*`（约 120 个包，本机 `dsh` CLI 已在 PATH，Node v24.15.0，profile 根 `C:\Users\Administrator\.dsh\`）。

| 领域 | DSH 提供 | 备注 |
|---|---|---|
| Agent 循环 | `dsh-agent` + `dsh-agent-loop` + `dsh-agent-presets`（per-session 组装） | 会话按 agent preset 组装工具/提示词 |
| 会话持久化 | `dsh-session-persistence-jsonl`（`~/.dsh/sessions`）+ `dsh-session-query-sqlite`（FTS 可选）+ 导出/标题/统计/遥测 | 追加式事件日志，可冷恢复、断点续聊 |
| 目标（Goal） | `dsh-goal` + `dsh-goal-round-driver`（持久化、修订号、pause/resume/blocked） | Web UI 有 GoalBar |
| 子代理 | `dsh-subagent`：spawn / fork / **codex** / **claude-code** / ACP 子代理 | **codex 与 claude-code provider 在 dsh-base 中休眠加载**，standard preset 中对应工具默认 disabled，复制 preset 后启用即可 |
| 工作流 | `dsh-workflow` + worker-thread（阶段化多 agent 编排，容错语义） | 脚本化编排 |
| 调度 | `dsh-schedule`：`schedule_create / schedule_list / schedule_delete`，持久（session 事件日志为唯一事实源） | session 级提醒；`every_seconds` 下限 5 分钟；`at` 支持绝对时刻+时区 |
| 技能 | `dsh-skill` + `dsh-skill-filesystem`（`$DSH_HOME/skills`，front-matter 技能卡，`/name` 触发） | Web UI 有技能菜单 |
| 工具 | fs（read/edit/write/glob/grep）、bash / pwsh（win 用 pwsh）、`web_search` / `web_fetch`、todo、jobs（后台任务）、ask-user | web_search 走 DeepSeek Anthropic 兼容端点，结构化结果 |
| MCP 客户端 | `dsh-mcp-client`：stdio + streamable-http 双传输，工具命名 `mcp__<server>__<tool>`，断线重连+指数退避 | 与 Claude Code/Codex 相同的 MCP 形状；**DSH 只做客户端，服务端需自建** |
| 安全 | `dsh-sandbox`（read-only / workspace-write / danger-full-access 三档，`DSH_PERMISSION_MODE`）+ `dsh-user-approval`（ask/never）+ `dsh-permission-presets` | 审批瀑布式事件，ACP 桥可机器应答 |
| LLM | `dsh-llm-deepseek`（deepseek-official：v4-flash/v4-pro，1M 上下文）+ `dsh-llm-pi-ai`（多 provider 目录，含 openai-codex）+ retry + token meter | 模型路由可配，settings.yaml 热加载 |
| Web UI | React 客户端 + **slot 注册系统**（`dsh-client-ui-slots`，可注入自定义组件）+ 目标/任务/技能/子代理/工作流/计划/轨迹/设置/权限/反馈等 30+ UI 插件 | chat-first，自定义业务页面需写 client 插件 |
| 无头模式 | `dsh --profile headless "task"`：一次性持久会话，打印最终答案退出 | 可作外部调度器（Task Scheduler / cron）的调用入口 |
| 编程接口 | `dsh-api-gateway`（typert RPC，HTTP `/api`）+ `dsh-api-remotes` + JSON-RPC/ACP 适配器 | 可被思忆集前端/后端驱动 |

**关键证据（原话）**：`dsh-base/README.md` —— "Codex and Claude Code providers load dormant; Agent Presets independently decide whether their agent contributes either model-facing delegation tool."；`dsh-agent-presets/standard/agent.cordis.yml` 中 `tool-subagent-codex`（provider: codex）与 `tool-subagent-claude-code`（provider: claude-code）均 `disabled: true`，复制 preset 移除 disabled 即启用。

---

## 四、三种结合方案对比

### 方案 A：DSH = 执行内核，思忆集 = 数据 + 功能壳（推荐）

```
Electron (desk-notes) ──WS JSON-RPC──▶ 思忆集 backend（瘦身：data/services/handlers 仅数据类）
                                          │  FastMCP（MCP Server，stdio / streamable-http）
                                          ▼
                              DSH 自定义 profile（base + web-app + schedule + mcp-client + web-search + sandbox）
                                          │  子代理 provider：codex / claude-code / ACP
                                          ▼
                              各大成熟 agent / 模型（deepseek v4 / claude / codex …）
```

- 思忆集保留：SQLite 数据层（notes/tasks/memory/vocab/info_items）、领域服务（info_collector / categorizer / translator / wordbook / reminder / notification）、现有 RPC 存储类 handler（337 方法中数据类）、Electron UI。
- 思忆集退役：`assistant/`（butler/judge/react/multi_agent/codex/cc_bridge…）、`ai/`（LLM 客户端栈）、`mcp/`（现有的是**消费端** client，方向相反，需新写 server）、`workflow/`、`evolution/`、`automation_hub`。
- DSH 侧：自定义 profile + 复制 standard 建个人 preset（启用 codex/claude-code provider）+ `$DSH_HOME/skills` 技能卡 + mcp-client 连思忆集。
- UI 接入：先用现有 Electron（经 DSH api-gateway/WS 发消息、收事件），不推翻前端投资。

### 方案 B：DSH Web 即壳（自定义 client 插件承载业务页）

- DSH Web 的 chat/agent UI 最成熟（goal/jobs/skills/子代理/工作流都有现成 UI 座位），把笔记/词库/新闻看板做成 slot 注册的 React 组件。
- 代价：需要 clone deepseek-harness 仓库并用其构建链（tsdown）编译 client 插件 bundle，开发重、跟随上游版本负担大；DSH Web 是 chat-first，做"个人数据工作台"要自建不少 UI。
- 定位：DSH 体系稳定、你想彻底放弃 Electron 之后的远期选项。

### 方案 C：维持现状路线 + 借鉴 DSH 模式（不推荐）

- 即执行 `docs/agent改进方案-借鉴ClaudeCode与DSH.md`，在 Python 里重造 hooks/持久子代理/goal/技能/工作流/插件运行时。
- 4 个月的实践已证明这条路产出"玩具"；文档中的 B1（持久子代理）、B3（goal）、B4（技能）正是 DSH 现成物，重复造轮子性价比最低。

### 对比表

| 维度 | A 推荐 | B | C 现状 |
|---|---|---|---|
| 到达"可用"的速度 | 快（内核白拿，壳已有） | 中（UI 白拿，插件要写） | 慢（全自建） |
| 保留现有投资 | 高（数据服务+Electron） | 低（数据服务，UI 重写） | 高 |
| 内核质量/维护成本 | 低（交给 DSH） | 低 | 高 |
| 个人数据看板 | Electron 继续演进 | 自定义插件（重） | 现状 |
| 风险 | DSH rc 版本演进 | 同上 + 构建链重 | 无新依赖但无改善 |

---

## 五、推荐落地路线（分阶段）

### S0 · 冻结范围（1 周内）
- 明确"壳 = 数据 + 服务 + 3~5 条功能路径"（建议首期：①每日新闻摘要 ②笔记/待办 ③记忆问答 ④单词学习 ⑤收件箱/通知）。
- 归档/冻结：`automation_hub/`、PyQt6 入口（`main.py`/`ui/`）、桌宠、浏览器扩展、web-ui/、dist/；云同步保留为可选服务。
- **搁置** `agent改进方案-借鉴ClaudeCode与DSH.md`，在仓库记录本决策（指向本报告）。

### S1 · 思忆集瘦身为"个人数据服务"
- 保留：`repositories/`、`domain/`、`services/`（info_collector_service / info_radar / info_categorizer / info_translator / notification_dispatcher / reminder 等）、`data/`、`sync/`（可选）、存储类 `backend/handlers/`。
- 冻结为"不再演进、仅修 bug"：`assistant/`、`ai/`、`mcp/`、`workflow/`、`evolution/`、`automation_hub/`。
- 现有 3450 测试继续作为数据层回归基线；把"AI 调用"从 handler 中摘除（chat/AI 类 RPC 改由 DSH 承载）。

### S2 · 新增 MCP Server 适配层（约 1~2 周）
- 用 Python FastMCP（`mcp` SDK）新写 `思忆集app/mcp_server/`（与现有 `mcp/` 客户端并存或替换）。
- 首批工具建议：
  - `siyi_news_collect(topics)` / `siyi_news_summarize(urls)` / `siyi_news_categorize(items)`
  - `siyi_notes_search / siyi_notes_create / siyi_notes_update`
  - `siyi_tasks_list / siyi_tasks_create`
  - `siyi_memory_save(text)` / `siyi_memory_search(query)`
  - `siyi_inbox_add(content, tags)` / `siyi_notify(title, content, priority)`
- 传输先用 `stdio`（DSH 同机 spawn），后续可加 `streamable-http`。

### S3 · DSH 自定义 profile + preset + skills（1 周）
- 新建 profile（如 `~/.dsh/profiles/siyi`）：bundles = `@deepseek-ai/dsh-base` + `@deepseek-ai/dsh-web-app`，patch 层追加：`dsh-mcp-client`（连思忆集 MCP Server）、`dsh-schedule`、`dsh-web-search-deepseek`、`dsh-sandbox`（`DSH_PERMISSION_MODE=workspace-write`，审批 ask）。
- 复制 standard preset 为 `siyi-personal`：启用 `tool-subagent-codex` / `tool-subagent-claude-code`，替换 persona 为个人助理人格（可移植现有 persona 文案）。
- 在 `$DSH_HOME/skills/` 写技能卡：`daily-news-digest`（每日新闻摘要）、`inbox-triage`（收件箱分类）、`memory-anchor`（记忆锚点）等，front-matter 声明触发词。

### S4 · 端到端验收"每日新闻摘要"（见第六节）
- 验收标准：连续 7 天，07:00 前后 15 分钟内自动产出当天摘要，写入思忆集资讯库并在桌面弹出通知；中途断网/进程重启可恢复（利用 DSH session 持久化）。

### S5 · UI 走向决策（1 个月后）
- 先用现有 Electron：前端把聊天/任务入口指向 DSH（经 api-gateway 或 `dsh --profile headless` 包装 RPC），数据页仍走思忆集 WS。
- 若 DSH Web 体验明显更好且自定义插件成本可控，再评估迁移到方案 B（保留思忆集 MCP Server 不变，UI 换壳）。

### S6 · 退役自建内核
- `assistant/` 等冻结模块整体移入 `archive/`（项目已有此惯例），仅保留 `trace_recorder` 数据导出迁移到 DSH session 记录。

---

## 六、示例端到端：每日新闻摘要

1. **数据**：思忆集 MCP Server 提供 `siyi_topics_list`（用户主题，来自 settings）、`siyi_news_save`（写 info_items 库）、`siyi_news_categorize`、`siyi_notify`。
2. **技能**：`$DSH_HOME/skills/daily-news-digest/SKILL.md` —— 定义流程：读主题 → 对每个主题 `web_search` → 去重/筛选 → `siyi_news_categorize` + 摘要 → `siyi_news_save` → `siyi_notify`（title/摘要/链接）。
3. **触发（二选一或都用）**：
   - 常驻会话内：DSH schedule 工具 `schedule_create`（`at: "2026-08-16T07:00:00", time_zone: "Asia/Shanghai"`，prompt = "运行 daily-news-digest 技能"）→ 到点由 session 的 root agent 执行；
   - 进程外：Windows 任务计划程序 07:00 运行 `dsh --profile headless "运行 daily-news-digest 技能"`（headless 一次性会话，同样可调 MCP 工具）。
4. **通知**：`siyi_notify` 经思忆集现有 `notification_dispatcher`（WebSocket + 桌面 toast）送达；DSH 侧也可用 jobs/goals UI 展示执行状态。
5. **可靠性**：DSH session 事件日志为唯一事实源，崩溃/重启后冷恢复；工具调用带超时（dsh-tool-call-timeout-policy）；审批 ask 模式对高危险作保持人在环。

---

## 七、风险与边界（诚实清单）

1. **DSH 是 rc.6**：版本演进快、破坏性变更可能频繁。对策：锁定版本、定期跟随、**用 MCP 作为唯一耦合面**（MCP 是标准协议，即使 DSH 换掉，思忆集服务不受影响）。
2. **schedule 是 session 级提醒，不是独立 cron 服务**：需要常驻 root agent 或外部触发 headless。对个人单机场景够用，但要接受这个边界。
3. **DSH Web 是 chat-first**：个人数据看板（笔记/词库/新闻卡片）要么写自定义 client 插件（需 clone 仓库 + 构建链，重），要么保留 Electron。短期应保留 Electron。
4. **DSH 只有 MCP 客户端，没有 MCP 服务端**：思忆集需要自己写 server（FastMCP，Python，工作量小）。
5. **"替换内核"≠"不用写壳"**：数据路径、领域服务、UI、通知仍然要维护——这正是思忆集的价值所在，也是省不掉的部分。省掉的是 agent 循环、工具调度、安全闸门、持久化会话这些**成熟商品**。
6. **双栈并存**：Node/TS（DSH）+ Python（思忆集）两套工具链，运维面多一个常驻进程；建议 DSH 用独立 profile 目录与端口，避免与现有 18765 冲突。
7. **数据分布**：思忆集业务数据在 `思忆集app/data/`（SQLite），DSH 会话/技能/预设/凭据在 `~/.dsh/`——需要明确备份策略（DSH 的 `~/.dsh` 也要纳入备份）。
8. **移动端/跨设备**：手机端走思忆集 WS 的路径可保留（DSH 无需上手机）；若未来要手机直连 agent，再评估 DSH headless 包装为远程服务。

---

## 八、生态参照（既有知识，需线上核实）

> 本环境 web 搜索不可用，以下为截至既有知识的状态，落地前请在线核实各项目最新定位与版本。

- **MCP（Model Context Protocol）**：Anthropic 2024-11 开源，2025 年起成为 agent 工具互操作事实标准（Claude Code、Codex、Cursor、众多 IDE/客户端支持）。DSH 的 mcp-client 正是该协议。思忆集转 MCP Server 有现成 Python SDK（`mcp` / FastMCP）。
- **ACP（Agent Client Protocol）**：agent 客户端 ↔ agent 后端协议，DSH 内置 ACP 桥（自动化桥可机器应答审批），可用它把 DSH 会话接到其他 ACP 客户端。
- **Claude Code**：Anthropic 官方 CLI，成熟度最高；headless（`claude -p`）可作外部内核，但模型绑定 Anthropic，且定位是编码 agent。DSH 已内置其 provider。
- **OpenAI Codex CLI / Agents SDK**：开源编码 agent / Python agent 框架；DSH 的 pi-ai 目录含 openai-codex 路由，codex provider 可作子代理。
- **smolagents**（Hugging Face）：Python 库，思忆集已在用；可嵌入但仍是"自建 harness"路线，不改变现状。
- **LangGraph**：Python 编排库，适合要自己搭图的团队，对个人场景过重。
- **n8n / Dify / Flowise**：GUI 自动化/Agent 平台，做"每日新闻摘要"类管道很成熟；但没有个人数据域（笔记/记忆/词库）与本地沙箱审批深度，可作为 DSH 之外的可选补充，非必需。
- **结论**：在你"个人数据壳 + 可替换成熟 agent 内核"的需求上，DSH 是**重合度最高的单一方案**；n8n/Dify 是纯自动化备选，Claude Code/Codex 是 DSH 之下的执行子内核，不冲突。

---

## 九、信息来源清单

**DSH（本机安装源码，版本 0.1.0-rc.6）**
- `node_modules/@deepseek-ai/dsh/README.zh.md`（profile/入口模式）
- `node_modules/@deepseek-ai/dsh-base/cordis.patch.yml` + `README.md`（base 组成；"Codex and Claude Code providers load dormant"）
- `node_modules/@deepseek-ai/dsh-web-app/cordis.patch.yml` + `README.md`（web 组成、client 插件 roster、slot 系统）
- `node_modules/@deepseek-ai/dsh-agent-presets/standard/agent.cordis.yml`（codex/claude-code provider 工具行）
- `node_modules/@deepseek-ai/dsh-schedule/README.md`（持久提醒语义）
- `node_modules/@deepseek-ai/dsh-mcp-client/README.md`（MCP 客户端双传输）
- `node_modules/@deepseek-ai/dsh-headless/README.md`（一次性会话）
- `node_modules/@deepseek-ai/dsh-subagent/README.md`、`dsh-api-gateway/README.md`、`dsh-api-remotes/README.md`、`dsh-tool-web/README.md`、`dsh-web-search-deepseek/README.md`、`dsh-session-query/README.md`、`dsh-client-ui-slots/README.md`

**思忆集（本仓库）**
- `AGENTS.md`（架构/337 RPC/测试基线）
- `docs/agent改进方案-借鉴ClaudeCode与DSH.md`（A/B 线清单）
- `docs/frontend-status-report-2026-08-09.md`、`docs/landing-plan-2026-08-09.md`
- `README.md`（automation_hub"已弱化"）
- `思忆集app/assistant/`（butler/judge/cc_bridge/codex 等）、`思忆集app/mcp/`（client + server_manager）、`思忆集app/services/info_*`（资讯栈）
- git 提交：`6cf44e20`（agent_loop 零引用归档）、`1bb0f410`~`8df4da5e`（前端阶段 A–D）
- 仓库根 8 月初 `siyi_desktop_crash_*.log` ×20+

---

## 十、功能路径 × 内核替换映射（调研）

> 用户核心功能需求：**个人知识体系、每日新闻推送**等。本节逐条调研：需求 → 思忆集现状（代码证据）→ 成熟外部方案（**需线上核实**）→ DSH 内核下的实现方式 → 成熟度评估。
> **核心洞察**：这 7 条路径思忆集**全部已有自建实现且功能不弱**——问题从来不是"没有功能"，而是 agent 执行质量、端到端可靠性与交互成熟度。因此每条路径的"内核替换"形态完全一致：**数据留思忆集，执行换 DSH**。

### 内核替换的通用模式（四层）

```
数据层   思忆集 SQLite + 领域服务（不动，继续用现有 337 RPC / 存储层）
适配层   思忆集暴露 MCP Server 工具（siyi_*，新写，FastMCP）
执行层   DSH 技能卡（skill）承载"流程"；schedule/外部 cron 承载"触发"；
         codex / claude-code 子代理承载"重型执行"
交互层   Electron（短期）→ DSH Web + 自定义插件（远期评估）
```

### 路径 1：每日新闻摘要与推送 ★ 最高优先级

- **需求**：每天自动按我的主题搜集→筛选→总结→推送，可读、可归档、可回查。
- **思忆集现状（已实现）**：`services/info_collector_service.py` 支持 **20 个源**（HN/Dev.to/GitHub Trending/Product Hunt/TechCrunch/Ars/The Verge + Solidot/开源中国/掘金/36氪/InfoQ/少数派/机器之心/阮一峰周刊/爱范儿/cnBeta 等），多源并发抓取 + AI 翻译 + 相似度去重（阈值 0.72）+ 简报生成（上限 40 条）；`info_radar.py` RSS 扫描 + 焦点话题网络搜索；`info_categorizer.py` / `info_translator.py` 分类翻译；每日 07:00 `morning_brief` 晨报（proactive.triggered 事件 + 通知白名单链路）。**功能完整，缺的是"agent 自主筛选/个性化"质量与端到端稳定。**
- **成熟外部方案（需核实）**：Feedly / Inoreader（RSS+AI 摘要）、Readwise Reader（稍后读+AI 总结）、Kill the Newsletter（邮件转 RSS）、RSSHub（万物转 RSS）、Telegram Bot 推送；n8n 模板做"定时抓取→LLM 摘要→推送"。
- **DSH 内核实现**：技能卡 `daily-news-digest`（读主题 → 每主题 `web_search` → 调 `siyi_news_categorize/summarize/save` → `siyi_notify`）+ 触发（schedule `at 07:00` 常驻会话，或 Windows 任务计划调 `dsh --profile headless "运行 daily-news-digest"`）。
- **评估**：闭环最短、收益最直接，**S4 验收路径**。

### 路径 2：个人知识体系（笔记 + 图谱 + RAG 问答）★ 高优先级

- **需求**：长期积累个人知识，能检索、能关联、能问答；最好支持"卡片→链接→图谱→问答"。
- **思忆集现状（已实现）**：`knowledge/` 有 `graph_store` + `graph_extractor`（实体/关系抽取）+ `unified_service`（跨源统一查询）+ `retrieval/` 完整 RAG 管道（`document_parser` / `embedding_service`（ChromaDB + sentence-transformers）/ `rag_pipeline` / `retriever` / `vector_store`）；`memory/` 有 `linker`（记忆关联）、`narrative/`（周叙事/里程碑/情感轨迹）；notes 有 auto_tag / generate_summary；`knowledge.unified_query` RPC 已存在。
- **成熟外部方案（需核实）**：Obsidian（本地 Markdown + 双链 + 图谱，生态最成熟）、Logseq（大纲+块引用）、Notion（云）、Zettelkasten/PARA/MOC 方法论；知识问答类：AnythingLLM / Open WebUI（RAG 聊天）、Khoj。
- **DSH 内核实现**：技能卡 `knowledge-digest`（读笔记 → 提炼要点 → `siyi_knowledge_link` 建关联 → 写图谱）；问答改走 DSH 会话（模型质量高）+ `siyi_knowledge_search` MCP 工具（复用思忆集 RAG/图谱，DSH 负责推理与表达）；`siyi_notes_*` 工具做增改查。
- **评估**：思忆集 RAG/图谱资产值得保留；**把"问答推理"从自建 react 引擎换成 DSH 模型即可显著提质**。

### 路径 3：长期记忆系统 ★ 高优先级

- **需求**：记住用户偏好、事实、对话上下文，跨会话一致；不泄露隐私。
- **思忆集现状（已实现）**：`memory/` 全套——`index` / `local_index_engine`（本地语义索引）、`search`、`linker`、`structured_store`、`narrative/`（`weekly_narrative` 周报、`milestone_detector`、`emotion_tracker`、`narrative_injector`）；对话锚点（`memory.save_conversation_anchor`）；反馈学习闭环（`chat.feedback.*`，偏好摘要注入 system prompt）。
- **成熟外部方案（需核实）**：ChatGPT/Claude 云记忆（绑定模型）、Mem.ai、Rewind / Limitless（自动记录）、Anki（间隔重复）；DSH 侧 = session 持久化 + `~/.dsh/sessions` 冷恢复本身就是"会话记忆"。
- **DSH 内核实现**：DSH 负责**会话级记忆**（session 日志持久化、冷恢复、title/查询），思忆集负责**长期语义记忆**（`siyi_memory_save / siyi_memory_search` MCP 工具）；技能卡 `memory-anchor` 在关键对话后自动落库；每周叙事可改为 schedule 周触发。
- **评估**：两者是互补而非重叠——**不要用 DSH 会话替代思忆集长期记忆**（会话会压缩/冷恢复，语义记忆要留 SQLite + 向量库）。

### 路径 4：收件箱与任务管理（GTD）★ 中优先级

- **需求**：快速收集（剪藏/语音/随手记）→ 分类 → 转任务 → 到期提醒。
- **思忆集现状（已实现）**：inbox 全套（add/classify/relay）、tasks（list/create/schedule/due 事件）、notification dispatcher（`notification.delivered` / toast）、proactive triggers（idle/clipboard/url_intercept 等）、`inbox.relay` 转发。
- **成熟外部方案（需核实）**：Todoist / Things / Microsoft To Do（成熟跨端）、Obsidian Tasks 插件；GTD 方法论。
- **DSH 内核实现**：技能卡 `inbox-triage`（读 inbox → 逐条分类/提炼 → `siyi_tasks_create` / `siyi_notes_create` / 归档）；`siyi_inbox_list` MCP 工具；定期触发（schedule 每小时或手动）。
- **评估**：思忆集已有完整链路，DSH 只需替换"分类/转写"的智能层。

### 路径 5：词汇与语言学习 ★ 中优先级（思忆集特色）

- **需求**：阅读/翻译中积累生词 → 闪卡复习（间隔重复）→ 测验。
- **思忆集现状（已实现）**：wordbook/word 全套 RPC（词库、FSRS 复习、听写、词义/完形/看义写词测验、AI 例句、云端 enrichment）、翻译历史与生词提取（`translate.extract_words`）。
- **成熟外部方案（需核实）**：Anki（FSRS 鼻祖，生态最大）、欧路词典、Readwise（阅读中高亮收集）。
- **DSH 内核实现**：技能卡 `wordbook-digest`（阅读/翻译后自动把生词经 `siyi_word_add` 入库）；复习调度**保留思忆集 FSRS**（DSH 不重复造）；DSH 仅在"生成例句/释义"时作为高质量模型后端。
- **评估**：FSRS 调度是确定性强逻辑，**不该搬进 agent**；此路径 DSH 介入最浅。

### 路径 6：定时提醒与通知 ★ 中优先级

- **需求**：任务到期、每日要点、事件提醒推送到桌面/手机。
- **思忆集现状（已实现）**：reminder RPC、cron service（APScheduler + polling 双引擎）、notification dispatcher（白名单事件 + 桌面 toast + WS）、手机端通知。
- **成熟外部方案（需核实）**：系统日历/任务应用自带提醒；n8n schedule 节点。
- **DSH 内核实现**：**DSH schedule 是 session 级提醒，不适合做全局提醒**——提醒继续用思忆集 cron + notification；DSH 只负责"到期后执行 agent 任务"（经 `dsh --profile headless` 触发或 MCP 回调用）。
- **评估**：边界清晰：**提醒 = 思忆集，执行 = DSH**。

### 路径 7：自动化执行（"帮我做"）★ 内核侧能力

- **需求**：让 agent 真正执行操作（查资料、写文件、调 API、跑脚本），带审批与审计。
- **思忆集现状（已实现）**：ActionBroker 闸门链、sandbox 审批、audit/rollback、tool_specs 声明式工具、cc_bridge/codex 外部 agent 桥——**但这正是要退役的自建内核**。
- **成熟外部方案（需核实）**：Claude Code / Codex CLI（成熟执行器）、MCP 生态（文件/数据库/浏览器/API 工具服务器）、n8n（管道执行）。
- **DSH 内核实现**：DSH 原生——工具（fs/bash/pwsh/web）、沙箱三档 + 审批 ask、后台 jobs、子代理（codex/claude-code）、`mcp__*` 外部工具。**这条路径 100% 由 DSH 接管，思忆集无需再写。**
- **评估**：替换收益最大的一条；也是"玩具感"的直接解药。

### 优先级与实施顺序建议

| 顺序 | 路径 | 理由 | 预估 |
|---|---|---|---|
| 1 | 每日新闻摘要（路径 1） | 闭环最短、需求最明确，验证 DSH+思忆集组合 | 1~2 周 |
| 2 | 知识问答（路径 2 问答部分） | 复用现有 RAG，DSH 提推理质量 | 1~2 周 |
| 3 | 收件箱/任务（路径 4）+ 记忆锚点（路径 3） | 日常高频，agent 提质明显 | 2 周 |
| 4 | 词库/提醒（路径 5/6） | 已有实现成熟，DSH 介入浅 | 按需 |
| 5 | 自动化执行（路径 7） | DSH 原生能力，随 preset 启用即得 | 配置级 |
