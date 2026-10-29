"""安全层：授权銆佸?璁掳?佺瓥略和人工兜底（集成权限矩阵）。"""
from __future__ import annotations

import logging
import threading
from typing import Any

from core.action_guard import ActionGuard
from .models import PlannedStep
from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO
from core.settings_schema import KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, KEY_PERMISSION_MATRIX_ENABLED

logger = logging.getLogger("siyi.assistant.security")


class AssistantSecurityLayer:
    """Security layer for request validation and access control."""

    def __init__(self, settings_repository=None, observability_service=None, integration_hub=None, permission_matrix=None, extension_registry=None):
        self.settings_repository = settings_repository
        self.observability_service = observability_service
        self.integration_hub = integration_hub
        self.permission_matrix = permission_matrix
        self.extension_registry = extension_registry
        self.guard = ActionGuard(settings_repository=settings_repository, observability_service=observability_service)
        # P2-2: 速率限制 — 防止 LLM 循环失控
        self._action_timestamps: dict[str, list[float]] = {}
        self._rate_limit_window = 60.0  # 60 秒窗口
        self._rate_limit_max = 20  # 每个 action_id 每窗口最多 20 次
        # P3-4: 全局速率限制 — 所有 action 合计，防止 AI 循环失控
        self._global_timestamps: list[float] = []
        self._global_rate_limit_max = 100  # 全局每窗口最多 100 次
        # 审查修复：速率限制状态需加锁，防止 to_thread/run_in_executor 并发竞态
        self._rate_limit_lock = threading.Lock()

    def authorize_step(
        self,
        step: PlannedStep,
        source: str = "assistant.plan",
        pre_approved: set | None = None,
        consume_quota: bool = True,
    ) -> dict[str, Any]:
        # 1) 权限矩阵妫?查（棰戞?闄愰? + 寮?鍏?+ 风险等级锛?
        if self.permission_matrix is not None and self._get_setting(KEY_PERMISSION_MATRIX_ENABLED, True):
            perm = self.permission_matrix.check_permission(step.action_id, source=source)
            if not perm.get("allowed"):
                decision = {
                    "allowed": False,
                    "reason": perm.get("reason", "permission_denied"),
                    "warnings": [perm.get("reason", "")],
                    "risk_level": perm.get("risk_tier", step.risk_level),
                    "approval_strategy": perm.get("approval_strategy", ""),
                }
                self._audit(step.action_id, "authorize", "permission_blocked", decision)
                return decision

            # 楂橀?险强制人工确认（如果用户宸查?批准则跳过）
            if perm.get("requires_confirmation"):
                require_confirm = bool(self._get_setting(KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, True))
                if require_confirm and not (pre_approved and step.action_id in pre_approved):
                    decision = {
                        "allowed": False,
                        "reason": "manual_confirmation_required",
                        "warnings": ["楂橀?险动作需要人工确璁?"],
                        "risk_level": "high",
                        "approval_strategy": "manual_confirm",
                    }
                    self._audit(step.action_id, "authorize", "manual_required", decision)
                    return decision

            # 鍙?啓任务半自动提绀?
            if perm.get("requires_review"):
                step.risk_level = "medium"

        # 2) 联网扩展权限妫?查（source 褰㈠? "extension.{source_id}"锛?
        EXTENSION_SOURCE_PREFIX = "extension."
        if source and source.startswith(EXTENSION_SOURCE_PREFIX):
            ext_source_id = source[len(EXTENSION_SOURCE_PREFIX):]
            ext_check = self._authorize_extension(ext_source_id, step.action_id, source)
            if not ext_check.get("allowed"):
                return ext_check

        # 3) ActionGuard 白名鍗?+ 风险妫?鏌?
        result = self.guard.validate(step.action_id, payload=step.payload, source=source)
        if not result.allowed:
            decision = {
                "allowed": False,
                "reason": result.reason or "policy_blocked",
                "warnings": result.warnings,
                "risk_level": result.risk_level or step.risk_level,
            }
            self._audit(step.action_id, "authorize", "blocked", decision)
            return decision

        require_confirm = bool(self._get_setting(KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, True))
        if require_confirm and (step.risk_level or result.risk_level) == "high" and not (pre_approved and step.action_id in pre_approved):
            decision = {
                "allowed": False,
                "reason": "manual_confirmation_required",
                "warnings": (result.warnings or []) + ["楂橀?险动作需要人工确璁?"],
                "risk_level": "high",
            }
            self._audit(step.action_id, "authorize", "manual_required", decision)
            return decision

        # P2-2: 速率限制检查 — 防止 LLM 来源的高频调用失控
        if source in ("llm", "agent", "assistant"):
            if not self._check_rate_limit(step.action_id):
                decision = {
                    "allowed": False,
                    "reason": "rate_limited",
                    "warnings": [f"动作 {step.action_id} 在 {self._rate_limit_window:.0f}s 内调用超过 {self._rate_limit_max} 次"],
                    "risk_level": "medium",
                }
                self._audit(step.action_id, "authorize", "rate_limited", decision)
                return decision

        # 3) 娑堣?楅厤棰?
        if consume_quota and self.permission_matrix is not None and self._get_setting(KEY_PERMISSION_MATRIX_ENABLED, True):
            self.permission_matrix.consume_quota(step.action_id)

        decision = {
            "allowed": True,
            "reason": "ok",
            "warnings": result.warnings,
            "risk_level": result.risk_level or step.risk_level,
        }
        self._audit(step.action_id, "authorize", "ok", decision)
        return decision

    def _get_setting(self, key: str, default: Any) -> Any:
        if self.settings_repository is None:
            return default
        return self.settings_repository.get_setting(key, default)

    def _check_rate_limit(self, action_id: str) -> bool:
        """P2-2/P3-4: 检查 action_id 是否超过速率限制。

        返回 True 表示允许，False 表示被限流。
        双层限制：单 action 每窗口 _rate_limit_max 次 + 全局每窗口 _global_rate_limit_max 次。
        审查修复：加锁防止 to_thread/run_in_executor 并发竞态。
        """
        import time as _time
        now = _time.time()
        cutoff = now - self._rate_limit_window

        with self._rate_limit_lock:
            # P3-4: 全局速率限制检查
            self._global_timestamps = [t for t in self._global_timestamps if t > cutoff]
            if len(self._global_timestamps) >= self._global_rate_limit_max:
                logger.warning(
                    "全局速率限制触发: %d 次/%.0fs (上限 %d)",
                    len(self._global_timestamps), self._rate_limit_window, self._global_rate_limit_max,
                )
                return False

            # 单 action 速率限制检查
            timestamps = self._action_timestamps.get(action_id, [])
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= self._rate_limit_max:
                self._action_timestamps[action_id] = timestamps
                return False

            # 两层都通过，记录时间戳
            timestamps.append(now)
            self._action_timestamps[action_id] = timestamps
            self._global_timestamps.append(now)
            return True

    def _authorize_extension(self, ext_source_id: str, action_id: str, source: str) -> dict[str, Any]:
        """妫?查联网扩展是否有权执行指定动浣溿?"""
        if self.extension_registry is None:
            return {"allowed": True, "reason": "no_extension_registry"}

        ext_source = self.extension_registry.get_source(ext_source_id)
        if ext_source is None:
            decision = {
                "allowed": False,
                "reason": "extension_not_found",
                "warnings": [f"联网扩展来源 {ext_source_id} 鏈?敞鍐?"],
                "risk_level": "high",
            }
            self._audit(ext_source_id, "authorize_extension", "not_found", decision)
            return decision

        if not ext_source.enabled:
            decision = {
                "allowed": False,
                "reason": "extension_disabled",
                "warnings": [f"联网扩展来源 {ext_source.name} 宸茶?禁用"],
                "risk_level": "high",
            }
            self._audit(ext_source_id, "authorize_extension", "disabled", decision)
            return decision

        if ext_source.health_status == "down":
            decision = {
                "allowed": False,
                "reason": "extension_unhealthy",
                "warnings": [f"联网扩展来源 {ext_source.name} 当前不可鐢?(health=down)"],
                "risk_level": "high",
            }
            self._audit(ext_source_id, "authorize_extension", "unhealthy", decision)
            return decision

        # 楂橀?险动浣滄?查：如果扩展没有 act 权限，但动作风险一?high，则拦截
        if not ext_source.has_act_permission:
            decision = {
                "allowed": False,
                "reason": "extension_permission_denied",
                "warnings": [f"联网扩展来源 {ext_source.name} 没有触发动作权限 (tier={ext_source.tier})"],
                "risk_level": "high",
                "approval_strategy": "manual_confirm",
            }
            if self._get_setting(KEY_ASSISTANT_HIGH_RISK_REQUIRE_CONFIRM, True):
                self._audit(ext_source_id, "authorize_extension", "permission_denied", decision)
                return decision

        return {"allowed": True, "reason": "extension_ok", "risk_level": ext_source.tier}

    def _audit(self, provider: str, event: str, status: str, payload: dict[str, Any]) -> None:
        if self.integration_hub is not None:
            try:
                self.integration_hub.append_audit_event(provider, event, status, payload)
            except Exception as exc:
                logger.warning("瀹陇?事件写入 integration_hub 失败 (provider=%s event=%s): %s", provider, event, exc, exc_info=True)
        if self.observability_service is not None:
            try:
                severity = SEVERITY_INFO if status == "ok" else SEVERITY_DEGRADED
                self.observability_service.track_event(
                    "assistant.security.audit",
                    severity=severity,
                    payload={
                        "provider": provider,
                        "event": event,
                        "status": status,
                        "payload": payload,
                    },
                )
            except Exception as exc:
                logger.warning("观测事件追踪失败 (provider=%s event=%s): %s", provider, event, exc, exc_info=True)
