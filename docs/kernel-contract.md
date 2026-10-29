# 思忆集 · 执行内核契约（kernel-contract）

> **版本**：v1.3（2026-08-31 E1/E2：AiTextService 端口新增 extract/embed 可选能力，按 §8 新增能力计 minor；同日 A1③ 曾 bump v1.2.1——新增 §2.5.1 params.kernel 统一词汇映射表，文字澄清不改行为；原 v1.2 2026-08-15 第三轮：确认壳保留轻量 AI、chat 三级路由、人设壳注入、触发留壳执行交内核、自动降级+状态提示）
> **性质**：稳定文档。这是"功能路径层（壳）↔ 执行内核（agent）"之间的**全部约定**。任何新内核接入、任何壳侧改动，都必须遵守本文；修改本文需在 `kernel-journal.md` 登记变更理由。
> **读者**：项目主人（架构决策参考，见 §9 决策记录）与未来接入任意新内核的人或 AI。
> 配套：施工计划 `kernel-migration-plan.md`｜日志 `kernel-journal.md`｜总览 `kernel-layering-design-2026-08-15.md`。

---

## 0. 一句话总结

**壳只认两个端口（`AiTextService` / `AgentGateway`）和一批 MCP 工具名（`siyi_*`）；内核只是端口的某个实现 + 一个 MCP client。换内核 = 换一个适配器类 + 一行配置。**

---

## 1. 分层不变量（Invariants）

任何改动不得违反。每条附验证方法（CI/评审必查）。

| # | 不变量 | 验证方法 |
|---|---|---|
| I1 | **反向依赖**：`backend/` `services/` `repositories/` `app_runtime/` 只允许 `from services.agent_gateway.protocol import ...`（端口），不得 import 适配器模块、内核 CLI 包装、`assistant/` 内核本体 | grep：四目录下 `from services.agent_gateway.adapters|from assistant|import smolagents` = 0（Legacy 实现与迁移期白名单除外，见迁移手册） |
| I2 | **数据经 MCP**：内核读写思忆集数据**只能**经 `siyi_*` MCP 工具；内核的 shell/文件操作属于内核自己的沙箱，壳不感知 | 评审 + MCP 工具清单 diff |
| I3 | **壳自有轻量 AI（长期形态，非过渡态）**：翻译/分类/摘要/嵌入/简单问答等高频小调用走壳内 `AiTextService`（模型链 deepseek-v4-flash -> moonshot -> 本地 ollama），**不依赖内核进程存活，也不随内核更换而改变** | 关停全部内核进程，跑 `translate/note auto_tag/inbox classify` 功能用例 |
| I4 | **配置即切换**：`agent_kernel.type` 是换内核的唯一改动点；不得出现"换内核还要改壳代码" | 契约测试 CT-06 + 切换演练记录 |
| I5 | **单向事件**：内核→壳只经 MCP 工具调用（`siyi_notify` / `siyi_confirm_request`），不存在内核→壳的推送通道；壳→内核只有 prompt 与配置 | 评审 |
| I6 | **降级不崩溃**：内核不可用（未装/挂起/超时）时，功能路径必须继续工作（降级链见 §2.4），数据读写永不受影响 | 契约测试 CT-02/CT-09 + `type=none` 冒烟 |

### 1.1 稳定区 / 可换区边界（Zone 契约）--本契约的核心

> 目标：**下次换内核（DSH -> 任何新 agent）时，稳定区一行代码不动。**

| 区 | 成员 | 规则 |
|---|---|---|
| **稳定区**（换内核零改动） | ① 数据层：`repositories/` + `data/`（SQLite WAL）② 功能路径：`services/`（业务）+ `backend/handlers/`（337 RPC）+ 壳内 AI（AiTextService）③ 调度层：`cron_service.py` + `task_queue.py` + **`services/cron_actions.py`（action 注册表，§6.1）** ④ 前端协议：337 RPC 签名 + WS 事件 + chat 流式 chunk 协议 + `confirmation.resolve` ⑤ **工具能力层（2026-08-15 决议）**：外部工具注册表（ToolSpec + Registry + GenericToolBridge，执行手册 §5.4.1 迁 `services/tooling/`）+ `siyi_tool_run`/`siyi_tool_list` MCP 工具 + sandbox/ConfirmationStore 审批（v1：MCP 进程对 risk=high 结构化拒绝，审批权在壳）--任何大脑（内核）经统一通道调用外部工具（codebuddy/gh/任意 CLI），换内核该能力不变 ⑥ **记忆与知识层（2026-08-15 补全）**：`memory/`（索引/搜索/链接/提取/叙事）+ `knowledge/`（图谱/RAG）--依赖仅 ai_client（M1.5 收口后完全干净），属个人数据资产，不随内核变 ⑦ **观测层（2026-08-15 补全）**：`trace_recorder`（M5 迁 services/）+ `usage_tracker` + `audit_rollback` + `system_health`--跨内核统一执行观测与成本归因（trace 前缀 `kernel:*`） | **禁止** import 任何内核/适配器/`assistant/`；对外只经端口（AgentGateway/AiTextService）与 MCP server 暴露数据 |
| **可换区**（换内核时替换/保持） | ① 大脑与循环：AgentGateway 适配器（dsh/claude_code/codex/...）+ 内核进程本身 ② 手：`mcp_server/`（siyi_* 工具面--**换内核时保持不变**，属接口不属于任何内核） | 只经 protocol.py / factory 注册表 / MCP 协议接入；不反向依赖稳定区内部实现 |

**边界仲裁规则**：
1. 稳定区 -> 可换区：只允许 `from services.agent_gateway.protocol import ...`（端口类型）；
2. 可换区 -> 稳定区：内核只能经 MCP 工具读写数据；适配器代码不得 import `repositories/`（MCP server 的 `SiyiStore` 是唯一数据门面）；
3. **调度层与内核的接缝 = `services/cron_actions.py` 注册表的 `kernel.*` action**（cron 触发 -> gateway）。现状审计（2026-08-15）：cron 执行分发仍走 `app_runtime/_context_cron.py::_execute_cron_action` -> `autonomous_assistant_manager.router`（= `assistant/capability_router`）--**这是稳定区对内核的隐性依赖，M5 归档 assistant 前必须先迁移**（见施工手册 M-cron），否则"下次换内核不影响调度层"不成立；
4. 前端协议冻结面：`chat.send`（含 stream chunk 形状）、`confirmation.*`、全部数据 RPC--换内核前后签名零变化（验收 A 系列）；
5. 数据层多进程并发：`sqlite_store.py` 已 WAL + `busy_timeout=10000` + `synchronous=NORMAL`（backend 与 mcp_server 双进程并发读写的基础已就绪，实测确认）；mcp_server 写入必须携带幂等键语义（§10-OQ1）。

### 1.2 AiTextService 端口契约（代码事实，`services/ai_text_service.py`）

不变量 I3 的壳内轻量 AI 端口。方法面（v1.3 起）：

| 方法 | 签名要点 | 说明 |
|---|---|---|
| `translate` / `classify` / `summarize` | async | v1 既有三件套 |
| `health` | sync，返回 dict | 模型链可用性 |
| `extract(text, schema, system_prompt="", temperature=0.1, max_tokens=800) -> dict` | **sync** | E1（2026-08-31）：受约束 JSON 抽取。底层复用 E3 json_mode（`supports_response_format` 能力探测 + 本地端点降级为提示词强约束）；schema 序列化注入提示词；解析失败带纠错提醒重试 1 次；最终失败抛 `AIServiceError`（端口既有错误约定） |
| `embed(texts) -> list[list[float]]` | **sync** | E2（2026-08-31）：批量向量化。委托 `knowledge/retrieval/embedding_service.py::EmbeddingService.get_embeddings`（组合复用，零重复向量逻辑，批处理/offline 行为由被委托方保持） |

规则：功能路径需要结构化抽取或向量化时**一律走本端口**，不得绕过端口直调 ai_client 或 EmbeddingService；新增实现（如未来换壳内模型链）只需满足 Protocol 签名 + 契约测试（fake 范式见 `tests/test_ai_text_service_json_mode.py`）。sync 决策与 `complete()/health()` 一致，async 消费者经 `asyncio.to_thread` 包裹。

---

## 2. AgentGateway 端口

### 2.1 v1 --已落地（代码事实，`services/agent_gateway/protocol.py`）

```python
@dataclass
class AgentRun:
    run_id: str = ""
    status: str = "pending"      # succeeded | failed | unavailable | pending
    result: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)   # 适配器自有细节
    @property
    def ok(self) -> bool: ...

@runtime_checkable
class AgentGateway(Protocol):
    name: str                                                    # 与注册键一致，如 "dsh"
    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun: ...
    def health(self) -> dict[str, Any]: ...
```

**语义约定（v1）**：
- `submit` 为**同步阻塞**实现（异步调用方必须 `asyncio.to_thread` 包裹）；
- `health()` 形状：`{"kernel": str, "available": bool, "exe": str|None, ...}`（available=False 时功能路径跳过该内核，不得 spawn 进程试探）；
- `AgentRun.raw` 建议键：`returncode`、`usage`（见 §5.2）、`session_ref`（v2）；
- 已注册适配器：`none`（降级）/ `dsh` / `claude_code` / `codex`；注册表 `AGENT_GATEWAY_ADAPTERS`（`factory.py`）。

### 2.2 v2 --演进目标（M2 落地，见施工手册；此处为契约定义）

新增三件事：**事件流**、**会话续接**、**取消**。

```python
@dataclass
class AgentRunEvent:
    kind: str            # "status" | "delta" | "step" | "confirm" | "result" | "error"
    payload: dict        # 见下表
    ts: float            # time.monotonic()

class AgentGateway(Protocol):   # v2 完整形状
    name: str
    def submit(self, prompt: str, timeout_s: int | None = None) -> AgentRun: ...
    def submit_stream(self, prompt: str, timeout_s: int | None = None) -> Iterator[AgentRunEvent]: ...
    def resume(self, session_ref: str, prompt: str, timeout_s: int | None = None) -> AgentRun: ...
    def cancel(self, session_ref: str) -> bool: ...
    def health(self) -> dict[str, Any]: ...
```

**事件 payload 契约**：

| kind | payload 必含 | 说明 |
|---|---|---|
| `status` | `phase: "started" \| "working"` | 生命周期状态 |
| `delta` | `text: str` | 增量正文（chat chunk 直接映射） |
| `step` | `tool: str, ok: bool, duration_ms: int`，可选 `args_preview: str`（截断 ≤200 字符） | 工具/子任务观测；**不得含参数全文**（防敏感数据进日志） |
| `confirm` | `confirmation_id: str, title: str, detail: str` | 请求用户审批（写入 ConfirmationStore §4.2） |
| `result` | `text: str`，可选 `session_ref: str, usage: dict` | 终止事件·成功 |
| `error` | `error_kind: str, message: str` | 终止事件·失败（error_kind 见 §2.3） |

**事件序列状态机**（任何适配器必须保证，CT-07 校验）：

```
有效序列： (status started)? (delta|step|confirm|status working)* (result | error)
规则： ① 恰好一个终止事件（result 或 error），其后不得再有事件
      ② confirm 事件发出后允许继续 delta（用户批准前后都可能干活）
      ③ 首事件必须是 status started 或直接终止
```

**默认实现兼容**：协议提供 `submit_stream` 的默认包装（submit -> 单个 result/error 事件），旧适配器零改造即合规；流式解析是增强而非义务。

**会话语义**：`session_ref` 是**内核侧**的会话句柄（DSH session / Claude `session_id` / codex session），壳原样存储（chat session metadata）并在 `resume` 时传回；壳**不解析**其内容。无会话能力的内核返回空 ref，`resume` 退化为 `submit`。

### 2.3 错误分类学（error_kind）

`AgentRun.status="failed"` 时，适配器**必须**在 `AgentRun.raw["error_kind"]`（v2 事件 `error.payload["error_kind"]`）填入以下之一；降级路由据此决策：

| error_kind | 含义 | 典型来源 | 壳侧行为 |
|---|---|---|---|
| `unavailable` | 内核不存在/配置缺失 | CLI 不在 PATH、profile 缺失 | **跳过本内核**（等价 status="unavailable"，不重试） |
| `timeout` | 超时 | TimeoutExpired | 本次失败、用户可见；连续 N 次→标记 degraded（§5.3） |
| `auth` | 认证失败 | claude 未登录 / codex auth 过期 | 告警通知 + 跳过本内核 |
| `quota` | 配额/欠费 | 402/429 持续 | 告警 + 转 fallback 链（RoutingGateway） |
| `internal` | 内核崩溃/非零退出 | crash、JSON 解析失败 | 可重试 1 次，再失败则本次失败 |

**v1 现状**：`unavailable` 已用 `status="unavailable"` 表达；其余四类 v1 统一为 `failed`，M2a 起补 `error_kind`（CT-04）。

### 2.4 配置 schema（settings 键 `agent_kernel`）

```jsonc
{
  "type": "dsh",                  // none | dsh | claude_code | codex | routing(v2.5)
  "chat_route": "gateway",        // gateway(默认走内核,A1终态) | legacy(butler兜底) | shell(壳内直答)
                                 //   Flip 已翻转（2026-08-16 默认 gateway）；迁移期曾默认 legacy，现运维可设 legacy 秒级回退
                                 //   运行时用户覆盖：chat.send params.kernel（完整词汇与行为见 §2.5.1 映射表）
  "profiles": {                   // DSH 双 profile 成本分级（A1 决议 D6）
    "fast": { "dsh_profile": "siyi-fast" },   // 日常/轻交互：默认 deepseek-v4-flash；V6a 通过后可切 hy3（执行手册 §2.5）
    "deep": { "dsh_profile": "siyi-deep" }    // deepseek-reasoner，硬问题/重任务
  },
  "dsh":         { "profile": "headless", "cwd": null, "timeout_s": 240 },
  "claude_code": { "model": null, "max_turns": 8, "timeout_s": 240 },
  "codex":       { "model": null, "timeout_s": 240 },
  "routing": {                    // type="routing" 时生效（M2.5）
    "default": "dsh",
    "rules":  [ { "match": ["代码","编程","重构","bug","实现","写个"], "kernel": "codebuddy" } ],
    "fallback": ["codebuddy", "dsh", "claude_code"],
    "on_error": "next"            // next=失败转移链 | stop=直接失败
  },
  "health": { "ttl_s": 60, "degraded_after": 3 }   // M2a 健康缓存与降级阈值
}
```

解析规则（`factory.parse_agent_kernel_value`，已实现）：dict / JSON 字符串均可；未知 `type` → 降级 `none` 并 warning；子配置按键合并默认值；适配器构造参数按签名过滤（`adapter_kwargs`）。

---


### 2.5 chat 三级路由契约（A1 决议 D2）

优先级从高到低，高级别命中即短路：

| 级 | 来源 | 取值 | 说明 |
|---|---|---|---|
| L1 用户指定 | chat.send 参数 kernel / 前端选择器 | fast / deep / dsh / claude_code / codex（完整词汇见 §2.5.1） | 用户指定必须按用户为准（用户原话）；前端"深度模式"入口落在这里 |
| L2 壳自动分流 | gateway_path 内置轻量预判 | 轻交互->fast；多步/工具/长上下文->deep；极简问法->shell 直答 | 关键词+长度启发式起步；预判不合适时允许在回复尾部附建议（如"这个问题建议用深度模式"），不强制改道 |
| L3 settings 默认 | agent_kernel.chat_route | gateway（A1 终态） | 全局兜底；**Flip 已完成（2026-08-16 默认 gateway）**，运维设 legacy 可秒级回退 |

降级链（选定路径失败时逐级下探）：deep -> fast -> legacy(butler) -> shell(simple_stream)。
降级状态通知（决议 D5 "auto-degrade with notices"）为**两级语义**：
- 显式降级：用户经 `params.kernel` 主动指定内核却落空 → chat 内必发 notice chunk（"深度模式不可用，已用经典模式回答"），知情权优先；
- 隐式降级：默认路由（L2/L3）降级 → 不发 chat notice，仅 StatusBar 内核徽章 + `kernel.status` degraded 标记（避免每次请求刷屏）；
- 节流：同原因 5 分钟内只发一次。

### 2.5.1 params.kernel 统一词汇映射表（A1③，2026-08-31）

代码事实源：`services/agent_gateway/routing.py`（`get_gateway` + `resolve_dsh_profile`）。`chat.send` 的 `params.kernel` 全量词汇与各自行文如下，前后端一律以本表为准：

| params.kernel | 生效前提 | 路由行为 | 代码路径 |
|---|---|---|---|
| 缺省 / `auto` / 空 | — | 使用 settings 注入网关；DSH profile 取 `dsh.profile`（默认 headless） | `get_gateway` 透传 + `resolve_dsh_profile` |
| `fast` | 不依赖任何开关 | DSH profile 强制 `profiles.fast.dsh_profile`（默认 siyi-fast） | `resolve_dsh_profile`（`_PROFILE_DEFAULTS`） |
| `deep` | 不依赖任何开关 | DSH profile 强制 `profiles.deep.dsh_profile`（默认 siyi-deep） | 同上 |
| `dsh` / `claude_code` / `codex` | `agent_kernel.per_request_selection = true`（A1① 起默认 true） | 按指定内核构造**临时网关**（纯对象构造，不回写 settings）；注册表未命中该内核时回落注入网关 | `get_gateway` → `_build_temp_gateway`（`_PER_REQUEST_KERNELS`） |
| `dsh` / `claude_code` / `codex` | 开关为 false | 忽略 per-request 指定，使用注入网关 | `get_gateway` 透传 |
| `shell` / `legacy`（历史词汇） | — | 不在 `_PER_REQUEST_KERNELS`，行为等同缺省（注入网关）。壳内直答 / butler 兜底由 `chat_route` 配置控制，**不是** per-request 参数 | — |

存量说明：`per_request_selection` 开关 2026-08-31 前默认 false，现翻转默认 true；老配置未持久化该键的，由 `core/settings_migration.py` 守卫式一次性补写 true（用户显式保存过该键、含 false 的，不覆盖）。

### 2.6 prompt 组装契约（人设壳注入，决议 D3）

gateway_path 提交给内核的 prompt 结构（顺序固定）：

```
[1] 人设块（壳渲染）：小问 persona + 当前情绪状态（数据存壳内 repositories，assistant/persona 迁 services/）
[2] 上下文锚点块（壳渲染）：用户画像 / 偏好摘要 / 记忆锚点（复用 build_messages 既有产出）
[3] 会话历史块：近 N 轮（内核有 session_ref 时可省略，由内核自管）
[4] 用户消息
```

- 人设/情绪数据永存壳内，注入只是渲染——换内核人设不丢；
- V6b 实测验证中文人设效果后方可把 chat_route 默认翻转为 gateway。

---
## 3. MCP Server 数据边界规范

### 3.1 协议事实

- 实现：`思忆集app/mcp_server/`（`protocol.py` / `store.py` / `tools.py` / `server.py`），**纯 Python 标准库，零 MCP SDK 依赖**；stdio 传输；协议版本 **2025-06-18**；方法：`initialize` / `notifications/*` / `ping` / `tools/list` / `tools/call`。
- 启动：`python 思忆集app/mcp_server/server.py [--data-dir PATH]`（缺省用生产数据目录）。
- `SiyiStore` 只依赖 `data/` + `repositories/`，**禁止** import `assistant/` / `backend/`（独立进程安全）。

### 3.2 工具注册规范

- 命名 `siyi_<域>_<动作>`；域 ∈ {news, notes, tasks, inbox, memory, knowledge, notify, confirm, health, ...}；
- `input_schema` 为 JSON Schema object；返回统一 JSON 文本（`ensure_ascii=False`）；
- **description 必须含使用场景**（内核据此决定何时调用，AGENTS.md 既有约定）；
- 换内核时工具名与 schema **不得变更**（I2/验收）。

### 3.3 工具权限分级（M2a 起强制执行）

每个工具必须声明级别；`SiyiStore` 层按 settings `agent_kernel.mcp_tool_policy`（默认 `allow_all`，可选 per-kernel 白名单）执行拦截。

> **源真值纪律（2026-08-20 修正）**：MCP 工具清单以 `思忆集app/mcp_server/tools.py` 的 `build_tool_specs` 为源真值，2026-08-20 实测 **33 个 `siyi_*` 工具**；cron action 注册表以 `思忆集app/services/cron_actions.py` 为源真值，2026-08-20 实测 **17 个已注册 action**（含 `kernel.submit` / `kernel.cron_demo` / 4 张技能卡）。下表为级别语义与代表性归属（示例，非穷举）——任何清单增删以代码源真值为准，文档不得自行维护。

| 级别 | 语义 | 代表性归属（示例，非穷举） |
|---|---|---|
| `RO` | 只读，无副作用 | `siyi_health` `siyi_notes_search` `siyi_tasks_list` `siyi_inbox_list` `siyi_news_list` `siyi_knowledge_query` `siyi_memory_recall` `siyi_confirm_status` ... |
| `RW` | 写入/创建，可逆或不破坏既有数据 | `siyi_notes_create` `siyi_tasks_create` `siyi_inbox_add` `siyi_news_save` `siyi_notify` `siyi_confirm_request` `siyi_tool_run` ... |
| `DANGER` | 删除/覆盖/不可逆 | （暂无；未来 notes_delete 等必须落此级并强制 ConfirmationStore） |

**新增工具的规则**：默认 `RO`；写操作 `RW`；删除/覆盖/批量操作 `DANGER`（必须走 §4.2 审批）。

### 3.4 扩展路线（占位）

memory 域（save/search/anchor）、knowledge 域（search/link）在 M4 随技能卡需要补入；远程（手机经 Tailscale 连 MCP）启用前必须加 token 认证条款（届时修订本节并登记 journal）。

---

## 4. 审批与密钥安全模型

### 4.1 双层审批边界

| 层 | 管什么 | 谁审批 |
|---|---|---|
| 内核沙箱层 | 内核自己的 shell/文件操作（DSH workspace-write、codex sandbox、claude permissions） | 内核自带机制；壳不干预 |
| 壳数据层 | 思忆集数据写入/删除（notes.delete、DANGER 级 MCP 工具、跨数据域批量动作） | **ConfirmationStore**（用户在前端点批准） |

两层互不越权：内核不得绕过壳审批直接改 SQLite（它根本没有 DB 句柄--只有 MCP）；壳不管内核内部文件操作。

### 4.2 ConfirmationStore 契约（M2a 从 butler 抽出，`services/confirmation_store.py`）

```python
class ConfirmationStore:
    """进程内单例 + TTL。纯状态管理，不执行任何动作（执行留给调用方）。"""

    def create(self, action_id: str, args: dict, *, source: str,
               title: str = "", detail: str = "", ttl_s: int = 300) -> str:
        """创建待审批条目，返回 confirmation_id（uuid4 hex）。"""

    def resolve(self, confirmation_id: str, approved: bool) -> dict | None:
        """pending -> approved/rejected；一次性（非 pending 返回 None）。"""

    def get(self, confirmation_id: str) -> dict | None: ...
    def list(self) -> list[dict]:            # pending 在前 + 最近已处理
    def purge_expired(self) -> int: ...
```

**条目形状**（与 butler 现状兼容 + 扩展）：

```jsonc
{ "confirmation_id": "...", "action_id": "notes.delete", "args": {...},
  "status": "pending|approved|rejected|expired",
  "source": "butler|mcp:<kernel>|gateway",     // 谁发起的
  "title": "...", "detail": "...",             // 给用户看的人话
  "created_at": 0.0, "expires_at": 0.0 }
```

**防自我批准不变量（核心安全条款）**：
1. `resolve()` 只能由 backend JSON-RPC `confirmation.resolve`（前端用户点击）调用；
2. MCP 工具**只能** `siyi_confirm_request`（创建）与 `siyi_confirm_status`（查询）--**协议上不存在**内核侧 resolve 入口；
3. confirmation_id 由壳生成；一次性消费；TTL 默认 300s；
4. `source` 标记发起方，前端展示（用户能看到是哪个内核在请求）。

### 4.3 siyi_confirm_* MCP 工具（M2a 新增，2 个）

| 工具 | 级别 | 语义 |
|---|---|---|
| `siyi_confirm_request(title, detail, action_id, args_json)` | RW | 写入 ConfirmationStore（source=`mcp:<kernel 名由 server 侧注入，内核不可伪造>`），返回 confirmation_id |
| `siyi_confirm_status(confirmation_id)` | RO | 查询状态（内核轮询用） |

> 内核等待批准的姿势：发起 `siyi_confirm_request` 后**继续轮询 status** 或直接结束本次 run，用户批准后由壳侧（或下一次 run）续跑--不阻塞、不挂起。

### 4.4 密钥注入模型

| 主体 | 密钥来源 | 规则 |
|---|---|---|
| 内核进程 | **自带认证**：claude 本机登录态 / dsh profile 内置 / codex auth login | 壳**不转发**自己的任何 API key 给内核（DeepSeek/Moonshot key 永不出壳） |
| MCP Server | **零密钥** | 只读写本地数据目录；不持有任何 API key |
| 壳 | settings/SecureKeyStorage 既有体系 | 只喂 `AiTextService`（Legacy 链） |

原则：**密钥不跨层**。内核花内核自己的钱；换内核不动壳的密钥配置。

### 4.5 F-1 回环自批准威胁模型（2026-09-17，P-A 探针基线）

**已声明的信任边界**

- 本产品假设**单用户、本机运行**。WS 默认绑定 `127.0.0.1`（N-D 批）+ token / Origin 校验，防的是"**同机其他程序或浏览器页面**"这一层。
- R4 审批队列防的是 **LLM 自主发起的越权动作**：内核侧协议上不存在 resolve 入口（§4.2 不变量），模型只能*请求*批准、不能自批。它**不防本机同用户进程**——同用户进程可以直连回环、并在同一条连接上自批准。

**已知残余风险（接受，不修）**

| 风险 | 实测事实 | 为何接受 |
|---|---|---|
| R-1 本机同用户进程自批准 R4 | 通过握手的 loopback 连接无条件 `trusted_ui=True`；同一连接发 `sandbox.approve` 即自批执行（P-A `test_anon_loopback_self_approves_r4_by_design` 端到端实证） | 单用户模型下"能连本机 loopback = 已在受信主机 = 全权"。审批闸门的目标是 LLM 失控，而非本机用户本人 |
| R-2 本机原生进程免凭证可调任意 RPC | loopback 免 token 放行；Origin 白名单仅挡浏览器 CSWSH，挡不住本机 exe | 同上；强制本机 token/session 会破坏单机零配置体验，收益与成本不匹配 |
| R-3 非白名单 Origin 的本机页面 | 握手期 close 1008 | 已封堵，非残余风险 |

**若威胁模型变更**（结论："本机也可能有不受信进程"）：加固路径是明确的——对无凭证的 loopback 连接置 `trusted=False` 强制走审批，并引入本机 token/session 强制；届时 R-1 / R-2 升级为 P1。**当前不实施**。

基线来源：`pa-auth-probe-report-0917.md`（P-A 项 F-1 / F-4）。后续安全评审以本节为基线，新增的回环相关结论追加于此，不散落在各批报告中。

---

## 5. 可观测性规范

### 5.1 Trace

- 复用 `assistant/trace_recorder.py` 的 `exec.step` 事件订阅（`TraceRecorder.subscribe(event_bus)`）；
- gateway 每次执行：`start_run(task_type="kernel:{name}")` → 每个 `step` 事件一条 `record_step` → `finish_run(status)`；
- 结果：`trace.list / trace.stats` 天然包含内核执行（跨内核统一观测，无需改 trace_recorder）。

### 5.2 Usage / Cost

- 适配器**必须**尽力从内核输出提取用量填 `AgentRun.raw["usage"]`：

```jsonc
{ "prompt_tokens": 0, "completion_tokens": 0,   // int | null（拿不到填 null）
  "cost_usd": 0.0,                               // float | null
  "model": "..." }
```

- claude_code：`-p --output-format json` 的结果含 usage/cost 字段，直接映射；
- dsh headless：无用量输出 → `usage=null`，壳按 `len(prompt)+len(result)` 字符估算记 1 次调用（`UsageTracker.record(source="kernel:dsh")`）；
- codex：`exec --json` 事件中的 token 计数（可用时）；
- **预算口径**：`usage.dashboard` 聚合时 kernel 调用单列 `by_source`，与壳内 LLM 调用分开可见。

### 5.3 健康监控与自动降级

- `health()` 结果缓存 TTL（settings `agent_kernel.health.ttl_s`，默认 60s）；
- **入口前置探测**：chat/cron 走 gateway 前 `available=False` → 直接走降级链，不 spawn 进程；
- **degraded 标记**：连续 `degraded_after`（默认 3）次 `timeout` → 标记 degraded → 发 `ai.usage_alert` 类通知 + 自动走降级链；探活成功自动恢复；
- 手动兜底永远可用：`type=none`。

### 5.4 日志与命名

- logger 命名空间：`siyi.agent_gateway.*`（factory/adapters/gateway_path）；
- 每次 submit 记一行 INFO：`kernel=%s prompt_len=%d timeout=%s status=%s error_kind=%s duration_ms=%d`；
- prompt 全文**不进日志**（隐私），只记长度与 trace_id。

---

## 6. 新内核接入 SOP（八步，任何新 agent 照此接入）

> 目标：**一周内**新内核可用、可切、可回。每步产出写入 journal。

| 步 | 动作 | 产出 / 通过标准 |
|---|---|---|
| **S0 评估五问** | ① 有 headless CLI（非交互、stdin/stdout 可驱动）？② 是 MCP client？③ 支持 Windows？④ 有会话/流式能力？⑤ 计费可控（自带 auth）？ | 五问答卷记 journal；①②③ 为硬门槛，缺一不接 |
| **S1 选型登记** | 在 journal 追加选型条目（对比矩阵一行 + 理由） | 无争议即过 |
| **S2 适配器骨架** | `adapters.py` 新增 `<Name>Gateway`：`name` / `__init__(超时等参数)` / `health` / `submit` | 类通过签名检查（`adapter_kwargs` 过滤生效） |
| **S3 健康检查** | `health()`：CLI 探测（`shutil.which`）+ 必要配置自检，**不 spawn 进程** | `available` 布尔可靠 |
| **S4 错误归一化** | 把内核退出码/stderr 映射到 §2.3 五类 error_kind | 对照表写进适配器 docstring |
| **S5 流式解析**（v2） | 实现 `submit_stream`（解析内核的流式输出为事件序列，遵守状态机） | CT-07 绿 |
| **S6 会话映射**（v2 可选） | 提取 `session_ref`、实现 `resume`；无能力则明示退化 | resume 行为登记 |
| **S7 注册 + 配置** | `AGENT_GATEWAY_ADAPTERS["<key>"] = <Cls>` 一行 + `DEFAULT_AGENT_KERNEL_CONFIG` 补默认子配置 | `build_gateway({"type":"<key>"})` 可用 |
| **S8 验收演练** | ① 契约测试全绿（§7）② 切换演练：`type` 切到新内核跑 chat 冒烟 + 1 个 MCP 工具调用 → 切回原内核 ③ journal 记录 | 演练记录 + 回切确认 |

---

## 7. 契约测试清单（`tests/test_agent_gateway_contract.py`，M2a 建立）

任何适配器注册前必须通过的**参数化**用例（以 mock/fake 内核驱动，不花真钱）：

| # | 用例 | GIVEN-WHEN-THEN |
|---|---|---|
| CT-01 | 健康形状 | 调 `health()` THEN 返回含 `kernel:str` + `available:bool`，且 `kernel == name` |
| CT-02 | unavailable | CLI 缺失（PATH 置空）THEN `submit` 返回 `status="unavailable"`，**不抛异常、不挂起**（<1s） |
| CT-03 | 超时 | 内核睡眠 > timeout_s THEN `status="failed"` + `raw.error_kind="timeout"`，耗时至多 timeout+5s |
| CT-04 | 错误归一化 | 伪造退出码/stderr 样例 THEN error_kind ∈ 五类枚举，message 非空 ≤400 字符 |
| CT-05 | 成功形状 | 正常输出 THEN `status="succeeded"` + `result` 非空 str |
| CT-06 | 注册一致 | `AGENT_GATEWAY_ADAPTERS[name]` 能构造，`name` 属性与注册键相等；未知 type → NoneGateway |
| CT-07 | 事件序列合法（v2） | `submit_stream` 事件流满足状态机（首事件、唯一终止事件、无终止后事件、step 无参数全文） |
| CT-08 | resume 尽力而为（v2） | 无会话内核 `resume(ref, ...)` 不抛且等价 submit；有会话内核 ref 透传 |
| CT-09 | 降级不抛 | 上述任一失败场景下，`gateway_path` 层降级到 butler/simple_stream，chunk 流正常终止（done 或 error chunk） |
| CT-10 | 配置过滤 | 含未知键的子配置构造适配器 THEN 不 TypeError（adapter_kwargs 生效） |
| CT-11 | mcp_tool_policy 拦截（2026-08-20 登记，实现本周批次） | Given settings `agent_kernel.mcp_tool_policy` 配置 per-kernel 白名单 When 内核经 MCP `tools/call` 调白名单外工具 THEN 被拦截并返回明确错误（无副作用），白名单内工具正常放行 |
| CT-12 | usage source 归因（2026-08-20 登记，实现本周批次） | Given 内核经 gateway 执行一次（submit/resume）When 用量写入 THEN `usage_records.source` 为 `kernel:<name>` 且 `usage.dashboard by_source` 出现对应行；shell 直调保持 `shell` |
| CT-13 | params.kernel 词汇一致性（2026-08-20 登记，实现本周批次，P0） | Given 契约/前端/后端三处 `params.kernel` 取值 When 逐处核对 THEN 词汇表统一（契约 `fast/deep/shell/legacy` ↔ 前端选择器 ↔ `routing.resolve_dsh_profile` 映射），UI 所选内核与后端实际执行一致 |

现有 `tests/test_agent_gateway.py`（22 用例）覆盖 CT-01/02/05/06/10 的雏形；M2a 起参数化重构并补齐 CT-03/04/07/08/09。

---

## 8. 版本与变更管理（最小条款）

1. **本文版本**：语义化。端口签名/MCP 工具名/安全条款变更 = major；新增可选能力 = minor；文字 = patch。变更必登记 journal。
2. **MCP 协议版本**锁定 2025-06-18；升级需全适配器回归（tools/list + tools/call 冒烟）。
3. **内核版本演进**：适配器对内核 CLI 的依赖参数（如 `--output-format`）变更时，按 SOP S2-S8 重走一遍；DSH rc 期接口不稳，`DshGateway` 只依赖 headless 出入口（prompt→stdout），不依赖 rc 特有 flag。

---

## 9. 决策记录（ADR，按时间序）

> 评审级要求：每个关键决策有背景、选项、理由。推翻旧决策 = 新条目，不删旧条目。

| # | 决策 | 背景 | 备选与理由 | 日期 |
|---|---|---|---|---|
| D1 | **壳保留轻量 AI 能力（现状不增不减）** | 用户确认翻译/分类/摘要留壳；执行内核才换成熟 agent | 备选"全部交给内核"被否：内核挂则 AI 全灭；备选"provider 可插拔优先"被否：fallback 链已够 | 08-15 |
| D2 | **chat 三级路由：用户指定 > 壳分流 > 默认 gateway** | 用户原话"我可以自行切换，我指定的就得按我的为准，不合适可以提建议" | 备选"全走内核"被否（80% 轻交互又慢又贵）；"壳预判独裁"被否（预判错误体验割裂） | 08-15 |
| D3 | **人设/情绪壳注入 prompt** | 小问人设是产品功能须保留 | 备选"内核技能卡"被否（绑内核生态，换内核要重写）；"轻交互壳内答"保留为 shell 路径子集 | 08-15 |
| D4 | **触发留壳 + 执行交内核** | autonomy/triggers 是数据感知非内核 | 备选"全留壳"质量受限；"全交内核"依赖内核存活时段 | 08-15 |
| D5 | **内核不可用时自动降级 + 状态提示** | 降级体验完整性 | 备选"静默降级"被否（用户困惑）；"硬失败"被否 | 08-15 |
| D6 | **A1 路线：butler+PydanticAI 整体换 DSH harness（换循环不换模型）** | 病灶=单循环+12 次预算；用户用 deepseek-v4-flash 满意且便宜；DSH 模型可配 v4-flash，成本不变，循环升级，亚秒启动 | 备选"双轨 B（butler+委托工具）"被否：胶水本身是复杂度，用户"还不如直接换"；备选"换 CC"成本 10x 暂不取；**前置 V6 三关实测闸门**（流式/人设/审批） | 08-15 |
| D7 | **不换 butler 模型**（v4-flash 继任壳内 AiTextService 与 legacy 路径） | 用户明确 v4-flash 比 GLM-4.7 便宜实用 | "换 GLM 改善 butler"提案被用户否决 | 08-15 |
| D8 | **稳定区/可换区边界固化（§1.1）** | 用户要求"下次换时不影响数据层+功能路径+调度层+前端协议" | 审计发现调度层执行分发深嵌 assistant（_context_cron -> capability_router），必须迁出为 cron_actions 注册表 | 08-15 |
| D9 | **自研 MCP 客户端退役归档；未来壳内若需直连外部 MCP server，一律用官方 `mcp` Python SDK，不再自研** | 壳内 `mcp/`（stdio + HTTP-SSE 自研客户端）与成熟内核自带的 MCP 客户端重复实现，MCP 协议演进（锁 2025-06-18，§8 第 2 条）要壳侧自己跟版；项目线 B（2026-08-31）以删除实现统一 | 备选"保留自研并补测试"被否：双份客户端=双份协议跟版成本，且外部工具接入已由 ToolSpec 注册表 + 内核自带客户端覆盖；备选"连 `mcp_server/` 一起删"被否：那是纯标准库零 SDK 的**服务端**（§5 数据边界与 `siyi_*` 工具面），是内核访问个人数据的唯一通道，必须保留为壳内唯一 MCP 实现；`assistant/_router_mcp.py` 只保留宿主注入通道，未注入时按 D5 返回 `mcp_unavailable` 降级 | 08-31 |

## 10. 开放问题（Open Questions）

| # | 问题 | 影响面 | 当前倾向 |
|---|---|---|---|
| OQ1 | **MCP 写操作幂等**：内核任务超时重试后 notes_create 可能重复建两条（news_save 是 upsert 无碍） | cron kernel.* / 确认后续跑 | news/tasks/inbox 写工具统一 upsert 语义或加 client_op_id 幂等键 |
| OQ2 | **MCP 浏览器会话归属**：browser.* 需要 Playwright 常驻会话，跨进程（mcp_server 进程）持有可见浏览器 | MCP P1 工具面 | mcp_server 内常驻 browser bridge，或 browser 工具暂留壳内（chat 内核先不暴露） |
| OQ3 | **内核写操作审计**：kernel 来源的 DANGER/RW 写入未入 audit trail | 数据安全 | SiyiStore 写路径统一记 audit（source=mcp） |
| OQ4 | **DSH rc 接口演进风险**：headless 流式输出格式可能变 | DshGateway 流式解析 | 解析器容错（未知行忽略）+ 契约测试锁定最小事件集 |
| OQ5 | **CodeBuddy Code CLI 无头能力未知**（2026-08-15 探测：本机仅 IDE 插件版，无 CLI）：是否有 `-p`/`exec` 单次任务模式、输出可否解析 | CodeBuddyGateway 适配器可行性 + 快车道 ToolSpec | 装 CLI 后 S1 探测；不可行则等官方支持，claude_code 兜底 fallback |
| OQ6 | **重内核启动审批（launch_approval）**：chat/cron 路由命中 codebuddy/claude_code 等外部重内核时，先经 ConfirmationStore 审批再启动（用户决议："我审批同意后直接启动"）；dsh 等常驻内核免审 | gateway_path / RoutingGateway | 配置 `agent_kernel.launch_approval: ["codebuddy","claude_code"]`；M2b 落地；防自我批准机制自动适用 |