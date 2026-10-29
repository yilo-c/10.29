# -*- coding: utf-8 -*-
"""AI 用量追踪与预算预警 (B1)。

记录每次 AI 调用的 token 用量与成本估算，支持：
- 按 provider/model/天的聚合统计
- 日预算上限（token 数 / USD 成本）配置
- 后台定时检查并在超出预算时通过 EventBus 推送 ``ai.usage_alert`` 事件
- 持久化到 ``data/ai_usage.db`` 的 ``usage_records`` 表（SQLite WAL）

线程安全：所有 SQLite 访问使用线程本地连接（与 ``data/sqlite_store.py`` 同模式），
避免 sqlite3 默认禁止跨线程共享 Connection 的限制。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from core.events import AppEvent

logger = logging.getLogger("siyi.ai.usage_tracker")

# ── 模型定价表（USD / 1K tokens）──
# C-38 记账修复（2026-09-12）：本表**只登记可核实的官方单价**。
# 不在表内的模型一律由 estimate_cost 返回 None（成本记 unknown），
# 绝不回落某个"看起来差不多"的默认单价——那会让内核链路的 cost
# 二重失真（provider/model 猜错 + 单价猜错）却看起来像有数。
#
# dsh 执行内核的实际默认模型（查 ~/.dsh/settings.yaml 顶层
# agent-default-model，2026-09-12 实测 = provider: bailian-qwen /
# model: qwen3.8-flash）走阿里云百炼 **Token Plan**（baseURL
# token-plan.<region>.maas.aliyuncs.com）——包月额度制、无公开
# per-token 单价可核实，故**故意不入表**：其成本按上面的规则记
# unknown，而不是按 deepseek-v4-flash 或任何近似价"假装有数"。
# 若将来 dsh 默认模型切到本表已有的 deepseek-v4-flash（官方 API
# 单价，行已存在），则该链路自动获得精确成本，无需再改代码。
PRICING_TABLE: dict[str, dict[str, float]] = {
    "deepseek-chat": {"prompt": 0.00014, "completion": 0.00028},
    "deepseek-reasoner": {"prompt": 0.00055, "completion": 0.00219},
    "deepseek-coder": {"prompt": 0.00014, "completion": 0.00028},
    "deepseek-v4-flash": {"prompt": 6e-05, "completion": 0.0003},
    "deepseek-v4-pro": {"prompt": 0.00028, "completion": 0.00056},
    "moonshot-v1-8k": {"prompt": 0.0017, "completion": 0.0017},
    "moonshot-v1-32k": {"prompt": 0.0034, "completion": 0.0034},
    "moonshot-v1-128k": {"prompt": 0.0085, "completion": 0.0085},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "qwen3-4b": {"prompt": 0.0, "completion": 0.0},
    "claude-sonnet-4.5": {"prompt": 0.003, "completion": 0.015},
    "claude-sonnet-4": {"prompt": 0.003, "completion": 0.015},
    "claude-haiku-4.5": {"prompt": 0.001, "completion": 0.005},
}

# C-38③：已移除 ``_DEFAULT_PRICING`` 回落单价。未知模型不再折算成本。

_BUDGET_SETTING_KEY = "ai_usage_budget"

# §9.5⑰（M1 尾 · 路由决策最小埋点）：chat 路由判定写入 usage_records 的固定标识。
# 决策标记不是模型调用，故 provider 故意取 'router'（既不含 'kernel%' 故不进入
# D20 观察窗的 kernel 样本，也不与任何真实厂商重名）；读数侧靠
# ``source LIKE 'route:%'`` 聚合算 fast_ratio（正源：scripts/route_report.py）。
ROUTER_PROVIDER = "router"
ROUTER_DECISION_MODEL = "decision"
ROUTE_SOURCE_PREFIX = "route:"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_records (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_estimate     REAL NOT NULL DEFAULT 0.0,
    success           INTEGER NOT NULL DEFAULT 1,
    -- A 项（观察期埋点 2026-08-20）：用量来源标识。
    -- 'shell'=命令/CLI 直接调用；'kernel:<name>'=执行内核（DSH 等）经网关执行。
    source            TEXT NOT NULL DEFAULT 'shell',
    -- C-38②（2026-09-12）：token 数是否为字符估算（1=估算，0=内核真实返回）。
    -- 下游 D20 观察窗可据 estimated=0 过滤出精确用量，避免把估算值当真实值判据。
    estimated         INTEGER NOT NULL DEFAULT 0,
    -- C-38③（2026-09-12）：成本定价是否命中 PRICING_TABLE。
    -- 'known'=cost_estimate 为按表折算值；'unknown'=模型无登记单价，
    -- cost_estimate 落 0.0 仅占位（不代表免费），读数须按 unknown 处理。
    pricing           TEXT NOT NULL DEFAULT 'known'
);
CREATE INDEX IF NOT EXISTS idx_usage_records_timestamp ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_records_provider ON usage_records(provider);
CREATE INDEX IF NOT EXISTS idx_usage_records_model ON usage_records(model);
-- P3.4 性能优化：get_stats(days, provider) 在 provider 不为空时
-- 执行 WHERE timestamp >= ? AND provider = ?，原有两个单列索引无法
-- 合并（SQLite 只能选其一），复合索引让两个过滤条件合并走单一索引。
CREATE INDEX IF NOT EXISTS idx_usage_records_provider_ts ON usage_records(provider, timestamp);
"""


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """根据模型与 token 数估算 USD 成本。

    C-38③（2026-09-12）：**未知模型返回 None**（成本 unknown），不再回落
    到 deepseek-chat 价格。此前回落会让内核（DSH 等）在 model 解析失真时
    再叠加一层"假单价"，形成 cost 二重失真——失真宁可显式 unknown。
    本地模型（如 qwen3-4b）价格在表中显式登记为 0，属"已知的零成本"，
    与"未知"区分开。
    """
    pricing = PRICING_TABLE.get(model)
    if pricing is None:
        return None
    cost = (
        prompt_tokens * pricing["prompt"] / 1000.0
        + completion_tokens * pricing["completion"] / 1000.0
    )
    return round(cost, 6)


def extract_provider_name(api_base: str) -> str:
    """从 api_base URL 提取 provider 友好名称。

    >>> extract_provider_name("https://api.deepseek.com/v1")
    'deepseek'
    >>> extract_provider_name("http://127.0.0.1:11434/v1")
    'local'
    """
    if not api_base:
        return "unknown"
    lowered = api_base.lower()
    if "api.deepseek.com" in lowered:
        return "deepseek"
    if "api.moonshot.cn" in lowered or "moonshot" in lowered:
        return "moonshot"
    if "api.openai.com" in lowered:
        return "openai"
    if "127.0.0.1" in lowered or "localhost" in lowered:
        return "local"
    try:
        host = urlparse(api_base).hostname
        return host or "unknown"
    except Exception:
        return "unknown"


def _ensure_column(conn: sqlite3.Connection, column: str, ddl: str) -> None:
    """为既有 usage_records 表补列（幂等；ADD COLUMN 不支持 IF NOT EXISTS）。

    零迁移兜底：老库缺列时 ALTER 补齐，重复列 / 并发下已被他人补上均吞掉。
    """
    try:
        conn.execute(f"ALTER TABLE usage_records ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as exc:  # noqa: BLE001 — duplicate column 等兼容
        if "duplicate column" not in str(exc).lower():
            logger.warning("ensure column %s failed: %s", column, exc)


@dataclass
class UsageRecord:
    """单次 AI 调用的用量记录。"""

    timestamp: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_estimate: Optional[float]
    success: bool = True
    source: str = "shell"
    estimated: bool = False
    pricing: str = "known"

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_estimate": self.cost_estimate,
            "success": self.success,
            "source": self.source,
            "estimated": self.estimated,
            "pricing": self.pricing,
        }


class UsageTracker:
    """AI 用量追踪器。

    线程安全：使用 ``threading.local`` 维护每线程 SQLite 连接，
    与 ``data/sqlite_store.py`` 同模式。
    """

    ALERT_INTERVAL_SECONDS = 300
    ALERT_EVENT_TYPE = "ai.usage_alert"

    def __init__(
        self, db_path: str, settings_repository: Any = None, event_bus: Any = None
    ) -> None:
        self.db_path = db_path
        self._settings_repository = settings_repository
        self._event_bus = event_bus
        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._all_conns: list[sqlite3.Connection] = []
        # 每日告警去重：{date: {"token": True, "cost": True}}
        self._alert_state: dict[str, dict[str, bool]] = {}
        self._alert_state_lock = threading.Lock()
        self._alert_task: Any = None

        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # A 项（观察期埋点）：为既有库补齐 source 列。ADD COLUMN 不支持
            # IF NOT EXISTS，用 try/except 吞掉 "duplicate column" 兼容性错误。
            _ensure_column(conn, "source", "TEXT NOT NULL DEFAULT 'shell'")
            # C-38②③（2026-09-12）：既有库补 estimated / pricing 列（同为加列，
            # 存量行按默认值回填：source=shell、estimated=0、pricing=known）。
            _ensure_column(conn, "estimated", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "pricing", "TEXT NOT NULL DEFAULT 'known'")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with self._conns_lock:
            self._all_conns.append(conn)
        self._local.conn = conn
        return conn

    def close_all(self) -> None:
        """关闭所有线程本地连接（测试 teardown 用）。"""
        with self._conns_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()
        self._local.conn = None

    def purge_before(self, cutoff_iso: str) -> int:
        """删除 timestamp 早于 ``cutoff_iso``（ISO 字符串）的全部记录。

        Args:
            cutoff_iso: ISO 格式时间戳（如 ``2026-07-01T00:00:00``）

        Returns:
            被删除的记录数。
        """
        if not cutoff_iso:
            return 0
        try:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM usage_records WHERE timestamp < ?",
                (cutoff_iso,),
            )
            deleted = cur.rowcount or 0
            conn.commit()
            logger.info("purged %d usage records older than %s", deleted, cutoff_iso)
            return deleted
        except Exception as exc:
            logger.warning("purge_before failed: %s", exc)
            return 0

    def record(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int | None = None,
        success: bool = True,
        source: str = "shell",
        estimated: bool | None = None,
        pricing: str | None = None,
    ) -> None:
        """记录一次 AI 调用的用量。

        Args:
            provider: provider 名称（deepseek / moonshot / openai / local / ...）
            model: 模型名
            prompt_tokens: 输入 token 数
            completion_tokens: 输出 token 数
            total_tokens: 总 token 数（None 时自动求和）
            success: 是否调用成功；失败时 cost_estimate 记为 0
            source: 用量来源标识（A 项观察期埋点）。
                ``'shell'``=命令/CLI 直接调用；
                ``'kernel:<name>'``=执行内核（DSH 等）经网关执行；
                ``'route:fast'`` / ``'route:deep'``=chat 路由决策标记（见
                :meth:`record_route_decision`，tokens/cost 恒为 0）。
            estimated: token 数是否为字符估算（C-38②）。True=估算，
                False=内核真实返回；None=调用方未声明，按"未估算"（0）处理
                ——保持既有 shell 直调调用方（provider client 用真实 usage）零变化。
            pricing: 定价口径覆盖（C-38③）。None=按 PRICING_TABLE 自动判定
                known/unknown；显式传 ``'known'`` 用于**本就不该有成本**的非模型
                记录（如路由决策标记），避免把 0 成本行误算进 unknown_cost_calls。

        成本口径（C-38③）：PRICING_TABLE 命中 → pricing='known' 且落折算成本；
        未命中 → pricing='unknown'、cost_estimate 占位 0.0（**不是免费**，读数端
        须按 unknown 过滤），绝不回落默认单价。
        """
        try:
            if total_tokens is None:
                total_tokens = prompt_tokens + completion_tokens
            if success:
                cost = estimate_cost(model, prompt_tokens, completion_tokens)
            else:
                cost = 0.0
            if pricing not in ("known", "unknown"):
                pricing = "known" if cost is not None else "unknown"
            cost_value = float(cost) if cost is not None else 0.0
            estimated_value = 0 if estimated in (None, False) else 1
            ts = datetime.now(timezone.utc).isoformat()
            conn = self._connect()
            params = (
                ts,
                provider or "unknown",
                model or "unknown",
                int(prompt_tokens),
                int(completion_tokens),
                int(total_tokens),
                cost_value,
                1 if success else 0,
                source or "shell",
                estimated_value,
                pricing,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO usage_records
                    (timestamp, provider, model, prompt_tokens, completion_tokens,
                     total_tokens, cost_estimate, success, source, estimated, pricing)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
            except sqlite3.OperationalError as exc:
                # 容错：既有库尚未含 source / estimated / pricing 列时自动补齐
                # 后再写（零迁移机制兜底）。SQLite 报错文案可能是 "no such column"
                # 或 "table ... has no column named ..."，两种情况都要覆盖。
                msg = str(exc).lower()
                if "no such column" in msg or "no column named" in msg:
                    try:
                        _ensure_column(conn, "source", "TEXT NOT NULL DEFAULT 'shell'")
                        _ensure_column(conn, "estimated", "INTEGER NOT NULL DEFAULT 0")
                        _ensure_column(conn, "pricing", "TEXT NOT NULL DEFAULT 'known'")
                        conn.execute(
                            """
                            INSERT INTO usage_records
                            (timestamp, provider, model, prompt_tokens, completion_tokens,
                             total_tokens, cost_estimate, success, source, estimated, pricing)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            params,
                        )
                    except sqlite3.OperationalError as exc2:
                        logger.warning("usage record failed (columns): %s", exc2, exc_info=True)
                else:
                    logger.warning("usage record failed: %s", exc, exc_info=True)
        except Exception as exc:
            logger.warning("usage record failed: %s", exc, exc_info=True)

    def record_route_decision(self, lane: str) -> None:
        """记录一次 chat 路由决策（§9.5⑰ M1 尾 · 最小埋点，让 fast_ratio 有数）。

        复用既有 ``usage_records`` 通道（**不新建表**），写一行决策标记：
        ``provider='router'`` / ``model='decision'`` / ``source='route:fast'|'route:deep'``
        / tokens=0 / cost=0 / success=1 / estimated=0 / pricing='known'。

        - ``lane`` 非 ``'fast'`` 一律落 ``'route:deep'``（快档=壳内管家，
          深档=执行内核网关；判错方向宁可记 deep，因为它才是成本侧）。
        - provider 不以 ``kernel`` 开头 → 不进入 route_report 的 kernel 样本，
          D20 观察窗（14 天 ≥20 次 kernel 成功）不被决策行污染。
        - pricing 显式 'known'：决策行本就零成本，不得混进 ``unknown_cost_calls``
          （该指标语义是"真实模型调用查不到单价"）。
        """
        normalized = "fast" if str(lane or "").strip().lower() == "fast" else "deep"
        self.record(
            provider=ROUTER_PROVIDER,
            model=ROUTER_DECISION_MODEL,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            success=True,
            source=f"{ROUTE_SOURCE_PREFIX}{normalized}",
            estimated=False,
            pricing="known",
        )

    def get_stats(self, days: int = 7, provider: str | None = None) -> dict[str, Any]:
        """返回最近 N 天的聚合统计。

        Returns:
            {
                "days": 7,
                "total_tokens": int,
                "total_cost": float,
                "total_calls": int,
                "by_day": [{"date": "2026-07-17", "tokens": int, "cost": float, "calls": int}, ...],
                "by_provider": [{"provider": str, "tokens": int, "cost": float, "calls": int}, ...],
                "by_model": [{"model": str, "tokens": int, "cost": float, "calls": int}, ...],
            }
        """
        days = max(1, int(days))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = self._connect()
        params = [cutoff]
        provider_clause = ""
        if provider:
            provider_clause = " AND provider = ?"
            params.append(provider)
        total_row = conn.execute(
            f"SELECT COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost, COUNT(*) AS calls FROM usage_records WHERE timestamp >= ?{provider_clause}",
            params,
        ).fetchone()
        by_day_rows = conn.execute(
            f"SELECT substr(timestamp, 1, 10) AS day, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost, COUNT(*) AS calls FROM usage_records WHERE timestamp >= ?{provider_clause} GROUP BY day ORDER BY day ASC",
            params,
        ).fetchall()
        by_provider_rows = conn.execute(
            f"SELECT provider, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost, COUNT(*) AS calls FROM usage_records WHERE timestamp >= ?{provider_clause} GROUP BY provider ORDER BY tokens DESC",
            params,
        ).fetchall()
        by_model_rows = conn.execute(
            f"SELECT model, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost, COUNT(*) AS calls FROM usage_records WHERE timestamp >= ?{provider_clause} GROUP BY model ORDER BY tokens DESC",
            params,
        ).fetchall()
        # C-38③：成本 unknown 的调用数（模型未登记单价，cost_estimate 仅占位）。
        # 供 D20 观察窗/用量面板判断"总成本被低估到什么程度"——绝不把 unknown
        # 混进 total_cost 冒充真实花费。
        unknown_cost_calls = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM usage_records WHERE timestamp >= ?{provider_clause} AND pricing = 'unknown'",
                params,
            ).fetchone()["c"]
            or 0
        )
        estimated_calls = int(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM usage_records WHERE timestamp >= ?{provider_clause} AND estimated = 1",
                params,
            ).fetchone()["c"]
            or 0
        )
        return {
            "days": days,
            "total_tokens": int(total_row["tokens"] or 0),
            "total_cost": round(float(total_row["cost"] or 0.0), 6),
            "total_calls": int(total_row["calls"] or 0),
            "unknown_cost_calls": unknown_cost_calls,
            "estimated_calls": estimated_calls,
            "by_day": [
                {
                    "date": r["day"],
                    "tokens": int(r["tokens"] or 0),
                    "cost": round(float(r["cost"] or 0.0), 6),
                    "calls": int(r["calls"] or 0),
                }
                for r in by_day_rows
            ],
            "by_provider": [
                {
                    "provider": r["provider"],
                    "tokens": int(r["tokens"] or 0),
                    "cost": round(float(r["cost"] or 0.0), 6),
                    "calls": int(r["calls"] or 0),
                }
                for r in by_provider_rows
            ],
            "by_model": [
                {
                    "model": r["model"],
                    "tokens": int(r["tokens"] or 0),
                    "cost": round(float(r["cost"] or 0.0), 6),
                    "calls": int(r["calls"] or 0),
                }
                for r in by_model_rows
            ],
            # A 项（观察期埋点）：按来源（shell / kernel:<name>）聚合，供用量面板
            # 区分命令直调与执行内核经网关的执行量。
            "by_source": [
                {
                    "source": r["source"],
                    "tokens": int(r["tokens"] or 0),
                    "cost": round(float(r["cost"] or 0.0), 6),
                    "calls": int(r["calls"] or 0),
                }
                for r in conn.execute(
                    f"SELECT source, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost, COUNT(*) AS calls FROM usage_records WHERE timestamp >= ?{provider_clause} GROUP BY source ORDER BY tokens DESC",
                    params,
                ).fetchall()
            ],
        }

    def get_records(
        self, limit: int = 100, offset: int = 0, provider: str | None = None
    ) -> list[dict[str, Any]]:
        """返回详细用量记录（用于日志视图），按时间倒序。"""
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))
        conn = self._connect()
        if provider:
            rows = conn.execute(
                "SELECT timestamp, provider, model, prompt_tokens, completion_tokens, total_tokens, cost_estimate, success, source, estimated, pricing FROM usage_records WHERE provider = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (provider, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT timestamp, provider, model, prompt_tokens, completion_tokens, total_tokens, cost_estimate, success, source, estimated, pricing FROM usage_records ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [
            {
                "timestamp": r["timestamp"],
                "provider": r["provider"],
                "model": r["model"],
                "prompt_tokens": int(r["prompt_tokens"]),
                "completion_tokens": int(r["completion_tokens"]),
                "total_tokens": int(r["total_tokens"]),
                "cost_estimate": round(float(r["cost_estimate"]), 6),
                "success": bool(r["success"]),
                "source": r["source"],
                "estimated": bool(r["estimated"]),
                "pricing": r["pricing"],
            }
            for r in rows
        ]

    def set_budget(
        self,
        daily_token_limit: int | None = None,
        daily_cost_limit: float | None = None,
    ) -> dict[str, Any]:
        """配置日预算上限。

        传 None 表示不修改对应字段。设为 0 或负数表示禁用该项限制。
        """
        budget = self.get_budget()
        if daily_token_limit is not None:
            budget["daily_token_limit"] = int(daily_token_limit)
        if daily_cost_limit is not None:
            budget["daily_cost_limit"] = round(float(daily_cost_limit), 6)
        self._persist_budget(budget)
        # 预算变更后重置当日告警去重状态，允许重新提醒
        with self._alert_state_lock:
            today = date.today().isoformat()
            self._alert_state.pop(today, None)
        return budget

    def get_budget(self) -> dict[str, Any]:
        """读取当前预算配置。"""
        if self._settings_repository is None:
            return {"daily_token_limit": None, "daily_cost_limit": None}
        try:
            stored = self._settings_repository.get_setting(_BUDGET_SETTING_KEY, None)
            if isinstance(stored, dict):
                return {
                    "daily_token_limit": stored.get("daily_token_limit"),
                    "daily_cost_limit": stored.get("daily_cost_limit"),
                }
        except Exception as exc:
            logger.debug("get_budget failed: %s", exc)
        return {"daily_token_limit": None, "daily_cost_limit": None}

    def _persist_budget(self, budget: dict[str, Any]) -> None:
        if self._settings_repository is None:
            return
        try:
            self._settings_repository.set_setting(_BUDGET_SETTING_KEY, budget)
        except Exception as exc:
            logger.warning("persist budget failed: %s", exc, exc_info=True)

    def check_budget(self) -> dict[str, Any]:
        """检查今日用量是否超过预算。

        Returns:
            {
                "exceeded": bool,         # 任一限制超额
                "token_exceeded": bool,
                "cost_exceeded": bool,
                "token_usage_pct": float, # 0~100+，未设限制时为 0
                "cost_usage_pct": float,
                "tokens_today": int,
                "cost_today": float,
                "token_limit": int | None,
                "cost_limit": float | None,
            }
        """
        budget = self.get_budget()
        token_limit = budget.get("daily_token_limit")
        cost_limit = budget.get("daily_cost_limit")
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        conn = self._connect()
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost_estimate), 0.0) AS cost FROM usage_records WHERE timestamp >= ?",
            (today_start,),
        ).fetchone()
        tokens_today = int(row["tokens"] or 0)
        cost_today = round(float(row["cost"] or 0.0), 6)
        token_pct = (
            tokens_today / token_limit * 100.0
            if token_limit and token_limit > 0
            else 0.0
        )
        cost_pct = (
            cost_today / cost_limit * 100.0
            if cost_limit and cost_limit > 0
            else 0.0
        )
        token_exceeded = bool(
            token_limit and token_limit > 0 and tokens_today >= token_limit
        )
        cost_exceeded = bool(cost_limit and cost_limit > 0 and cost_today >= cost_limit)
        return {
            "exceeded": token_exceeded or cost_exceeded,
            "token_exceeded": token_exceeded,
            "cost_exceeded": cost_exceeded,
            "token_usage_pct": round(token_pct, 2),
            "cost_usage_pct": round(cost_pct, 2),
            "tokens_today": tokens_today,
            "cost_today": cost_today,
            "token_limit": token_limit,
            "cost_limit": cost_limit,
        }

    async def check_and_alert(self) -> dict[str, Any] | None:
        """执行一次预算检查，超限时通过 EventBus 推送 ``ai.usage_alert`` 事件。

        每个告警类型（token / cost）每天最多推送一次。
        无事件总线或未超限时返回 None。
        """
        status = self.check_budget()
        if not status["exceeded"]:
            return None
        today = date.today().isoformat()
        with self._alert_state_lock:
            already = self._alert_state.setdefault(today, {})
        events_to_emit = []
        if status["token_exceeded"] and not already.get("token"):
            events_to_emit.append(
                {
                    "alert_type": "token",
                    "tokens_today": status["tokens_today"],
                    "token_limit": status["token_limit"],
                    "usage_pct": status["token_usage_pct"],
                }
            )
        if status["cost_exceeded"] and not already.get("cost"):
            events_to_emit.append(
                {
                    "alert_type": "cost",
                    "cost_today": status["cost_today"],
                    "cost_limit": status["cost_limit"],
                    "usage_pct": status["cost_usage_pct"],
                }
            )
        if not events_to_emit:
            return None
        if self._event_bus is None:
            return None
        for evt in events_to_emit:
            try:
                self._event_bus.emit(
                    AppEvent(event_type=self.ALERT_EVENT_TYPE, payload=evt)
                )
            except Exception as exc:
                logger.warning("emit usage_alert failed: %s", exc, exc_info=True)
        with self._alert_state_lock:
            for evt in events_to_emit:
                self._alert_state[today][evt["alert_type"]] = True
        return {"emitted": events_to_emit, "status": status}

    async def _alert_loop(self) -> None:
        """后台循环：每 ALERT_INTERVAL_SECONDS 秒检查一次预算。"""
        logger.info(
            "usage tracker alert loop started (interval=%ss)",
            self.ALERT_INTERVAL_SECONDS,
        )
        while True:
            try:
                await asyncio.sleep(self.ALERT_INTERVAL_SECONDS)
                await self.check_and_alert()
            except asyncio.CancelledError:
                logger.info("usage tracker alert loop cancelled")
                return
            except Exception as exc:
                logger.warning("usage alert loop error: %s", exc, exc_info=True)

    def start_alert_loop(self) -> None:
        """启动后台告警循环。需在事件循环内调用。"""
        if self._alert_task is not None and not self._alert_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("start_alert_loop skipped: no running event loop")
            return
        self._alert_task = loop.create_task(self._alert_loop())

    async def stop_alert_loop(self) -> None:
        """停止后台告警循环。"""
        if self._alert_task is None:
            return
        self._alert_task.cancel()
        try:
            await self._alert_task
        except asyncio.CancelledError:
            pass
        self._alert_task: Any = None


# ── 全局单例 ──

_tracker_singleton: Optional[UsageTracker] = None
_tracker_lock = threading.Lock()


def get_usage_tracker() -> Optional[UsageTracker]:
    """返回全局 UsageTracker 单例（若已初始化），否则返回 None。"""
    return _tracker_singleton


def init_usage_tracker(
    db_path: str, settings_repository: Any = None, event_bus: Any = None
) -> UsageTracker:
    """初始化全局 UsageTracker 单例。

    幂等：重复调用会更新 settings_repository / event_bus 引用但保留 db_path。
    """
    global _tracker_singleton
    with _tracker_lock:
        if _tracker_singleton is None:
            _tracker_singleton = UsageTracker(
                db_path,
                settings_repository=settings_repository,
                event_bus=event_bus,
            )
        else:
            if settings_repository is not None:
                _tracker_singleton._settings_repository = settings_repository
            if event_bus is not None:
                _tracker_singleton._event_bus = event_bus
        return _tracker_singleton


def init_usage_tracker_from_context(context: Any) -> UsageTracker:
    """从 AppContext 初始化全局 UsageTracker。

    db_path 派生自 ``context.local_data_store.base_dir``，与
    ``local_storage.db`` 同目录。
    """
    data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
    if not data_dir:
        data_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, "ai_usage.db")
    settings_repo = getattr(context, "settings_repository", None)
    event_bus = getattr(context, "event_bus", None)
    if event_bus is None:
        from core.events import event_bus as _main_event_bus

        event_bus = _main_event_bus
    if event_bus is not None and not hasattr(event_bus, "emit"):
        logger.warning("event_bus has no emit method, alerts will be disabled")
        event_bus = None
    return init_usage_tracker(
        db_path,
        settings_repository=settings_repo,
        event_bus=event_bus,
    )


def reset_usage_tracker_for_tests() -> None:
    """测试钩子：清空全局单例并关闭所有连接。"""
    global _tracker_singleton
    with _tracker_lock:
        if _tracker_singleton is not None:
            try:
                _tracker_singleton.close_all()
            except Exception:
                pass
        _tracker_singleton = None


__all__ = [
    "UsageRecord",
    "UsageTracker",
    "PRICING_TABLE",
    "ROUTER_PROVIDER",
    "ROUTER_DECISION_MODEL",
    "ROUTE_SOURCE_PREFIX",
    "estimate_cost",
    "extract_provider_name",
    "get_usage_tracker",
    "init_usage_tracker",
    "init_usage_tracker_from_context",
    "reset_usage_tracker_for_tests",
]
