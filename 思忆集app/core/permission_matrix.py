"""权限矩阵：按动作 鈫?风险等级 鈫?审批策略 鈫?棰戞?闄愰? 鈫?外部系统闅旂?。?
三级任务分类锛?- readonly: 鍙??任务全自鍔?紙妫?索新闂汇?佹眹总日蹇椼?佺敓成建璁?級
- writable: 鍙?啓任务半自鍔?紙写笔记草绋裤?佸垱建待办，先确认再落库锛?- high_risk: 楂橀?险任务强制人工确认（删除、批量改鍐欍?佸?外发送）

每个外部系统单独授权、单鐙?Token、最小权闄愩??每个能力单独寮?鍏炽??增加时间鍜岄?次限鍒躲??楂橀?险动作必须二次确璁ゃ??"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("siyi.permission_matrix")

TIER_READONLY = "readonly"
TIER_WRITABLE = "writable"
TIER_HIGH_RISK = "high_risk"

APPROVAL_AUTO = "auto"
APPROVAL_SEMI_AUTO = "semi_auto"
APPROVAL_MANUAL_CONFIRM = "manual_confirm"


@dataclass
class PermissionEntry:
    action_id: str
    risk_tier: str = TIER_READONLY
    approval_strategy: str = APPROVAL_AUTO
    rate_limit_per_day: int = 0
    enabled: bool = True
    external_system: str = ""
    required_scopes: list[str] = field(default_factory=list)
    description: str = ""


DEFAULT_PERMISSION_MATRIX: list[PermissionEntry] = [
    # 鈹?鈹? 鍙??任务：全鑷?姩 鈹?鈹?
    PermissionEntry(
        action_id="news.lookup",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=50,
        description="Access Google News RSS via search and fetch",
        external_system="google_news",
    ),
    PermissionEntry(
        action_id="usage.snapshot",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=30,
        description="杞?欢使用蹇?収（本鍦?tasklist锛?",
    ),
    PermissionEntry(
        action_id="upgrade.check",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=5,
        description="妫?查升级建璁?",
    ),
    PermissionEntry(
        action_id="notes.organize",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=20,
        description="笔记?分类统计?（只读汇总）",
    ),
    # 鈹?鈹? 鍙?啓任务：半鑷?姩 鈹?鈹?
    PermissionEntry(
        action_id="journal.daily_draft",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=10,
        description="生成日志鑽夌?（写笔记?+收件箱）",
    ),
    PermissionEntry(
        action_id="notes.sync",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=20,
        description="笔记?双向同步?",
        external_system="automation_hub",
        required_scopes=["notes:read", "notes:write"],
    ),
    PermissionEntry(
        action_id="capture",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="收件箱收褰?",
    ),
    PermissionEntry(
        action_id="tasks",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="待办管理",
    ),
    PermissionEntry(
        action_id="notes",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="笔记?管理",
    ),
    PermissionEntry(
        action_id="translate",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=50,
        description="翻译（调外部 API锛?",
        external_system="youdao",
        required_scopes=["translate:use"],
    ),
    # 鈹?鈹? 楂橀?险任务：强制人工纭?? 鈹?鈹?
    PermissionEntry(
        action_id="settings",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=10,
        description="配置淇?敼入口",
    ),
    PermissionEntry(
        action_id="self_optimize",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=5,
        description="鑷?垜优化鎻愭?",
    ),
    PermissionEntry(
        action_id="organize_current",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=10,
        description="批量整理当前数据",
    ),
    PermissionEntry(
        action_id="data.delete",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=5,
        description="数据删除（需浜屾?纭??码）",
    ),
    PermissionEntry(
        action_id="data.batch_rewrite",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=3,
        description="批量改写（需浜屾?纭??码）",
    ),
    PermissionEntry(
        action_id="external.send",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=5,
        description="瀵瑰?鍙戦?侊紙閭?欢、消鎭?瓑锛?",
    ),
    # 鈹?鈹? 外部连接鍣?鈹?鈹?
    PermissionEntry(
        action_id="browser.open",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=100,
        description="打开娴忚?鍣ㄨ?闂?綉鍧?或搜绱?",
    ),
    PermissionEntry(
        action_id="file.read",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=100,
        description="读取鏈?湴文件或列出目褰?",
    ),
    PermissionEntry(
        action_id="file.write",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=50,
        description="向本地文件写入内瀹?",
    ),
    PermissionEntry(
        action_id="clipboard.read",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=50,
        description="读取系统鍓?创鏉?",
    ),
    PermissionEntry(
        action_id="clipboard.write",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="向系统剪贴板写入",
    ),
    PermissionEntry(
        action_id="app.launch",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="鍚?姩鏈?湴应用程序",
    ),
    PermissionEntry(
        action_id="shell.run",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="执行?系统命令",
    ),
    PermissionEntry(
        action_id="web.download",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="下载网页文件到本鍦?",
    ),
    PermissionEntry(
        action_id="process.list",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=50,
        description="列出当前杩愯?的进绋?",
    ),
    PermissionEntry(
        action_id="process.kill",
        risk_tier=TIER_HIGH_RISK,
        approval_strategy=APPROVAL_MANUAL_CONFIRM,
        rate_limit_per_day=10,
        description="结束指定进程",
    ),
    PermissionEntry(
        action_id="system.info",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=50,
        description="获取系统信息（CPU/内存/磁盘/网络锛?",
    ),
    PermissionEntry(
        action_id="cron.list",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=100,
        description="查看桌面绔?畾时任鍔?",
    ),
    PermissionEntry(
        action_id="cron.add",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="鏂板?桌面绔?畾时任鍔?",
    ),
    PermissionEntry(
        action_id="cron.remove",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=30,
        description="删除桌面绔?畾时任鍔?",
    ),
    PermissionEntry(
        action_id="mcp.tools.list",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=100,
        description="列出 MCP 工具鐩?綍",
        external_system="mcp",
    ),
    PermissionEntry(
        action_id="mcp.tool.call",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=50,
        description="调用 MCP 外部工具",
        external_system="mcp",
    ),
    PermissionEntry(
        action_id="subagent.list",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=100,
        description="查看后台子代理运琛岃?褰?",
    ),
    PermissionEntry(
        action_id="subagent.get",
        risk_tier=TIER_READONLY,
        approval_strategy=APPROVAL_AUTO,
        rate_limit_per_day=100,
        description="查看单个后台子代鐞嗚?鎯?",
    ),
    PermissionEntry(
        action_id="subagent.spawn",
        risk_tier=TIER_WRITABLE,
        approval_strategy=APPROVAL_SEMI_AUTO,
        rate_limit_per_day=20,
        description="鍚?姩后台子代理任鍔?",
    ),
]


class PermissionMatrix:
    """Permission matrix for access control."""

    def __init__(self, data_store, observability_service=None):
        self.data_store = data_store
        self.observability_service = observability_service

    # 鈹?鈹? 矩阵读写 鈹?鈹?

    def get_matrix(self) -> list[dict[str, Any]]:
        saved = self.data_store.load_json("permission_matrix", None)
        if isinstance(saved, list) and saved:
            return saved
        return [self._entry_to_dict(entry) for entry in DEFAULT_PERMISSION_MATRIX]

    def save_matrix(self, matrix: list[dict[str, Any]]) -> None:
        self.data_store.save_json("permission_matrix", matrix)

    def get_entry(self, action_id: str) -> dict[str, Any] | None:
        for entry in self.get_matrix():
            if entry.get("action_id") == action_id:
                return entry
        return None

    def update_entry(self, action_id: str, updates: dict[str, Any]) -> bool:
        matrix = self.get_matrix()
        for entry in matrix:
            if entry.get("action_id") == action_id:
                for key in ("risk_tier", "approval_strategy", "rate_limit_per_day", "enabled", "description"):
                    if key in updates:
                        entry[key] = updates[key]
                self.save_matrix(matrix)
                return True
        return False

    # 鈹?鈹? 权限妫?鏌?鈹?鈹?

    def check_permission(self, action_id: str, *, source: str = "assistant") -> dict[str, Any]:
        entry = self.get_entry(action_id)
        if entry is None:
            return {
                "allowed": True,
                "reason": "no_matrix_entry",
                "risk_tier": TIER_READONLY,
                "approval_strategy": APPROVAL_AUTO,
            }

        if not entry.get("enabled", True):
            self._track("permission.disabled", action_id, source)
            return {
                "allowed": False,
                "reason": "action_disabled",
                "risk_tier": entry.get("risk_tier", TIER_READONLY),
                "approval_strategy": entry.get("approval_strategy", APPROVAL_AUTO),
            }

        rate_limit = int(entry.get("rate_limit_per_day") or 0)
        if rate_limit > 0:
            used = self._get_today_usage(action_id)
            if used >= rate_limit:
                self._track("permission.rate_limited", action_id, source,
                            extra={"used": used, "limit": rate_limit})
                return {
                    "allowed": False,
                    "reason": "rate_limit_exceeded",
                    "used": used,
                    "limit": rate_limit,
                    "risk_tier": entry.get("risk_tier", TIER_READONLY),
                    "approval_strategy": entry.get("approval_strategy", APPROVAL_AUTO),
                }

        risk_tier = entry.get("risk_tier", TIER_READONLY)
        approval = entry.get("approval_strategy", APPROVAL_AUTO)

        return {
            "allowed": True,
            "reason": "ok",
            "risk_tier": risk_tier,
            "approval_strategy": approval,
            "requires_confirmation": approval == APPROVAL_MANUAL_CONFIRM,
            "requires_review": approval == APPROVAL_SEMI_AUTO,
        }

    def consume_quota(self, action_id: str) -> dict[str, Any]:
        entry = self.get_entry(action_id)
        rate_limit = int((entry or {}).get("rate_limit_per_day") or 0)
        used = self._consume_today_usage(action_id)
        remaining = max(0, rate_limit - used) if rate_limit > 0 else -1
        return {"action_id": action_id, "used": used, "remaining": remaining}

    def get_remaining_quota(self, action_id: str) -> int:
        entry = self.get_entry(action_id)
        rate_limit = int((entry or {}).get("rate_limit_per_day") or 0)
        if rate_limit <= 0:
            return -1
        used = self._get_today_usage(action_id)
        return max(0, rate_limit - used)

    def reset_daily_quotas(self) -> None:
        self.data_store.save_json("permission_quotas", {"date": "", "usage": {}})

    # 鈹?鈹? 閰嶉?内部 鈹?鈹?

    def _get_quota_store(self) -> dict[str, Any]:
        today = datetime.now().strftime("%Y-%m-%d")
        holder: dict[str, Any] = {}

        def _updater(store: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(store) if isinstance(store, dict) else {}
            if normalized.get("date") != today:
                normalized = {"date": today, "usage": {}}
            else:
                normalized["usage"] = dict(normalized.get("usage") or {})
            holder["value"] = normalized
            return normalized

        self.data_store.update_json("permission_quotas", {}, _updater)
        return holder.get("value") or {"date": today, "usage": {}}

    def _get_today_usage(self, action_id: str) -> int:
        store = self._get_quota_store()
        return int(store.get("usage", {}).get(action_id) or 0)

    def _consume_today_usage(self, action_id: str) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        holder: dict[str, int] = {}

        def _updater(store: dict[str, Any]) -> dict[str, Any]:
            normalized = dict(store) if isinstance(store, dict) else {}
            if normalized.get("date") != today:
                normalized = {"date": today, "usage": {}}
            usage = dict(normalized.get("usage") or {})
            current = int(usage.get(action_id) or 0) + 1
            usage[action_id] = current
            normalized["usage"] = usage
            holder["value"] = current
            return normalized

        self.data_store.update_json("permission_quotas", {}, _updater)
        return holder.get("value", 0)

    def _track(self, event: str, action_id: str, source: str, extra: dict[str, Any] | None = None) -> None:
        if self.observability_service is None:
            return
        try:
            from core.observability import SEVERITY_DEGRADED
            payload = {"action_id": action_id, "source": source}
            if extra:
                payload.update(extra)
            self.observability_service.track_event(
                f"assistant.{event}", severity=SEVERITY_DEGRADED, payload=payload
            )
        except Exception:
            logger.warning("failed to track permission matrix event")

    @staticmethod
    def _entry_to_dict(entry: PermissionEntry) -> dict[str, Any]:
        return {
            "action_id": entry.action_id,
            "risk_tier": entry.risk_tier,
            "approval_strategy": entry.approval_strategy,
            "rate_limit_per_day": entry.rate_limit_per_day,
            "enabled": entry.enabled,
            "external_system": entry.external_system,
            "required_scopes": list(entry.required_scopes),
            "description": entry.description,
        }
