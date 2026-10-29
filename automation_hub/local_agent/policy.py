"""local_agent.policy — 本地策略缓存与执行前判定。"""

from __future__ import annotations

import time
from typing import Any

from local_agent.models import PolicyBundle, RiskLevel


class PolicyCache:
    """本地策略缓存。

    从控制面拉取 PolicyBundle 后缓存在内存，
    在 TTL 过期前为每次执行请求做本地策略初判。
    """

    def __init__(self) -> None:
        self._bundle: PolicyBundle | None = None
        self._fetched_at: float = 0

    def update(self, bundle: PolicyBundle) -> None:
        self._bundle = bundle
        self._fetched_at = time.monotonic()

    @property
    def is_valid(self) -> bool:
        if self._bundle is None:
            return False
        return (time.monotonic() - self._fetched_at) < self._bundle.cache_ttl_sec

    @property
    def bundle(self) -> PolicyBundle | None:
        return self._bundle if self.is_valid else None

    # ------------------------------------------------------------------
    # 策略判定
    # ------------------------------------------------------------------

    def is_tool_allowed(self, tool_id: str) -> bool:
        """工具是否在白名单。"""
        b = self.bundle
        if b is None:
            return False
        if not b.allowed_tools:
            return True  # 空列表 = 不限制
        return tool_id in b.allowed_tools

    def requires_approval(self, risk_level: str) -> bool:
        """该风险等级是否需要审批。"""
        b = self.bundle
        if b is None:
            return True  # 无策略时默认需要审批
        for rule in b.approval_rules:
            if RiskLevel(risk_level) in rule.risk_levels:
                return rule.require_approval
        # 不在规则里的默认不需要
        return False

    def check_path(self, path: str) -> bool:
        """路径是否在允许范围。"""
        b = self.bundle
        if b is None:
            return False
        if not b.allowed_paths:
            return True
        import os
        norm = os.path.normpath(path)
        return any(norm.startswith(os.path.normpath(p)) for p in b.allowed_paths)

    def pre_check(self, tool_id: str, risk_level: str, paths: list[str] | None = None) -> dict[str, Any]:
        """综合策略预判，返回 {allowed, needs_approval, reason}。"""
        if not self.is_valid:
            return {"allowed": False, "needs_approval": True, "reason": "policy cache expired"}
        if not self.is_tool_allowed(tool_id):
            return {"allowed": False, "needs_approval": False, "reason": f"tool {tool_id} not in whitelist"}
        for p in (paths or []):
            if not self.check_path(p):
                return {"allowed": False, "needs_approval": False, "reason": f"path {p} not allowed"}
        needs = self.requires_approval(risk_level)
        return {"allowed": True, "needs_approval": needs, "reason": "ok"}
