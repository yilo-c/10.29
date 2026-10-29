"""联网扩展注册中心：来源注册、权限分层、启停控制、健康追踪。

所有外部联网扩展来源（搜索、网页抓取、浏览器桥接、MCP provider 等）
必须先在此注册，才能被 planner 选择和执行。

权限三层模型：
- read:      仅获取信息（默认 ON）
- suggest:   获取信息并产生建议（默认 ON）
- act:       获取信息后触发受控动作（默认 OFF，需用户显式启用）

与现有系统的关系：
- AssistantSourceRegistry: 信息源可信度评分（注册后关联）
- CapabilityRegistry: 能力注册（扩展可注册能力）
- AuditTrail: 审计轨迹（启停、权限变更等操作留痕）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("siyi.extension_registry")

# ── 权限分级 ───────────────────────────────────────────────


class ExtensionPermission(Enum):
    """联网扩展权限三级模型。"""
    READ = "read"          # 仅获取信息（默认启用）
    SUGGEST = "suggest"    # 获取信息并产生建议（默认启用）
    ACT = "act"            # 获取信息后触发受控动作（默认关闭）


# 默认权限层级映射
_TIER_TO_PERMISSIONS: dict[str, set[ExtensionPermission]] = {
    "info-only": {ExtensionPermission.READ},
    "info+suggest": {ExtensionPermission.READ, ExtensionPermission.SUGGEST},
    "trigger-action": {ExtensionPermission.READ, ExtensionPermission.SUGGEST, ExtensionPermission.ACT},
}

# 默认层级（前两层默认开启，第三层默认关闭）
_DEFAULT_TIER = "info+suggest"
_HIGH_RISK_PERMISSIONS = {ExtensionPermission.ACT}


# ── 来源类型 ───────────────────────────────────────────────


class ExtensionSourceType(Enum):
    SEARCH = "search"          # 搜索引擎
    WEB_FETCH = "web_fetch"    # 网页抓取
    BROWSER_BRIDGE = "browser_bridge"  # 浏览器桥接
    MCP_PROVIDER = "mcp_provider"      # MCP provider
    DATA_SOURCE = "data_source"        # 数据源
    API_PROVIDER = "api_provider"      # API provider
    CUSTOM = "custom"                  # 用户自定义


# ── 数据模型 ───────────────────────────────────────────────


@dataclass
class ExtensionSource:
    """单个联网扩展来源的完整信息。"""
    source_id: str
    name: str
    source_type: str  # ExtensionSourceType value
    trust_level: str = "unknown"  # 参考 AssistantSourceRegistry: official/authority/community/general/unknown
    tier: str = _DEFAULT_TIER  # info-only / info+suggest / trigger-action
    enabled: bool = True
    health_status: str = "unknown"  # unknown / healthy / degraded / down
    last_health_check: str = ""
    description: str = ""
    version: str = "1.0.0"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def permissions(self) -> set[str]:
        """返回当前 tier 对应的权限集合。"""
        return {p.value for p in _TIER_TO_PERMISSIONS.get(self.tier, _TIER_TO_PERMISSIONS[_DEFAULT_TIER])}

    @property
    def has_act_permission(self) -> bool:
        """是否拥有触发动作的权限（高风险）。"""
        return ExtensionPermission.ACT.value in self.permissions

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "source_type": self.source_type,
            "trust_level": self.trust_level,
            "tier": self.tier,
            "enabled": self.enabled,
            "health_status": self.health_status,
            "last_health_check": self.last_health_check,
            "description": self.description,
            "version": self.version,
            "extra": dict(self.extra),
        }


@dataclass
class ExtensionAuditEntry:
    """扩展操作审计条目。"""
    entry_id: str
    timestamp: str
    source_id: str
    operation: str  # register / enable / disable / tier_change / health_update / unregister
    detail: str
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None


# ── 内置来源注册表 ─────────────────────────────────────────

_BUILTIN_EXTENSIONS: list[dict[str, Any]] = [
    {
        "source_id": "builtin.web_search",
        "name": "联网搜索",
        "source_type": ExtensionSourceType.SEARCH.value,
        "trust_level": "general",
        "tier": "info+suggest",
        "description": "通过搜索引擎获取实时互联网信息",
    },
    {
        "source_id": "builtin.web_fetch",
        "name": "网页抓取",
        "source_type": ExtensionSourceType.WEB_FETCH.value,
        "trust_level": "general",
        "tier": "info+suggest",
        "description": "抓取指定网页的正文内容",
    },
    {
        "source_id": "builtin.browser_bridge",
        "name": "浏览器桥接",
        "source_type": ExtensionSourceType.BROWSER_BRIDGE.value,
        "trust_level": "authority",
        "tier": "info+suggest",
        "description": "通过浏览器扩展获取结构化上下文",
    },
    {
        "source_id": "builtin.mcp_provider",
        "name": "MCP 工具",
        "source_type": ExtensionSourceType.MCP_PROVIDER.value,
        "trust_level": "community",
        "tier": "info-only",
        "description": "通过 MCP 协议接入的第三方工具",
    },
    {
        "source_id": "builtin.news_provider",
        "name": "新闻资讯",
        "source_type": ExtensionSourceType.DATA_SOURCE.value,
        "trust_level": "authority",
        "tier": "info+suggest",
        "description": "聚合多源新闻资讯检索",
    },
]


# ── 注册中心 ───────────────────────────────────────────────


class ExtensionRegistry:
    """联网扩展统一注册中心。

    职责：
    - 来源注册/注销
    - 启停控制
    - 权限层级管理
    - 健康追踪
    - 操作审计
    """

    _STORAGE_KEY = "extension_registry"

    def __init__(self, data_store=None, audit_trail=None):
        self.data_store = data_store
        self.audit_trail = audit_trail
        self._sources: dict[str, ExtensionSource] = {}
        self._audit_log: list[ExtensionAuditEntry] = []
        self._load_builtin_sources()

    # ── 内置来源加载 ─────────────────────────────────────

    def _load_builtin_sources(self) -> None:
        """加载内置来源 + 从持久化恢复用户设置。"""
        persisted = self._load_persisted()
        persisted_ids = set(persisted.keys())

        for entry in _BUILTIN_EXTENSIONS:
            source = ExtensionSource(**entry)
            if source.source_id in persisted_ids:
                saved = persisted[source.source_id]
                source.enabled = bool(saved.get("enabled", source.enabled))
                source.tier = str(saved.get("tier", source.tier))
                source.health_status = str(saved.get("health_status", source.health_status))
            self._sources[source.source_id] = source

        # 加载持久化中的用户自定义来源
        for src_id, saved in persisted.items():
            if src_id not in self._sources:
                try:
                    source = ExtensionSource(
                        source_id=src_id,
                        name=str(saved.get("name", src_id)),
                        source_type=str(saved.get("source_type", ExtensionSourceType.CUSTOM.value)),
                        trust_level=str(saved.get("trust_level", "unknown")),
                        tier=str(saved.get("tier", _DEFAULT_TIER)),
                        enabled=bool(saved.get("enabled", True)),
                        health_status=str(saved.get("health_status", "unknown")),
                        description=str(saved.get("description", "")),
                        version=str(saved.get("version", "1.0.0")),
                        extra=dict(saved.get("extra", {})),
                    )
                    self._sources[src_id] = source
                except Exception as exc:
                    logger.warning("无法恢复来源 %s: %s", src_id, exc)

    def _load_persisted(self) -> dict[str, Any]:
        if self.data_store is None:
            return {}
        stored = self.data_store.load_json(self._STORAGE_KEY, {})
        return dict(stored) if isinstance(stored, dict) else {}

    def _save_persisted(self) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json(
            self._STORAGE_KEY,
            {sid: src.to_dict() for sid, src in self._sources.items()},
        )

    # ── 注册管理 ─────────────────────────────────────────

    def register_source(self, source: ExtensionSource, *, auto_persist: bool = True) -> dict[str, Any]:
        """注册新来源。如果已存在则更新。"""
        previous = self._sources.get(source.source_id)
        self._sources[source.source_id] = source
        self._append_audit(
            source_id=source.source_id,
            operation="register" if previous is None else "update",
            detail=f"来源 {'更新' if previous else '注册'}: {source.name} (tier={source.tier})",
            previous_state=previous.to_dict() if previous else None,
            new_state=source.to_dict(),
        )
        if auto_persist:
            self._save_persisted()
        return {"registered": True, "source_id": source.source_id}

    def unregister_source(self, source_id: str, *, auto_persist: bool = True) -> dict[str, Any]:
        """注销来源。"""
        source = self._sources.pop(source_id, None)
        if source is None:
            return {"unregistered": False, "reason": "not_found"}
        self._append_audit(
            source_id=source_id,
            operation="unregister",
            detail=f"来源注销: {source.name}",
            previous_state=source.to_dict(),
            new_state=None,
        )
        if auto_persist:
            self._save_persisted()
        return {"unregistered": True, "source_id": source_id}

    def get_source(self, source_id: str) -> ExtensionSource | None:
        return self._sources.get(source_id)

    def list_sources(
        self,
        *,
        enabled_only: bool = False,
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for src in self._sources.values():
            if enabled_only and not src.enabled:
                continue
            if source_type and src.source_type != source_type:
                continue
            result.append(src.to_dict())
        return sorted(result, key=lambda x: (not x["enabled"], x["source_id"]))

    # ── 启停控制 ─────────────────────────────────────────

    def set_enabled(self, source_id: str, enabled: bool, *, auto_persist: bool = True) -> dict[str, Any]:
        """启用或禁用某来源。"""
        source = self._sources.get(source_id)
        if source is None:
            return {"success": False, "reason": "not_found"}
        previous = source.to_dict()
        source.enabled = enabled
        self._append_audit(
            source_id=source_id,
            operation="enable" if enabled else "disable",
            detail=f"来源 {'启用' if enabled else '禁用'}: {source.name}",
            previous_state=previous,
            new_state=source.to_dict(),
        )
        if auto_persist:
            self._save_persisted()
        if self.audit_trail is not None:
            try:
                entry_id = self.audit_trail.record_write_intent(
                    actor="extension_registry",
                    action_id=f"extension.{'enable' if enabled else 'disable'}",
                    target_type="extension_source",
                    target_id=source_id,
                    operation="update",
                    before_snapshot=previous,
                    metadata={"source_id": source_id, "name": source.name},
                )
                self.audit_trail.record_write_complete(
                    entry_id,
                    after_snapshot=source.to_dict(),
                )
            except Exception as exc:
                logger.warning("审计写入失败 (source=%s)", source_id, exc_info=True)
        return {"success": True, "source_id": source_id, "enabled": enabled}

    def is_enabled(self, source_id: str) -> bool:
        source = self._sources.get(source_id)
        if source is None:
            return False
        return source.enabled

    # ── 权限管理 ─────────────────────────────────────────

    def set_tier(self, source_id: str, tier: str, *, auto_persist: bool = True) -> dict[str, Any]:
        """设置来源的权限层级。"""
        source = self._sources.get(source_id)
        if source is None:
            return {"success": False, "reason": "not_found"}
        valid_tiers = set(_TIER_TO_PERMISSIONS.keys())
        if tier not in valid_tiers:
            return {"success": False, "reason": f"invalid_tier: {tier}, valid: {valid_tiers}"}
        previous = source.to_dict()
        source.tier = tier
        self._append_audit(
            source_id=source_id,
            operation="tier_change",
            detail=f"来源权限变更: {source.name} → tier={tier}",
            previous_state=previous,
            new_state=source.to_dict(),
        )
        if auto_persist:
            self._save_persisted()
        return {"success": True, "source_id": source_id, "tier": tier, "permissions": list(source.permissions)}

    def get_effective_permissions(self, source_id: str) -> set[str]:
        """获取某来源当前生效的权限集合。"""
        source = self._sources.get(source_id)
        if source is None or not source.enabled:
            return set()
        return source.permissions

    def has_permission(self, source_id: str, permission: str) -> bool:
        """检查某来源是否拥有指定权限。"""
        return permission in self.get_effective_permissions(source_id)

    # ── 健康追踪 ─────────────────────────────────────────

    def update_health(self, source_id: str, status: str, *, auto_persist: bool = True) -> dict[str, Any]:
        """更新来源的健康状态。"""
        source = self._sources.get(source_id)
        if source is None:
            return {"success": False, "reason": "not_found"}
        previous = source.to_dict()
        source.health_status = status
        source.last_health_check = datetime.now(timezone.utc).isoformat()
        self._append_audit(
            source_id=source_id,
            operation="health_update",
            detail=f"来源健康状态: {source.name} → {status}",
            previous_state=previous,
            new_state=source.to_dict(),
        )
        if auto_persist:
            self._save_persisted()
        return {"success": True, "source_id": source_id, "health_status": status}

    def list_healthy_sources(self, *, min_permissions: set[str] | None = None) -> list[dict[str, Any]]:
        """列出健康且启用的来源，可选按最低权限过滤。"""
        result = []
        for src in self._sources.values():
            if not src.enabled:
                continue
            if src.health_status == "down":
                continue
            if min_permissions:
                if not min_permissions.issubset(src.permissions):
                    continue
            result.append(src.to_dict())
        return result

    # ── Planner 集成支持 ─────────────────────────────────

    def get_enabled_source_ids(self) -> set[str]:
        """返回所有已启用的来源 ID 集合（planner 过滤用）。"""
        return {sid for sid, src in self._sources.items() if src.enabled}

    def get_disabled_source_ids(self) -> set[str]:
        """返回所有已禁用的来源 ID 集合（planner 过滤用）。"""
        return {sid for sid, src in self._sources.items() if not src.enabled}

    def is_source_available_for_action(self, source_id: str, action_id: str) -> bool:
        """planner 检查某来源是否能执行某动作。"""
        source = self._sources.get(source_id)
        if source is None or not source.enabled:
            return False
        if source.health_status == "down":
            return False
        return True

    # ── 审计 ─────────────────────────────────────────────

    def _append_audit(
        self,
        source_id: str,
        operation: str,
        detail: str,
        previous_state: dict[str, Any] | None = None,
        new_state: dict[str, Any] | None = None,
    ) -> None:
        entry = ExtensionAuditEntry(
            entry_id=uuid.uuid4().hex[:12],
            timestamp=datetime.now(timezone.utc).isoformat(),
            source_id=source_id,
            operation=operation,
            detail=detail,
            previous_state=previous_state,
            new_state=new_state,
        )
        self._audit_log.append(entry)
        logger.info("[审计] %s: %s", operation, detail)

    def get_audit_log(
        self,
        *,
        source_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        entries = self._audit_log
        if source_id:
            entries = [e for e in entries if e.source_id == source_id]
        return [
            {
                "entry_id": e.entry_id,
                "timestamp": e.timestamp,
                "source_id": e.source_id,
                "operation": e.operation,
                "detail": e.detail,
            }
            for e in entries[-limit:]
        ]

    # ── 回滚支持 ─────────────────────────────────────────

    def get_rollback_snapshot(self) -> dict[str, dict[str, Any]]:
        """返回当前所有来源的快照，用于回滚恢复。"""
        return {sid: src.to_dict() for sid, src in self._sources.items()}

    def restore_from_snapshot(self, snapshot: dict[str, dict[str, Any]]) -> None:
        """从快照恢复所有来源状态（回滚用）。"""
        if not isinstance(snapshot, dict):
            logger.warning("restore_from_snapshot: 无效快照类型 %s", type(snapshot))
            return
        restored = 0
        for source_id, data in snapshot.items():
            if not isinstance(data, dict):
                continue
            existing = self._sources.get(source_id)
            try:
                source = ExtensionSource(
                    source_id=source_id,
                    name=str(data.get("name", source_id)),
                    source_type=str(data.get("source_type", ExtensionSourceType.CUSTOM.value)),
                    trust_level=str(data.get("trust_level", "unknown")),
                    tier=str(data.get("tier", _DEFAULT_TIER)),
                    enabled=bool(data.get("enabled", True)),
                    health_status=str(data.get("health_status", "unknown")),
                    description=str(data.get("description", "")),
                    version=str(data.get("version", "1.0.0")),
                    extra=dict(data.get("extra", {})),
                )
                self._sources[source_id] = source
                restored += 1
            except Exception as exc:
                logger.warning("恢复来源 %s 失败: %s", source_id, exc)
        self._save_persisted()
        logger.info("从快照恢复了 %d 个来源", restored)
