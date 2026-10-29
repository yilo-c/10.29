from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO

KEY_ACTION_WHITELIST = "assistant_action_whitelist"
# 显式禁用列表（2026-09-11 新增）。与 whitelist 配合构成三态中的 deny：
# allowed = (DEFAULT_ALLOWED_ACTIONS ∪ whitelist) − denylist。
# 存量 whitelist 的语义已从「完整名单」改为「增量追加」（见 _resolve_allowed_actions）。
KEY_ACTION_DENYLIST = "assistant_action_denylist"
KEY_ACTION_RISK_WARN_ONLY = "assistant_action_risk_warn_only"
KEY_ACTION_GUARD_PROFILE = "assistant_action_guard_profile"
KEY_ACTION_RISK_ENFORCEMENT = "assistant_action_risk_enforcement"
KEY_COMMAND_WHITELIST = "assistant_command_whitelist"
# B2（2026-09-11）：审批弹窗「始终允许」的持久化名单（action_id 列表）。
# 用户在某次审批选择「始终允许」后，该能力不再触发审批——优先级高于
# approval_policy（用户显式授权 > 全局策略）。
KEY_ACTION_ALWAYS_ALLOW = "assistant_confirm_always_allow"

SHELL_UNSAFE_TOKENS: tuple[str, ...] = ("&&", "||", "|", "&", ">", "<", ";", "`", "$(", "\n", "\r")
SHELL_DANGEROUS_COMMAND_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-rf\s+/",
    r"\bformat\s+[a-zA-Z]:",
    r"\bdel\s+/[sfq]",
    r"\brd\s+/s\s+/q",
    r":\\\s*$",
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=/dev/",
    r"\breg\s+delete\s+hk",
    r"\bnet\s+user\s+.*\s+/delete",
    r"\bshutdown\s+/[srp]",
)


PROFILE_STRICT = "strict"
PROFILE_BALANCED = "balanced"
PROFILE_DEV = "dev"
DEFAULT_PROFILE = PROFILE_BALANCED

ENFORCEMENT_AUTO = "auto"
ENFORCEMENT_WARN = "warn"
ENFORCEMENT_BLOCK = "block"

# ── B1（2026-09-11）：权限模型正交两维 ──────────────────────────────────────
# 成熟 agent 把「能做什么」与「要不要问」拆成两个独立维度（如 Codex CLI 的
# --sandbox / --ask-for-approval）。思忆集此前把二者混在白名单 + 硬编码
# CONFIRM_TOOLS 里，典型后果是无人值守场景无法显式声明「不询问」。
#
# 默认值刻意选择「等同现状」，保证引入本机制不产生任何行为变化。
KEY_ASSISTANT_SANDBOX_LEVEL = "assistant_sandbox_level"
KEY_ASSISTANT_APPROVAL_POLICY = "assistant_approval_policy"

SANDBOX_READ_ONLY = "read-only"
SANDBOX_WORKSPACE_WRITE = "workspace-write"
SANDBOX_FULL_ACCESS = "full-access"
DEFAULT_SANDBOX_LEVEL = SANDBOX_FULL_ACCESS

APPROVAL_NEVER = "never"
APPROVAL_ON_RISK = "on-risk"
APPROVAL_ALWAYS = "always"
DEFAULT_APPROVAL_POLICY = APPROVAL_ON_RISK

# read-only 档位允许的能力（保守白名单：仅明确的只读能力）
_READ_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        "news",
        "news.lookup",
        "news.briefing",
        "memory.recall",
        "knowledge.query",
        "translate",
        "translate.text",
        "file.read",
        "file.find",
        "file.grep",
        "screen.read",
        "desktop.screenshot",
        "clipboard.read",
        "system.info",
        "system.capabilities",
        "system.scan",
        "process.list",
        "usage.snapshot",
        "upgrade.check",
        "browser.open",
        "browser.navigate",
        "browser.extract",
        "browser.screenshot",
        "browser.scroll",
        "browser.get_tabs",
        "browser.wait",
        "window.find",
        "cron.list",
        "mcp.tools.list",
        "subagent.list",
        "subagent.get",
        "vocab.quiz_in_chat",
        "vocab.review_status",
        # 2026-09-15: 本地资源的只读查询。原先只登记了 knowledge.query /
        # vocab.*，导致 read-only 档位下 notes.list / tasks.list / inbox.list
        # 这批纯读工具被一并掐掉，档位形同虚设（机制建了但没启用）。
        "notes.list",
        "notes.search",
        "notes.get",
        "tasks.list",
        "tasks.search",
        "tasks.get",
        "inbox.list",
        "info.brief",
    }
)

# 危险能力：workspace-write 档位下排除（需 full-access 才允许）
_DANGEROUS_ACTIONS: frozenset[str] = frozenset(
    {
        "shell.run",
        "app.launch",
        "process.kill",
        "web.download",
        "file.delete",
        "file.batch_delete",
        "notes.batch_delete",
        "mouse.click",
        "mouse.click_text",
        "keyboard.type",
        "keyboard.press",
        "window.activate",
        "browser.automate",
        "browser.close",
        "code.execute",
    }
)

# 高风险能力（approval_policy=on-risk 时需要人工确认）
_HIGH_RISK_ACTIONS: frozenset[str] = frozenset(
    {
        "shell.run",
        "process.kill",
        "browser.click",
        "browser.type",
        "browser.automate",
        "app.launch",
        "web.download",
        "cron.add",
        "cron.remove",
        "code.execute",
    }
)

# 写类能力（approval_policy=always 时同样需要确认）
_WRITE_ACTIONS: frozenset[str] = frozenset(
    {
        "file.write",
        "file.copy",
        "file.edit",
        "file.delete",
        "clipboard.write",
        "capture",
        "organize_current",
        "self_optimize",
        "settings",
        "notes.create",
        "notes.update",
        "notes.tag",
        "notes.delete",
        "notes.batch_delete",
        "tasks.create",
        "tasks.update",
        "tasks.toggle",
        "tasks.delete",
        "inbox.add",
        "inbox.update",
        "transfer.send",
    }
)

_VALID_SANDBOX_LEVELS: frozenset[str] = frozenset({SANDBOX_READ_ONLY, SANDBOX_WORKSPACE_WRITE, SANDBOX_FULL_ACCESS})
_VALID_APPROVAL_POLICIES: frozenset[str] = frozenset({APPROVAL_NEVER, APPROVAL_ON_RISK, APPROVAL_ALWAYS})


def _apply_sandbox_level(allowed: set[str], level: str) -> set[str]:
    """按 sandbox 档位收窄能力集合（B1「能做什么」维度）。

    - ``full-access``（默认）：不收窄，保持既有行为
    - ``workspace-write``：在 allowed 基础上排除危险能力
    - ``read-only``：只保留 ``_READ_ONLY_ACTIONS`` 中明确列出的能力
    """
    if level == SANDBOX_FULL_ACCESS:
        return allowed
    if level == SANDBOX_WORKSPACE_WRITE:
        return {action for action in allowed if action not in _DANGEROUS_ACTIONS}
    return {action for action in allowed if action in _READ_ONLY_ACTIONS}


logger = logging.getLogger(__name__)

# ── 本地资源工具（2026-09-15 修复）───────────────────────────────────────────
# 运行时真正派发的是「命名空间 id」：notes.list / tasks.search / inbox.list …
# 来源：services/react_tool_catalog.REACT_TOOL_CATALOG（butler 向模型声明的工具）
# 与 assistant/capability/_local_resource_tools.py（执行侧接线）。
#
# 历史缺陷：DEFAULT_ALLOWED_ACTIONS 里只有裸域名（notes / tasks / inbox），
# 而准入判定（validate）是精确成员测试 → 这批工具全量命中 not_in_whitelist，
# butler 的「帮你管笔记/待办」运行时 0 可用，每次对话空烧推理步数。
#
# 修复策略是「显式登记真实 id」而非通配放行：只有这里列出的已知工具被放行，
# 未登记的能力（含未来新增的同域工具）仍按 not_in_whitelist 拦截。
LOCAL_RESOURCE_ACTIONS: tuple[str, ...] = (
    "vocab.quiz_in_chat",
    "vocab.review_status",
    "notes.list",
    "notes.search",
    "notes.create",
    "notes.get",
    "notes.update",
    "notes.tag",
    "notes.delete",
    "notes.batch_delete",
    "tasks.list",
    "tasks.search",
    "tasks.create",
    "tasks.get",
    "tasks.update",
    "tasks.delete",
    "tasks.toggle",
    "inbox.add",
    "inbox.list",
    "info.brief",
    "knowledge.query",
    "transfer.send",
)

DEFAULT_ALLOWED_ACTIONS: tuple[str, ...] = (
    "news",
    "news.lookup",
    "tasks",
    "notes",
    "notes.sync",
    "notes.organize",
    "translate",
    "inbox",
    "capture",
    "organize_current",
    "journal.daily_draft",
    "usage.snapshot",
    "upgrade.check",
    "floating",
    "self_optimize",
    "alarms",
    "settings",
    "wordbook",
    "url_learning",
    "browser.open",
    "file.read",
    "file.write",
    "file.find",
    "file.copy",
    "file.grep",
    "file.edit",
    "screen.read",
    "desktop.screenshot",
    "clipboard.read",
    "clipboard.write",
    "app.launch",
    "shell.run",
    "web.download",
    "process.list",
    "process.kill",
    "system.info",
    # 本地资源工具的真实派发 id（butler 的 22 个本地资源工具）
    *LOCAL_RESOURCE_ACTIONS,
)

DEFAULT_ALLOWED_COMMANDS: tuple[str, ...] = (
    "detect_nana_pet_process",
    "launch_nana_pet",
    "detect_neuro_pet_process",
    "launch_neuro_pet",
    "docker_version_check",
    "start_local_agent_backend",
    "start_docker_stack",
    "open_external_url",
)


def namespace_of(action_id: str) -> str:
    """取 action_id 的命名空间（首段）。``notes.list`` → ``notes``。"""
    return action_id.split(".", 1)[0] if action_id else ""


def match_action_id(
    action_id: str,
    patterns: set[str] | frozenset[str] | tuple[str, ...],
    *,
    allow_namespace: bool = False,
) -> bool:
    """action_id 与名单条目的匹配（2026-09-15 修复）。

    名单（默认白名单 / 用户追加 / 用户禁用）里同时存在两种形态：

    - 完整命名空间 id：``notes.list``、``tasks.search``（运行时真正派发的形态）
    - 裸域名：``notes``、``tasks``、``inbox``（语义 = 整个域放行）

    历史实现只做 ``action_id in allowed`` 精确成员测试，导致所有命名空间
    id 一律 not_in_whitelist。这里改为按来源区分的双形态匹配：

    1. 全名精确匹配（``notes.list`` in 名单）——始终生效；
    2. 命名空间匹配（``notes`` in 名单 → 放行 ``notes.list``）——仅当
       ``allow_namespace=True`` 时生效。

    ``allow_namespace`` 按名单来源取值（见 ``_resolve_allowed_action_groups``）：

    - 代码默认（``DEFAULT_ALLOWED_ACTIONS``）：**False**。默认白名单里的裸域是
      历史遗留写法，若按整域解释，等于让代码默认值隐式授权同域未知能力
      （``notes.nuke_everything`` 会被放行）。真实可用的工具已显式登记在
      ``LOCAL_RESOURCE_ACTIONS``，无需靠裸域兜底。
    - 用户显式追加（``assistant_action_whitelist``）：**True**。设置页既有语义
      就是「追加一个域 = 整个域放行」，不能破坏。
    - 用户显式禁用（``assistant_action_denylist``）：**True**。禁用必须能覆盖
      整域，否则「禁用裸域 notes」拦不住 notes.list。
    """

    if not action_id:
        return False
    if action_id in patterns:
        return True
    if not allow_namespace:
        return False
    head = namespace_of(action_id)
    return bool(head) and head in patterns


@dataclass
class ActionRiskRule:
    rule_id: str
    message: str
    action_ids: set[str] = field(default_factory=set)
    command_ids: set[str] = field(default_factory=set)
    level: str = "medium"


@dataclass
class ActionCheckResult:
    allowed: bool
    action_id: str
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    risk_level: str = ""


RISK_RULES: tuple[ActionRiskRule, ...] = (
    ActionRiskRule(
        rule_id="write_local_content",
        action_ids={"capture", "organize_current", "self_optimize"},
        level="medium",
        message="此操作会写入本地数据（收件箱、个人空间或提示），请确认输入内容不含敏感信息。",
    ),
    ActionRiskRule(
        rule_id="external_data_fetch",
        action_ids={"news", "translate", "url_learning"},
        level="medium",
        message="此操作可能触发联网请求，请确认当前网络与数据来源可信。",
    ),
    ActionRiskRule(
        rule_id="runtime_or_setting_change",
        action_ids={"settings"},
        level="high",
        message="此操作涉及配置修改入口，请确认不会更改 API 地址、令牌或自动同步设置。",
    ),
    ActionRiskRule(
        rule_id="spawn_external_process",
        command_ids={"launch_nana_pet", "launch_neuro_pet"},
        level="medium",
        message="此命令会启动外部进程，请确认来源可信且本机运行环境安全。",
    ),
    ActionRiskRule(
        rule_id="inspect_system_process",
        command_ids={"detect_nana_pet_process", "detect_neuro_pet_process"},
        level="medium",
        message="此命令会读取系统进程信息，建议在可信环境下执行。",
    ),
    ActionRiskRule(
        rule_id="start_runtime_backend",
        command_ids={"start_local_agent_backend", "start_docker_stack"},
        level="high",
        message="此命令会拉起后台服务进程，请确认当前运行目录、端口与配置可信。",
    ),
    ActionRiskRule(
        rule_id="docker_daemon_probe",
        command_ids={"docker_version_check"},
        level="medium",
        message="此命令会检测 Docker 运行状态，建议在受控环境中执行。",
    ),
    ActionRiskRule(
        rule_id="open_external_url",
        command_ids={"open_external_url"},
        level="medium",
        message="此命令会打开外部链接，请确认链接来源可信并避免访问敏感页面。",
    ),
    ActionRiskRule(
        rule_id="filesystem_mutation",
        action_ids={"file.write"},
        level="high",
        message="此操作会写入本地文件系统，请确认目标路径和内容安全。",
    ),
    ActionRiskRule(
        rule_id="shell_execution",
        action_ids={"shell.run"},
        level="high",
        message="此操作会执行 shell 命令，存在命令注入风险。仅允许预定义安全命令。",
    ),
    ActionRiskRule(
        rule_id="process_termination",
        action_ids={"process.kill"},
        level="high",
        message="此操作会终止系统进程，此操作可能导致数据丢失。",
    ),
    ActionRiskRule(
        rule_id="app_launch",
        action_ids={"app.launch"},
        level="medium",
        message="此操作会启动外部应用程序，请确认目标应用路径可信。",
    ),
    ActionRiskRule(
        rule_id="web_download",
        action_ids={"web.download"},
        level="medium",
        message="此操作会从网络下载内容到本地，请确认来源 URL 可信。",
    ),
    # P2-3: 补充破坏性数据操作的风险规则
    ActionRiskRule(
        rule_id="data_deletion",
        action_ids={"notes.delete", "tasks.delete", "inbox.delete"},
        level="medium",
        message="此操作会删除数据（软删除，可恢复），请确认删除范围正确。",
    ),
)


# --- LLM 来源的动作必须额外校验 ---
# P0-4: warn_only=False 时这些 action 被 LLM 调用默认拦截，需用户确认
#
# 语义统一（裁决 3 / authorization-model-adjudication-2026-09-17）：
#   elevated = 「LLM 源需要人类批准，且**批准有出路**」。
#   过去 warn_only=False 时这些动作在 broker 层被 ENFORCEMENT_BLOCK 终态拦死
#   （error dict 回 LLM，拦截文案谎称"需手动确认"但系统里无确认路径 → agent
#   永远做不了删除/写入类操作）。自裁决 3 起，管家（butler）工具执行链在把动作
#   下沉给 broker **之前**，就把 elevated 动作升格为队列2 confirmation（复用
#   shell.run 既有 CONFIRM → action.confirm_required → confirmation.resolve →
#   chat.resume 闭环，见 ActionGuard.is_llm_elevated + butler._requires_confirmation）。
#   validate() 内的 elevated→block 分支**保留**，作为「不经管家升格的直调 broker
#   路径」的纵深防御最后一道闸（这些路径由各自的入队机制给出出路，见下）。
_LLM_ELEVATED_ACTION_RISK_TAGS: tuple[str, ...] = ("shell", "exec", "run", "launch", "kill", "write", "delete")

_LLM_ELEVATED_ACTIONS: frozenset[str] = frozenset(
    {
        "file.write",
        "shell.run",
        "process.kill",
        "app.launch",
        "web.download",
        "settings",
        "clipboard.write",
        # P0/C2: 补充破坏性数据操作
        "notes.delete",
        "tasks.delete",
        "inbox.delete",
        # 2026-09-15: 批量软删同属破坏性操作，LLM 调用必须触发人工确认
        "notes.batch_delete",
    }
)


class ActionGuard:
    """Action authorization and risk classification."""

    def __init__(self, settings_repository=None, observability_service=None):
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self._policies: list[tuple[int, Any]] = []

    # ── 审批策略链（waterfall 中间件）─────────────────────────────

    def register_policy(self, priority: int, policy_fn: Any) -> None:
        """注册审批策略（waterfall 中间件）。

        策略优先级：priority 越大越先执行。策略签名::

            policy(guard, next_fn, *, action_id, payload, source, approved, kind)

        - 继续放行：调用 ``result = next_fn()`` 并返回其返回值（链尾返回 None）；
        - 短路拦截/放行：不调用 next_fn，直接返回 ActionCheckResult。
        kind ∈ {"action", "command", "shell"}，便于策略按审批类别分支。
        """
        self._policies.append((int(priority), policy_fn))

    def _run_policy_chain(
        self,
        kind: str,
        action_id: str,
        payload: dict[str, Any] | None,
        source: str,
        approved: bool,
    ) -> ActionCheckResult | None:
        """按优先级执行策略链。

        Returns:
            有策略短路时返回其 ActionCheckResult，否则返回 None（继续默认逻辑）。
        """
        policies = sorted(self._policies, key=lambda item: -item[0])
        if not policies:
            return None
        state = {"idx": 0}

        def next_fn():
            i = state["idx"]
            if i >= len(policies):
                return None
            state["idx"] = i + 1
            return policies[i][1](
                self,
                next_fn,
                action_id=action_id,
                payload=payload,
                source=source,
                approved=approved,
                kind=kind,
            )

        return next_fn()

    @staticmethod
    def _classify_mcp_risk(action_id: str) -> str:
        """Classify MCP operation risk."""
        if any(tag in action_id for tag in ("read", "list", "get", "search", "status", "log")):
            return "low"
        if any(tag in action_id for tag in ("shell", "custom", "execute", "exec", "run", "spawn", "command")):
            return "high"
        return "medium"

    def validate(
        self,
        action_id: str,
        payload: dict[str, Any] | None = None,
        source: str = "unknown",
        approved: bool = False,
    ) -> ActionCheckResult:
        normalized_action = str(action_id or "").strip()
        if not normalized_action:
            self._track(
                "assistant.action.rejected",
                SEVERITY_DEGRADED,
                {
                    "action_id": normalized_action,
                    "reason": "empty_action",
                    "source": source,
                },
            )
            return ActionCheckResult(False, normalized_action, reason="empty_action")

        # waterfall：审批策略链先跑，短路则直接返回
        chain_result = self._run_policy_chain("action", normalized_action, payload, source, approved)
        if chain_result is not None:
            return chain_result

        # 已人工确认（approved=True）：用户通过确认闭环显式授权了本次动作。
        # 人工确认是最高授权，直接放行，不再重复经过 LLM 高危拦截 / 风险规则 /
        # 白名单检查；capability 存在性与启用状态仍由 ActionBroker 层校验。
        # 此处记录审计事件，保证"已人工确认放行"可追踪。
        if approved:
            self._track(
                "assistant.action.human_approved",
                SEVERITY_INFO,
                {
                    "action_id": normalized_action,
                    "source": source,
                    "reason": "human_confirmed",
                },
            )
            return ActionCheckResult(True, normalized_action, risk_level="high")

        # --- MCP 动作：基浜庨险分绾褍鐞?---
        if normalized_action.startswith("mcp."):
            risk_level = self._classify_mcp_risk(normalized_action)
            self._track(
                "assistant.action.mcp_allowed",
                SEVERITY_INFO,
                {"action_id": normalized_action, "risk_level": risk_level, "source": source},
            )
            if risk_level == "high":
                self._track(
                    "assistant.action.mcp_high_risk",
                    SEVERITY_DEGRADED,
                    {"action_id": normalized_action, "risk_level": risk_level, "source": source},
                )
                return ActionCheckResult(False, normalized_action, reason="requires_confirmation", risk_level="high")
            if risk_level == "medium" and source in ("llm", "agent", "assistant"):
                return ActionCheckResult(False, normalized_action, reason="requires_confirmation", risk_level="medium")
            return ActionCheckResult(True, normalized_action, risk_level=risk_level)

        # --- LLM 来源的高风险动作：根据 warn_only 设置决定是否拦截 ---
        # 判定逻辑收敛到 is_llm_elevated（与 butler 升格路径共用同一判据，见裁决3）。
        if self.is_llm_elevated(normalized_action, source):
            # 出路①（B2「始终允许」）：用户曾在审批卡显式点过「始终允许」= 已获
            # 永久人类批准，等价于本次 approved，放行并留痕（裁决3 总纲：批准有出路）。
            if normalized_action in self._resolve_always_allow():
                self._track(
                    "assistant.action.human_approved",
                    SEVERITY_INFO,
                    {"action_id": normalized_action, "source": source, "reason": "always_allow"},
                )
                return ActionCheckResult(True, normalized_action, risk_level="high")
            if self._is_warn_only_enabled():
                self._track(
                    "assistant.action.llm_elevated_warn",
                    SEVERITY_INFO,
                    {
                        "action_id": normalized_action,
                        "source": source,
                        "reason": "llm_source_high_risk_warn_only",
                    },
                )
                return ActionCheckResult(
                    True,
                    normalized_action,
                    warnings=["LLM 建议的高风险操作，请确认后放行。"],
                    risk_level="high",
                )
            self._track(
                "assistant.action.llm_elevated_blocked",
                SEVERITY_DEGRADED,
                {
                    "action_id": normalized_action,
                    "source": source,
                    "reason": "llm_source_high_risk",
                },
            )
            # 文案如实化（裁决3）：经 butler 工具链的 elevated 动作已在下发前
            # 升格为队列2 confirmation（有出路）；走到这里说明是**不经升格层的
            # 直调 broker 路径**（RPC dispatch 由队列1 sandbox pending 给出出路，
            # react_executor 由 _DESTRUCTIVE_ACTIONS 入队 / shell 两阶段确认给出出路）。
            # 若这些路径也没有发起确认（如纯内部调用），则维持终态拒绝——纵深防御。
            return ActionCheckResult(
                False,
                normalized_action,
                reason="llm_source_high_risk",
                warnings=[
                    "LLM 发起的高风险操作需经用户批准：管家对话路径会自动生成确认请求（审批卡片）；"
                    "此拦截来自未接入确认闭环的调用路径，请改用对话工具或 RPC 审批通道。"
                ],
                risk_level="high",
            )
        default_actions, user_actions = self._resolve_allowed_action_groups()
        # 按来源区分匹配粒度：
        # - 代码默认组只做全名精确匹配（裸域 notes 不再隐式放行 notes.*）；
        # - 用户显式追加组保留整域放行（设置页既有语义）；
        # - denylist 先判且允许整域，保证「显式禁用」不会被新增的完整 id 绕过。
        permitted = match_action_id(normalized_action, default_actions) or match_action_id(normalized_action, user_actions, allow_namespace=True)
        if self._is_action_denied(normalized_action) or not permitted:
            self._track(
                "assistant.action.rejected",
                SEVERITY_DEGRADED,
                {
                    "action_id": normalized_action,
                    "reason": "not_in_whitelist",
                    "source": source,
                },
            )
            return ActionCheckResult(False, normalized_action, reason="not_in_whitelist")

        risk_result = self._evaluate_risk(
            target_id=normalized_action,
            payload=payload or {},
            source=source,
            target_type="action",
        )
        if not risk_result.allowed:
            return ActionCheckResult(
                False,
                normalized_action,
                reason=risk_result.reason,
                warnings=risk_result.warnings,
                risk_level=risk_result.risk_level,
            )
        return ActionCheckResult(True, normalized_action, warnings=risk_result.warnings)

    def validate_command(self, command_id: str, payload: dict[str, Any] | None = None, source: str = "unknown") -> ActionCheckResult:
        normalized_command = str(command_id or "").strip()
        if not normalized_command:
            self._track(
                "assistant.command.rejected",
                SEVERITY_DEGRADED,
                {
                    "command_id": normalized_command,
                    "reason": "empty_command",
                    "source": source,
                },
            )
            return ActionCheckResult(False, normalized_command, reason="empty_command")

        # waterfall：审批策略链先跑，短路则直接返回
        chain_result = self._run_policy_chain("command", normalized_command, payload, source, False)
        if chain_result is not None:
            return chain_result

        allowed_commands = self._resolve_allowed_commands()
        if normalized_command not in allowed_commands:
            self._track(
                "assistant.command.rejected",
                SEVERITY_DEGRADED,
                {
                    "command_id": normalized_command,
                    "reason": "not_in_command_whitelist",
                    "source": source,
                },
            )
            return ActionCheckResult(False, normalized_command, reason="not_in_command_whitelist")

        risk_result = self._evaluate_risk(
            target_id=normalized_command,
            payload=payload or {},
            source=source,
            target_type="command",
        )
        if not risk_result.allowed:
            return ActionCheckResult(
                False,
                normalized_command,
                reason=risk_result.reason,
                warnings=risk_result.warnings,
                risk_level=risk_result.risk_level,
            )
        return ActionCheckResult(True, normalized_command, warnings=risk_result.warnings)

    def validate_shell_command(self, command: str, payload: dict[str, Any] | None = None, source: str = "unknown") -> ActionCheckResult:
        normalized_command = str(command or "").strip()
        if not normalized_command:
            self._track(
                "assistant.shell.rejected",
                SEVERITY_DEGRADED,
                {
                    "reason": "empty_shell_command",
                    "source": source,
                },
            )
            return ActionCheckResult(False, "shell.run", reason="empty_shell_command")

        lowered_command = normalized_command.lower()
        for pattern in SHELL_DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, lowered_command):
                self._track(
                    "assistant.shell.rejected",
                    SEVERITY_DEGRADED,
                    {
                        "reason": "dangerous_command_blocked",
                        "source": source,
                        "payload_preview": self._build_payload_preview(payload or {"command": normalized_command}),
                    },
                )
                return ActionCheckResult(False, "shell.run", reason="dangerous_command_blocked", risk_level="high")

        if any(token in normalized_command for token in SHELL_UNSAFE_TOKENS):
            self._track(
                "assistant.shell.rejected",
                SEVERITY_DEGRADED,
                {
                    "reason": "unsafe_shell_syntax_blocked",
                    "source": source,
                    "payload_preview": self._build_payload_preview(payload or {"command": normalized_command}),
                },
            )
            return ActionCheckResult(False, "shell.run", reason="unsafe_shell_syntax_blocked", risk_level="high")

        return ActionCheckResult(True, "shell.run")

    def _resolve_allowed_actions(self) -> set[str]:
        """解析允许执行的 action 集合（2026-09-11 起为「合并」语义）。

        历史缺陷：此前直接 ``get_setting(KEY_ACTION_WHITELIST, DEFAULT)`` 取值，
        settings 里一旦存过白名单，代码默认值就完全失效。于是一份早期保存的
        快照会**静默锁死**此后默认值新增的全部能力——生产实测：28 项旧快照
        锁死 15 项（含 file.write / app.launch / shell.run / screen.read），
        任务队列 146 条 not_in_whitelist 死信即由此而来。

        现改为三段式合并：
            allowed = (DEFAULT_ALLOWED_ACTIONS ∪ whitelist) − denylist

        - whitelist 语义由「完整名单」变为「用户增量追加」
        - denylist 为新增，用于表达显式禁用意图

        这样默认值的演进不会再被用户旧快照冻死，同时用户仍可显式排除某项。
        """
        default_actions, user_actions = self._resolve_allowed_action_groups()
        return default_actions | user_actions

    def _resolve_allowed_action_groups(self) -> tuple[set[str], set[str]]:
        """按来源分组返回允许项：``(default, user_added)``（2026-09-15）。

        为什么要分组：两组的裸域条目语义不同。

        - ``DEFAULT_ALLOWED_ACTIONS`` 里的裸域（notes / tasks / inbox / news …）
          是历史遗留写法。若按「整域放行」解释，代码默认值就会隐式授权同域
          的未知能力（``notes.nuke_everything`` 也能过 guard）。真实可用工具
          已显式登记在 ``LOCAL_RESOURCE_ACTIONS``，无需裸域兜底 → 本组只做
          全名精确匹配。
        - 用户通过 ``assistant_action_whitelist`` 显式追加的裸域，是设置页
          既有语义「追加一个域 = 整个域放行」→ 本组保留命名空间匹配。

        denylist 对两组统一生效（先减再返回，等价于对并集取差）。
        """
        denied = self._read_action_set(KEY_ACTION_DENYLIST)
        level = self._resolve_sandbox_level()
        default_actions = _apply_sandbox_level(set(DEFAULT_ALLOWED_ACTIONS) - denied, level)
        user_actions = _apply_sandbox_level(self._read_action_set(KEY_ACTION_WHITELIST) - denied, level)
        return default_actions, user_actions

    def _resolve_denied_actions(self) -> set[str]:
        """用户显式禁用的 action 集合（``assistant_action_denylist``）。

        单独暴露给 validate：denylist 必须按 match_action_id 的双形态语义判定
        （allow_namespace=True），仅靠集合减法无法覆盖「禁用裸域名 notes →
        拦截 notes.list」这一情形。
        """
        return self._read_action_set(KEY_ACTION_DENYLIST)

    def _is_action_denied(self, action_id: str) -> bool:
        denied = self._resolve_denied_actions()
        if not denied:
            return False
        return match_action_id(action_id, denied, allow_namespace=True)

    def _resolve_sandbox_level(self) -> str:
        """读 ``assistant_sandbox_level``（B1）。缺省 full-access = 不额外限制。"""
        if self.settings_repository is None:
            return DEFAULT_SANDBOX_LEVEL
        try:
            raw = (
                str(self.settings_repository.get_setting(KEY_ASSISTANT_SANDBOX_LEVEL, DEFAULT_SANDBOX_LEVEL) or DEFAULT_SANDBOX_LEVEL).strip().lower()
            )
        except Exception:
            return DEFAULT_SANDBOX_LEVEL
        return raw if raw in _VALID_SANDBOX_LEVELS else DEFAULT_SANDBOX_LEVEL

    def _resolve_approval_policy(self) -> str:
        """读 ``assistant_approval_policy``（B1）。缺省 on-risk = 等同现状。"""
        if self.settings_repository is None:
            return DEFAULT_APPROVAL_POLICY
        try:
            raw = (
                str(self.settings_repository.get_setting(KEY_ASSISTANT_APPROVAL_POLICY, DEFAULT_APPROVAL_POLICY) or DEFAULT_APPROVAL_POLICY)
                .strip()
                .lower()
            )
        except Exception:
            return DEFAULT_APPROVAL_POLICY
        return raw if raw in _VALID_APPROVAL_POLICIES else DEFAULT_APPROVAL_POLICY

    def _resolve_always_allow(self) -> set[str]:
        """读 ``assistant_confirm_always_allow``（B2：用户点过「始终允许」的名单）。"""
        return self._read_action_set(KEY_ACTION_ALWAYS_ALLOW)

    def grant_always_allow(self, action_id: str) -> bool:
        """把某能力加入「始终允许」名单（B2；由 confirmation.resolve 写入）。

        注意：白名单（准入）与确认名单（审批）是两回事——加入本名单只表示
        「以后不再询问」，不改变该能力是否允许执行。
        """
        action = str(action_id or "").strip()
        if not action or self.settings_repository is None:
            return False
        try:
            current = self._resolve_always_allow()
            if action in current:
                return True
            updated = sorted(current | {action})
            self.settings_repository.set_setting(KEY_ACTION_ALWAYS_ALLOW, updated)
            return True
        except Exception:
            logger.warning("写入 always_allow 失败: %s", action, exc_info=True)
            return False

    def revoke_always_allow(self, action_id: str) -> bool:
        """把某能力移出「始终允许」名单（B2 收尾，2026-09-11）。

        安全模型对称性：能授予必须能收回——用户在设置页移除某项后，
        该能力下次执行恢复人工确认。幂等：移除不在名单中的项也算成功。
        """
        action = str(action_id or "").strip()
        if not action or self.settings_repository is None:
            return False
        try:
            current = self._resolve_always_allow()
            if action not in current:
                return True
            updated = sorted(current - {action})
            self.settings_repository.set_setting(KEY_ACTION_ALWAYS_ALLOW, updated)
            return True
        except Exception:
            logger.warning("移除 always_allow 失败: %s", action, exc_info=True)
            return False

    def is_llm_elevated(self, action_id: str, source: str) -> bool:
        """该动作在此来源下是否属于「LLM 源高危（elevated）」——需人类批准。

        裁决 3（authorization-model-adjudication-2026-09-17）：elevated 判定是
        管家（butler）工具链「升格为队列2 confirmation」的唯一入口判据，必须与
        ``validate()`` 内部 elevated→block 分支**完全同源**，否则又会出现
        「两副面孔」——guard 认为要拦、butler 认为不必问。

        判据（历史，保持语义不变）：

        1. 显式名单命中：``action_id ∈ _LLM_ELEVATED_ACTIONS``（file.write /
           shell.run / process.kill / app.launch / web.download / settings /
           clipboard.write / notes.delete / tasks.delete / inbox.delete /
           notes.batch_delete）；
        2. 动态工具名启发式（P0-3）：来源属 LLM 族且 id 以 ``tool.`` 开头、
           名字含 shell/exec/run/launch/kill/write/delete 任一危险词。

        ``warn_only`` 不影响本判定（它是「命中后拦还是放」的降档开关，由
        ``validate()`` 与调用方各自处理）；本方法只回答「是不是 elevated」。
        """
        action = str(action_id or "").strip()
        if not action:
            return False
        if source not in ("llm", "agent", "assistant"):
            return False
        if action in _LLM_ELEVATED_ACTIONS:
            return True
        # P0-3: LLM 通过 ToolSpec 注册的动态工具（tool.*）也可能执行高危操作
        # （如 shell/run/exec/launch/kill/write/delete 等）。对这类动作默认需要确认，
        # 避免绕过 file.write / shell.run / app.launch 等显式高危动作拦截。
        if action.startswith("tool."):
            lowered = action.lower()
            return any(tag in lowered for tag in _LLM_ELEVATED_ACTION_RISK_TAGS)
        return False

    def requires_llm_elevation_approval(self, action_id: str, source: str) -> bool:
        """elevated 动作是否需要**升格为队列2 confirmation**（裁决3 执行入口判据）。

        与 :meth:`is_llm_elevated` 的差集 = 已被显式降档/授权的三种情形：

        - ``warn_only=True``：显式降档是合法配置，不升格（维持告警放行，
          行为与旧版一致 —— 有防回归断言）；
        - ``approval_policy=never``：无人值守显式声明不询问，不升格；
        - ``action ∈ always_allow``：用户显式永久批准过，不升格。

        返回 True 时调用方（butler 工具链）**必须**给出确认出路，而不是把动作
        下沉给 broker 吃终态 block —— 这就是「elevated = 需要人类批准，批准有出路」
        的落地形态。
        """
        if not self.is_llm_elevated(action_id, source):
            return False
        if self._is_warn_only_enabled():
            return False
        if self._resolve_approval_policy() == APPROVAL_NEVER:
            return False
        action = str(action_id or "").strip()
        if action and action in self._resolve_always_allow():
            return False
        return True

    def should_require_confirmation(self, action_id: str) -> bool:
        """该能力是否需要人工确认（B1 审批维度；供 butler 工具分级调用）。

        判定顺序（B2）：「始终允许」名单优先于全局策略——
        用户在某次审批中显式选择过「始终允许」，以后不再询问。

        - ``never``   : 一律不询问（无人值守场景显式声明）
        - ``on-risk`` : 仅高风险能力询问（默认，等同既有 CONFIRM_TOOLS 行为）
        - ``always``  : 高风险 + 所有写类能力都询问

        注意：本方法只回答「要不要问」，不回答「能不能做」——后者由
        ``_resolve_allowed_actions()`` 负责。这正是正交两维的分工。
        """
        action = str(action_id or "").strip()
        if not action:
            return False
        if action in self._resolve_always_allow():
            return False
        policy = self._resolve_approval_policy()
        if policy == APPROVAL_NEVER:
            return False
        if action in _HIGH_RISK_ACTIONS:
            return True
        if policy == APPROVAL_ALWAYS and action in _WRITE_ACTIONS:
            return True
        return False

    def _read_action_set(self, key: str) -> set[str]:
        """读取一个 action 名集合设置（兼容字符串与序列两种存储形态）。"""
        if self.settings_repository is None:
            return set()
        try:
            raw = self.settings_repository.get_setting(key, [])
        except Exception:
            logger.debug("读取 action 集合设置失败: %s", key, exc_info=True)
            return set()
        if isinstance(raw, str):
            return {part.strip() for part in raw.split(",") if part.strip()}
        if isinstance(raw, Sequence):
            return {str(item).strip() for item in raw if str(item).strip()}
        return set()

    def _resolve_allowed_commands(self) -> set[str]:
        raw = None
        if self.settings_repository is not None:
            raw = self.settings_repository.get_setting(KEY_COMMAND_WHITELIST, list(DEFAULT_ALLOWED_COMMANDS))

        if isinstance(raw, str):
            items = [part.strip() for part in raw.split(",")]
            return {item for item in items if item}

        if isinstance(raw, Sequence):
            result = {str(item).strip() for item in raw if str(item).strip()}
            return result or set(DEFAULT_ALLOWED_COMMANDS)

        return set(DEFAULT_ALLOWED_COMMANDS)

    def _collect_risk_warnings(self, target_id: str, payload: dict[str, Any], source: str, target_type: str) -> list[str]:
        warnings: list[str] = []
        for rule in RISK_RULES:
            matched = False
            if target_type == "action":
                matched = target_id in rule.action_ids
            elif target_type == "command":
                matched = target_id in rule.command_ids
            if not matched:
                continue
            warnings.append(rule.message)
            self._track(
                f"assistant.{target_type}.risk_warning",
                SEVERITY_INFO,
                {
                    f"{target_type}_id": target_id,
                    "rule_id": rule.rule_id,
                    "risk_level": rule.level,
                    "source": source,
                    "payload_preview": self._build_payload_preview(payload),
                    "enforcement": self._resolve_risk_enforcement_mode(),
                },
            )
        return warnings

    def _collect_highest_risk_level(self, target_id: str, target_type: str) -> str:
        rank = {"": 0, "low": 1, "medium": 2, "high": 3}
        current = ""
        for rule in RISK_RULES:
            matched = target_id in rule.action_ids if target_type == "action" else target_id in rule.command_ids
            if not matched:
                continue
            if rank.get(rule.level, 0) > rank.get(current, 0):
                current = rule.level
        return current

    def _evaluate_risk(self, target_id: str, payload: dict[str, Any], source: str, target_type: str) -> ActionCheckResult:
        warnings = self._collect_risk_warnings(target_id, payload, source=source, target_type=target_type)
        if not warnings:
            return ActionCheckResult(True, target_id)

        risk_level = self._collect_highest_risk_level(target_id, target_type)
        if not self._should_block(risk_level):
            return ActionCheckResult(True, target_id, warnings=warnings, risk_level=risk_level)

        self._track(
            f"assistant.{target_type}.risk_blocked",
            SEVERITY_DEGRADED,
            {
                f"{target_type}_id": target_id,
                "risk_level": risk_level,
                "source": source,
                "payload_preview": self._build_payload_preview(payload),
                "enforcement": self._resolve_risk_enforcement_mode(),
            },
        )
        return ActionCheckResult(False, target_id, reason="risk_blocked", warnings=warnings, risk_level=risk_level)

    def _is_warn_only_enabled(self) -> bool:
        """LLM 来源高危动作 warn-only 模式开关。

        安全语义（P0-4 修复）：
        - 默认 False = 拦截。LLM 建议的高危动作（shell.run / file.write /
          process.kill 等）默认必须用户手动确认，不允许仅记日志放行。
        - 用户可显式设置为 True 来允许"仅警告"模式（高级用户/开发模式）。
        - settings_repository 为 None 时（如纯单元测试场景）也走默认值 False，
          以保持安全默认。

        历史 bug：默认为 True 时，未配置环境下 LLM 调用 shell.run 仅记日志即
        放行，使 ActionGuard 在生产默认配置下形同虚设。
        """
        if self.settings_repository is None:
            return False
        return bool(self.settings_repository.get_setting(KEY_ACTION_RISK_WARN_ONLY, False))

    def _resolve_profile(self) -> str:
        if self.settings_repository is None:
            return DEFAULT_PROFILE
        raw = str(self.settings_repository.get_setting(KEY_ACTION_GUARD_PROFILE, DEFAULT_PROFILE) or DEFAULT_PROFILE).strip().lower()
        if raw in {PROFILE_STRICT, PROFILE_BALANCED, PROFILE_DEV}:
            return raw
        return DEFAULT_PROFILE

    def _resolve_risk_enforcement_mode(self) -> str:
        if self.settings_repository is None:
            return ENFORCEMENT_WARN
        raw_mode = str(self.settings_repository.get_setting(KEY_ACTION_RISK_ENFORCEMENT, ENFORCEMENT_AUTO) or ENFORCEMENT_AUTO).strip().lower()
        if raw_mode == ENFORCEMENT_AUTO:
            profile = self._resolve_profile()
            if profile == PROFILE_STRICT:
                return ENFORCEMENT_BLOCK
            if profile == PROFILE_DEV:
                return ENFORCEMENT_WARN
            return ENFORCEMENT_WARN if self._is_warn_only_enabled() else ENFORCEMENT_BLOCK
        if raw_mode in {ENFORCEMENT_WARN, ENFORCEMENT_BLOCK}:
            return raw_mode
        return ENFORCEMENT_WARN

    def _should_block(self, risk_level: str) -> bool:
        enforcement = self._resolve_risk_enforcement_mode()
        if enforcement != ENFORCEMENT_BLOCK:
            return False
        profile = self._resolve_profile()
        # balanced/dev 模式下，鎵有操作都允裨由 UI 弹窗纭保障安全锛?
        if profile in (PROFILE_BALANCED, PROFILE_DEV):
            return False
        # strict 模式下仍阻断楂橀闄?
        if risk_level == "high":
            return True
        if risk_level == "medium":
            return profile == PROFILE_STRICT
        return False

    def _build_payload_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        preview: dict[str, Any] = {}
        for key in ("target_id", "source", "url", "launcher_path", "command"):
            if key in payload:
                preview[key] = payload.get(key)
        return preview

    def _track(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        service = self.observability_service
        if service is None:
            return
        try:
            service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.debug("Non-critical error tracking event: %s", event_name, exc_info=True)


def normalize_whitelist(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
        return [item for item in items if item]
    if isinstance(value, Iterable):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized
    return list(DEFAULT_ALLOWED_ACTIONS)


def normalize_profile(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {PROFILE_STRICT, PROFILE_BALANCED, PROFILE_DEV}:
        return normalized
    return DEFAULT_PROFILE


def normalize_enforcement(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {ENFORCEMENT_AUTO, ENFORCEMENT_WARN, ENFORCEMENT_BLOCK}:
        return normalized
    return ENFORCEMENT_AUTO


def normalize_command_whitelist(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
        return [item for item in items if item]
    if isinstance(value, Iterable):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized
    return list(DEFAULT_ALLOWED_COMMANDS)
