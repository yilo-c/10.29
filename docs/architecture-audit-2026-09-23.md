<!-- 模块说明：全栈架构深度技术审计（2026-09-23）。5 条并行只读调查线的汇总结案。
     状态：✅ 现行（修复执行前有效；每波次完成后在 §10 勾选并同步关联 finding 状态） -->

# 思忆集全栈架构深度审计报告（2026-09-23）

> **审计方式**：5 条并行只读调查线——AI 对话链路 / 记忆·知识系统 / 存储层 / 前后端桥接 / 遗留双栈。
> 所有结论均有 `文件:行号` 实测证据；行号以 2026-09-23 工作树（HEAD 418 commits 窗口内）为准。
> **口径**：本文只登记"证据支持的现存后果"，推断与待证项全部收敛到 §9，不得引用为缺陷。

## 0. 总体判定

| 疑问 | 判定 | 核心证据锚点 |
|---|---|---|
| 多套执行的功能？ | **是**：2 套 action 执行器（安全语义分叉）、3 套 Agent 工具循环（仅 1 套生产）、3 套同步栈（仅 1 套主用）、2 套混合检索、2 套链接器、5 个 LLM 调用入口（2 个零接线） | C-1 / H-1 / H-3 / H-4 / H-6 |
| 多套存储方式？ | **是**：同一笔记实体 2 条互斥物理路径（kv SQLite ↔ desknotes .md）；inbox/info 表↔kv 双路径无对账；sync_state 分裂 4 类载体；settings ≥4 真源 | C-3 / H-2 / H-3 / H-7 |
| 架构强行贴合？ | **是**：pydantic-ai 测试桩充当生产流式适配器；desknotes 适配层"包装而非复用"repository；旧 QWebChannel 桥整链未摘；AIManager 双份逐行构造链 | C-1 / H-5 / M-2 / H-4 |

---

## 1. 🔴 Critical

### C-1 两套 action 执行器，`file.*` 安全语义分叉——破坏性写入有无回滚取决于"从哪进"

- **证据**：
  - A 套：`思忆集app/assistant/capability_router.py:439-504`（`execute/execute_direct`，butler 走这条）；`file_write` 经 `assistant/capability/_file_ops.py:118-120` 的 `router.write_guard.protect(...)` 带 before_snapshot 回滚。
  - B 套：`思忆集app/services/react_executor.py:151-286`（1143 行 if/elif），用裸 `os/shutil` **重写** `file.read/write/find/copy`；`:218-245` 的 `file.write` **无 write_guard 快照/回滚**，改为"强制落 `agent_outputs/` + `SIYI_AGENT_ALLOW_FILE_WRITE=1` 放开"的另一套策略。
  - 挂载：sandbox 审批闭环执行 B 套（`backend/headless_main.py:473-475`、`backend/handlers/_sandbox.py:60-62`）；chat 工具执行 A 套。
  - B 套内部混第三风格：`react_executor.py:919-935` 伸手拿 A 套私有属性 `router._handlers`；`:942-960` 对 `tool.*` 走 dynamic_tool_domain 失败再回退 `_handlers`。
- **成因**：sandbox/headless 场景当年为绕开 router 依赖就地内联执行，从未回收。
- **影响**：**经审批的高危写入恰恰落在无回滚的那套上**；三份工具目录（`CAPABILITY_CATALOG` / `services/react_tool_catalog.py` / CapabilityRegistry）靠 `capability_router.py:368-398` backfill 补丁对齐（注释自认 2026-09-11 才修过一次"模型看得见工具、ActionBroker 查不到能力"）。
- **治本方案**（整合动作，一次消 5 个问题）：
  1. `make_react_executor` 收缩为薄适配层（参数转换 + 调 `capability_router.execute(source="sandbox")` + 确认转发），删除 `_execute` 内所有 `file.*/notes.*/web.*` 内联实现；
  2. router 增公开 `dispatch(action_id, payload, source=)`，禁止 `_handlers` 私有访问；
  3. `react_tool_catalog` 为唯一声明源（模型可见性 + registry + risk_level 全从它生成），删 `CAPABILITY_CATALOG` 与 backfill 段；
  4. `make_brokered_react_executor`（`react_executor.py:1056-1139`，第三套执行体，唯一生产调用方为不可达的 MultiAgentOrchestrator）随之下线。
- **迁移/风险缓解**：先为 A/B 两套各写 6-8 个 action 的契约测试（先红后绿，符合 AGENTS.md 守则第 7 条）；B 套独有的 `SIYI_AGENT_ALLOW_FILE_WRITE`、敏感路径拦截等安全策略**先迁入 A 套 write_guard 再删 B 套**，防安全回归。

### C-2 `StructuredMemoryStore` 启动快照整表覆盖写——并发吞掉外部写入，静默丢数据

- **证据**：`思忆集app/memory/structured_store.py:57` 仅 `__init__` 一次性 `_load()`；`:378-383` `_save()` 以内存 `self._entries` 整表覆盖 `structured_memory_entries` 键。该键同时被 `services/knowledge_synthesis.py:131,170,183,305,345,363,423,427`、`services/cron_actions.py:347-395`、`backend/handlers/_inbox_knowledge.py:197` 用裸 `load_json/save_json` 直写。
- **成因**：知识卡片/简报/资讯要点功能后来者没走 store，store 也不感知外部写。
- **影响**：cron 生成的知识卡片、资讯要点可能在 store 下一次 mutation 时被启动期旧快照**整体回滚**（静默、无告警）。
- **治本**：所有读写收口到该 store（写前 reload-merge），把它注册为该键唯一所有者；中期 `structured_memory_entries` 升为 SQLite 表 + repository（同治 M-4）。
- **风险缓解**：迁移前对现键做一次性备份快照；merge 冲突以 `updated_at` 新者胜并落审计日志。

### C-3 desknotes 形态下 `store.json` 非原子并发写 + 任务同步全 stub——"另一套存储是残的"

- **证据**：
  - `思忆集app/data/repositories/desknotes_task_adapter.py:71-84` `_save_state` 非原子 `write_text`（note 适配器 `:354` 已改 `atomic_write_text`，同包两实现不一致）；note 写 `store.json.folders`、task 写 `store.json.todos` **同一文件并发无锁** → 后写覆盖前写。
  - 任务同步全 stub：`:346-380`（`list_pending_sync_tasks→[]`、`mark_task_as_synced→pass`、`merge_remote_tasks→全0`）；`_todo_to_task:115` 硬编码 `sync_state="local_only"` → **desknotes 形态下任务实际无法同步**。
  - `data/desknotes_id_map.py:88-99`：笔记与任务共享同一 `note_map/_int_to_uuid/_next_id` 自增命名空间；`sync_from_stores:35` 重扫时若 id_map.json 落后于 store.json（崩溃窗口）→ 映射重复/错位。
- **治本**：① `store.json` 单写者化（一个 `StoreJsonWriter` 串行化 + 原子写，folders/todos 共用）；② 任务同步二选一——实现，或显式禁用并在 RPC 返回值带 `unsupported_in_desknotes_mode` 标记；③ id_map 按实体分命名空间（一次性迁移脚本重排 `_next_id`，两侧各存 map）。

---

## 2. 🟠 High

### H-1 "多 Agent 模式"前端在售、后端 9 行空 stub；聚合路径生产恒死——静默假功能

- **证据**：`backend/handlers/chat_paths/multi_agent_path.py:1-9` 全文只有 return（docstring 自认 "Not yet wired"）；dispatcher 仍为其保留 19 行分支（`backend/handlers/_chat_memory.py:397-415`），门控 `hasattr(context,"multi_agent_orchestrator")` 恒真（`app_runtime/_context_builder.py:435-437` 无条件构造）；前端 `desk-notes/packages/desktop/.../ChatModule.tsx:49-56,303-312`、`useChatStream.ts:116`（等待 `multi_agent_summary` 帧）、`ws-client/types.ts:63,79`。
  `chat_paths/aggregate_path.py:13-15` 依赖 `context.aggregator`，而 `_context_builder.py`/AppContext **生产从未赋值**（唯一赋值在 `tests/test_handlers_chat.py:907,933`）。
- **伴生参数黑洞**：`preferred_provider/preferred_model` 三处签名接收零消费（`simple_stream_path.py:38-42,114-116`、`aggregate_path.py:7-23`、dispatcher 解析于 `_chat_memory.py:358-360`）；同类缺陷 `ai/client/fallback.py:1225-1236` 修过下游忘了上游，注释可查证。根因 = `**kwargs` 吞参。
- **治本**：二选一——真接线或全链删除（前端开关、dispatcher 分支、路径文件、`helpers.py:196-212` 四层转发）。`chat_completion_stream` 显式接受并透传 provider/model（或显式拒绝）；加 lint 门禁：公开 API 签名禁裸 `**kwargs` 转发。

### H-2 同一实体两条互斥存储路径 + 表↔kv 双路径无对账——"笔记到底在哪"不可判定

- **证据**：
  - 笔记/任务 kv-blob 路径（`repositories/_note.py:49,54`、`_task.py:38`）↔ desknotes .md 适配器，启动期二选一接线（`data/app_storage_context.py:36`：`if desknotes_data_dir: _init_desknotes() else: _init_local()`）。切换 `SIYI_DESKNOTES_DATA_DIR` 即换数据源，历史不互通。
  - inbox/info 每个 CRUD 方法"表存在走表、否则回退 kv"（`repositories/_inbox.py:35`、`_info_item.py` 同构），两侧可各自漂移、无对账。
  - `core/storage_transaction.py:108` `commit()` 逐键 save_json（多键非原子）；`:135` rollback 失败仅 log 继续；热表（`data/_hot_tables.py` 的 `info_items_tb/inbox_entries_tb`）直连 `_sqlite._connect()` 完全绕过事务层。
- **治本**：① inbox/info 设**迁移栅栏**——表就绪后删 kv 回退分支，配一次性回填 + 对账脚本（表 vs kv diff 报告）；② 笔记双形态定唯一权威：以 desknotes .md 为真源（Electron 已是主前端），kv 路径提供一次性迁移器后废弃，或明确 kv 仅供无 desknotes 的 headless 测试并禁止生产共存；③ `StorageTransaction` 同 db 多键写合并为单事务；派生索引（FTS/Chroma）改为带游标的全量可重建契约，不再假装参与事务。

### H-3 三套同步栈 + sync_state 四类载体 + 实体词汇三套不一致——同步语义无单一真源

- **证据**：
  - `sync/orchestrator.py:48` `RESOURCE_SPECS`（复数资源名 notes/tasks/news…，生产接线于 `app_runtime/providers/_tooling_provider.py:85`）∥ `sync/cloud_sync_client.py:91`（E2E 通道，经 `backend/headless_main.py:821` SyncBridge，**覆盖同一批实体**）∥ `sync/generic_sync_service.py:16-41`（**仅测试引用**，生产未接线）。
  - SSOT `core/sync_protocol.py:24` `RESOURCE_TYPES` 用单数（note/personal_task/inbox_item…），仅被 `sync/channels.py`、`sync/file_sync_service.py` 导入，orchestrator/cloud/generic 与 repositories 全部绕过；`file_sync_service` 以 `f"file_{subdir}"` 查 `CONFLICT_STRATEGY_MATRIX`（`sync_protocol.py:166`）——矩阵无 `file_*` 键，**恒回落默认 REMOTE_WINS**（名义复用 SSOT、实际不匹配）。
  - sync_state 分裂 4 类载体：实体 dict 字段（`sync/note_sync.py:49-62` 等）、desknotes `.sync-meta.json` sidecar（`desknotes_note_adapter.py:185,649-667`）、kv `integration_sync_cursors`（`data/local_storage.py:70,235`）、kv `remote_snapshot_*`（`local_storage.py:87-103,209-226`）。
- **治本**：定 `sync_protocol.py` 为唯一词汇与冲突策略真源（三方收敛）；`GenericSyncService` 删除或并入 orchestrator 资源表；sync_state 收敛为"表列 + 单游标表"，sidecar/snapshot 降级为可丢弃缓存并定义权威方向；给矩阵补 `file_*` 键或删假查找。
- **前置待证**：orchestrator 与 cloud SyncBridge 是否对同一资源同时启用（§9-4）。

### H-4 LLM 客户端旁路丛生 + AIManager 双份构造链——"改配置后行为 ≠ 重启后行为"的隐患

- **证据**：
  - 零接线并行实现：`ai/client/_direct_chat_client.py`（283 行，docstring 自称"替代 JudgeAwareClient"，全库无任何 import/实例化）；`ai/client/_deprecated/_judge_aware_client.py`（24.2KB，目录无 `__init__.py` 不可导入，却仍占 `scripts/quality-baseline.json:61` 指标）；`core/provider_health_cache.py`（205 行死模块，唯一引用者是上述死类）。
  - 绕过主链的构造口：`assistant/capability/_knowledge.py:145,265` 自建 `create_fallback_client`（跳过 Judge 层）；`services/info_collector_service.py:163` 直 new `OpenAICompatibleClient`（跳过 fallback+Judge 两层，不享受降级/配额/遥测/隐私路由）。
  - 双份构造链：`app_runtime/ai_manager.py:32-87`（`__init__`）与 `:89-127`（`recreate_ai_client`）逐行重复整条链；生产绕过工厂 `ai/client/config.py:331-340 create_judge_aware_client`（消费者仅测试）；`_recreate_ai_client` 又在 `backend/handlers/_api_keys.py:15-19` 与 `_system_settings.py:24-28` 逐字双份。
- **治本**（整合动作：构造单点）：`__init__` 只调 `_build_stack()`；生产改走 `create_judge_aware_client`；`_recreate_ai_client` 提公共模块；零接线客户端/死模块/_deprecated 目录/baseline 条目同波删除。
- **待前置核实**：`ai_cloud_only`（`ai/client/config.py:355-360`，2026-08-30 决策"全改云端"）线上取值——若常开，`_judge_aware_client.py:445-600` 五套 provider 决策器中的 local 腿为第二层可删死腿（§9-1）。

### H-5 伪流式：用 pydantic-ai 的测试桩充当生产流式适配器

- **证据**：`assistant/agents/butler_agent.py:568-596` `request_stream` 直接 `from pydantic_ai.models.test import TestStreamedResponse`，docstring 自认"底层 ai_client 同步非流式，一次性产出完整响应再拆 delta"——TTFT 收益为 0，但外观与配置像"已启用流式"；temperature/max_tokens 在 butler（0.3/4096）与 `chat_paths/simple_stream_path.py:114-116`（0.2/2000）两处硬编码漂移。
- **治本**：base 客户端补真 `chat_completion_stream(tools=...)`（httpx 具备流式能力，`ai/client/base.py` 已有非 tools 版），`request_stream` 真透传；模型参数收进单一 settings 键；`TestStreamedResponse` 回归仅测试用途。

### H-6 记忆入库多通道无去重 + 双重记忆注入 + RAG 腿静默死亡 + embedding 配置假接线

- **证据**：
  - 4 条"对话→记忆"通道全部收敛到无去重的 `memory/index.py:657-719 index_memory()`（只 append，mem_id 含时间戳前缀）：锚点通道（`backend/handlers/_chat_memory.py:1089-1109`，**绕过** `memory/extractor/_store.py:94-142` 的 DedupEngine）、`knowledge.ingest_conversation`（`backend/handlers/__init__.py:618-624`）、`memory/context.py:215-228`、抽取器通道。
  - 双重记忆注入：`chat_paths/helpers.py:160-193`（`[Related Memories]`，search limit=3）+ `backend/handlers/_chat_context_anchor.py:50-73`（`[对话记忆锚点]`，find_related limit=4），注入点 `_chat_memory.py:463-476`——同一次对话近义记忆塞两遍，不同检索 API/limit/格式。
  - RAG 腿死亡：`knowledge/retrieval/bridge.py:29` RagAdapter 传 `top_k=`，而 `rag_pipeline.py:195-202 query_knowledge_base` 形参为 `n_results` → 必抛 TypeError，被 `knowledge/unified_service.py:126-130` 吞成 debug——`knowledge.unified_query` 的知识库腿长期静默失效。
  - embedding 假接线：配置的 `bge-small-zh-v1.5`(512)（`knowledge/retrieval/embedding_service.py:31-59`、`core/settings_keys.py:360`）**从未进入任何向量库**——`memory/search/_engine.py:41` 与 `knowledge/retrieval/vector_store.py:42-59` 均未传 `embedding_function`，实际回落 Chroma 内置 MiniLM(384)（vector_store.py:27-29 注释自证）；`EmbeddingService` 仅被 `services/ai_text_service.py:228-241 embed()` 用而 embed() 无生产调用方。
  - 双实例：`KnowledgeBaseBridge` 未注册 service_registry → `app_runtime/app_context.py:128-132`（offline=True）与 `assistant/profile/prompt_context.py:422-435`（resolve 恒 None 再 new，offline=False）各建一个 ChromaVectorStore 指向同一目录。
- **治本**（整合动作：记忆写读收口）：
  1. 去重下沉到 `index_memory`（content 归一 hash 命中即跳过），所有通道含锚点强制走它；
  2. 删 `build_messages` 的 `[Related Memories]` 段，保留 anchor 一份（anchor 是已退役 PyQt6 prompt_context 的无头镜像，覆盖面更全）；
  3. 修 RagAdapter 传参 `n_results=`，吞异常的 debug 升为 warn + 契约测试（结构性防复发）;
  4. embedding 二选一但配置与事实必须一致：`EmbeddingService` 作为 `EmbeddingFunction` 注入两处 collection（**换模型需全量重建两个向量库**——影子重建、校验召回后原子切换目录），或删 bge 配置承认用 MiniLM；
  5. `KnowledgeBaseBridge` 注册进 registry 单例。

### H-7 数据目录与配置多真源——一根 settings 值四处可定义，两根 env 各拼各的路径

- **证据**：
  - 同一 LLM 配置可定义于 4 处：flat kv `llm_*`、同 settings blob 内 `provider_configs`、`os.environ`（`app_runtime/config_manager.py:19-22`）、代码默认（`core/settings_keys.py SETTINGS_DEFAULTS`）；`backend/handlers/_system_settings.py:89-140` 存在"写 flat→同步 provider_configs→成功清空 flat/失败保留"的双写回滚窗口。
  - settings 物理双份：SQLite kv `settings` 键（`repositories/_settings.py:84,216`）+ 明文镜像 `settings.json`（`data/local_storage.py:147` `_MIRROR_TO_FILE`）——崩溃窗口可分叉。
  - 双数据根：`SIYI_DATA_DIR` 在 `headless_main.py:234/1280`、`ws_server.py:487`、`instance_lock.py:54`、`knowledge/graph_store.py:98` 等各自 `os.environ.get` 拼路径，无集中解析器；`SIYI_DESKNOTES_DATA_DIR` 走 `app_runtime/desknotes_path.py`（缺 `notes/` 直接 raise）；`backend/handlers/_notes.py:750-758` 资产目录在两根间二选一——**两根同时指向不同位置时，笔记正文与附件资产跨根分家**。
- **治本**：单一 data-root resolver（一个函数产出 LocalDataStore/graph/log/asset/desknotes 全部路径 + 启动期一致性断言）；settings 以 `provider_configs`/credential_store 为唯一权威，flat 键改只读兼容视图、停止回写；镜像文件降级为单向导出快照（敏感键脱敏）。

---

## 3. 🟡 Medium

| # | 问题 | 证据 | 治本方向 |
|---|---|---|---|
| M-1 | **~5300 行 PyQt6 死路径**：`app_entry/main.py`（510 行，:329 还 import 已不存在的 `ui/` 包，无启动方）、`assistant/chat_ui.py`（592 行，宣称消费方 `ui/ai_chat_page.py` 不存在）、`workbench/controller.py+card_renderer.py`（1546 行，0 活引用）、`workflow/editor_canvas.py+node_config.py`（607 行，`workflow/__init__.py:3` 已宣告删除但文件仍在）、`desktop/web_bridge.py|pet_controller|selection_monitor`、`services/tts|screenshot_service|voice_input_service` 等；`tests/` 8 个文件整文件 skip"去留待统一决策" | 见各文件；`requirements-desktop.txt` 已不列 PyQt6、`pyproject.toml:49-53` 移 optional | 一次性归档波：workflow 死对直接删（零风险，`__init__` 已宣告）；其余移 `_deprecated_desktop/` 并清 PEP562 惰性映射；**保留** `workbench/service|layout_config|viewmodel`（活的，被 `_context_builder.py:306`/`core/settings_migration.py:346` import） |
| M-2 | **QWebChannel 旧桥整链未摘**：`思忆集app/web/appBridgeClient.ts`（291 行，`call_service('notes','create_note')` 式平行双契约）；`AIActivityPanel.tsx:51,91`、`FloatingChatApp.tsx:548` 运行时仍探测 `window.bridge`；后端 `app_runtime/app_context.py:336-349` 仍 start/stop `web_bridge_service` | docs/architecture-overview.md:5 已宣告该架构淘汰 | 确认 Electron 打包下 `window.bridge` 恒 undefined 后整链删除（ts + web_bridge.py + 挂载点），或标注冻结兼容层并不再加功能 |
| M-3 | **automation_hub 第二套底座 ~60% 无消费**：`api/main.py` 挂 35 router，与主后端重叠（agent/tools/cron/knowledge/evolution/workflows）；双向耦合——app→hub `integrations/automation_hub_client.py:16`（有 try/except 兜底）、hub→app `api/main.py:49 WEB_DIST_DIR` 直指 `思忆集app/web_dist`；处置文档 §3 裁剪清单未执行 | docs/automation-hub-disposition.md §3 vs api/main.py 实际 include；CI `python-tests.yml:160-162` 仍测 hub | 按已批准 disposition 落地阶段 A：从 `api/main.py` 摘无消费路由（cron/workflows/evolution/intel/web_search/plugins），`local_agent/` 归档；worker/审批/审计/工具白名单内核保留（ADR 认定不可替代）；`WEB_DIST_DIR` 改可配置 |
| M-4 | **local_storage 键值垃圾场**：`notifications`（`services/notification_dispatcher.py:401-492` 裸读写）、`chat_feedback`（`core/settings_keys.py:201-202` 注释自认"占位，实际通过 load_json 访问"）、`structured_memory_entries`（完全未注册）——实体级数据无 schema/事务/repository/备份归属 | 注册表在 `data/local_storage.py:65-144` | 三键各建 repository + SQLite 表（并入 §5 波次③，一次动 local_storage 一个面） |
| M-5 | **三套 handler 错误风格**：`backend/handlers/_wrap.py:101-140` 契约仅 4 文件 30 处采用；手写 `except Exception: return {"error":...}` 338 处；`wrap_degraded` 装饰器零生产使用（仅测试） | 实测计数 30 vs 338 | 推广 `degrade` 到高频域 + lint 门禁禁裸 except-return-error；删未用装饰器 |
| M-6 | **60 个 `@deprecated` 僵尸 RPC 仍注册**（`_ai_usage.py` 9、`_system_settings.py` 8、`_api_keys.py:121-218` rotation 七连等）；注册机制三套并存、全靠 import 副作用手写 45 个 `register_*` 调用 | `backend/handlers/__init__.py:172-224`（域委托）、`:252-778`（内联闭包）、`:784-796`（注册后二次改属性 + `_R4_METHODS` 40 项分离维护） | 按 `tests/test_jsonrpc_contract.py::UNUSED_METHODS_BASELINE` 批量下线一波；`register_all` 改域列表循环；方法清单真源 codegen 前端 typed client（消 `shared/src/rpc/methods.ts` 半截表 + 调用点字符串字面量漂移） |
| M-7 | **ws_server.py 1597 行 11 职责**（4 通道鉴权、token 生命周期、配对、会话表、3 限流桶、心跳、流式、体积守卫、LLM 中继、观测、静态托管）；WS 与 HTTP-RPC 错误映射表两份各 ~50 行 | `backend/ws_server.py:821-869` vs `:1305-1369`；路由集中于 `:251-271` | 先合并 `error_to_envelope()` 单点（小、即时收益）；auth/relay/admin/static 拆 `routes_*.py` 另立项 |
| M-8 | **手机端第三套服务层**：`思忆集手机端app/src/lib/bridges/` 15 文件 + syncEngine/contentSync/cloudSync/pendingOps/eventBridge/chatSocket 自成体系，与 shared `store/slices` + `utils/syncQueue` 语义重叠、方法名靠字符串对齐 | 传输层已共享（Junction 实测 `@desk-notes/shared`，ws-client 仅 `shared/src/ws-client/ws-client.ts` 702 行一份） | 纯 `callRpc` 薄封装并入 shared rpc 层（配合 M-6 codegen 同波）；平台专属（cloudSync/secureStore）保留；eventBridge/chatSocket 与 shared backendEventBus 逐行 diff 后处置（§9-3） |
| M-9 | **gateway 事件消费三套实现 + `_safe_chunk` 两份 + 错误标记词表三份**：`chat_paths/gateway_path.py:706-744`（旧实现，仅测试引用）/ `:566-703`（progressive，生产唯一）/ `:803-833`（`_resume_drain` 自认等价）；`_safe_chunk` 在 gateway_path:267-279 与 pydantic_agent_path.py:30-40 逐份双写；`_FATAL_KERNEL_ERROR_MARKERS`（gateway_path.py:449-461）与 `ai/llm_resilience.py:15-53` 并行；`_chat_memory.py:422` `run_gateway_path is not None` 恒真（测试钩子固化进生产分支） | 见左 | 以 progressive drain 为唯一函数，删旧实现 + `_drain_events` + `is not None` 检查（测试迁 patch `_use_gateway_route`）；`_safe_chunk` 上提 helpers 单点；网关标记收进 `classify_error` 的一个类别 |

---

## 4. ⚪ Low

1. **门脚本三重复**：`scripts/done_check.py`（规范聚合器）∥ `scripts/automated_check.py`（`--phase all` 平行 runner，0 引用孤儿）；docs 对齐 3 份（`check_doc_index_consistency.py` 在用 vs `verify_docs_alignment.py`、`project_health_check.py` 的 doc 段均 0 引用）；`check-layer-boundaries.py` 与 `kernel_boundary_ratchet.py` docstring 自认"RULES 完全同源"靠人工同步 → 抽共享 RULES 模块、归档孤儿。
2. **`verify_tmp/A-headtree/` 1048 个 .py 幽灵源码副本**（非 git worktree、被 .gitignore:283 忽略，但污染一切不尊重 ignore 的扫描/统计/覆盖率通配）+ `.pytest-tmp-root/`、`MagicMock/`、根 `_tmp_*.py`——直接删（纯工作区垃圾，git 无感）。
3. **`domain/repositories/`、`domain/usecases/` 空脚手架**（`__init__.py` 仅 docstring；真协议在 `repositories/_protocols.py` 与实现 1:1）；`domain/models/{note,task,memory,vocabulary,translation}.py` 未检到生产 import（待证 §9-5），仓储层清一色返回裸 dict——要么把 protocols 迁入坐实该层，要么删空壳，别留半拉子 DDD。
4. **QUA-12 路由规则"改代码不激活"永久休眠**：`chat_paths/helpers.py:39-47` 注释自认收紧版规则因 `chat_route` 缺省 legacy 而不点火——维护成本已发生、收益未发生。给激活日期或删除规则。
5. **三处同名 `capability_router.py`**（assistant 1050 行 / cross_device 303 行 / plugins 158 行，语义各异）——改名消歧即可。
6. **`assistant/capability_router_recovered.py`**（乱码、类名与真身重名、0 引用）+ 7 个陈旧 `.pyc`（`capability_router_recovered.pyc` 87KB 等）——删。
7. **smolagents 摘除后**：无悬空 import、`force_tools` 全库 0 命中（AGENTS.md 该兼容句可删）；`archive/mcp-client-202608/` 实际不存在（AGENTS.md 目录注释已过期，修文档）；`stream_tools_path` 仅存 3 处注释残留（`chat_paths/__init__.py:13`、`_chat_memory.py:48`、`assistant/capability/_tool_management.py:14,234`）。
8. **`mcp_server/`（纯 stdlib 服务端）仓库内零启动方零消费方**，tools.py 31KB/store.py 28KB 与后端 store 逻辑重复——先确认 DSH 外部接入与否（§9-6）再决定归档。
9. **re-export 兼容层集中营**：`assistant/` 顶层约 18 个 0.1-0.4KB shim（`butler_agent.py`、`planner.py`、`judge.py` 等）+ `_chat_memory.py:23` `_is_complex_task` re-export + `:46-49` 旧私有别名——给退役截止期后批量删（先核 §9-7）。

---

## 5. 推荐执行次序（按"一次动作消多问题"排序）

| 波次 | 动作 | 一次性解决的问题 | 风险 |
|---|---|---|---|
| **① 零风险删除波** | DirectChatClient、`client/_deprecated/`(+baseline 条目)、`provider_health_cache`、workflow 死对、recovered.py+pyc、verify_tmp、GenericSyncService、僵尸 RPC 批量下线 | H-4 部分、M-1 部分、H-3 部分、M-6、Low-2/5/6/7 | 纯删 ~4000+ 行死码 + 缩 60 个攻击面 |
| **② 执行器收敛波（最高价值）** | C-1 四步（B 套收缩为 A 套薄适配 + catalog 单源 + 契约测试先红后绿） | C-1、risk 借用、三目录 backfill、第三执行体、`_handlers` 私有访问 | 中（先迁 B 套安全策略再删） |
| **③ 存储收口波** | C-2 + C-3 + H-2 迁移栅栏 + M-4 三键建表；同波做 H-7 data-root resolver | 全部丢数据类缺陷 + 双写路径 + 事务覆盖 | 中（迁移脚本 + 备份快照先行） |
| **④ 同步 SSOT 波** | H-3 全套（词汇统一 + sync_state 载体收敛 + 假查找清除） | 三栈漂移、假 SSOT、恒 miss 冲突矩阵 | 中（依赖 §9-4 取证） |
| **⑤ 对话链路波** | H-1 + H-6 + H-5 + M-9（死路径处置、记忆收口、真流式、drain 单函数） | 用户可感知的假功能 / 静默失效 / 静默低质量 | 中（embedding 切换需影子重建验证） |
| **⑥ 治理波** | M-2 旧桥删除、M-3 hub 按 disposition 裁剪、M-5 错误契约、M-7 ws_server 拆分、M-8 手机端并入 shared | 表面积与双契约 | 低-中 |

每波收口标准（AGENTS.md 守则第 4 条）：原子 commit（带任务编号）+ `python scripts/gate_all.py` 绿 + 结构性测试先红后绿。C-1/C-2/H-6③ 各天然对应一条可先行落地的契约测试。

---

## 6. 确认健康、切勿误伤的部分（负面清单）

- **LLM 主链本身干净**：`ai/client/base.py:169` `OpenAICompatibleClient` 是唯一真实 HTTP 出口（fallback.py/config.py 全部在构造它）；base→fallback→judge_aware 三级洋葱仅 AIManager 一处组装。
- **韧性四件套职责正交**：熔断（`ai/circuit_breaker.py:29-254` 含 Registry 单例）、错误分型（`llm_resilience.classify_error`）、配额（`provider_quota`）、主动健康监控（`ai/provider_health_monitor.py:67`，`fallback.py:35` 单挂载）；聚合器经 `ai_manager.py:129-210 client_factory` 复用底层客户端，未另起炉灶。
- **三端前端共享是真的**：`@desk-notes/shared` 经 Junction 同一份；ws-client 仅 1 实现（702 行，重连/心跳/流式/watchdog 齐备）；desktop store 为薄 re-export + 平台落盘适配，非复制。
- **事件出站单链干净**：handlers 目录 0 处直接抓 socket 广播，全部 `event_bus → EventBridge → _ClientOutbox → ws`（`backend/event_bridge.py:420-425`）；关键事件优先级表单点维护；`event.emit` 反向注入有 4 类白名单守卫。
- **R4 审批闸门单点**（`backend/method_registry.py:304-350,478`，WS/HTTP/流式三入口复用）；限流按 conn_id/IP 分桶互不挤占。
- **确认/挂起闭环单套**：`ConfirmationStore + services/confirmation_flow + chat.resume`；butler"不发明第二套挂起机制"注释属实。
- **单套无问题清单**：narrative 存储（唯一 `NarrativeStore`）、知识图谱写入（`graph_extractor` 唯一写者）、Chroma 双 collection 目录隔离（`memory_entries` vs `knowledge_base` 无锁互踩）、FSRS(词库) vs SM-2(记忆) 分域、shell 执行（`core/shell_execution.py` 单点，不存在第二 shell loop）、词汇/翻译/提醒/提案仓储写路径单一、`_exec_step.emit_exec_step` 单点、trae 单真身三适配面（`integrations/trae_bridge.py`）。
- 门脚本互补簇保留：`check_core_integrity` + `check_no_sourceless_pyc` + `check_ignored_source` + `check_git_integrity`（机制各异，自述互补）。

## 7. 事件桥接的次要越界（记录在案）

`event_bus.emit` handler 层直发 20 处 vs services 层 42 处（如 `backend/handlers/_notes.py:73,119,190`）——通道不冗余，但业务事件发送职责下沉到了 RPC 层。修复方向：随波次⑤把 handler 内 emit 收敛到对应 service，RPC 层只触发不广播。

## 8. 与既有文档的关系

- 本文是**跨域全景审计**；域内现行设计真源仍以 INDEX.md 路由为准：`architecture-execution-engines.md`（执行分层）、`automation-hub-disposition.md`（hub 处置，本文 M-3 是其执行核查）、`memory-system-design.md`、`sync-protocol-design.md`、`assistant-organization-map.md`（W4 杂物箱，本文 M-1/Low-9 与其"分批迁移"互补）。
- `docs/verified-facts.md` 已核验事实优先于本文推断；本文引用其结论处已避免重复立约。
- 修复完成后：每波次在 §5 表标注完成 + commit SHA + gate_all 绿，本文相应 finding 追加"已修复 @SHA"行；全部收口后本文降级为 🔴 归档。

## 9. 待证清单（2026-09-23 取证波已全部落橎，结论如下）

| # | 事项 | 取证结论 |
|---|---|---|
| 1 | `ai_cloud_only` 线上取值 | ✅ 已证：运行时 SQLite 实测 **True**（env 未设、blob 真值；镜像 settings.json 无此键——坐实 H-7 双份分叉）。影响：**Judge 层 local 腿已事实死（judge.py:184-250 全短路）可删；但 `fallback.py:567` 链末端 local 终端兑底腿仍活（云端全挂时生效），不在 cloud_only 覆盖范围，删否需产品确认** |
| 2 | 前端 fileStore ↔ 后端 adapter 双写 | ✅ 已证实：**双写者同目录（`%LOCALAPPDATA%\desk-notes\siyi-notes`，盘面实测 431 个 .md 中 325 个 `siyi_*.md` + 106 个客户端 id 文件）；create 路径因 adapter:489 丢弃 client id 而写**不同文件**（复制式分叉），update/delete 命中同一 .md 无锁互盖；`store.json` 为**三写者**（Electron persist + note adapter + task adapter）且 task 侧非原子 | 
| 3 | 手机端桥接 vs shared | ✅ 已证：`eventBridge.ts:15-35` 是 shared backendEventBus 的**逐行等价第二实现**（shared bus 已随 getWsClient 自动安装），且其 13 类订阅中 8 类挂在后端不存在的死事件名上（含 inboxBridge/settingsBridge 死守卫）；`chatSocket.ts` 为无消费者空壳（send 是 no-op）；**新发现：聊天生产腿走 `mobileStore.ts:439` HTTP SSE 绕开 ws-client（拿不到 typed chunk/心跳保护）；`TransferView.tsx:166-210` 存在第三条裸 WebSocket（无退避、握手缺 token/client_version 被后端判 legacy）**；Junction 共享实测成立 |
| 4 | orchestrator vs SyncBridge 启用关系 | ✅ 已证：**可同开，两栈守卫完全互不感知**；生产接线的是 `api/sync_orchestrator.py`（行号漂移更正：非 H-3 所写的 `sync/orchestrator.py`，后者为第四份孤儿拷贝，仅 1 测试引用可入删除波）；重叠资源 notes/tasks（同实体两栁词汇 tasks↔todos 即 SSOT 违约活例）；SyncBridge 受 settings 三键守卫，`sync.enable` 无反向互斥；默认装机态两栈都不跑 |
| 5 | `domain/models/{note,task,memory,vocabulary,translation}.py` 间接使用 | ✅ 已证：五文件类符号**零消费**（全写法 grep；`MemoryCandidate` 撞名澄清：生产用的是 `memory.types` 同名类）；删除须同步清 `domain/models/__init__.py:13-17` re-export → 入下一删除波 |
| 6 | `mcp_server/` 仓外消费方 | ⚠️ 仓内零生产启动/消费（仅测试）；纯 stdio 服务端面向仓外内核，deliverables 存留 2026-09-18 DSH 实测消费证据（`mcp__siyi__*` 504 次），profile 不在仓内 → **不能判死，待用户确认外部是否在线**；另其 `store.py:61-68` 自建 LocalDataStore 读已死键（数据面缺陷另案） |
| 7 | `assistant/` 18 shim + 旧别名 + PyQt6 skip 测试 | ✅ 已证：8 个有生产消费必留；**7 个零消费/被同名包遮蔽不可达可删**（environment/computer_use/planner/judge_supertag/capability/chain/execution）；3 个仅测试消费待决（code_worker/prompt_context/judge_decision）；`_chat_memory.py:23/49` 两旧别名零消费可删（注释失实）；PyQt6 skip 测试 6 个目标已消失可随 M-1 删，2 个（workbench_card_renderer/smoke_quality_gate）目标仍活需决策 |
| 8 | `build_llm_provider_topology` 优先级 vs 文档 | ✅ 已证：拓扑 resolver 只读 flat kv+env，**对 provider_configs blob 不可见**；而真工厂 `create_fallback_client` 是 provider-first（config.py:439-453）→ 拓扑与运行时优先级相反（doctor 显示 ≠ 实际行为，H-7 新证）；另 `environ or {}` 吞 None（llm_provider_topology.py:59）；**新发现：AGENTS.md:105 `SIYI_LLM_API_BASE` 为幽灵键，真名 `SIYI_LLM_BASE_URL`**（全库 0 命中，按文档设置永不生效） |
| 9 | doctor 探针 | ✅ 已证：**遗留且无意义**。实指 23 行 shim `思忆集app/main.py`（非 app_entry/main.py），仅 `is_file()` 存在性粗检；真身已断链（`ui/` 已删）且生产从不经过 → 应改指 `backend/headless_main.py`；归档 app_entry/main.py 前需同步探针 + `cold_start_stability_check.py` + 各基线条目，非纯删 |

### 波次① 完成登记（2026-09-23）

- ①-a 已收：`c179ac5`（DirectChatClient / `_deprecated/_judge_aware_client.py` / `core/provider_health_cache.py` + quality-baseline 3 条 + 注释收口，净删 1076 行）
- ①-b 已收：`a54f707`（workflow 死对 / recovered.py / GenericSyncService + 孤儿 pyc 4 枚清理，净删 805 行）
- ①-c 僵尸 RPC：~~暂缓~~ → **已收**：9-21 半成品经用户裁决先固化（`0ba3a4f`，32 files）后，`58e5fa8` 下线 59 个 @deprecated 僵尸 RPC（三重确认法；净删 785 行，运行时真值 397→339，inventory 同步重生成）。保留样本：`file.write` / `notification.test` 三套 R4 安全回归的规范调用样本，待重锚定后另议下线；误删 9 个（info.sections.* 5、usage.budget.get、evolution 3）因前端真实调用已当场恢复注册
- 补充删除波（用户裁决④「兼容层一次清」）：`a103d5f`（assistant/ 8 shim、domain/models 5 死模型、sync/orchestrator.py 第四份孤儿拷贝、6 个 ui 死测试、doctor 探针改指 headless_main 等，净删 1408 行；取证翻案 2 处：planner/computer_use 有相对导入消费者、`_react_catalog_to_openai_tools` alias 有测试消费者，均按「宁可不删」复原）
- broad-except 9-21 遗留 3 处补日志：`195b350`（_sandbox.py ×2、gateway_path.py ×1，棘轮回绿）
- 文档同步：`21196b8`（AGENTS.md 删 47 个下线方法表行、397→339 三处计数、B-10 追加 09-23 下线条目、目录树 4 处死引用清理、knowledge 22→21）
- gate_all 既有红不属于本波：mypy 尺子版本不符（基线 2.3.1 vs 现 1.20.2，环境既有）；broad-except 新增 3 处均在 9-21 半成品文件（_sandbox.py:105/188、gateway_path.py:535）；inventory DRIFT=9-21 新增 RPC 未重生成（纯 HEAD 基线实测 PASS）
- 取证波附带发现（入波次④增补，本轮未动）：`sync/orchestrator.py` 实为第四份孤儿拷贝（生产真身是 `api/sync_orchestrator.py`，仅 1 处测试引用可入下删除波）；`web_bridge.py:307` 调不存在的 `trigger_sync` 吞异常后假成功；`cross_device/session.py:218` 签名错配必 TypeError 静默；`info_scheduler.py:138` 绕过 `cloud_sync_enabled` 总开关纯 env 上云；`backend/sync/cloud_push.py` 纯 env 死岛

## 10. 修复进度（收工时勾选）

- [x] 波次① 零风险删除波（①-a `c179ac5` / ①-b `a54f707` / ①-c `58e5fa8`（前置固化 `0ba3a4f`）/ 补充删除波 `a103d5f` / broad-except `195b350` / 文档同步 `21196b8`；遗留处置：test_r2_verify 行号漂移已内容锚定修复 `4b93b12`；file.write/notification.test 重锚定评估=改锚需动三套 R4 回归 37+ 断言，成本>收益，**维持保留**（lead 决策，`docs/architecture-audit-2026-09-23.md` §C-1 登记段））
- [x] 波次② 执行器收敛波（C-1 四步全部落地 `37fd773`，前置裁决注释 `44f1cbf`）：B 套 react_executor 收缩为薄适配 1143→148 行（全部动作经公开 `router.dispatch/dispatch_sync(source="sandbox")`，`_handlers` 跨层私有访问清零）；B 套三样安全策略（敏感黑名单/env 门控/agent_outputs 夹取）**先迁 A 套再删内联**（新契约 `test_capability_file_ops_safety_contract.py` 先红 6 转绿，单真源 `core/path_safety.py`）；工具声明合并为 `react_tool_catalog` 115 条唯一源 + 删 backfill + CAPABILITY_CATALOG 派生化（risk 词表统一 + `to_step_risk()` 回落，confirmation 零漂移实证）；`make_brokered_react_executor` + orchestrator 死腿/压缩腿/缓存腿下线（966→152，净 -1319 行）；附带：context=None 兑底 executor 改 fail-closed、flaky test_qua4 补 drain+终态显式断言、`_resolve_router` 公开化。定向 125 绿 / 相关选型 486 绿。另携裁决③ local 腿标记注释（judge/fallback，`44f1cbf`）
- [ ] 波次③ 存储收口波（C-2/C-3/H-2/M-4/H-7）⚠️ §9-2 已证实双写者且含前端 fileStore，需先裁决笔记唯一权威源
- [x] 波次④ 同步 SSOT 波（H-3）——包 A/B/C 并行落地 `9e0a9c5`（取证蓝本 `deliverables/wave4-sync-ssot-blueprint-2026-09-24.md`）：删 5 死岛（backend/sync/ 整目录 + delta_sync/services/channels/file_sync_service，净 -2172 行）；附带发现 4 项全修（info_scheduler 注入 settings+cloud_sync_enabled 总开关门控、web_bridge 假成功反转真调 sync_all、cross_device 幻影 orchestrator 分支改显式本地 only、cloud_push 清除+三 baseline 同步）；SyncState 枚举上提 core/sync_protocol 单源 + note_sync re-export + 散字面量棘轮 84→80；resource_vocab tasks↔todos 单向映射（未接线，仅锁词表）；双栈启动互感守卫（可同开必 WARN）+ A/Worker 词表分歧锁；跨端 wire 零变更（test_worker_sync_contract 全绿实证）。新登记：`sync/transport.py`+`sync/directory_manager.py` 删岛后成新孤儿（归尾波处置）；PP-3（info_brief 受总开关门控 = 对手机端可见数据流的语义收紧，已按修复意图落地，手机降级行为待验）；daily_summary 孤儿测试系波次①-c 误伤面，已重指向 cron 路径+防复活断言（一并入本提交）
- [ ] 波次⑤ 对话链路波（H-1/H-5/H-6/M-9）部分与 9-21 半成品相撞（butler_agent/gateway_path）；H-1/H-6 可先行
- [ ] 波次⑥ 治理波（M-2/M-3/M-5/M-7/M-8）M-8 已按 §9-3 改写为三件精确动作（事件总线收口/chatSocket 删除/TransferView 裸 WS 并入+聊天腿切 ws-client）

### E2E 集成测试战役完成登记（2026-09-24，五模块+跨模块旅程）

- 总判定：全量 `-k "integration or e2e or journey"` 终扫 **415 passed / 4 skipped / 0 failed**；gate_all 4/4 绿（含 pip-audit 本轮网络恢复）。新增五套 E2E（`39fd397` 聊天帧序契约/笔记任务双形态/收件箱通知协作链；`eb96e05` 同步域波④后首次回归 342 例全绿+记忆域 C-2/M-4 证据型复现）。
- 战役挖出并收口的债：① **波次①-c 孤儿测试同步债 15 例**（notes.ai_assist×5/batch_delete×3/agent 面×4/evolution×2/word.review_fsrs——重锚防复活锁，journey4 重指向存活等价入口 `word.review`，`f78ffa3`；取证确认 review_fsrs 系冗余别名非误伤，`vocab.rollback_fsrs` 无存活承接维持下线）；② **conftest AUTOSTART 不隔离 desk-notes 桥**（写类集成测试可污染真实 .md 库，补 `SIYI_DESKNOTES_DATA_DIR` 权威隔离 `7407d7c`）；③ fallback 单测 11 个 ai_cache 生产泄漏红（SIYI_DATA_DIR 注入 `db16755`）；④ concurrency import 时 WS_URL 快照锁死 18765（惰性化同先例）；⑤ `knowledge.graph_for_viz` 取证定性为①-c 有意下线非漏接线，测试改判防复活锁（本役零源码改动）。
- 新登记缺口（未修，待对应波次）：`tasks.toggle/delete` 对不存在 id 假 True（_tasks.py:299/:203，历史遗留，E2 已 xfail(strict=False) 钉住）；事件 shape 三处漂移（inbox.delivered 前端 TS 缺 P2.3 metadata/tags；proactive.suggestion 两生产者 content/suggestions 与前端 description 互不对齐，E3 登记型测试钉住）；桥形态 todo 冲突面不可达（task adapter 无 sync_state 通道，H-3 对称欠账）；非 fatal sync.failed payload 无 error 字段；僵尸闭包残留 handler 源码（notes_ai_assist 等，归尾波源码清理）。诚实边界：真机 Electron UI/手机↔桌面直连/活体 Worker wire 层未覆盖（以双 WS 客户端代理+静态契约锁+mock 单元承接）。
