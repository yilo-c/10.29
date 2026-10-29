# 思忆集 — AGENTS.md

## 项目概述

> **AI 会话卫生守则（debt-v2 TASK-M6，2026-08-24；V2.1 增补 2026-08-25）**——任何 AI 执行会话必须遵守：
> 1. **开工前 `git status` 必须干净**（有未提交残留先确认归属再动，禁止叠加在别人/上一会话的半成品上）；
> 2. **收工必须 commit + push**（凭据可用时）；禁止 staged 残留（历史教训：约 250 个"已完成"文件曾长期滞留 staged 险些丢失，0 push 至今）；
> 3. **长任务分批小 commit**（一个原子改动一个 commit），commit message 带任务编号；
> 4. "完成"定义机械化（M1，2026-09-16 修订，见 deliverables/fix-and-upgrade-execution-plan-2026-09-16.md S2-4 用户拍板）：**commit SHA + `python scripts/gate_all.py` 绿**，纯文字 ✅ 不算完成。原定义「CI 绿 + 已 push」因 push/CI 结构性不可达（远端空仓、15 道门禁从未运行，实测确证）改为本地 gate_all（汇总 11 道静态门禁 + kwarg/facade 防复发门禁 + bundle 备份新鲜度≤7天）；远端 push 修复另案，恢复后回挂 CI。
> 5. **审计回归强制（V2.1 §8 #4）**：每轮新审计的第一个动作是回归上一轮所有 ✅ 修复——操作入口读 `deliverables/risk-register.md` 全表，逐行复测。「问题是否真的死了」优先于找新问题；复测方式必须引用测试文件 / CI job / 演练记录。
> 6. **干净会话复核（V2.1 §8 #5 新条）**：每批收工后开一个无上下文新会话，只给「验证矩阵 + 文件:行号 + git diff 范围」，令其独立复核输出「属实/不属实/行号漂移」三态表；任一不属实 → 该条降级回待修。本项在每批「完成」判定之前执行。
> 7. **covering 第 5 条**：修复侧优先把结论固化为机器断言（结构性测试 / CI 门禁 / 攻击链 PoC 先红后绿），弱化对任何单一会话持续诚实的依赖。
> 8. 违反本守则等于制造新债，修复优先于推进新任务。

思忆集是一个个人 AI 助手桌面应用，采用 **Electron 前端 (desk-notes)** + **Python 无头后端** 架构。跨设备链路为「手机端 ↔ 桌面 WS 直连 / ↔ Cloudflare Worker 云同步」，`automation_hub/` 为已被边缘化的可选独立底座（现行真源见 [docs/automation-hub-disposition.md](docs/automation-hub-disposition.md)）。

最终架构：
```
Electron (desk-notes/)                Python 后端 (思忆集app/)
┌─────────────────────┐    WS JSON-RPC     ┌────────────────────────────┐
│  desktop + mobile   │ ◄────────────────► │ backend/ws_server.py       │
│  + shared 子包      │    port 18765      │  method_registry.py        │
│  React/TypeScript   │                    │  event_bridge.py           │
│  Zustand / Tailwind │                    │  handlers/（62 模块）       │
│                     │                    │  340 个 RPC 方法            │
└─────────────────────┘                    │ AppContext (headless DI)   │
                                           │  AI / Memory / Assistant / │
                                           │  Cron / Plugin(插件 SDK)   │
                                           └────────────────────────────┘
         ▲                                                │
         │  Tailscale LAN 直连                           │ 云同步
         │  (WS JSON-RPC)                                ▼
┌───────────────────┐                    ┌─────────────────────────────┐
│ 思忆集手机端app/    │                    │ cloudflare/worker/          │
│  Capacitor+React  │ ◄── E2E 加密信封 ──►│  同步 Worker（AES-GCM v4）  │
└───────────────────┘                    └─────────────────────────────┘
         └── automation_hub/（FastAPI，端口 8000）— 已边缘化，仅按需独立部署
```

主仓库位于 `D:\h\3.25\思忆集`。

## 技术栈

- **前端 (desk-notes)**: Electron, React, TypeScript, TailwindCSS, Zustand（pnpm monorepo：desktop + mobile + shared）
- **后端 (思忆集app/backend)**: Python 3.11, asyncio, aiohttp WebSocket（JSON-RPC，340 方法）
- **手机端 (思忆集手机端app)**: Capacitor 原生壳 + React（5 Tab + 收件箱），独立项目，依赖 @desk-notes/shared
- **云同步**: cloudflare/worker（TypeScript，AES-GCM v4 端到端加密）
- **自动化底座 (automation_hub)**: FastAPI, SQLAlchemy, Pydantic, Alembic, ARQ worker（已边缘化，按需独立部署）
- **AI 核心**: DeepSeek (主), Moonshot (路由), Ollama 本地 qwen3-4b；工具/Agent 流由 butler 管家单循环（pydantic-ai）承接，smolagents 已于 2026-09-03 摘除（对话链路治理）
- **向量搜索**: ChromaDB, sentence-transformers
- **桌面 UI 全部经 Electron 前端访问**；PyQt6 仅残留于 `app_entry/main.py`、`workflow/`、`workbench/`、`assistant/chat_ui.py` 等旧桌面路径，不再作为使用入口

## 启动方式

### 无头后端 (Electron 模式，推荐)
```powershell
cd D:\h\3.25\思忆集\思忆集app
..\.venv3.11\Scripts\python.exe -m backend.headless_main
# 输出: 思忆集 backend ready -> ws://127.0.0.1:18765
```

也支持便捷脚本：
- `..\启动.py` / `..\启动.bat` — 仓库根目录一键启动（headless 模式）
- `思忆集app\.scripts\_start_backend.py` — 后端专用启动器
- `思忆集app\start_backend.bat` — 后端批处理启动

### Automation Hub（独立底座，已边缘化）
```powershell
cd D:\h\3.25\思忆集\automation_hub
..\.venv3.11\Scripts\python.exe -m api.main
# 默认端口 8000，见 .env.example
```
> 现状定位见 [docs/automation-hub-disposition.md](docs/automation-hub-disposition.md)；主战场是桌面 `思忆集app` 自带 WS 后端与 Cloudflare Worker 云同步，不要新增依赖 hub 的核心功能。

## 虚拟环境

- 路径: `.venv3.11\` (Python 3.11.15, uv 管理；3.13 也可运行；旧 `.venv` 为遗留路径，勿再引用)
- 桌面依赖: `requirements-desktop.txt`（PyQt6 仅为旧桌面路径遗留，新功能不依赖）
- 核心依赖: `pyproject.toml`
- Automation Hub 依赖: `automation_hub/requirements.txt`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SIYI_WS_HOST` | `127.0.0.1` | WebSocket 绑定地址（N-D 收口 2026-09-17：默认仅本机；Tailscale/LAN 直连须显式设 `0.0.0.0` 或指定网卡地址，且认证不可用时会 fail-closed 拒启） |
| `SIYI_WS_PORT` | `18765` | WebSocket 端口 |
| `SIYI_DATA_DIR` | (自动) | 数据目录覆盖 |
| `SIYI_DEBUG` | (空) | 设为 `1` 启用调试日志 |
| `SIYI_WS_MAX_MSG_SIZE` | `16777216` | WebSocket 最大消息体（字节） |
| ~~`SIYI_WS_ALLOW_URL_TOKEN`~~ | ~~`0`~~ | **已移除（2026-08-25 决策）**：`?token=` 已从 V3-01 迁移期门控转为手机/远程 WS 正式通道——浏览器 WS 无法带 header/cookie，有效主 token 走 URL 无条件放行（`ws_server.py` `_verify_auth`）；原门控语义被该分支完全覆盖，故删除，不再接受该环境变量 |
| `SIYI_WS_BROADCAST_TIMEOUT` | (空) | 事件广播超时（秒） |
| `SIYI_DESKNOTES_DATA_DIR` | (空) | Electron 端数据目录覆盖 |
| `SIYI_DISABLE_DESKNOTES_BRIDGE` | (空) | 置任意值禁用 desk-notes 桥接 |
| `SIYI_TRANSFER_KEY` | (空) | 双端传送 token |
| `SIYI_DEVICE_ID` | `desktop-main` | 云同步设备 ID |
| `SIYI_WORKER_URL` | (空) | Cloudflare Worker URL |
| `SIYI_SYNC_TOKEN` | (空) | Cloudflare Worker 同步 token |
| `SIYI_HEALTH_CACHE_TTL` | `10.0` | system.health 缓存秒数 |
| `SIYI_AI_CACHE_TTL` | (空) | AI 缓存 TTL（秒） |
| `SIYI_AI_CACHE_MAX` | (空) | AI 缓存最大条目数 |
| `SIYI_KIRO_API_BASE` | `http://127.0.0.1:8000` | Kiro API 地址 |
| `SIYI_KIRO_API_KEY` | `sk-kiro-local` | Kiro API Key |
| `SIYI_KIRO_HEALTH_TTL` | `60` | Kiro 健康缓存 TTL |
| `SIYI_LLM_API_BASE` | `https://api.deepseek.com/v1` | LLM API Base |
| `SIYI_LLM_MODEL` | `deepseek-v4-flash` | 默认 LLM 模型（原 `deepseek-chat` / `deepseek-reasoner` 官方已于 2026-07-24 退役，勿再用） |
| `SIYI_LLM_API_KEY` | (空) | LLM API Key |
| `SIYI_TRANSLATE_LOCAL_MAX_CHARS` | (空) | 本地翻译最大字符数 |
| `SIYI_AGENT_ALLOW_FILE_WRITE` | (空) | 设为 `1` 允许 agent 文件写入 |
| `SIYI_LOCK_TAKEOVER` | `1` | 僵死锁接管开关（P0-1）；0=关闭 |
| `SIYI_WATCHDOG_HEALTH_ENABLED` | `1` | watchdog 端口存活探测开关（P0-1）；GRACE/INTERVAL/FAILS 另可调 |
| `SIYI_WS_CLIENT_QUEUE` | `256` | 每客户端事件队列长度（P0-3） |
| `SIYI_CRON_ALERT_THRESHOLD` / `SIYI_CRON_ALERT_COOLDOWN` | `3` / `3600` | cron 连续失败告警阈值/冷却秒数（P0-4） |
| `SIYI_SANDBOX_TIMEOUT_SECONDS` / `SIYI_SANDBOX_TIMEOUT_POLICY` | `1800` / `reject` | 高危审批超时秒数（0=禁用）/策略 reject\|dry_run（P1-5） |
| `SIYI_RPC_TIMEOUT` | `60` | 集成测试 RPC 接收超时（原 30s 高负载下过紧） |
| `SIYI_INTEGRATION_AUTOSTART` | (空) | 置 `1` 时集成测试在后端不可达时自启临时后端（随机端口+临时数据目录，会话级，P2-3） |
| `SIYI_INTEGRATION_REQUIRED` | (空) | 置 `1` 强制要求后端在场，不可达时测试直接失败而非跳过 |
| `SIYI_HUB_PERSONAL_MIRROR_LOCK` | `1` | hub personal 域镜像锁（桌面唯一真源）；`0`=显式关闭（仅迁移期回退，V2-01） |
| `GH_TOKEN` | (空) | GitHub token — 通用工具的 `env_keys` 默认从这里读取（如 gh copilot）。优先级 1：系统环境变量 > SecureKeyStorage > settings 加密值 |
| `ANTHROPIC_API_KEY` | (空) | Anthropic API Key — claude_code 工具的 `env_keys` 默认从这里读取 |
| `API_TOKEN` | (空) | 通用 API token — http 工具样例的 `env_keys` 默认从这里读取 |
| `DEEPSEEK_API_KEY` | (空) | DeepSeek API Key（兼容旧配置） |
| `MOONSHOT_API_KEY` | (空) | Moonshot API Key |
| `OLLAMA_MODELS` | (空) | Ollama 模型配置 |
| `SMTP_PASSWORD` | (空) | SMTP 密码 |

## 关键目录

```
思忆集app/           # 桌面端主源码
   backend/          # ★ 无头后端（Electron 前端接口层）
     headless_main.py #   后端正入口（asyncio + 文件锁）
     ws_server.py    #   aiohttp WebSocket JSON-RPC 服务器
     method_registry.py # JSON-RPC 方法注册中心
     event_bridge.py #   EventBus → WebSocket 事件桥接
     handlers/       #   62 个 JSON-RPC handler 模块（340 个方法）
       _ai.py, _ai_chat.py, _ai_translate.py, _ai_health.py, _api_keys.py,
       _audit.py, _capabilities.py, _chat_memory.py, _cron.py, _dictation.py,
       _evolution.py, _external_content.py, _feedback.py, _inbox_crud.py,
       _inbox_data.py, _inbox_info.py, _inbox_knowledge.py, _inbox_web.py,
       _info_items.py, _info_proxy.py, _info_sources.py, _job_hunter.py,
       _kernel.py, _maintenance.py, _narrative.py, _notes.py, _notifications.py,
       _ocr.py, _personal.py, _process.py, _provider_health.py, _reading.py,
       _reminder.py, _remote.py, _rss.py, _sandbox.py, _skin.py, _sync.py,
       _system_settings.py, _tasks.py, _task_queue.py, _timeline.py, _tools.py,
       _trace.py, _trae.py, _transfer.py, _transcription.py, _url_learning.py,
       _weather.py, _wordbook.py, _word_extract.py, _agent_tasks.py,
       _confirmations.py, _bg_tasks.py, _chat_context_anchor.py,
       _ai_pattern.py, _ai_usage.py, _cascade_cleanup.py, _image.py,
       _json_utils.py, _path_guard.py, _wrap.py 等
   app_entry/        # 应用入口（main.py shim + __init__）
   app_runtime/      # 运行时上下文 + DI 容器
                     #   AppContext + mixins + providers/ + 配置管理
                     #   关键：app_context.py / ai_manager.py /
                     #   _context_*.py / service_registry*.py /
                     #   config_manager.py / desknotes_path.py
   ai/               # AI 核心
                     #   client/ 子包：base, fallback, config,
                     #   _judge_aware_client.py, _cache.py
                     #   aggregator, circuit_breaker, provider_quota,
                     #   provider_routing, llm_resilience, usage_stats
   core/             # 核心基础设施
                     #   audit_rollback, observability, settings*,
                     #   event_bus, rate_limiter, health_check,
                     #   provider_config, storage_transaction, sync_protocol 等
   assistant/        # 助手调度
                     #   judge, autonomy, intent_router, task_manager,
                     #   multi_agent_orchestrator, react_engine,
                     #   chain/, capability/, triggers/ 子包
   memory/           # 记忆系统
                     #   index, search, linker, extractor/, narrative/,
                     #   local_index_engine, structured_store
   data/             # 数据存储层
                     #   local_storage, sqlite_store, desknotes_id_map,
                     #   repositories/desknotes_*_adapter
   services/         # 业务服务
                     #   api_key_service, backup_service, copilot_engine,
                     #   cron_service, info_collector_service, info_radar,
                     #   inbox_capture_service, system_health, web_search,
                     #   url_learning_service, sandbox_manager 等
   domain/           # 领域模型
                     #   models/ (note, task, memory, vocabulary,
                     #   translation, assistant_task, behavior, ...),
                     #   repositories/, inbox_models, suggestion_models
   repositories/     # 数据仓库实现
                     #   _note, _task, _vocab, _inbox, _reminder,
                     #   _settings, _chat_session, _fsrs_scheduler,
                     #   _translation, _info_item, _proposal
   sync/             # 同步模块
                     #   cloud_sync_client, channels, delta_sync,
                     #   note_sync, task_sync, vocabulary_sync,
                     #   translation_sync, memory_sync, transport 等
                     #   （generic_sync_service/orchestrator 死拷贝已于
                     #   2026-09-23 审计波次①删除，生产编排在 api/sync_orchestrator.py）
   knowledge/        # 知识库与 RAG
                     #   graph_store, graph_extractor, source,
                     #   unified_service, retrieval/ (RAG 管道)
   integrations/     # 外部集成
                     #   kimi_sync, youdao_oauth_provider（youdao/llm/plugins
                     #   等空占位子包已于 2026-07 清理）
   mcp_server/       # MCP 服务端（纯标准库 JSON-RPC，零 SDK；自研客户端 mcp/ 已于
                     #   2026-08-31 退役归档至 archive/mcp-client-202608/）
   browser/          # 浏览器扩展桥接
   desktop/          # 桌面服务（doctor, pet, web_bridge, companion）
   evolution/        # 进化分析（orchestrator, sandbox, tech_radar, report_generator）
   adapters/         # 外部适配器（browser, todoist, youdao_note）
   plugins/          # 插件系统（registry/base/manifest/sandbox/permissions/
                     #   capability_router/subprocess_host）
   workflow/         # 工作流引擎（PyQt6 遗留 editor_canvas/node_config 已于
                     #   2026-09-23 审计波次①删除，余旧桌面代码）
   orchestration/    # 外部 Agent 编排（drivers/ + agent_directory）
   workbench/        # 工作台服务（含 PyQt6 遗留 controller 等旧桌面代码）
   web/              # TypeScript 桥接（appBridgeClient.ts）
   web_dist/         # 前端构建产物
   assets/           # 静态资源
   tests/            # 测试（378 个 .py 文件）

desk-notes/          # Electron 前端（当前桌面端）

automation_hub/      # 自动化底座（FastAPI，可选独立部署；已边缘化，见
                     #   docs/automation-hub-disposition.md）
   api/              #   FastAPI 主应用与路由
     routes/         #     agent, tasks, runs, tools, personal_*,
                     #     mobile, mobile_ws, mcp, cron, workflows,
                     #     evolution, knowledge_base, web_search 等
     auth/           #     认证与 RBAC
     audit/          #     审计
     approvals/      #     审批
     repos/          #     代码仓库
     proposals/      #     提案
     security/       #     安全扫描、数据分级
     tools/          #     工具注册
     db/             #     SQLAlchemy 模型与数据库操作
     intel/          #     情报简报
   agent/            #   Agent 规划/上下文/执行
   local_agent/      #   本地 Agent 运行时
   worker/           #   ARQ 异步任务 worker
   scheduler/        #   调度器
   scripts/          #   运维与数据脚本
   tests/            #   测试

思忆集手机端app/      # ★ 手机端 — Capacitor 原生壳 + React（v2.0.0，80+ 源文件）
                     #   5 Tab (todo/reference/notes/chat/settings) + 收件箱
                     #   同步引擎、AI 路由、悬浮球插件、PIN 锁屏
                     #   独立项目，依赖 @desk-notes/shared

claudejiaoben/       # 开发脚本
cloudflare/worker/   # Cloudflare Worker 同步服务（TypeScript）
docs/                # 文档（索引见 docs/INDEX.md，地图见 docs/document-map.md）
scripts/             # 仓库级脚本（build, health-check, quality-gate）
                     # （仓库根 tests/ 已于 2026-09-03 处置：4 个有效测试迁入 思忆集app/tests/，
                     #   7 个死/冗余文件删除；不要在仓库根新建 tests/）
desktop-pet-qinai/   # 七奈桌宠资源（Shimeji-ee，独立运行，与桌面 app 无代码依赖）
```

## WebSocket JSON-RPC 协议

### 连接
```
ws://127.0.0.1:18765/ws
```

### 请求格式
```json
{"jsonrpc": "2.0", "id": 1, "method": "method.name", "params": {}}
```

### 流式响应 (AI chat)
```json
{"jsonrpc": "2.0", "id": 1, "method": "chat.send", "params": {"message": "hi"}, "stream": true}
```
返回 chunk:
```json
{"jsonrpc": "2.0", "id": 1, "result": {"type": "chunk", "data": "你好"}}
```
结束:
```json
{"jsonrpc": "2.0", "id": 1, "result": {"type": "done"}}
```

### 服务端推送事件
```json
{"type": "event", "event_type": "mood.changed", "payload": {"mood": "happy"}}
```

#### 云同步事件（`sync.completed` / `sync.failed`）
由 `cloud_sync_client.py` 在每次 `sync_once` 结束时通过 EventBus → EventBridge → WebSocket 推送。前端可订阅以刷新 UI 状态。

**`sync.completed`**（push/pull 均成功时触发）：
```json
{"type": "event", "event_type": "sync.completed", "payload": {"device_id": "desktop-abc123", "push_ok": true, "pull_ok": true, "push_count": 3, "pull_count": 2, "cursor": 1718900000}}
```

**`sync.failed`**（push 或 pull 失败时触发，payload 与 completed 同结构，由 `push_ok`/`pull_ok` 标识哪一侧失败）：
```json
{"type": "event", "event_type": "sync.failed", "payload": {"device_id": "desktop-abc123", "push_ok": false, "pull_ok": true, "push_count": 0, "pull_count": 2, "cursor": 1718900000}}
```

**`sync.failed`**（认证错误 / 通用异常时触发，payload 携带 `error` 字段；认证错误额外带 `fatal: true` 并会停止同步循环）：
```json
{"type": "event", "event_type": "sync.failed", "payload": {"device_id": "desktop-abc123", "error": "register rejected: invalid register_secret or token", "fatal": true}}
```

#### 主动引擎事件（`proactive.triggered` / `proactive.suggestion`，P2.3）

由 `ProactiveTriggerEngine.deliver()` 与 `SuggestionEngine.analyze()` 通过 `core.events.event_bus` → `EventBridge` 推送。前端可订阅以实时显示主动建议气泡（无需等 inbox 刷新或下一轮 cron）。

**`proactive.triggered`**（每次主动建议投递前触发，含完整 trigger 元数据）：
```json
{"type": "event", "event_type": "proactive.triggered", "payload": {"trigger": "idle", "title": "空闲提醒", "content": "你已空闲 30 分钟", "priority": "normal", "source": "proactive", "source_id": "", "channel": "inbox", "tags": ["proactive", "idle"], "metadata": {"trigger": "idle", "idle_minutes": 30.5}}}
```

`trigger` 取值：`idle` / `morning_brief` / `content` / `system` / `emotion` / `selection` / `blank_hotkey` / `system_tray` / `clipboard` / `foreground_window` / `url_intercept` / `timed` / `unknown`。

**`proactive.suggestion`**（`SuggestionEngine.analyze()` 排序完成后触发，含 Top 3 建议）：
```json
{"type": "event", "event_type": "proactive.suggestion", "payload": {"suggestions": [{"id": "abc123", "text": "考虑添加快捷键...", "reason": "...", "priority": 90, "category": "habit", ...}], "total_ranked": 6, "top_category": "habit", "top_priority": 90}}
```

> **注意**：`inbox.delivered` 事件的 payload 自 P2.3 起新增 `metadata` 与 `tags` 字段（向后兼容），前端可据此区分"主动建议"与"普通收件箱条目"。但 `proactive.triggered` 是更精确的专属事件，建议优先订阅。

### 已注册方法（按域分组，共 340 个）

> **未文档化方法声明（B-10 处置，2026-08-27 首标；2026-09-13 复核）**：方法表是 INDEX.md 声明的
> 唯一源真值，但存在**存量未文档化方法**。
> **★ 2026-09-18 计数更正**：`scripts/rpc_census.py` 经 `registry.list_methods()`
> 取运行时真值 = **387**（本文此前记 375）。故上方"共 375 个"已更正为 387；
> **但下面"未文档化 99 项 / 基线清单 100 行"是按 375 算出的派生数，未同步重算**
> —— 重扫需另开一轮（表格行数亦未变），此处**刻意不静改**以免制造新的假账目。
> **★ 2026-09-21 部分重扫（知识图谱域）**：本文上方计数已按双口径实测更新为 **395**
> （`scripts/check_method_coverage.py` 与 `scripts/rpc_census.py` 一致）；知识图谱方法表
> 按代码全量重扫为 20 行并修正了三处参数签名漂移（KG-DEBT/MOB-KG 批注）。
> 同步从 `undocumented_methods_baseline.txt` 移除已文档化的 11 行 `knowledge.*`（基线 100→89）。
> **同日稍后（KG-MERGE/BACKFILL 批）**：本域新增 `knowledge.entity.merge` 与
> `knowledge.backfill` 两个方法（均随带文档入表），故上方计数再更新为 **397**；
> `docs/inventory.md` 属自动生成件，需由改动方跑 `python scripts/gen_inventory.py` 重生成
> （当下它已因另一批在 `_sandbox.py` 新增的 sandbox.action_probe 方法而 DRIFT，不属本域；
> 该方法名在本文故意不加反引号，以免被文档覆盖率检测误计为“已文档化”而替它销债）。
> **仍存遗留（不属本域，未动）**：其余 16 项新增未文档化（`clipboard.*`、`relay.*`、
> `notes.asset.*`、`*.status` 等）仍在，故 `--undocumented` 门禁本轮后**仍为红**；
> 非 knowledge 域的表格行数与其它域计数也仍未重扫。
> **★ 2026-09-23 审计波次①-c 批量下线（@58e5fa8）**：58 个 `@deprecated` 僵尸 RPC 已从注册表摘除
> （deprecated ∩ UNUSED_BASELINE ∩ 前端零调用三重确认；`file.write`/`notification.test` 作 R4 安全
> 回归样本暂留待重锚定），运行时真值 397→**339**（inventory 同步重生成）；本表已同步删 47 行登记，
> `undocumented_methods_baseline.txt` 89→78；上述 09-21“16 项新增未文档化仍红”叙述在本波后仍需重算
（下线僵尸不影响新增未文档化项）；旧计数叙述（387/395/397）保留作账目历史。
> **★ 2026-09-26（P4-KG 批）**：本域再加 `knowledge.entity.detail`（节点详情归一层），
> 339→**340**；`docs/inventory.md` 已随本次后端改动重跑 `scripts/gen_inventory.py` 同步。
> 原记录：实测注册 375 vs 本文档表格 277 行，
> 未文档化 99 项、基线清单 100 行且新增 0 / 失效 0；首标 2026-08-27 时为注册 372 / 表格 272 /
> 差额 100 项，含 job_hunter 22、knowledge.md_* 11、personal.* 7、rss.* 5、
> confirmation.* 3、external_content.* 5、auth.*/pairing.* 4、deprecated file.* 3
> 等，完整清单见 `scripts/undocumented_methods_baseline.txt`）。这些方法**显式
> 标注为「内部 / 实验域」**：按文档开发的前端不得依赖；新增未文档化方法由
> `scripts/check_method_coverage.py --undocumented` CI 门禁拦截（基线外→红）。
> 如需正式对外，须在本方法表补登记后同步移除基线行。

#### 自维护提案（P2-7 监督式自优化）

每日 05:00 巡检（`system.self_maintenance` cron）只生成提案不执行；执行必须经用户审批，白名单硬边界（仅队列清理/checkpoint 归档）。

| 方法 | 参数 | 说明 |
|------|------|------|
| `maintenance.proposals.list` | `status` | 提案列表（可按状态过滤） |
| `maintenance.proposals.approve` | `proposal_id` | 批准并执行白名单维护动作（含验证复查） |
| `maintenance.proposals.reject` | `proposal_id` | 拒绝提案 |

#### 执行观测与断点续跑（P1-1/P1-2）

统一执行观测：`assistant/trace_recorder.py`（SQLite）跨 butler/cron/task_queue 记录任务运行与步骤（各路径经 `exec.step` 事件上报，统一任务契约见 `domain/models/task_contract.py`）。

| 方法 | 参数 | 说明 |
|------|------|------|
| `trace.list` | `limit`, `task_type`, `status` | 任务运行列表（可按类型/状态过滤） |
| `trace.get` | `task_id` | 单任务运行详情（含步骤） |
| `trace.stats` | `days` | 近 N 天运行统计（成功率/类型/状态分布） |
| `assistant.unfinished_tasks` | — | 列出 checkpoint 可续跑的未完成任务 |
| `assistant.resume_task` | `task_id` | 从最后 checkpoint 续跑 |

> **启动行为**：headless 启动后扫描 checkpoint，存在未完成任务时经 EventBus 发 `task.resumable` 事件（含 count + 前 20 条摘要）供前端提示续跑。

#### 同步冲突裁决（P0-2）

| 方法 | 参数 | 说明 |
|------|------|------|
| `sync.conflicts.list` | — | 列出 sync_state=conflict 的笔记/任务 |
| `sync.resolve_conflict` | `resource`, `id`, `choice` | 裁决单条冲突：choice=remote 接受远端 / local 恢复本地快照（标 dirty 下次推回） |

> **事件**：同步拉取检测到真冲突时发 `sync.conflict` 事件（含 resource/id/title/双方时间戳）；裁决操作仅在桌面端进行，手机端只收通知（边界约束）。

#### 健康检查
| 方法 | 参数 | 说明 |
|------|------|------|
| `ping` | — | 健康检查 → `"pong"` |
| `siyi.ping` | — | 别名健康检查 |

#### 聊天与会话
| 方法 | 参数 | 说明 |
|------|------|------|
| `chat.send` | `message`, `session_id`, ... | AI 对话 (streaming)；多路径分发见 `backend/handlers/chat_paths/`：默认 butler 管家单循环（legacy 路由），AgentGateway 灰度需显式 `chat_route="gateway"` 或 `params.kernel`。AI 完全未配置（无云端 key 且未配置本地）时派发入口 fail-fast 立即发 `not_configured` 分型错误帧，不进回退链（V1②，2026-09-13）。`force_tools` 已随 smolagents 摘除（2026-09-03），传参被忽略 |
| `chat.history` | `session_id`, `limit` | 聊天历史（有 session_id 返回消息，否则返回会话列表） |
| `chat.list_conversations` | `limit` | 会话列表 |
| `chat.get_conversation` | `session_id` | 获取单个会话 |
| `chat.save_messages` | `session_id`, `messages` | 保存聊天消息 |
| `chat.compress_context` | `session_id` | 压缩对话上下文 |

##### 用户反馈学习闭环（P2.8）

记录用户对 AI 回复的反馈信号（👍/👎/编辑后重发），聚合为偏好摘要文本，在后续对话中通过 `backend/handlers/_chat_context_anchor.py::_preference_summary()` 自动注入到「对话锚点」system prompt 中（位于用户画像之后）。反馈样本存储在 `local_storage` 的 `chat_feedback` 键下（list[dict]，最多 500 条 LRU）；偏好摘要存储在 `chat_feedback_preference` 键下。聚合触发条件：👎 + edited 样本数 ≥ 阈值（默认 5，由 `chat_feedback_aggregate_threshold` 配置）；优先调用本地 qwen 提炼，AI 不可用时回退到规则兜底。

| 方法 | 参数 | 说明 |
|------|------|------|
| `chat.feedback.set` | `session_id`, `message_index`, `rating`, `original_user_msg`, `original_ai_msg`, `edited_text`, `comment` | 记录一条反馈；`rating` 必填（"up"/"down"）；其余可空。返回 `{recorded, feedback}` |
| `chat.feedback.list` | `session_id`, `rating`, `limit` | 查询反馈列表（按写入顺序倒序）；可按会话/评分过滤；默认 limit=50，最大 500 |
| `chat.feedback.stats` | `days` | 聚合统计：按 rating 计数 + 近 N 天每日分布 + 编辑计数 + 学习样本数 + 配置状态 |
| `chat.feedback.delete` | `feedback_id` | 按 ID 删除单条反馈 |
| `chat.feedback.preference.get` | — | 读取已聚合的偏好摘要文本 |
| `chat.feedback.preference.set` | `summary` | 手动覆盖偏好摘要（限长 600 字） |
| `chat.feedback.aggregate` | `force` | 触发一次偏好聚合；`force=False`（默认）仅当样本数 ≥ 阈值时执行；`force=True` 强制重新聚合 |

> **事件**：反馈写入后通过 `event_bus.emit("feedback.recorded", rec)` 发布事件，聚合完成后发布 `feedback.aggregated`。EventBridge 自动转发给前端。
>
> **注入路径**：`build_chat_context_anchor()` 在「用户画像」段之后追加「用户偏好（基于反馈学习）」段，每行以 `  - ` 前缀输出。偏好为空时跳过。
>
> **隐私**：聚合时只取最近 20 条学习样本，prompt 中原文截断到 200 字；偏好摘要限长 600 字，仅存规则不存原文。

##### 通知推送通道（P2.9）

统一通知分发器，订阅 EventBus 事件后按用户偏好过滤、构造通知、持久化并 fan-out 到多个通道。

- **通道抽象**：`NotificationChannel` 接口（`send` / `health` / `name`），两个内置实现：
  - `WebSocketChannel`：复用 EventBus → EventBridge 转发链，发布 `notification.delivered` 事件给所有 WebSocket 客户端
  - `DesktopToastChannel`：仅对 high/urgent 优先级通知触发，发布 `notification.toast` 事件给前端 hook（前端 `useDesktopNotifications` 调用 Web Notification API）
- **事件白名单**（`DEFAULT_SUBSCRIBED_EVENTS`，存储在 `notification_subscribed_events` 设置）：
  - `inbox.delivered` — 新收件箱条目
  - `proactive.triggered` — 主动建议
  - `vocabulary.due_reminder` — 单词到期
  - `task.due` — 任务到期
  - `sync.failed` — 仅 `payload.fatal=true` 时转发（避免同步瞬时错误噪音）
  - `job_hunter.new_jobs` — 招聘新岗位
  - `ai.usage_alert` — AI 用量告警
- **特殊过滤**：`sync.failed` 事件仅在 payload 携带 `fatal: true` 时才进入通知管道，瞬时错误直接跳过
- **持久化**：通知列表存储在 `local_storage` 的 `notifications` 键下（list[dict]，最多 500 条 LRU）
- **偏好过滤**：`notification_preferences` 字段（dict[event_type, {ws, toast, min_priority}]）控制每个事件类型是否进入 WebSocket 通道、是否触发 toast、最低通知优先级

| 方法 | 参数 | 说明 |
|------|------|------|
| `notification.list` | `unread_only`, `event_type`, `limit` | 通知列表（按写入顺序倒序）；`limit` 默认 50，最大 500 |
| `notification.mark_read` | `notification_id`, `read` | 标记已读/未读（`read` 默认 True） |
| `notification.mark_all_read` | — | 全部标记已读，返回更新条数 |
| `notification.delete` | `notification_id` | 按 ID 删除单条通知 |
| `notification.stats` | — | 聚合统计：总数/未读/按事件类型/按优先级 + 通道健康 + 订阅事件列表 + 总开关 |
| `notification.test` | `channel`, `event_type` | 向指定通道发送一条测试通知（诊断用）；`channel` 默认 `websocket` |

> **事件**：分发成功后通过 EventBus 发布 `notification.delivered`（WebSocket 通道）和 `notification.toast`（toast 通道，仅 high/urgent 优先级）。EventBridge 自动转发给前端。
>
> **启动接入**：`NotificationDispatcher.start()` 订阅所有白名单事件。生产环境应在 `headless_main.py` 启动时调用；当前 handler 通过 `_get_dispatcher(context)` 懒构建（生产路径建议显式注入 `context.notification_dispatcher`）。
>
> **设置键**：`notifications_enabled`（总开关，默认 True）、`notifications`（存储键）、`notification_preferences`（偏好 dict）、`notification_subscribed_events`（订阅白名单 list）。

#### 记忆系统
| 方法 | 参数 | 说明 |
|------|------|------|
| `memory.search` | `query`, `limit` | 语义搜索记忆 |
| `memory.delete` | `entry_id`, `reason` | 软删记忆条目（可溯源，U3） |
| `memory.list` | `limit`, `offset`, `include_revoked` | 全量分页列表（updated_at 倒序；管理面板数据源，U3 批1） |
| `memory.restore` | `entry_id`, `reason` | 恢复被软删条目（清 revoked 标记 + 审计 + 引擎重同步） |
| `memory.recent` | `limit` | ⚠ 名为「最近」实为 focus-topics 相关性召回（空查询兜底）且 ≤20 条；面板勿用（审计 §10） |
| `memory.related` | `memory_id`, `limit` | 相关记忆（基于 ID） |
| `memory.related_text` | `text`, `limit` | 相关记忆（基于文本） |
| `memory.save_conversation_anchor` | `session_id`, `anchor` | 保存对话锚点 |
| `memory.surface_for_input` | `text` | 输入时浮现相关记忆 |
| `memory.narrative.latest` | — | 最新记忆叙事 |
| `memory.narrative.types` | — | 叙事类型列表 |
| `tags.list` | — | 所有标签 |

#### 笔记
| 方法 | 参数 | 说明 |
|------|------|------|
| `notes.list` | `limit`, `include_empty`, `folder` | 笔记列表 |
| `notes.create` | `title`, `content`, `folder`, `tags` | 新建笔记 |
| `notes.get` | `note_id` | 获取单条笔记 |
| `notes.update` | `note_id`, `title`, `content`, ... | 更新笔记字段 |
| `notes.delete` | `note_id` | 删除笔记（软删除，需 sandbox 审批） |
| `notes.list_trashed` | — | 回收站列表 |
| `notes.restore` | `note_id` | 恢复回收站笔记 |
| `notes.permanent_delete` | `note_id` | 永久删除 |
| `notes.auto_tag` | `note_id` | AI 自动打标签 |
| `notes.generate_summary` | `note_id` | AI 生成摘要 |

#### 任务
| 方法 | 参数 | 说明 |
|------|------|------|
| `tasks.list` | `limit` | 任务列表 |
| `tasks.create` | `title`, `priority`, `due_date`, ... | 新建任务 |
| `tasks.get` | `task_id` | 获取单个任务 |
| `tasks.update` | `task_id`, `title`, `priority`, ... | 更新任务字段 |
| `tasks.delete` | `task_id` | 删除任务（软删除，需 sandbox 审批） |
| `tasks.toggle` | `task_id`, `completed` | 切换任务完成状态 |
| `tasks.schedule` | `task_id`, `schedule`, `run_at`, `repeat`, `reminder_text` | 安排任务（★ 2026-09-18 更正：原记 `task_id`,`due_date` 与 handler 实参不符；`task_id` 现接受 `str \| int`）|
| `tasks.unschedule` | `task_id` | 取消安排 |

#### 翻译
| 方法 | 参数 | 说明 |
|------|------|------|
| `translate.text` | `text`, `source`, `target` | 快速翻译 |
| `translate.full` | `text`, `source`, `target` | 完整翻译（含解析） |
| `translate.languages` | — | 支持的语言列表 |
| `translate.extract_words` | `text` | 提取生词 |
| `translate.word_detail` | `word`, `source`, `target` | 单词深度查询（音标/释义/同义词/例句，词库优先 + AI 兜底） |
| `translations.save` | `source_text`, `translated_text`, ... | 保存翻译历史（跨设备同步） |
| `translations.list` | — | 翻译历史列表 |

#### 词库与闪卡
| 方法 | 参数 | 说明 |
|------|------|------|
| `wordbook.list` | — | 词库列表 |
| `wordbook.create` | `name` | 创建词库 |
| `wordbook.rename` | `wordbook_id`, `name` | 重命名词库 |
| `wordbook.delete` | `wordbook_id` | 删除词库 |
| `wordbook.import_batch` | `wordbook_id`, `words` | 批量导入单词 |
| `word.list` | `wordbook_id`, `limit` | 单词列表 |
| `word.add` | `wordbook_id`, `word`, `definition`, ... | 添加单词 |
| `word.update` | `word_id`, ... | 更新单词 |
| `word.delete` | `word_id` | 删除单词 |
| `word.dictation_check` | `word_id`, `user_input` | 听写检查（含 SM-2 评分建议） |
| `word.review` | `word_id`, `rating` | 单词复习 |
| `word.list_due` | `wordbook_id`, `limit` | 到期单词列表 |
| `word.count_due` | `wordbook_id` | 到期单词计数 |
| `word.enrich_cloud` | `word_id` | 云端 enrichment |
| `word.generate_examples` | `word_id` | AI 生成例句 |

#### 收件箱
| 方法 | 参数 | 说明 |
|------|------|------|
| `inbox.list` | `include_archived` | 收件箱列表 |
| `inbox.add` | `content`, ... | 添加到收件箱 |
| `inbox.update` | `inbox_id`, ... | 更新收件箱条目 |
| `inbox.classify` | `entry_id` | AI 分类单条收件箱条目并写回 |
| `inbox.relay` | `entry_id`, `target` | 转发收件箱条目 |

#### 网络搜索与资讯
| 方法 | 参数 | 说明 |
|------|------|------|
| `web.search` | `query` | 网络搜索 |
| `web.fetch` | `url` | 获取网页内容 |
| `info.brief` | `topics` | 技术资讯简报（异步抓取 HN/Dev.to/GitHub/Reddit + AI 筛选） |
| `info.cached_items` | `limit`, `section` | 已缓存资讯条目 |
| `info.auto_config` | `enabled`, `interval`, `topics` | 自动资讯配置 |
| `info.summarize` | `urls` | 资讯摘要 |
| `info.fetch_proxy` | `url` | 代理获取网页（绕过 CORS） |
| `info.personalize` | `preferences` | 个性化资讯偏好 |
| `info.items.list` | `limit`, `read`, `saved` | 资讯条目列表 |
| `info.items.get` | `item_id` | 获取单条资讯 |
| `info.items.toggle_read` | `item_id` | 切换已读 |
| `info.items.toggle_saved` | `item_id` | 切换收藏 |
| `info.items.update` | `item_id`, ... | 更新资讯条目 |
| `info.items.sources` | — | 资讯来源列表 |
| `info.items.stats` | — | 资讯统计 |
| `info.items.save_to_note` | `item_id` | 保存资讯到笔记 |
| `info.sections.list` | — | 资讯板块列表 |
| `info.sections.create` | `name`, `keywords` | 创建板块 |
| `info.sections.update` | `section_id`, ... | 更新板块 |
| `info.sections.delete` | `section_id` | 删除板块 |
| `info.sections.ai_suggest_keywords` | `name` | AI 推荐板块关键词 |
| `report.generate` | `topic`, `format` | 生成报告 |

#### 知识图谱（`knowledge.*` 共 22 个方法 —— 2026-09-21 重扫；09-23 波次①-c 下线 graph_for_viz 后 22→21；09-26 P4-KG 批新增 detail 后 21→22）

> 真源：`backend/handlers/_inbox_knowledge.py`（20 个）+ `backend/handlers/__init__.py`（3 个）。
> 本表参数列**以 handler 签名为准**；旧表把 `knowledge.graph` 记成带 `query`、
> 把 `knowledge.traverse` 记成 `entity_id`/`depth`、把 `knowledge.organize` 记成
> `entity_ids` 均与代码不符（按文档写出的手机端桥因此静默拿到空结果，
> 2026-09-21 MOB-KG 已修）。

| 方法 | 参数 | 说明 |
|------|------|------|
| `knowledge.search` | `query`, `entity_type` | 实体检索：`entity_type` 优先（find_by_type），否则 `query` 走名称/别名子串召回；两者皆空→全实体 |
| `knowledge.graph` | `max_entities` | 全图 `{entities, relations, total_entities, truncated}`；**不支持名称/类型过滤**（要检索走 `knowledge.search`）；`max_entities`>0 时截断且优先保留连通节点（P3-KG-CAP，缺省 0=全量） |
| `knowledge.relations` | `entity_id` | 单实体全部关系（出边 + 入边） |
| `knowledge.traverse` | `start_id`, `max_hops`, `relation_types` | BFS 遍历，路径展平为 `{entities, relations}` |
| `knowledge.entity.delete` | `entity_id` | 删除实体及其全部关系（2026-09-18 P2-PD-NODEL）；属 R4 → 未批准只回 `{pending:true}` 不执行；桌面图谱详情面板已接线 |
| `knowledge.entity.merge` | `entity_id`, `into_entity_id`, `keep_alias` | 把一个实体**并入**另一个（2026-09-21 P3-KG-MERGE）：source 消失、其主名/别名降为 target 别名、出处 `sources` 去重合并、边改指 target（自环丢弃、同键边归一取高 confidence）；target 的 name/type **不变**；属 R4（不可逆，已入 `_R4_METHODS` 与删除类守卫正则 `\.merge$`） |
| `knowledge.entity.detail` | `entity_id` | **节点详情归一层**（2026-09-26 P4-KG-DETAIL）：一次返回实体自述 + **出处列表（带标题与 `jumpable` 标记）** + 按谓词/方向分组的邻居 + 卡片正文。存在的理由：图谱 6+ 条写入通道往 `properties` 各写各的键，而旧面板只认 `source_id`（生产 739 实体里 **0 个**），另有 658 个带 `sources` 但因为是数组被前端丢弃——所“点开节点没有完整信息可显示”。实现 `knowledge/node_profile.py`（与 `_timeline.py::_build_entry` 同范式）；实体不存在返回 `{found:false}`，不编造空详情。桌面详情面板已接（后端不可达时降级为本地字段） |
| `knowledge.backfill` | `days`, `limit`, `offset`, `domains`, `dry_run` | 关系回填（2026-09-21 P1-2；09-26 P4-KG-CAPTURE 扩域）：把 **记忆/笔记/收件箱/任务/词库**（`domains`，缺省只 `memory` 以保持旧行为，可传 `"all"`）按 `created_at` 倒序取 `[offset, offset+limit)` 重跑 **LLM 抽取**，补写存量图谱缺的语义关系。**严格 opt-in**：只由用户显式调用，不挂 cron、不启动自动跑（每跑一条是一次真实 LLM 调用）；`dry_run=True` 零调用先看清单；返回 `new_entities`/`new_relations`/`by_via`/`by_domain`/`next_offset`/`remaining` 供逐批推进；无 AI 内核直接返回 `no_ai_client`（不拿正则冒充） |
| `knowledge.unified_query` | `text`, `limit` | 意图分类 + 多源 RRF 融合（参数名是 `text`，不是 `query`） |
| `knowledge.ingest_conversation` | `session_id`, `max_messages`, `auto_write` | 会话→记忆抽取→图谱回灌（回灌走 LLM 通道，见 KG-CH） |
| `knowledge.organize` | `memory_limit`, `days` | AI 个人知识体系整理报告（`services/knowledge_organizer.py`） |
| `knowledge.cards` | `days` | 原子知识卡片列表（`structured_memory_entries` 的 `knowledge.card`） |
| `knowledge.mocs` | — | 主题 MOC 列表 |
| `knowledge.synthesize` | `max_topics` | 卡片聚类→主题 MOC，并把主题回写实体 `properties.topic` |
| `knowledge.brief` | `days` | 最近 N 天知识简报（S3-2 已回退为同步直调） |
| `knowledge.brief.status` | `job_id`, `task_id`, `wait_seconds` | 异步任务轮询（骨架留存待 S3-2b） |
| `knowledge.md_export` | `max_conversations`, `max_graph_entities` | 沉淀数据全量重建为知识库 MD（含图谱快照） |
| `knowledge.md_list` | — | 知识库文件树 |
| `knowledge.md_get` | `rel` | 读单个 MD（含 frontmatter） |
| `knowledge.md_save` | `rel`, `content` | 保存并**反向回灌**记忆/图谱（`md_parser.sync_md_to_backend`） |
| `knowledge.md_sync` | `rel` | 重放指定文件的反向回灌（幂等） |
| `knowledge.md_stats` | — | 知识库文件数/时间分布/卡片与 MOC 计数 |

> **写入通道契约（KG-CH，2026-09-21）**：`KnowledgeGraphExtractor.extract_from_text`
> 与 `extract_from_memory` 的 `use_llm` **默认为 True**。省略参数 = LLM 通道；
> 只有结构化短文本（如 `info.collect` 的资讯摘要）才应显式传 `use_llm=False` 走正则。
> 原因：正则通道对聊天/叙述体文本会产出句子碎片噪声实体，抽取器自身早已明令禁止，
> 但 2026-09-16 批3 的 X1 只堵住了「LLM 失败静默回落正则」、没堵住「默认值」，
> 于是记忆抽取的 auto_write 回灌（`memory/extractor/_core.py`）一直在吃正则。
> 无 AI 内核且 `use_llm=True` 时返回 `via="skipped"` + error 留痕（宁缺毋滥）。
> 断言见 `思忆集app/tests/test_kg_write_channel_20260921.py`。
>
> **出处模型（W1，2026-09-16）**：来源**不建容器实体、不建 `mentioned_in` 边**，
> 统一写 `entity.properties.sources`（上限 200 条/实体）；`relations` 表只存语义边。
> 注入 prompt 的谓词白名单为 `part_of`/`apply_to`/`support`/`oppose`。
>
> **融合面实况（重要，与旧描述不符）**：`knowledge.unified_query` 与工具
> `knowledge.query` 两处构造 `UnifiedKnowledgeService` 时都传 `rag_pipeline=None`
> → `RagAdapter` **永不注册**，文档里“统一查询含 RAG 知识库”一项属虚。
> 现有 5 源：记忆 / 知识图谱 / 笔记 / 词库 / 资讯。
>
> **图谱数据不在云同步范围**：`sync/` 不覆盖 `knowledge_graph.json`（手机端只能走
> RPC 读）；但备份覆盖（`services/backup_service.py` 数C1 扩展域，且恢复对称）。
>
> **存量待处置登记（KG-DEBT，2026-09-21 拍定“登记不删”）**：
> 1. `memory/search/_engine.py::graph_search` —— 图增强检索，全仓零调用者（无 RPC、无工具入口）。
> 2. ~~`backend/handlers/_inbox_knowledge.py::knowledge_graph_for_viz`~~ —— **2026-09-23 波次①-c 已下线**（@58e5fa8，僵尸 RPC 批量清剿）。
> 3. `knowledge/source.py::RagAdapter` —— 因 `rag_pipeline=None` 而永不生效（与上“融合面实况”同根）。
> 4. `desk-notes/packages/shared/src/components/KnowledgeGraphView.ts` +
>    `shared/src/graph/knowledgeGraph.ts` —— 约 1000 行旧 SVG 渲染实现，零 importers；
>    现役渲染是 `desktop/src/components/Graph/ForceGraph.tsx`（自研力导向，非 Cytoscape）。
> 5. 图谱质量修补长期依赖仓库根一次性脚本（`思忆集app/_graph_rebuild.py`、
>    `_graph_edge_cleanup.py`、`_graph_entity_cleanup2.py`、`_graph_governance.py`、
>    `_graph_card_nodes.py`、`_graph_semantic_edges.py`、`_graph_topic_backfill.py`、
>    `_import_knowledge_cards.py`），不在生产代码与测试覆盖内；真实图谱里的
>    `card_*` / `moc_*` 实体与多数 `part_of` 边即由这些脚本产生。
>    **部分收敛（2026-09-21 KG-BACKFILL/MERGE）**：语义建边与去重已有了生产入口
>    （`knowledge.backfill` + `knowledge.entity.merge`），`_graph_semantic_edges.py` 与
>    两个 cleanup 脚本的职能已被覆盖；**尚未做**：把“逐批推进 offset 跑一次全库回填”
>    跑成一次真实现场（需用户显式触发，属花钱操作）、以及 `card_*`/`moc_*` 节点
>    的正规生成（backfill 只从条目重抽取实体/关系，不重建这些容器节点）。
> 6. 桌面图谱手工编辑（`graphService.ts` 的 `addNode`/`addEdge`）
>    只写 Electron 本地 `knowledge_graph.json`，**不回写后端图谱**（同名文件易混）。
>    其中 `removeNode` 对 `bk-` 节点已改为先过后端 RPC（见上 `knowledge.entity.delete`）。
>
> **P4-KG 批已收口（2026-09-26，四项均已接上读写两端）**：
> - 节点详情归一：`knowledge.entity.detail` + `knowledge/node_profile.py`，面板改吃聚合结果（别名/来处/正文/方向邻居）。
> - 出处命名空间：`add_entity_source` 双写结构化 `properties.origins=[{kind,id}]` 并镜像回 `sources`；
>   抽取器加 `source_kind`（记忆回灌默认 `memory`、info.collect 走 `info`、Kimi 走 `kimi:`、MD 回灌走 `memory`）。
>   **存量未迁**：生产图谱仍有 445 条裸 id 出处（无域）；`remove_by_source` 已改为
>   origins/sources/`source_id` 三态匹配，并在“已带其他域”时拒绝裸 id 误删。
> - 采集面：`knowledge/domain_capture.py` 把记忆/笔记/收件箱/任务/词库归一（之前后四域根本无通道）。
> - 可点性：`ForceGraph` 命中圈撑到 ≥r+6、标签可点、左键平移加 3px 阈值并拖拽后抑制选中、
>   `pinnedIds` 保证选中/搜索命中的孤立点在超量采样时仍可见。
> **仍待做**：todos/inbox/info/vocab 四域的跳转路由（`JUMPABLE_TARGET_MODULES` 目前只接 `notes`）、
> 存量裸 id 出处一次性迁移、实体合并的前端入口（后端 `knowledge.entity.merge` 已就绪）。
>

#### 阅读
| 方法 | 参数 | 说明 |
|------|------|------|
| `reading.push` | `url`, `title`, `content` | 推送阅读内容 |
| `reading.get` | `reading_id` | 获取阅读内容 |
| `reading.list` | `limit` | 阅读列表 |
| `reading.delete` | `reading_id` | 删除阅读内容 |
| `reading.save_article` | `url`, `title`, `content` | 保存文章 |
| `reading.list_articles` | `limit` | 文章列表 |
| `reading.delete_article` | `article_id` | 删除文章 |

#### 提醒
| 方法 | 参数 | 说明 |
|------|------|------|
| `reminder.list` | — | 提醒列表 |
| `reminder.create` | `title`, `time`, ... | 创建提醒 |
| `reminder.delete` | `reminder_id` | 删除提醒 |

#### 天气
| 方法 | 参数 | 说明 |
|------|------|------|
| `weather.current` | — | 当前天气 |

#### 双端传送
| 方法 | 参数 | 说明 |
|------|------|------|
| `transfer.token_info` | — | 当前 token 信息 |
| `transfer.send` | `type`, `data`, `target_device` | 发送数据 |
| `transfer.upload` | `file_data`, `filename` | 上传文件 |
| `transfer.download` | `file_id` | 下载文件 |
| `transfer.files` | `limit` | 文件列表 |
| `transfer.delete` | `file_id` | 删除文件 |
| `transfer.history` | `limit` | 传送历史 |

#### URL 学习
| 方法 | 参数 | 说明 |
|------|------|------|
| `url_learning.learn` | `url`, `file_path` | 从 URL 或本地文件学习内容（P1.3：`url` 走 SSRF 防护，`file_path` 走路径遍历防护；`file://` URI 不再支持，需用 `file_path`） |

#### 沙箱
| 方法 | 参数 | 说明 |
|------|------|------|
| `sandbox.preview` | `operation` | 预览沙箱操作 |
| `sandbox.approve` | `sandbox_id` | 批准操作 |
| `sandbox.reject` | `sandbox_id` | 拒绝操作 |
| `sandbox.list_pending` | — | 待审批操作列表 |
| `sandbox.batch_approve` | `pending_ids` | 批量批准待审批条目（P1-5） |
| `sandbox.history` | `limit` | 审批历史列表（approved/rejected/timeout/failed 摘要，新的在前；默认 50，上限 200；只存摘要不存 args/preview 原文，AG-C1③） |
| `sandbox.history.clear` | — | 清空审批历史（进程内环形 + kv 落盘键，AG-C1③） |
| `confirmation.revoke_always` | `action_id` | 把能力移出「始终允许」名单（幂等，不在名单返回 revoked=False；B2 收尾存量补登） |

#### OCR
| 方法 | 参数 | 说明 |
|------|------|------|
| `ocr.extract_text` | `image_path`, `language?` | OCR 文字提取（文件路径；`language` 留空走 settings `ocr_language`，仍为空默认 `chi_sim+eng`） |
| `ocr.extract_base64` | `image_base64`, `language?` | OCR 文字提取（Base64） |
| `ocr.status` | — | OCR 能力探测：`{available, pytesseract_version, tesseract_binary_path, tesseract_version, languages, tessdata_prefix}`（需 `pip install -e ".[ocr]"` + 系统 Tesseract 引擎） |

#### 音频转写 (P2.1)
| 方法 | 参数 | 说明 |
|------|------|------|
| `transcribe.transcribe_file` | `audio_path`, `language?` | 本地音频文件转文字（faster-whisper，需 `pip install -e ".[transcription]"`；非 WAV 格式需系统 ffmpeg） |
| `transcribe.transcribe_base64` | `data`, `language?`, `format?` | base64 音频字节流转文字（tmp 文件中转，自动清理） |
| `transcribe.status` | — | 转写能力探测：`{available, version, cached_models}`（前端调用前先探测避免失败路径） |

#### AI 与意图解析
| 方法 | 参数 | 说明 |
|------|------|------|
| `model.list` | — | AI 模型列表 |
| `pattern.execute` | `pattern_id`, `params` | 执行模式 |
| `ai.parse_intent` | `text` | 解析意图 |
| `agent.state.summary` | — | Agent 状态摘要 |
| `xiaowen.quote` | — | 小问台词/状态语 |

#### Agent 任务
| 方法 | 参数 | 说明 |
|------|------|------|
| `agent.list` | — | Agent 任务列表 |

#### Cron
| 方法 | 参数 | 说明 |
|------|------|------|
| `cron.list` | — | Cron 任务列表 |
| `cron.add` | `name`, `action_id`, `payload`, `schedule`, `run_at`, `repeat`, `max_retries`, `retry_backoff`, `run_every_days` | 添加 Cron（action_id 须在 cron_actions 注册表；headless 下经持久队列执行） |
| `cron.remove` | `cron_id` | 删除 Cron |
| `cron.toggle` | `cron_id`, `enabled` | 启用/禁用 |
| `cron.toggle_batch` | `job_ids`, `enabled` | 批量启用/禁用（一键静音自主任务，U2） |
| `cron.update` | `cron_id`, ... | 更新 Cron |
| `cron.run_now` | `cron_id` | 立即执行 |
| `cron.status` | — | Cron 状态 |
| `cron.history` | `limit` | Cron 执行历史 |

#### 持久任务队列（P1.2）
| 方法 | 参数 | 说明 |
|------|------|------|
| `task_queue.enqueue` | `capability_id`, `payload`, `queue_name`, `priority`, `max_retries`, `backoff_base`, `backoff_cap` | 入队一条持久任务（at-least-once 投递 + 失败指数退避重试） |
| `task_queue.list` | `queue_name`, `status`, `limit` | 任务列表（按 created_at 倒序） |
| `task_queue.get` | `task_id` | 单条任务详情 |
| `task_queue.cancel` | `task_id` | 取消 pending/running 任务 |
| `task_queue.stats` | `queue_name` | 各状态计数（pending/running/completed/failed/dead_letter） |
| `task_queue.purge_completed` | `older_than_seconds` | 清理旧的已完成任务（默认 24h 前） |
| `task_queue.dead_letter.list` | `queue_name`, `limit` | 死信任务列表（重试耗尽） |
| `task_queue.dead_letter.requeue` | `task_id` | 把死信任务重新置为 pending 立即可执行 |

> **持久任务队列**：所有一次性 retryable 工作（资讯抓取、URL 学习、外部 API 调用）收敛到 SQLite 持久队列（`data/task_queue.db`）。进程崩溃后未完成任务在 lease 过期后由 reaper 自动回收重试；超过 `max_retries` 进入死信。Worker executor 经 `ActionBroker.execute_sync()` 走统一闸门审批 + 产出 receipt。与 `CronService` 边界：Cron 负责"什么时候触发"，TaskQueue 负责"可靠执行 + 失败重试"。

#### 进程
| 方法 | 参数 | 说明 |
|------|------|------|
| `process.list` | — | 进程列表 |
| `process.status` | `pid` | 进程状态 |
| `process.output` | `pid` | 进程输出 |
| `process.terminate` | `pid` | 终止进程 |

#### 远程控制
| 方法 | 参数 | 说明 |
|------|------|------|

> **✅ V2.1 批次1#7（2026-08-25）**：`remote.shell` / `remote.screenshot` 已**移除**（攻击面净收缩，二者前端从未调用，原为弃用桩）。新代码不得注册类似远程命令/截图方法。

> **⚠️ 历史弃用记录（debt-v2 V1-05，2026-08-24）**：remote.shell / remote.screenshot 曾标注弃用（远程任意命令/截图面过宽，仅剩 Tailscale 直连场景）；已于批次1#7 正式移除。

#### Trae IDE 集成
| 方法 | 参数 | 说明 |
|------|------|------|

#### 外部工具
| 方法 | 参数 | 说明 |
|------|------|------|
| `tool.register` | `tool_id`, `display_name`, `cli_path`, `description`, `commands`, `enabled`, `env_keys`, `health_check_command` | 注册动态工具（声明式 ToolSpec，零代码） |
| `tool.list` | — | 工具列表（含 cli_resolved/available/commands/action_ids） |
| `tool.unregister` | `tool_id` | 注销工具 |
| `tool.refresh` | — | 重新加载 tool_specs.json 并刷新 handler |
| `tool.health_check` | `tool_id`, `command_name?` | 健康检查（执行 health_check_command + 检查 env_keys） |
| `tool.samples` | — | 列出内置工具样例（位于 `data/tool_specs_samples/`） |
| `tool.validate` | `spec` | 校验 ToolSpec schema 是否合法（不实际注册） |

#### 系统管理
| 方法 | 参数 | 说明 |
|------|------|------|
| `system.status` | — | 服务状态 |
| `system.info` | — | 版本信息 |
| `system.active_tasks` | — | 活跃任务列表 |
| `system.tailscale_status` | — | 检测本机 Tailscale 状态（IP/hostname/connected），供手机端自动发现 |
| `system.health` | `force_refresh` | 聚合 10+ 个子系统健康状态，带缓存 |
| `provider.list` | — | API Provider 列表 |
| `provider.add` | `name`, `base_url`, `api_key`, `model` | 添加 Provider |
| `provider.remove` | `provider_id` | 移除 Provider |
| `provider.update` | `provider_id`, ... | 更新 Provider |
| `provider.health` | `provider_id` | 健康检查（真实 POST /chat/completions） |
| `kiro.status` | — | Kiro 状态 |

##### Provider 动态路由（P2.6）

按查询类型（reasoning / coding / chinese_knowledge / summarization / general_chat）自动选择 provider，接入 `JudgeAwareClient.chat_completion` 的 PRIORITY 1.8（位于 `preferred_provider` 之后、聚合层之前）。配置存储在 settings `llm_provider_routing` 字段（JSON 字符串），格式 `{"reasoning": "deepseek", "default": "glm"}`。preset provider（deepseek/glm/moonshot/qwen）有独立 `env_key`，无需在 `provider_configs` 中重复配置；自定义 provider 名运行时校验。路由表接入 `ProviderHealthMonitor`，provider 不可用时静默回退到默认回退链。

| 方法 | 参数 | 说明 |
|------|------|------|

> **查询类型关键词**：reasoning=推理/分析/为什么/原因/逻辑/证明/推导/计算；coding=代码/编程/函数/bug/debug/重构/实现/算法/python/javascript；chinese_knowledge=中国/中文/历史/古诗/成语/文言/诗词；summarization=总结/摘要/概括/归纳/提炼；general_chat=其他。

#### API Key 管理
| 方法 | 参数 | 说明 |
|------|------|------|
| `api_keys.list` | — | API Key 列表 |
| `api_keys.create` | `name`, `key` | 创建 API Key |
| `api_keys.update` | `key_id`, ... | 更新 API Key |
| `api_keys.delete` | `key_id` | 删除 API Key |
| `api_keys.test` | `key_id` | 测试 API Key（直接 provider，不走 fallback） |

##### API Key 轮换（P2.5）

每个 provider 维护一个密钥池（primary + backups），状态机为 `active` → `backup` → `disabled`。轮换触发条件：`billing_error`（402/欠费/配额耗尽）和 `auth_error`（401/403）；瞬时错误（`rate_limit`/`server_error`/`network_error`）不触发。轮换成功后调用方需触发 `context.recreate_ai_client()` 重建 AI 客户端。

| 方法 | 参数 | 说明 |
|------|------|------|

> **存储约定**：轮换状态存储在 settings `provider_key_rotation` 字段（dict[provider_name -> rotation_state]）。密钥值通过 `SecureKeyStorage.encrypt(value, key_id="rotation/{provider}")` 加密；兼容旧数据明文 token（`_decrypt_value` 遇到非加密格式直接返回原值）。每个 provider 最多保留 5 个密钥（`MAX_KEYS_PER_PROVIDER`）和 50 条历史记录（`MAX_HISTORY`）。

#### 审计
| 方法 | 参数 | 说明 |
|------|------|------|
| `audit.list` | `limit` | 审计日志列表 |
| `audit.get` | `audit_id` | 单条审计日志 |
| `audit.list_rollbackable` | — | 可回滚操作列表 |
| `audit.can_rollback` | `audit_id` | 检查是否可回滚 |
| `audit.rollback` | `audit_id` | 回滚单条操作 |
| `audit.batch_rollback` | `audit_ids` | 批量回滚 |
| `audit.stats` | — | 审计统计 |

#### 时间线聚合（P2.4）
| 方法 | 参数 | 说明 |
|------|------|------|
| `timeline.list` | `limit`, `source_types`, `cursor` | 跨源时间线（按 `updated_at` 倒序），游标分页 |
| `timeline.range` | `start_at`, `end_at`, `source_types`, `limit` | 按时间范围 `[start_at, end_at]` 过滤（闭区间） |
| `timeline.by_type` | `source_type`, `limit` | 按数据源类型过滤；`source_type` ∈ `note`/`task`/`inbox`/`info`/`translation`/`chat`/`vocab` |
| `timeline.stats` | `days`, `source_types` | 时间线统计：按 类型/天/小时 分组，含最近 N 天计数 |

> **数据源**：notes / tasks / inbox / info_items / translations / chat_sessions / vocab_words 共 7 类。
> **时间字段**：info_items 以 `collected_at` 为主，其余以 `updated_at` 为主（缺失时回退 `created_at`）。
> **游标**：`cursor` 为 base64 编码的 opaque 字符串，包含 offset 信息；空字符串表示第一页。
> **降级**：仓储缺失或异常时该数据源被跳过，不影响其他源；所有同步 I/O 通过 `asyncio.to_thread` 包裹。

#### 能力与使用统计
| 方法 | 参数 | 说明 |
|------|------|------|
| `capability.list` | — | 能力列表 |
| `capability.approve` | `capability_id` | 批准能力 |
| `capability.dismiss` | `capability_id` | 忽略能力 |
| `usage.record` | `event`, `data` | 记录使用事件 |
| `usage.stats` | — | 使用统计 |
| `usage.dashboard` | `days` | (P2.7) 聚合今日 + N 天统计 + 预算 + provider 健康 + tier 维度 + 当日告警状态（默认 7 天，最大 90 天） |
| `usage.today` | — | (P2.7) 轻量今日端点（calls/tokens/cost + by_provider + by_model），用于状态栏实时显示 |
| `usage.export` | `days` | (P2.7) 导出最近 N 天明细为 CSV 字符串（前端可保存为文件） |

##### 成本可观测 Dashboard（P2.7）

为前端成本看板提供聚合数据，统一整合 UsageTracker（token 级明细）、CostTracker（tier 维度）和 ProviderHealthMonitor 三大源。后端启动时由 `headless_main.py` 调用 `init_usage_tracker_from_context(context)` + `start_alert_loop()` 完成单例初始化（修复早期版本中 `_tracker_singleton` 从未在生产代码中初始化导致 `usage.*` RPC 空跑的缺陷）。

> **数据来源**：`usage.dashboard` 聚合 6 个源 — UsageTracker.get_stats(1) 取今日、UsageTracker.get_stats(N) 取 N 天聚合、UsageTracker.check_budget() 取预算状态、ProviderHealthMonitor.get_status() 取 provider 健康、CostTracker.get_daily_summary() 取 tier 维度（local/judge/mid_cloud/high_cloud）、UsageTracker._alert_state 取当日告警状态。所有同步 SQLite 读写通过 `asyncio.to_thread` 包裹。`usage.purge` 调用 `UsageTracker.purge_before(cutoff_iso)`，返回被删除记录数。

#### 进化分析
| 方法 | 参数 | 说明 |
|------|------|------|
| `evolution.scan_and_report` | — | 扫描并生成升级报告 |
| `evolution.last_briefing` | — | 最近升级简报 |
| `evolution.status` | — | 进化状态 |

#### 设置与数据管理
| 方法 | 参数 | 说明 |
|------|------|------|
| `settings.get` | `key` | 获取设置（不传 key 返回 `success:false` 错误结构） |
| `settings.set` | `key`, `value` | 修改设置 |
| `data.import_chat` | `format`, `content` | 导入聊天记录 |
| `data.import_chat_folder` | `path` | 批量导入聊天记录 |
| `daily.get` | `date` | 获取日记 |
| `daily.append` | `date`, `content` | 追加日记 |
| `kimi.sync_once` | `max_chats` | 从 Kimi 同步对话 |

#### 云同步（Cloudflare Worker，与手机端协议兼容）
| 方法 | 参数 | 说明 |
|------|------|------|
| `sync.status` | — | 查询云同步状态（enabled/configured/running/last_sync_at） |
| `sync.sync_now` | — | 立即执行一次 push + pull |
| `sync.configure` | `worker_url`, `token`, `register_secret`, `interval_seconds` | 配置云同步参数（所有参数可选） |
| `sync.enable` | — | 启用云同步（需先配置 worker_url + token） |
| `sync.disable` | — | 禁用云同步（停止循环，保留配置） |
| `sync.register_device` | `name` | 向 Worker 注册当前设备（需先配置 register_secret） |

### 健康检查 (HTTP)
```
GET http://127.0.0.1:18765/health -> {"status": "ok"}
```

## AI 架构

1. **本地 AI** (Ollama qwen3-4b) — 日常分类、整理、简单对话，数据不出本机
2. **DeepSeek** (deepseek-v4-flash) — 主云端 AI，复杂推理和编码
3. **Moonshot** (moonshot-v1-8k) — 路由表配置，中文类任务

> 原「四层」中的 smolagents 已于 2026-09-03 摘除（对话链路治理：依赖、代码路径、测试一并清除）；tool/agent 路径由 butler 管家单循环（pydantic-ai）承接。（残留待清：`requirements-desktop.txt` 仍钉 `smolagents>=1.0,<2.0`，代码已零引用，待依赖批次移除。）

## AI 调用链路

```
用户输入 -> chat.send 多路径分发 (backend/handlers/chat_paths/)
  ├─ 默认 (legacy 路由) -> butler 管家单循环工具流 (pydantic_agent_path)
  │     └─ JudgeAwareClient：简单问题直连 / 复杂问题 JudgeScheduler.route()
  │          / 回退 FallbackOpenAIClient 链 (primary -> fallback -> local -> cache)
  ├─ 灰度 -> gateway_path (AgentGateway 外部内核；需显式 chat_route="gateway" 或 params.kernel)
  ├─ 显式 mode="multi_agent" -> multi_agent_path (Planner + Worker)
  ├─ 显式 aggregation -> aggregate_path (多 provider 聚合)
  └─ 保底 -> simple_stream_path (纯流式，无工具)
```

FallbackOpenAIClient 内置重试（stream 仅连接阶段重试）、缓存 TTL（24h 默认，500 条上限）。

## 关键配置

- `思忆集app/data/settings.json` / SQLite — 所有应用设置（数据已迁移到 SQLite，`local_storage.py` 为统一访问层）
- AI 设置键: `llm_api_base`, `llm_api_key`, `llm_model`, `llm_local_api_base`, `llm_local_model`, `llm_provider_routing`, `provider_configs`
- 环境变量: `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `OLLAMA_MODELS`, `SMTP_PASSWORD`, `SIYI_*`

## 根目录入口

`思忆集app/` 根目录保留 `main.py`（已退化为兼容 shim，转发到 `app_entry.main`）和若干启动辅助脚本。
- `main.py` -> `app_entry.main`（兼容 shim，非推荐入口）
- `backend.headless_main` -> 无头后端（推荐）
- `思忆集app\.scripts\_start_backend.py` / `思忆集app\start_backend.bat` -> 后端快捷启动
- 仓库根目录 `启动.bat` / `启动.py` -> 统一启动入口（headless 模式）

## 关键模块说明

- `app_runtime/app_context.py` — 依赖注入根容器，支持 `headless=True` 跳过 Qt
- `app_runtime/_context_builder.py` — 15 阶段 Builder，所有属性赋值集中于此（P3.2: 含 P2.8/P2.9 服务槽位预留）
- `app_runtime/ai_client_consumers.py` — 声明式 AI 客户端 binding（AIClientBinding dataclass，7 个消费者）
- `backend/headless_main.py` — 无头后端入口，asyncio 事件循环 + WebSocket
- `backend/ws_server.py` — aiohttp WebSocket JSON-RPC 服务器 (端口 18765)
- `backend/method_registry.py` — 方法注册中心
- `backend/event_bridge.py` — EventBus → WebSocket 事件桥接
- `backend/handlers/` — JSON-RPC 方法实现集合（62 模块，340 个方法）
- `services/task_queue.py` — SQLite 持久任务队列（P1.2），at-least-once 投递 + 指数退避重试 + 死信队列
- `services/cron_service.py` — 定时任务调度器，支持 APScheduler 和 polling 双引擎；P0.1 集成 PersistentTaskQueue，所有定时任务走持久队列执行，崩溃后自动恢复
- `sync/cloud_sync_client.py` — 云同步客户端（AES-256-GCM + PBKDF2，与手机端 cloudSync.ts 协议兼容）
- `data/local_storage.py` — JSON/SQLite 统一存储层 + 路径锁定 + FTS 搜索
- `data/sqlite_store.py` — SQLite 底层存储
- `core/audit_rollback.py` — 审计轨迹与回滚（S15）
- `core/provider_config.py` / `ai/client/config.py` — Provider 配置与校验
- `assistant/multi_agent_orchestrator.py` — SubAgent 能力声明注册表（原 Planner + Worker 执行链已随 C-1 执行器收敛下线，工具执行唯一入口 `assistant/capability_router.py`）
- `knowledge/retrieval/` — RAG 检索管道
- `services/api_key_service.py` — API Key 管理
- `services/system_health.py` — 子系统健康聚合（15 个子系统，getattr 为合理的防御性编程，检测服务可用性；P1.1 新增任务队列/反馈学习/通知推送 3 个子系统）
- `services/feedback_service.py` — 用户反馈学习（P2.8，由 headless_main 显式注入 context.feedback_service）
- `services/notification_dispatcher.py` — 通知分发器（P2.9，由 headless_main 显式注入 + start() 订阅事件）
- `integrations/automation_hub_client.py` — 连接 automation_hub

### DI 模式约定（P3.2）

服务注入遵循以下优先级（新服务应采用方式 1）：

1. **显式注入 + property 访问**（推荐）：服务在 `headless_main.py` 启动时构造并 `context.xxx_service = svc`，AppContext 提供 `@property` + setter 槽位（如 `feedback_service` / `notification_dispatcher` / `task_queue`）。handler 通过 `getattr(context, "xxx_service", None)` 访问，服务未注入时返回 None。
2. **Builder 阶段构造**：服务在 `_context_builder.py` 的 `build_*` 方法中构造并赋值给 `ctx.xxx`（如 `weather_service`、`suggestion_engine` 等懒加载属性；P0.2 修复了 ProactiveTriggerEngine 和 SuggestionEngine 的依赖缺失问题，确保 `desktop_doctor_service` / `workbench_service` / `weather_service` / `event_bus` 全部注入）。
3. **Handler 懒构建兜底**（仅作兜底）：当服务未在 1/2 中注入时，handler 内 `_get_service(context)` 懒构建。**新服务不应采用此方式**，应在 `headless_main.py` 显式注入。

> **状态更新（2026-08-02）**：P0/P1 修复后，P2.8（FeedbackService）和 P2.9（NotificationDispatcher）的 P3.2 预留槽位已由 `headless_main.py` 显式注入，不再依赖 handler 懒构建。`_context_builder.py` 中 `ctx._feedback_service` / `ctx._notification_dispatcher` 占位符保留作为初始化前状态哨兵。

> **声明式 binding 标杆**：`ai_client_consumers.py` 的 `AIClientBinding` dataclass 是项目内最优雅的 DI 模式，把 18 处 `hasattr` 层叠分支收敛为声明式列表。新增类似的多消费者注入应参考此模式。

## Tailscale 直连架构

桌面端默认监听 `127.0.0.1:18765`（仅本机，N-D 收口 2026-09-17）。需要手机经 Tailscale 直连时，显式设 `SIYI_WS_HOST=0.0.0.0` 打开监听，手机端直连桌面 WebSocket，使用全部 340 个 RPC 方法。

```
手机 -- Tailscale (WireGuard) --> 桌面 0.0.0.0:18765 (WebSocket JSON-RPC，需显式开启)
  │                                    │
  └── Tailscale 不可达 --> Cloudflare Worker --> 桌面（降级同步，仅 notes/todos/settings）
```

- **Tailscale 在线**：手机填 MagicDNS 主机名（如 `my-desktop`）或 `100.x.x.x` IP，全功能可用
- **Tailscale 不在线**：走 Cloudflare Worker 做加密 push/pull（仅 3 类数据）
- **安全**：非 localhost 连接由 `ws_server._verify_auth` 强制要求 token 认证；非回环绑定 + 认证不可用时 `headless_main.security_fail_closed_gate` 直接拒启（exit 8）
- **手机端探测**：配对成功后调用 `system.tailscale_status` 显示 Tailscale 徽章

## 重要约定

- 数据存储在 `思忆集app/data/` 下，以 SQLite 为主存储，`local_storage.py` 提供统一访问接口
- 日志使用 `siyi.*` logger 命名空间
- 所有新功能应通过 `backend/` 的 JSON-RPC 接口暴露，而非直接操作 Qt
- 异步 handler 中同步操作（文件 I/O、AI 调用、SQLite 查询）必须包裹 `asyncio.to_thread`
- 所有 `json.loads` 调用必须 try/except
- 路径遍历防护需要同时检查 `..` 与使用 `os.path.basename()`
- 日期时间比较统一使用 UTC-aware 或统一使用 naive，禁止混用
- 文件 I/O 必须使用 `with` 语句
- 破坏性操作（`notes.delete`、`notes.batch_delete`、`tasks.delete`）必须经过 sandbox 审批
- `notes.batch_delete` 使用 `empty_only` 参数（非 `delete_empty`）指定仅删除空笔记
- 空笔记定义：content 为空或仅含空白；title 是否为空不影响空笔记判断
- `notes_list` 与 `notes_batch_delete` 的空笔记判断标准必须一致（仅检查 content）
- 工具描述必须包含场景化示例
- ~~前端工具模式按钮（绿色扳手）触发 `force_tools=true`~~ —— force_tools 已随 smolagents 摘除（2026-09-03），后端不再识别该参数；desk-notes 侧绿色扳手按钮待同步处置
- API/provider 连通性测试必须调用真实 `POST /chat/completions` 以检测欠费/quota 耗尽
- API key 测试必须绕过 fallback client，使用直连 provider client
- 版本号遵循语义化版本（SemVer）；`git tag -a vX.Y.Z` 即视为一次发布
- tag 即发布：tag 所指 commit 即该版本正式产物，无独立发布流程
- 每次打 tag 发版前必须更新 `CHANGELOG.md`（Keep a Changelog 格式）

### 动态工具接入（零代码）

新增外部 CLI 工具**不应编写 Python 代码**，应通过声明式 ToolSpec 配置接入：

1. **配置位置**：`思忆集app/data/tool_specs.json`（已加载）或 `data/tool_specs_samples/*.json`（参考样例）
2. **运行时注册**：JSON-RPC `tool.register` / `tool.validate` / `tool.health_check` / `tool.samples`
3. **ToolSpec schema 字段**（详见 `assistant/tool_spec.py`）：
   - 工具级：`tool_id` / `display_name` / `cli_path` / `env_keys` / `health_check_command` / `enabled`
   - 命令级：`template` / `stdin_template` / `params` / `risk` / `confirmation` / `detach` / `timeout` / `env_keys` / `health_check_command` / `output_regex` / `output_fields`
4. **安全约定**：
   - `risk=high` 必须配 `confirmation=first_time` 或 `every_time`（不能是 `auto`），否则 schema 校验拒绝
   - `risk=high` 命令调用时自动进入 sandbox 审批队列（`DynamicToolDomain._maybe_block_for_sandbox`）
   - `env_keys` 通过 `GenericToolBridge._default_secret_resolver` 解析，**不写回 `os.environ`**。当前实现为两段式：
     1. **系统环境变量**（如 shell 里 `export GH_TOKEN=...`）— 优先级 1
     2. **SecureKeyStorage.retrieve(key_name)** — 优先级 2，仅 keyring 类后端可用（macOS Keychain / Linux keyring）
     - 第三段（settings 加密 token 解密）当前 `_default_secret_resolver` 直接 `return None`，**未实现**；如需读 settings 加密值，需注入 `_settings_repo` 或在调用方处显式 `SecureKeyStorage.decrypt`
   - `stdin_template` 通过子进程 stdin 传入（避免 argv 长度/转义问题）
   - `output_regex` 命名分组提取到 `result.extracted` 字典
5. **环境变量约定**：通用 secret 环境变量名（`GH_TOKEN` / `ANTHROPIC_API_KEY` / `API_TOKEN` 等）见上方环境变量表；用户可通过 shell export 或 SecureKeyStorage 配置
6. **平台限制（SecureKeyStorage 后端）**：
   - **macOS**：Keychain（命名存取可用）
   - **Linux**：keyring（命名存取可用，需安装 `keyring` 包）
   - **Windows**：默认走 DPAPI 后端，**不支持命名存取**（`store`/`retrieve`/`delete` 会抛 `SecureKeyStorageError`）；Windows 用户应改用环境变量或 `encrypt`/`decrypt` 显式 token
   - 任何平台都可降级到 Fernet（机器派生密钥）作为兜底

## 已知注意事项

- `data/settings.json` 包含 API Key，已加入 `.gitignore`
- 数据存储已完成 SQLite 迁移：`repositories/` 与 `services/backup_service.py` 等应通过 `data_store.load_json()` / SQLite 读取，而非直接读旧 JSON 文件
- 总代码量：~175K 行 Python（思忆集app 非测试 668 文件；含 tests 共 1051 文件 / ~255K 行，均不含 .venv；2026-09-13 复核）
- 2026-05-16 清理：砍掉废弃子项目、News 模块、清理日志/测试产物
- 2026-07-18 P3.1 清理：删除根目录 10+ 临时文件（.txt/.patch/诊断脚本）、`temp/` 目录、`.scripts/debug/` 6 个调试脚本、`integrations/llm/` + `integrations/plugins/` 空占位符目录、`思忆集手机端app-main/` 并行 workspace 备份、两个 `思忆集手机端app-backup-20260630-*` 备份目录、`桌宠小问设计/` 设计资产目录；移除 `repositories/__init__.py` 中未使用的 `QDateTime` 死代码导入
- 2026-07-18 源码丢失事故：清理脚本误删 82 个模块源码，仅靠裸 pyc 运行；2026-08-08 已全部按字节码反汇编重建完成（零裸 pyc），方法论见 `docs/PYC_REBUILD_RUNBOOK.md`；守卫脚本 `scripts/check_no_sourceless_pyc.py` 已接入 CI 防再发
- 2026-08-08 项目整理：根目录临时文件清理、web_dist 与最新前端构建对齐（818→105 产物）、git 基线建立
- 后续开发路线图/深度功能规划已归档：`docs/todo-unimplemented-features.md`、`docs/deep-development-roadmap.md`（移至 `docs/archive/`）；当前执行计划唯一真源见 `deliverables/gstack/project-status-and-execution-plan-2026-08-22.md`
- 文档真源索引：**`docs/INDEX.md` 为唯一导航入口**（含三态状态标记：✅ 现行 / 🔴 过期 / 📌 待写）；`docs/document-map.md` 为历史模块地图；过期文档归档至 `docs/archive/`
- 现行决策/处置声明：`docs/automation-hub-disposition.md`（automation_hub 定位）、`docs/adr/`（ADR 决策记录）
- `automation_hub/` 为已边缘化的独立演进项目，与桌面端通过 HTTP/Client 集成，文档见 `automation_hub/agent/README.md` 与 `automation_hub/docs/`

## 文档索引指引

修改或新增文档时，请遵循以下规则（详见 `docs/INDEX.md`）：

1. **先查后写**：改文档前先看 `docs/INDEX.md` 中该文档的状态标记——🔴 已过期的文档不要直接改，先判断是「修正为现行」还是「归档到 `docs/archive/`」。
2. **roadmap 唯一化**：`docs/project-roadmap.md` 为当前主路线图（废弃口径，修订方向以执行计划为准）；`docs/roadmap-next.md` 已归档；不要在多个 roadmap 文件里重复维护同一件事。
3. **新增文档必登记**：新建任何 `docs/*.md` 都必须在 `docs/INDEX.md` 中登记一条（标题 / 目的 / 状态），否则视为孤儿文档。
4. **文档过期直接归档**：`mv docs/xxx.md docs/archive/`，不要删除。
5. **一致性由 CI 守护**：`scripts/check_doc_index_consistency.py`（已接入 CI）自动检查死链 / 孤儿文档 / 静默归档 / 新旧双份，本地可先跑 `python scripts/check_doc_index_consistency.py` 验证。

## 测试基线（2026-09-13 复测，作为后续回归基准）

| 套件 | 命令 | 基线 |
|---|---|---|
| 根级 pytest（思忆集app/tests） | `python -m pytest -q`（仓库根；并行 `-n 4 --dist loadfile` 实测约 3 分钟） | **4697 过 / 4 failed（全环境性，已归因）/ 145 跳过 / 3 xfailed**。4 failed = auth_chain ACL（Windows temp 环境敏感）×1 + 直连 18765 无后端（`test_integration_concurrency` ×2、`test_ws_16mb_boundary` ×1，该三例无跳过守卫）+ 并行时序抖动（`test_morning_brief`，单跑绿）×1 |
| 集成测试（自包含） | `SIYI_INTEGRATION_AUTOSTART=1 python -m pytest -k integration` | **196 过**；3 failed / 4 error 为上述 18765 直连族与 web_dist 竞争（环境性） |
| automation_hub | `cd automation_hub && python -m pytest tests -q` | **186/186** |
| 前端 typecheck | `cd desk-notes && pnpm run typecheck:desktop` | 通过（0 error） |
| 前端单测 | `cd desk-notes && pnpm run test`（vitest） | **886 passed / 0 failed**（89 个测试文件；2026-09-13 交接单口径） |
| WS 冒烟 | `思忆集app/scripts/ws_smoke_readonly.py`（正式脚本；需后端运行，`SIYI_WS_PORT` 可指端口；逻辑经 mock 双向验证） | 16/16 只读方法 |
| 覆盖率门禁 | `python scripts/coverage_gate.py --threshold 20` | 25.8% ≥ 20% |

注意事项：
- 集成测试（`test_integration_*`）需后端在 18765 端口运行；conftest 已实现后端不可达时自动跳过（CI 环境）；设 `SIYI_INTEGRATION_REQUIRED=1` 可强制要求后端在场；`SIYI_INTEGRATION_AUTOSTART=1` 自启临时后端（随机端口+临时数据目录）。例外：`test_integration_concurrency` 与 `test_ws_16mb_boundary` 直连硬编码 18765 且无跳过守卫，无后端全量跑时会计失败（已归因，非回归）
- 少数时序敏感用例（`test_code_worker`、`test_connection_manager` heartbeat、`test_integration_inbox::test_add_and_list`、`test_morning_brief`）在机器高负载并行跑多套件时可能抖动，单跑均通过；RPC 接收超时可通过 `SIYI_RPC_TIMEOUT` 环境变量调整（默认 60s）

> **基线口径沿革**：2026-09-03 口径（4371 collected + 2 collection errors）已被 2026-09-13 取代——PyQt6/ui 退役路径经 `da5b8ba` 统一为 8 个测试文件模块级 skip，收集错误清零；当前全量收集 **4849 用例 / 0 收集错误**。测试唯一真源为 `思忆集app/tests/`（仓库根 `tests/` 已于 2026-09-03 处置，勿在仓库根新建 tests/）。

## 测试与审查口径（2026-09-03 沉淀，对话链路治理卫生项）

- **testpaths 口径**：仓库根 `pyproject.toml` 的 `[tool.pytest.ini_options] testpaths = ["思忆集app/tests"]`——测试唯一真源是 `思忆集app/tests/`；**不要在仓库根新建 `tests/`**，pytest 不会收集它（真孤儿）。
- **venv pytest 口径**：pytest/pytest-asyncio 声明在 `思忆集app` 的 `[project.optional-dependencies].dev`，`uv sync` 默认不装 extras。venv 缺 pytest 时 `uv run pytest` 会**静默回退 PATH 上的全局 pytest**（诊断特征：`uv run python -c "import pytest"` 报 No module named pytest，但 `uv run pytest` 能跑）。跑测试前先验证解析：`uv run python -c "import pytest, pytest_asyncio"`；补装必须对齐 pyproject 版本范围：`uv pip install "pytest>=8.0,<9.0" "pytest-asyncio>=0.24,<1.0"`（`uv pip install` 不看 pyproject 约束，裸装会越界）。
- **desktop extras 有意不装**：PyQt6 / PyQt6-WebEngine 属 `desktop` extras（旧桌面路径遗留），无头 venv 不装。2026-09-13（`da5b8ba`）起 8 个 PyQt6 依赖测试文件已统一为**模块级 skip 留档**（含 `test_crash_fixes.py`、`test_desktop_web_bridge.py`，与 `tests/conftest.py` 的 `LEGACY_PYQT_UI_TESTS` 排除清单同类，删除不可逆，待统一决策）；全量收集**零收集错误**，不再需要 `--continue-on-collection-errors`。
- **marker 口径**：pyproject 声明了 `smoke/unit/integration/slow` marker，但当前无任何测试显式打 `smoke` 标——`-m smoke` 是空集，不要以 `-m smoke` 作冒烟门禁；集成测试跳过逻辑见 conftest `pytest_collection_modifyitems`（后端不可达自动 skip，`SIYI_INTEGRATION_AUTOSTART=1` 自启临时后端 fail-not-skip）。
- **注册中心急切副作用（任务 #2/#3 教训）**：单测里 mock 方法注册中心后，须 `reset_mock` 清掉急切副作用再断言，否则跨用例注册记录互相泄漏、断言假绿/假红。
