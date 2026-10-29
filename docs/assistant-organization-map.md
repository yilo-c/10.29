# assistant/ 分包地图与收口计划（W4 地图先行，非盲目重排）

> 阶段二 W4 方向。本文档是**收口地图与计划**，不是执行记录。
> 实测数据见 [architecture-execution-engines.md](architecture-execution-engines.md)（已澄清四引擎真实角色）。

## 1. 实测现状（2026-08-23）

| 维度 | 数据 |
|---|---|
| 顶层 `.py` 文件数 | **77**（总计 ~25.2k 行） |
| 已有子包 | `autonomy_components/`(6)、`capability/`(12)、`chain/`(6)、`persona/`(3)、`triggers/`(3) |
| 无 sibling 内部依赖的顶层模块 | **69/77**（仅被 `backend/`/`services/`/`plugins/` 调用，是理想归类单元） |
| 被 sibling 引用的模块 | `butler_checkpoint`、`capability_registry`、`capability_router`、`checkpoint`、`code_executor`、`context_compressor`、`inbox`、`models`（各 1 处） |
| 外部引用面最重 | `autonomy`(21)、`chat_ui`(11)、`butler_agent`/`user_profile`/`judge`(各 9)、`intent_router`(8) |

**关键判断**：报告称"60+ 文件混装人格/执行/编排/路由/桥接/安全/成本"基本属实，但
已有 5 个子包骨架，且 69/77 模块**零内部耦合**——这其实是低风险归类的好基础，而非死结。

## 2. 真实语义聚类（基于文件职责 + import 关系）

### 2.1 已存在的子包（维持，补 `__init__` 职责声明）
- `persona/` — 人格定义与注入（3 文件）
- `capability/` — 能力路由/注册/工具（12 文件）★ 已是最大子包
- `chain/` — 多步链路编排（6 文件）
- `autonomy_components/` — 自治生命周期组件（6 文件）
- `triggers/` — 事件触发器（3 文件）

### 2.2 建议新增的子包（从顶层迁入，保留 re-export 兼容层）
| 新子包 | 候选迁入模块（低耦合优先） | 风险 |
|---|---|---|
| `assistant/execution/` | `execution.py`、`action_broker.py`、`react_engine.py`（活跃，慎）、`context_compressor.py` | 中（`action_broker` 被 2 处外部引用） |
| `assistant/bridging/` | `cc_bridge.py`、`trae_bridge.py`、`codex.py`、`code_worker.py`、`computer_use.py` | 低（CLI 适配器，外部引用少） |
| `assistant/routing/` | `intent_router.py`、`capability_router.py`（活跃，慎）、`judge.py` | 高（`judge` 9 处外部引用；`capability_router` 活跃依赖） |
| `assistant/delivery/` | `inbox.py`、`chat_ui.py`（活跃，慎） | 高（`chat_ui` 11 处外部引用） |
| `assistant/profile/` | `user_profile.py`、`prompt_context.py` | 中 |
| `assistant/agents/` | `butler_agent.py`、`planner.py`、`multi_agent_orchestrator.py`（活跃，慎） | 高（编排器活跃依赖，见执行引擎文档） |

### 2.3 暂不动的活跃大文件（前轮已验证，标 deprecated 会崩）
- `multi_agent_orchestrator.py`(926) — 被 `_context_builder`/`_chat_memory`/`react_tool_catalog` 调用
- `capability_router.py`(939) — 被 `capability_router` 外部 + `backend` 调用
- `task_manager.py`(958) — 任务状态机核心，被多处引用
- `autonomy.py`(554) — 自治入口，21 处外部引用

## 3. 收口原则（避免重蹈 react_engine 误判）

1. **地图先行**：本文件为唯一权威，任何迁移先在此登记批次。
2. **每批 ≤ 5 个模块**，且只选"无 sibling 依赖 + 外部引用 ≤ 4"的模块先行。
3. **保留兼容层**：迁入子包后，在 `assistant/__init__.py` 或原位置留 `from assistant.X import Y` re-export，外部 import 零改动。
4. **不碰活跃大文件**：`multi_agent_orchestrator`/`capability_router`/`task_manager`/`autonomy` 在依赖解耦前禁止移动。
5. **与并行会话协调**：阶段一接线由其他会话执行，assistant 迁移需避开其改动文件（`headless_main.py` 等启动序列）。

## 4. 建议执行批次（按风险升序）

- **批次 A（✅ 已完成 2026-08-23）**：`bridging/` 子包 — `cc_bridge`、`trae_bridge`、`codex`、`code_worker`、`computer_use` 已 `git mv` 迁入 `assistant/bridging/`，原位置留 re-export 兼容文件。**修复两处迁移回归**：
  1. `codex.py`/`computer_use.py` 的 `from .models import` → `from assistant.models import`（`models` 是顶层共享模块，不可迁入 bridging）。
  2. `WorkerCodeExecutor._start` 子进程入口 `python -m assistant.code_worker` → `python -m assistant.bridging.code_worker`（否则被顶层 compat re-export 劫持，worker 立即退出）。`_find_project_root` 适配 bridging 层级。
  - 验证：`tests/test_code_worker.py` 17 passed；外部依赖方（`ai`/`orchestration.drivers.*`/`backend.handlers._trae`/`app_runtime._context_builder`）导入全 OK。
- **批次 B（✅ 已完成 2026-08-23）**：`profile/` 子包 — `user_profile.py`(393)、`prompt_context.py`(839) 已 `git mv` 迁入 `assistant/profile/`，原位置留 re-export 兼容文件（`UserProfileService`/`DEFAULT_PROFILE`/`PromptContext`/`logger`）。两模块只依赖标准库，无 sibling 耦合，迁移零回归。验证：`test_assistant_user_profile` + `test_profile_mirror_invariant` 全过；`ai`/`app_runtime.*`/`memory.context` 等 8 个 import 方全 OK。注：`test_assistant_dialog.py` 3 个失败为**预先存在**（其 `_make_bare_mw` 尝试 `from ui.main_window import`，而 PyQt6 `ui/` 已归档），与迁移无关。**待办**：补 `persona/` 与 `profile/` 边界声明。
- **批次 C 子集（✅ 已完成 2026-08-23）**：新建 `routing/`（`intent_router`）、`delivery/`（`inbox`）、`agents/`（`butler_agent`）三子包，git mv 迁入 + 原位置 `import *` 兼容层。**修复两处迁移回归**：
  1. **私有符号 re-export**：`import *` 不导入 `_` 开头符号，但 `tests` 直接访问 `butler_agent._agent_cache`/`_pending_contexts`/`_resolve_capability_router` → compat 文件显式补（`intent_router._ai_client` 是实例属性非模块符号，无需补）。
  2. **compat 层 monkeypatch 陷阱**：`test_assistant_intent_router.py` 用 `monkeypatch.setattr(intent_router_module, "datetime", ...)` patch 的是 compat 层模块绑定，打不到子包真实模块（`import *` 只复制绑定）→ 测试改为直接 import `assistant.routing.intent_router`。
  - 验证：intent/inbox/butler 相关 12+ passed；`test_pydantic_agent_path_streams_chunks_and_progress` 1 failed 为**预先存在**（`backend.handlers.chat_paths._exec_step` 缺失，与迁移无关）。
  - **待办**：`judge`/`planner`/`execution`/`action_broker` 有相对导入（`.models`/`.capability_registry` 等），需连带处理后再迁，留后续。
- **批次 C 剩余（✅ 已完成 2026-08-23）**：全部带相对导入模块归位——
  - `routing/` 增：`judge` + 辅助 `judge_decision`/`judge_supertag`/`privacy_classifier`（连带迁移）。
  - `execution/`（新建）：`execution`、`action_broker`、`context_compressor`、`environment`。
  - `agents/` 增：`planner`。`capability/` 增：`capability_registry`。
  - **相对导入治理（迁移必备步骤）**：`.models`/`.capability_registry`/`.cost_tracker`/`.execution_plan`/`.checkpoint` 等指向**顶层共享模块**的相对导入 → 改为 `from assistant.X import` 绝对导入。**牵连的顶层模块**（`capability_router`/`autonomy`/`_router_mcp`/`execution_router`/`judge_mixin`/`capability_router_recovered`）的 `from .<已迁移>` 一并改为绝对导入（12 处）。
  - **测试 import 治理**：15 个测试文件 24 处 `from assistant.<migrated> import` → 改为直接 import 子包真实模块（compat 层 `import *` 在批量 collect 时遇部分初始化会失败——monkeypatch 与循环导入两类坑）。
  - **验证**：318 passed / 0 failed（judge/planner/execution/capability_registry/react_engine/autonomy/capability_router/runtime_guard 等 17 个测试文件）。`test_mcp_integration::test_mcp_server_manager_add_remove_server` 1 failed 为**环境噪音**（safe-delete 回收站不可用，非迁移）。
- **批次 D（禁区）**：`multi_agent_orchestrator`/`capability_router`/`task_manager`/`autonomy` 暂留。

## 5. 验收标准
- 每批迁移后：`pytest` 全量 0 回归（重点 `tests/` 中涉及 assistant 的用例）。
- 外部 `from assistant.X import Y` 仍可工作（re-export 保障）。
- 迁移后顶层文件数下降，子包 `__init__.py` 职责清晰。
- **compat 层限制（三坑）**：
  1. `import *` 不导入 `_` 开头私有符号 → compat 文件显式补。
  2. 被测试 monkeypatch 模块级属性的模块 → 测试须直接 import 子包真实模块。
  3. 批量 collect 时 `import *` 在循环导入链中会遇部分初始化失败 → 测试一律直接 import 子包真实模块（生产代码走 compat 无碍）。
- **迁移前置检查**：源模块所有相对导入目标须确认在迁移后仍存在（同子包）或改绝对导入；牵连模块（`from .<已迁移>`）须同步改绝对导入。
- **compat 层限制**：被 monkeypatch 模块级属性的测试必须直接 import 子包真实模块，不能走 compat 层。
