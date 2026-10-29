# 思忆集 · 执行手册（kernel-execution-guide）

> **定位**：给一个跑在 **hy3（腾讯混元3 免费模型）上的执行 agent** 看的自动驾驶 SOP。
> **核心假设**：执行者没有本项目的历史记忆；每一步都必须自包含（路径/当前代码/改成什么/验证命令/预期输出）。按序执行，**禁止自由发挥**。
> **配套**：契约 `kernel-contract.md` v1.2（Zone §1.1 / 三级路由 §2.5 / ADR §9）；里程碑顺序见 `kernel-migration-plan.md` §0。
> **注意**：hy3 在这里是**执行 agent 的模型**，不是要接入思忆集项目。项目内核默认仍是 DSH 内置的 `deepseek-v4-flash`（见附录 A）。

---

## 1. 执行协议（先读。违反 = 返工）

1. **照做**：每节命令与代码原样执行/粘贴，不要"改进"、不要跳步、不要提前做后续节的活；
2. **小步提交**：每个文件改完 → 跑该节验证 → 全绿 → `git add <file> && git commit -m "[kernel-migration] <节号> <动作>"`；
3. **失败即停**：验证命令非预期输出 → 停止，把"命令 + 实际输出"原样记入 journal 后报告，**不猜原因不硬改**；
4. **禁碰**（改了即违规）：`repositories/` `data/` `sync/` `workflow/`（除本手册指定文件）、`desk-notes/`、`automation_hub/`、`思忆集手机端app/`、`ai/`、`memory/`（除 5.2 指定）；
5. **Zone 纪律**：`services/` 新代码禁止 `import assistant/`、禁止 `import services.agent_gateway.adapters`；只允许 `services.agent_gateway.protocol`；
6. **全量回归**：不要在任何沙箱/受限环境跑全量 pytest（会假失败）；只在普通 PowerShell：`$env:PYTEST_ADDOPTS="--basetemp=$env:TEMP\siyi-run"; python -m pytest -q`（3450 基线）；
7. **每完成一节**：在 `docs/kernel-journal.md` 追加"做了什么 / 验证输出 / 遗留"，格式见 §6。
8. **引用纪律（防幻觉，2026-08-15 实战案例后新增）**：凡声称"手册第 X 行写了 Y"或"代码第 X 行是 Y"，**必须 read 原文后复制粘贴原文片段（含行号）**；贴不出原文的"引用/冲突"一律视为误报，不停止、不报告，继续执行。报告"手册与代码不符"时，必须同时贴出：手册原文片段 + 实际代码片段 + 差异说明，三缺一即无效。

---

## 2. 执行 agent 运行规范（hy3 级模型）

| 事项 | 规范 |
|---|---|
| 会话组织 | 一个会话只做一节（5.1 或 5.2 等）；开始前先跑 §3 检查清单 |
| 读取代码 | 先 `read` 本手册给出的源文件行范围，再动手；看不到的内容用 `grep` 定位，**不要假设** |
| 写代码 | 优先**复制现有实现并参数化**（见 5.1 样板），不重写逻辑 |
| 验证 | 每节末尾的命令必须跑，输出与"预期"逐条比对 |
| 不确定 | 停下，把问题原样写进 journal，报告；宁可慢不可错 |

---

## 3. 开工检查清单（每次会话开头跑，10 分钟）

```powershell
cd D:\h\3.25\思忆集
# 1) 工具链
& .\.venv3.11\Scripts\python.exe --version          # 期望 Python 3.11.15
dsh --version                                        # 期望 0.1.0-rc.6
# 2) 迁移相关基线（38 个用例全过）
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_agent_gateway.py 思忆集app/tests/test_mcp_server.py -q
# 3) git 干净（允许 docs/ 四个文件处于修改态）
git status --short
# 4) 后端能起（可选，10 秒看头部日志）
#    & .\.venv3.11\Scripts\python.exe -m backend.headless_main    # Ctrl+C 退出
```

全部通过才进入 5.x；任何一条不符 → 记 journal 并报告。

---

## 4. V6 三关实测（项目内核验证，与执行模型无关；约 1 小时）

> 目的：验证 DSH 内核能否当 chat 主大脑（闸门）。执行 agent 用 hy3 跑这些命令不受影响。

| # | 命令 | 记录 | 通过标准 |
|---|---|---|---|
| V6a | `dsh --profile siyi "用500字介绍量子计算，分3段输出"` | 首字延迟(s)、输出节奏 | 增量输出（逐段出现），非一次性 |
| V6b | 以"你是小问，温柔的中文个人助手"为前缀 prompt，跑 3 轮对话 | 观感 | 人设风格保持、中文自然 |
| V6c | 触发内核 `siyi_confirm_request`（需 M2a 后；可先用 mock store） | 闭环 | 批准后下次轮询读到 approved |

结论三选一记入 journal：三关全过 → M2b 按计划；V6a 不过 → chat fast 档保持 v4-flash；V6c 不过 → 确认后自动重提交语义。

---

## 5. Wave-1 可执行步骤

### 5.1 S1.1 调度层脱内核：`services/cron_actions.py` 注册表

**目标**：cron 执行分发从 `assistant/capability_router` 迁出到稳定区注册表。

**前置**：§3 清单通过。

#### 步骤 5.1.1 迁移 ProactiveTriggerEngine 模块（Zone 纪律要求 services 不能 import assistant）

```powershell
git mv 思忆集app\assistant\proactive_triggers.py 思忆集app\services\proactive_triggers.py
# 修复引用：搜出所有 import 点
rg -n "proactive_triggers" 思忆集app --glob "*.py"
```

逐处把 `from .proactive_triggers import` / `from assistant.proactive_triggers import` 改为 `from services.proactive_triggers import`。改完：

```powershell
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_proactive_triggers.py 思忆集app/tests/test_router_schedule.py -q   # 若有这些文件
```

> 若测试文件不存在或名字不同：`Get-ChildItem 思忆集app\tests -Filter "*proactive*"` 确认后跑对应文件。全绿后提交。

#### 步骤 5.1.2 新建注册表 `思忆集app/services/cron_actions.py`（原样粘贴）

```python
"""services/cron_actions.py — 稳定区 cron action 注册表（A1 D8 / 契约 §1.1-3）

调度层与执行内核的解耦接缝：cron action 执行入口收敛于此，
不再经 assistant/capability_router。新增 action = register_cron_action(...)。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app_runtime.app_context import AppContext

logger = logging.getLogger("siyi.cron_actions")


@dataclass(frozen=True)
class CronActionSpec:
    action_id: str
    handler: Callable[["AppContext", dict], dict]
    description: str = ""


_REGISTRY: dict[str, CronActionSpec] = {}


def register_cron_action(spec: CronActionSpec) -> None:
    _REGISTRY[spec.action_id] = spec
    logger.info("cron action registered: %s", spec.action_id)


def list_cron_actions() -> list[dict]:
    return [{"action_id": s.action_id, "description": s.description} for s in _REGISTRY.values()]


def execute_cron_action(context: "AppContext", action_id: str, payload: dict) -> dict | None:
    """返回 None = 未注册（调用方决定是否落旧 router，过渡期白名单）。"""
    spec = _REGISTRY.get(action_id)
    if spec is None:
        return None
    try:
        return spec.handler(context, payload or {})
    except Exception as exc:  # noqa: BLE001
        logger.exception("cron action %s failed", action_id)
        return {"success": False, "error": str(exc)}
```

#### 步骤 5.1.3 迁移样板：`proactive.tick`（照抄此模式做其余 8 个）

**源**：`思忆集app/assistant/_router_schedule.py` L142-197（`_proactive_tick` 完整实现，已确认）。
**手法**：把方法体复制到注册表 handler，`self.X` 全部改成 `context.X`。本 handler 用到的 `self.X` 与 `context.X` 映射（已核对均在 AppContext 上）：

| self.X | context.X |
|---|---|
| `settings_repository` | `context.settings_repository` |
| `data_store` | `context.local_data_store` |
| `user_profile_service` | `context.user_profile_service` |
| `inbox_service` | `context.inbox_service` |
| `task_repository` | `context.task_repository` |
| `note_repository` | `context.note_repository` |
| `desktop_doctor_service` | `getattr(context, "desktop_doctor_service", None)` |
| `workbench_service` | `getattr(context, "workbench_service", None)` |
| `weather_service` | `getattr(context, "weather_service", None)` |
| `environment_probe` | `getattr(context, "environment_probe", None)` |

在 `cron_actions.py` 末尾追加（**注意 `KEY_PROACTIVE_ENABLED` 的 import 源：在 `_router_schedule.py` 顶部 `grep -n "KEY_PROACTIVE_ENABLED"` 找到原 import 行，照抄同一来源**）：

```python
def _register_proactive_tick() -> None:
    def _handler(context, payload):
        # ← 粘贴 _router_schedule.py L142-197 的 _proactive_tick 方法体，
        #   self. 全部改为 context.（按上表映射，getattr 项保留 getattr 写法）
        ...
        return {...}
    register_cron_action(CronActionSpec(
        "proactive.tick", _handler, "主动建议触发器 tick（原 assistant._router_schedule）"))
_register_proactive_tick()
```

> 关键校验点：`ProactiveTriggerEngine` 的 import 改为 `from services.proactive_triggers import ProactiveTriggerEngine`（5.1.1 已迁移）。

#### 步骤 5.1.4 其余 8 个 action 同法迁移（定位锚点 = 分发表）

**分发表**：`思忆集app/assistant/capability_router.py` L167-220（handler 在 capability_router 或其 mixin 文件，用 `rg -n "def _info_collect" 思忆集app/assistant` 定位实现文件与行号）。

| action_id | 定位命令（找到后复制方法体，同 5.1.3 手法） |
|---|---|
| `evolution.daily_scan` | `rg -n "def _evolution_daily_scan" 思忆集app/assistant` |
| `tech_watch.collect` | `rg -n "def _tech_watch_collect" 思忆集app/assistant` |
| `tech_watch.judge` | `rg -n "def _tech_watch_judge" 思忆集app/assistant` |
| `suggestion.daily` | `rg -n "def _suggestion_daily" 思忆集app/assistant`（在 _router_schedule.py L199-239） |
| `info.collect` | `rg -n "def _info_collect" 思忆集app/assistant` |
| `system.auto_backup` | `rg -n "def _system_auto_backup" 思忆集app/assistant` |
| `system.memory_health` | `rg -n "def _system_memory_health" 思忆集app/assistant` |
| `task.execute` | `rg -n "def _task_execute" 思忆集app/assistant`（在 _router_schedule.py L299 起） |

规则：每个 action 一次提交；若 handler 依赖 assistant 内部对象（非 AppContext 属性），**不硬迁**——把该 action 留白名单（见 5.1.5），在 journal 记录"依赖 XX，暂留旧路径"。

#### 步骤 5.1.5 接缝改造：`app_runtime/_context_cron.py`

`_execute_cron_action`（当前 L59-74）在 `system.self_maintenance` 分支后、原 `router = ...` 行**之前**插入（**原 router 回退代码保持不动**）：

```python
        from services.cron_actions import execute_cron_action
        result = execute_cron_action(self, action_id, payload)
        if result is not None:
            return result
        # 过渡期白名单：未迁移的 action 仍走旧 router（M5 前清零）
```

> 插入后结构 = self_maintenance 直处理 -> 注册表查询（新）-> 旧 router 回退（原有，保留）。
> **2026-08-15 勘误**：原 DoD②"autonomous_assistant_manager 0 命中"与本节"保留旧回退"自相矛盾（tech_watch.collect / task.execute 两个 action 尚依赖旧路径）。DoD② 已改为过渡期收敛标准，M5 迁完遗留 action 后才要求 0 命中。

`get_cron_service_status`（当前 L54-57）去掉 `autonomous_assistant_manager.router` 中转，改：

```python
    def get_cron_service_status(self):
        service = self.cron_service
        if service is None:
            return {"running": False, "engine": "unavailable"}
        return service.get_status_snapshot()
```

#### 5.1.6 DoD（逐条执行，全绿才算完成）

```powershell
# ① 注册表可加载且非空
& .\.venv3.11\Scripts\python.exe -c "import sys; sys.path.insert(0,'思忆集app'); from services.cron_actions import list_cron_actions; print(len(list_cron_actions()))"
#   期望输出 ≥ 1（迁移了几个就 ≥ 几）
# ② 接缝依赖收敛（过渡期标准，2026-08-15 勘误）
Select-String -Path 思忆集app\app_runtime\_context_cron.py -Pattern "autonomous_assistant_manager"
#   期望 ≤1 处命中（仅 _execute_cron_action 的旧 router 回退；get_cron_service_status 改直连后不再命中）
#   M5 前置条件 = 0 命中（tech_watch.collect / task.execute 迁壳后删回退，见手册遗留清单）
# ③ 相关测试
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_cron_service.py 思忆集app/tests/test_task_queue.py -q
# ④ 后端冒烟（普通 PowerShell，起后端后）：
#   cron.run_now {action_id:"proactive.tick"}  → 返回 {"status":"completed",...} 或 {"success":...,"error":...}（不崩即可）
```

**回滚**：`git revert` 本节提交；注册表 miss 自动落旧 router，无需额外处理。

---

### 5.2 M1.5 壳内 AI 端口：`services/ai_text_service.py`

**目标**：5 个 services + memory/extractor + desktop/web_bridge 的 `chat_completion` 直调收敛到 `AiTextService` 端口（A 类耦合清零）。

**真实耦合清单（已核对，以 grep 为准）**：

| 文件:行 | 现状 |
|---|---|
| `services/inbox_classifier_service.py` L53 | `self._ai_client.chat_completion(messages, temperature=0.1, max_tokens=200)` |
| `services/translate_service.py` L466 | `client.chat_completion(...)`（有道优先、AI 兜底翻译） |
| `services/inbox_capture_service.py` L217 | `self.ai_client.chat_completion(...)`（收件箱智能捕获） |
| `services/feedback_service.py` L257 | `self._ai.chat_completion(...)`（偏好聚合） |
| `services/knowledge_organizer.py` L156 | `self._ai.chat_completion(...)`（知识整理） |
| `memory/extractor/_core.py` L251 | `self._ai_client.chat_completion(...)`（记忆提取） |
| `desktop/web_bridge.py` L245 | `ai_client.chat_completion(...)`（网页桥小问答） |

#### 步骤 5.2.1 新建端口 `思忆集app/services/ai_text_service.py`

```python
"""services/ai_text_service.py — 壳内轻 AI 端口（契约 I3 / 决议 D1,D7）

高频小调用（翻译/分类/摘要/嵌入/简单问答）统一入口，模型链由底层
ai_client 提供（deepseek-v4-flash -> moonshot -> 本地 ollama），
本端口是稳定区与 AI 客户端的唯一接缝。
"""
from __future__ import annotations
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ai.client.base import BaseOpenAIClient

logger = logging.getLogger("siyi.ai_text_service")


class AiTextService(Protocol):
    async def translate(self, text: str, source: str = "auto", target: str = "zh") -> str: ...
    async def classify(self, content: str, title: str = "") -> dict[str, Any]: ...
    async def summarize(self, text: str, max_chars: int = 200) -> str: ...
    def health(self) -> dict[str, Any]: ...


class LegacyLlmTextService:
    """包一层 context.ai_client（现有 JudgeAwareClient/Fallback 链），实现 AiTextService。"""

    def __init__(self, client: "BaseOpenAIClient"):
        self._client = client

    async def classify(self, content: str, title: str = "") -> dict[str, Any]:
        # 逻辑从 inbox_classifier_service.classify 平移（见 5.2.3 样板）
        if self._client is None:
            raise RuntimeError("ai_client not available")
        prompt = (...)  # 与 inbox_classifier_service L40-50 相同
        resp = self._client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=200,
        )
        text = _extract_json(resp)
        result = json.loads(text)
        result.setdefault("kind", "uncertain"); result.setdefault("confidence", 0.0)
        result.setdefault("reason", ""); result.setdefault("suggested_action", "none")
        return result

    # translate / summarize：按 5.2.4 配方从对应 service 平移；health 返回 {"available": self._client is not None}
```

> `_extract_json` 从 `inbox_classifier_service.py` 复制（它是模块级函数）。

#### 步骤 5.2.2 注入：`backend/headless_main.py`

在 1.4d（`context.agent_gateway = ...`）邻位追加：

```python
    from services.ai_text_service import LegacyLlmTextService
    context.ai_text_service = LegacyLlmTextService(context.ai_client)
```

#### 步骤 5.2.3 迁移样板：`services/inbox_classifier_service.py`

把 `classify` 方法（L28-69）改为委托端口：

```python
    def __init__(self, ai_client: Any = None, settings_repository: Any = None,
                 ai_text_service: Any = None):
        self._ai_client = ai_client           # 保留兼容（过渡期）
        self._settings = settings_repository
        self._ai_service = ai_text_service

    async def classify(self, content: str, title: str = "") -> dict[str, Any]:
        if not content.strip():
            return {"kind": "uncertain", "confidence": 0.0, "reason": "内容为空", "suggested_action": "none"}
        if self._ai_service is not None:
            try:
                return await self._ai_service.classify(content, title)
            except Exception as exc:
                logger.warning("AI 分类失败，回退到关键词: %s", exc)
                return self._keyword_fallback(content, title)
        if self._ai_client is None:
            return self._keyword_fallback(content, title)
        # ← 保留原 chat_completion 实现作为无端口时的兼容路径（M5 删）
```

调用方注入点：`inbox_classifier` 服务在 `_context_builder.py` / `headless_main.py` 构造处加 `ai_text_service=context.ai_text_service`（`rg -n "InboxClassifierService(" 思忆集app` 找构造点）。

#### 步骤 5.2.4 其余 6 处同法（配方）

对每个文件：构造器加 `ai_text_service` 可选参数 → 业务方法改委托端口方法（把该文件的 prompt+调用平移进 `ai_text_service.py` 对应方法）→ 保留原 `chat_completion` 为兼容路径 → 构造点注入。**一次一处，每处一个提交**；`memory/extractor/_core.py` 与 `desktop/web_bridge.py` 同法（注意它们不在 services/，迁移目标是"不再直调"，入口仍可经端口）。

#### 5.2.5 DoD

```powershell
# ① A 类耦合清零（services + desktop + memory 直调归零；ai/ assistant/ tests 不计）
Select-String -Path 思忆集app\services\*.py,思忆集app\desktop\web_bridge.py,思忆集app\memory\extractor\_core.py -Pattern "chat_completion"
#   期望：只剩 ai_text_service.py 内部 1-2 处
# ② 分类服务测试
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_inbox_classifier_service.py 思忆集app/tests/test_translate.py 思忆集app/tests/test_feedback_service.py -q
# ③ 功能冒烟（后端运行）：translate.text / notes.auto_tag / inbox.classify 三 RPC 全绿
# ④ 关停内核后上述三 RPC 仍绿（I3 验收：壳内 AI 不依赖内核进程）
```

**回滚**：删除 settings 键/按文件粒度 revert。

---

### 5.3 M-cron：`kernel.submit` action（依赖 5.1 完成）

在 `services/cron_actions.py` 末尾追加：

```python
def _register_kernel_submit() -> None:
    def _submit(context, payload):
        prompt = str(payload.get("prompt") or "")
        if not prompt:
            return {"success": False, "error": "missing prompt"}
        gateway = getattr(context, "agent_gateway", None)
        if gateway is None:
            return {"success": False, "error": "agent_gateway not injected"}
        run = gateway.submit(prompt, timeout_s=int(payload.get("timeout_s", 240)))
        return {"success": run.ok, "run_id": run.run_id, "status": run.status,
                "error": run.error, "result": run.result}
    register_cron_action(CronActionSpec(
        "kernel.submit", _submit, "提交一次性任务给执行内核（DSH），结果经 MCP 回写"))
_register_kernel_submit()
```

**DoD**：后端运行时 `cron.run_now {action_id:"kernel.submit", payload:{prompt:"列出最近3条资讯并总结"}}` -> 返回 `success:true`；`trace.list` 出现 `kernel:*` 条目。

---

### 5.4 M-tools：工具能力层身体化（契约 §1.1-⑤；前置：§5.1-5.3 完成）

**目标**：任何内核（DSH/claude/...）经统一 MCP 通道 `siyi_tool_run` 调用工具注册表里的外部工具；高危工具必须桌面侧审批（**内核不自我批准**）。

**已核实的架构事实（设计依据，不要重新质疑）**：
- `SandboxManager` 是主进程**内存态单例**（`services/sandbox_manager.py` L37 class + L310 `get_sandbox_manager()`，无 SQLite 持久化）；MCP server 是**独立 stdio 进程**（`mcp_server/server.py`）-> 跨进程审批 v1 不可行；
- **v1 裁决**：`siyi_tool_run` 直接执行 risk=low/medium；risk=high 返回结构化拒绝 `{"success": false, "requires_shell_approval": true}`，由内核告知用户、用户走桌面端路径（前端/cron）触发审批链。这符合契约"审批权在壳"，M2a ConfirmationStore 若落 SQLite 持久化后可升级为跨进程审批（登记 OQ，不在本节做）；
- 现有执行样板 = `assistant/capability/_dynamic_tools.py` L65-113（`DynamicToolDomain.dispatch`：ActionGuard -> `_maybe_block_for_sandbox` -> `GenericToolBridge(registry).execute(action_id, payload)` -> dict）。

#### 步骤 5.4.1 tooling 模块迁入稳定区

```powershell
New-Item -ItemType Directory 思忆集app\services\tooling -Force
New-Item -ItemType File 思忆集app\services\tooling\__init__.py -Force
git mv 思忆集app\assistant\tool_spec.py 思忆集app\services\tooling\tool_spec.py
git mv 思忆集app\assistant\generic_tool_bridge.py 思忆集app\services\tooling\generic_tool_bridge.py
```

1. 检查两模块自身 import（必须零 assistant 依赖）：
```powershell
Select-String -Path 思忆集app\services\tooling\*.py -Pattern "^from \.|^from assistant|^import assistant"
# 期望 0 命中；若有相对导入（from .xxx）改为绝对路径
```
2. 全仓更新引用点：
```powershell
Get-ChildItem 思忆集app -Recurse -Filter *.py | Select-String -Pattern "assistant\.tool_spec|assistant\.generic_tool_bridge|from \.tool_spec|from \.generic_tool_bridge|from \.generic_tool_bridge"
# 逐处改为 from services.tooling.tool_spec import ... / from services.tooling.generic_tool_bridge import ...
# 已知引用点（以实际 grep 为准）：assistant/capability/_dynamic_tools.py、backend/handlers/_tools.py、services/react_tool_catalog.py
```
3. 验证：
```powershell
Get-ChildItem 思忆集app\tests -Filter "*.py" | Where-Object Name -Match "tool_spec|tool_bridge|dynamic_tool|generic_tool"
& .\.venv3.11\Scripts\python.exe -m pytest <上一步找到的文件> -q
# 再跑基线：test_agent_gateway + test_mcp_server（43 用例不破坏）
```
4. 提交：`git commit -m "[kernel-migration] 5.4.1 tooling modules -> services/tooling"`

#### 步骤 5.4.2 新建 `思忆集app\mcp_server\tool_runner.py`（模板原样粘贴，字段名以实际为准）

> **先做**：read `services/tooling/tool_spec.py` 的 ToolSpec（L201 起）/ ToolCommand（L94 起）字段名（tool_id/display_name/enabled/commands/risk/confirmation 等），与下面模板不一致处**按实际字段名修正**；并 read L329-346 确认 `ToolSpecRegistry.__init__` 默认 config_path 是否依赖 cwd——MCP 进程 cwd 不固定，若依赖 cwd 必须显式传绝对路径（模板已按 data_dir 处理）。

```python
"""mcp_server/tool_runner.py — 内核调用外部工具的统一通道（契约 §1.1-⑤ / M-tools）

v1 审批边界：SandboxManager 是主进程内存态单例，MCP 为独立进程，
故 risk=high 工具在此结构化拒绝（内核不自我批准）；用户走桌面端路径触发审批。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from services.tooling.tool_spec import ToolSpecRegistry
from services.tooling.generic_tool_bridge import GenericToolBridge

logger = logging.getLogger("siyi.mcp_server.tool_runner")


class ToolRunner:
    def __init__(self, data_dir: str | None = None, working_dir: str | None = None):
        config_path = str(Path(data_dir) / "tool_specs.json") if data_dir else None
        self._registry = ToolSpecRegistry(config_path=config_path)
        # MCP 进程无 workspace 概念：working_dir 缺省用用户主目录（命令在此目录下执行）
        self._bridge = GenericToolBridge(self._registry, working_dir=working_dir or str(Path.home()))

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"tool_id": s.tool_id, "display_name": s.display_name,
             "enabled": s.enabled, "available": s.is_available()}
            for s in self._registry.list_specs()
        ]

    def run(self, tool_id: str, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not tool_id:
            return {"success": False, "error": "missing tool_id"}
        spec = self._registry.get(tool_id)
        if spec is None:
            return {"success": False, "error": f"unknown tool: {tool_id}"}
        if not spec.enabled:
            return {"success": False, "error": f"tool disabled: {tool_id}"}
        action_id = spec.get_action_id(command)
        found = self._registry.find_by_action_id(action_id)
        if found is None:
            return {"success": False, "error": f"unknown command: {tool_id}.{command}"}
        _, cmd = found
        if cmd.risk == "high":
            return {
                "success": False,
                "requires_shell_approval": True,
                "error": "高风险工具需在桌面端审批后执行，内核不可自我批准（契约 I2）",
                "tool_id": tool_id, "command": command,
            }
        try:
            result = asyncio.run(self._bridge.execute(action_id, params or {}))
        except Exception as exc:  # noqa: BLE001
            logger.exception("siyi_tool_run failed: %s", action_id)
            return {"success": False, "error": str(exc), "tool_id": tool_id}
        return {
            "success": result.success,
            "tool_id": tool_id,
            "action_id": result.action_id,
            "output": result.output,
            "error": result.error,
            "duration_seconds": result.duration_seconds,
            "extracted": result.extracted or {},
            "missing_secrets": result.missing_secrets or [],
        }
```

#### 步骤 5.4.3 挂载：`mcp_server/tools.py` + `mcp_server/server.py`

1. `tools.py`：签名改 `def build_tool_specs(store: SiyiStore, runner: Any = None) -> list[ToolSpec]:`，列表末尾追加（runner 为 None 时跳过，保持现有测试兼容）：
```python
    specs = [ ...现有 10 个... ]
    if runner is not None:
        specs.append(ToolSpec(
            name="siyi_tool_run",
            description="Run a registered external CLI tool from the tool registry. "
                        "risk=high tools are refused here (requires desktop-side approval).",
            input_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "description": "registered tool id"},
                    "command": {"type": "string", "description": "command name of the tool"},
                    "params": {"type": "object", "description": "command params"},
                },
                "required": ["tool_id", "command"],
            },
            handler=lambda args: _json(runner.run(
                args.get("tool_id", ""), args.get("command", ""), args.get("params") or {})),
        ))
        specs.append(ToolSpec(
            name="siyi_tool_list",
            description="List registered external tools (tool registry).",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args: _json(runner.list_tools()),
        ))
    return specs
```
（现有代码是直接 `return [...]`——改为先赋值 `specs = [...]` 再按上述追加；照抄时保持 10 个现有工具一字不动。）

2. `server.py` main() 中：
```python
    from mcp_server.tool_runner import ToolRunner
    store = SiyiStore(data_dir=args.data_dir)
    runner = ToolRunner(data_dir=args.data_dir)
    server = McpStdioServer(name="siyi", version="0.1.0", tools=build_tool_specs(store, runner))
```

3. 测试（照 `tests/test_mcp_server.py` 现有风格追加 4 用例）：
   - `tools/list` 含 `siyi_tool_run` 与 `siyi_tool_list`；
   - 未知 tool_id -> `{"success": false, "error": "unknown tool: ..."}`；
   - 临时注册 risk=high 工具（写入临时 tool_specs.json）-> 调用 -> `requires_shell_approval: true`；
   - 临时注册 risk=low 的 echo 类命令 -> 调用 -> `success: true` 且有 output。

提交：`[kernel-migration] 5.4.2 siyi_tool_run MCP tool`

#### 步骤 5.4.4 工具执行审计

1. 定位现有审计 API 与用法：
```powershell
Select-String -Path 思忆集app\core\audit_rollback.py -Pattern "def |class "
Get-ChildItem 思忆集app -Recurse -Filter *.py | Select-String -Pattern "audit_rollback" | Select-Object -First 10
```
2. 在 `ToolRunner.run` 的两个返回点（成功/失败）各记一条（source="mcp_tool"，字段 tool_id/command/success/duration_seconds）；
3. **若 audit_rollback 绑定主进程对象、MCP 进程不可用**：退化为写 SQLite 审计表（WAL 多进程安全，参照 `data/sqlite_store.py` 用法）；两者都不可行则登记 journal 延后到 M2a——如实记录，不硬造。

提交：`[kernel-migration] 5.4.4 tool execution audit`

#### 步骤 5.4.5 注册 codebuddy（条件步骤：用户装好 CodeBuddy Code CLI 后才做）

1. 探测无头能力：`codebuddy --version; codebuddy --help`，找一次性任务模式（`-p`/`exec`/`print` 类）与输出格式参数，结果记 journal；
2. 后端运行时经 RPC 注册（或直编 `思忆集app\data\tool_specs.json` 后 `tool.refresh`）：
   `tool.register {tool_id:"codebuddy", display_name:"CodeBuddy Code", cli_path:"codebuddy", enabled:true, health_check_command:"codebuddy --version", commands:[{name:"run", template:"<按探测结果填无头命令模板>", risk:"high", confirmation:"every_time", timeout:1800}]}`
3. 验证：`tool.health_check {tool_id:"codebuddy"}` -> available；`siyi_tool_list` 出现 codebuddy；`siyi_tool_run {tool_id:"codebuddy", command:"run", ...}` -> `requires_shell_approval: true`（v1 预期行为）。

#### 5.4.6 DoD（逐条执行）

```powershell
# ① 旧路径零残留
Get-ChildItem 思忆集app -Recurse -Filter *.py | Select-String -Pattern "assistant\.tool_spec|assistant\.generic_tool_bridge"
#   期望 0 命中
# ② MCP 工具面
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_mcp_server.py -q
#   全绿（含新增 4 用例）
# ③ 基线不破坏
& .\.venv3.11\Scripts\python.exe -m pytest 思忆集app/tests/test_agent_gateway.py 思忆集app/tests/test_sandbox_manager_executor.py -q
# ④ 端到端（起后端 + dsh）：dsh --profile siyi "调用 siyi_tool_list" -> 返回工具列表 JSON
```

**回滚**：按提交粒度 revert；tool_runner.py 删除 + tools.py/server.py 还原即可，注册表数据不受影响。

---

## 6. Journal 记录模板（每节完成后追加到 `docs/kernel-journal.md`）

```markdown
### <日期> <节号> <标题>
- 做了什么：<文件级清单>
- 验证结果：<命令 + 实际输出摘要>
- 遗留/风险：<未迁移的 action / 兼容路径等>
- 回滚预案：<本节提交的 revert 命令>
```

---

## 7. 附录 A：hy3 作为项目模型（与执行无关，后续如需再展开）

执行 agent 用 hy3 跑本手册；**项目内核**默认 DSH 内置 `deepseek-v4-flash`。若将来想用 hy3 跑项目路径（chat fast 档等）：
- 壳内轻 AI：`provider_configs` + `llm_provider_routing`（P2.6）直连 `https://api.hunyuan.cloud.tencent.com/v1`（低风险）；
- DSH 内核：`$DSH_HOME/settings.yaml` 的 `llm-deepseek:` 节热覆盖（baseURL/apiKeyEnv/thinking: disabled）——**需实测混元端点是否接受 DSH 序列化的 thinking 字段**（开放问题 OQ5）；
- 可行性：翻译/分类/摘要 ✅；chat fast 档 ✅ 待验证；deep 档 ⚠ 保留 DeepSeek；claude_code ❌ 仅演练。

---

## 8. 本手册状态

| 阶段 | 状态 |
|---|---|
| §3 开工检查清单 | ✅（基线 152 用例绿，2026-08-16 实测） |
| §4 V6 三关实测 | ✅ |
| §5.1 S1.1 cron_actions（含 proactive_triggers 迁移） | ✅（9 action 已注册，test_cron_actions 4 passed；`tech_watch.collect`/`task.execute` 仍走旧 router 白名单，M5 清零） |
| §5.2 M1.5 AiTextService（7 处耦合） | ✅（`services/ai_text_service.py` 已存在并注入；消费者迁移顺序进行中，余兼容分支 M5 清） |
| §5.3 M-cron kernel.submit | ✅（已注册，test_cron_actions 4 passed） |
| §5.4 M-tools 工具能力层 | ✅ 5.4.1-4（tooling 迁入 `services/tooling/` + `siyi_tool_run`/`siyi_tool_list` 已挂载 + `_McpAudit` 在 `tool_runner.py` 三返回点落 `source=mcp_tool` 退化 SQLite 审计表，test_mcp_server 26 passed 含两条审计断言）；⬜ 5.4.5 codebuddy 注册（CLI 未装，条件步跳过） |
