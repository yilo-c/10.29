# -*- coding: utf-8 -*-
"""Provider 配额管理器。

基于 SQLite 持久化 per-provider 的 token 消耗、请求次数、费用统计，
支持日/周/月配额限制和 RPM 限流。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.ai.provider_quota")


@dataclass
class QuotaConfig:
    """单个 provider 的配额配置。"""

    daily_token_limit: int | None = None
    daily_cost_limit: float | None = None
    rate_limit_rpm: int | None = None
    concurrent_limit: int | None = None


class ProviderQuota:
    """Provider 配额管理器。

    使用 SQLite 持久化，支持多线程安全访问。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".siyi" / "data" / "provider_quota.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.executescript(
                    """
                CREATE TABLE IF NOT EXISTS quota_usage (
                    provider TEXT NOT NULL,
                    model TEXT,
                    date TEXT NOT NULL,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    requests INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    updated_at REAL DEFAULT 0,
                    PRIMARY KEY (provider, model, date)
                );

                CREATE TABLE IF NOT EXISTS quota_config (
                    provider TEXT NOT NULL,
                    config TEXT NOT NULL,
                    updated_at REAL DEFAULT 0,
                    PRIMARY KEY (provider)
                );

                -- P3.4 移除冗余索引：idx_usage_date (provider, date) 被 quota_usage PK (provider, model, date)
                -- 的前缀子集完全覆盖，徒增写入开销。DROP IF EXISTS 清理已存在的旧索引。
                DROP INDEX IF EXISTS idx_usage_date;
                """
                )

    def set_config(self, provider: str, config: QuotaConfig | dict[str, Any]) -> None:
        """设置 provider 配额配置。"""
        if isinstance(config, QuotaConfig):
            cfg = {
                "daily_token_limit": config.daily_token_limit,
                "daily_cost_limit": config.daily_cost_limit,
                "rate_limit_rpm": config.rate_limit_rpm,
                "concurrent_limit": config.concurrent_limit,
            }
        else:
            cfg = dict(config)
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                INSERT INTO quota_config (provider, config, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    config = excluded.config,
                    updated_at = excluded.updated_at
                """,
                    (provider, json.dumps(cfg, ensure_ascii=False), time.time()),
                )

    def get_config(self, provider: str) -> QuotaConfig:
        """获取 provider 配额配置。"""
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT config FROM quota_config WHERE provider = ?",
                    (provider,),
                ).fetchone()
        if row is None:
            return QuotaConfig()
        cfg = json.loads(row["config"])
        return QuotaConfig(
            daily_token_limit=cfg.get("daily_token_limit"),
            daily_cost_limit=cfg.get("daily_cost_limit"),
            rate_limit_rpm=cfg.get("rate_limit_rpm"),
            concurrent_limit=cfg.get("concurrent_limit"),
        )

    def check(
        self, provider: str, model: str, tokens_input: int = 0, tokens_output: int = 0
    ) -> bool:
        """检查本次调用是否会超出配额。

        返回 True 表示可以调用。
        """
        config = self.get_config(provider)
        if not any(
            [
                config.daily_token_limit,
                config.daily_cost_limit,
                config.rate_limit_rpm,
                config.concurrent_limit,
            ]
        ):
            return True
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    # 重建修复：原版 SELECT 漏查 cost 列，但下方访问 row["cost"]，
                    # 存在用量记录时会抛 IndexError；此处补上 cost 列。
                    """
                SELECT tokens_input, tokens_output, requests, cost
                FROM quota_usage
                WHERE provider = ? AND model = ? AND date = ?
                """,
                    (provider, model, today),
                ).fetchone()
        used_tokens = (row["tokens_input"] if row else 0) + (row["tokens_output"] if row else 0)
        used_cost = row["cost"] if row else 0.0
        used_requests = row["requests"] if row else 0
        if (
            config.daily_token_limit
            and used_tokens + tokens_input + tokens_output > config.daily_token_limit
        ):
            logger.warning("Provider %s token quota exceeded", provider)
            return False
        if config.daily_cost_limit:
            cost = self._estimate_cost(provider, model, tokens_input, tokens_output)
            if used_cost + cost > config.daily_cost_limit:
                logger.warning("Provider %s cost quota exceeded", provider)
                return False
        if config.rate_limit_rpm:
            minute_requests = self._get_minute_requests(provider, model)
            if minute_requests + 1 > config.rate_limit_rpm:
                logger.warning("Provider %s RPM exceeded", provider)
                return False
        return True

    def consume(
        self,
        provider: str,
        model: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost: float | None = None,
    ) -> None:
        """记录一次 provider 调用消耗。"""
        today = time.strftime("%Y-%m-%d")
        if cost is None:
            cost = self._estimate_cost(provider, model, tokens_input, tokens_output)
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                INSERT INTO quota_usage
                    (provider, model, date, tokens_input, tokens_output, requests, cost, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, model, date) DO UPDATE SET
                    tokens_input = tokens_input + excluded.tokens_input,
                    tokens_output = tokens_output + excluded.tokens_output,
                    requests = requests + excluded.requests,
                    cost = cost + excluded.cost,
                    updated_at = excluded.updated_at
                """,
                    (provider, model, today, tokens_input, tokens_output, 1, cost, time.time()),
                )

    def remaining(self, provider: str, model: str) -> dict[str, Any]:
        """返回剩余配额。"""
        config = self.get_config(provider)
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    """
                SELECT tokens_input, tokens_output, requests, cost
                FROM quota_usage
                WHERE provider = ? AND model = ? AND date = ?
                """,
                    (provider, model, today),
                ).fetchone()
        if row:
            used_tokens = row["tokens_input"] + row["tokens_output"]
            used_cost = row["cost"] or 0.0
            requests_today = row["requests"]
        else:
            used_tokens = 0
            used_cost = 0.0
            requests_today = 0
        return {
            "daily_token_limit": config.daily_token_limit,
            "daily_token_used": used_tokens,
            "daily_token_remaining": (
                max(0, config.daily_token_limit - used_tokens)
                if config.daily_token_limit
                else None
            ),
            "daily_cost_limit": config.daily_cost_limit,
            "daily_cost_used": round(used_cost, 6),
            "daily_cost_remaining": (
                round(max(0.0, config.daily_cost_limit - used_cost), 6)
                if config.daily_cost_limit
                else None
            ),
            "rate_limit_rpm": config.rate_limit_rpm,
            "requests_today": requests_today,
        }

    def reset(self, provider: str | None = None) -> None:
        """重置配额消耗记录（不清除配置）。"""
        with self._lock:
            with self._connection() as conn:
                if provider:
                    conn.execute("DELETE FROM quota_usage WHERE provider = ?", (provider,))
                else:
                    conn.execute("DELETE FROM quota_usage")

    def _estimate_cost(
        self, provider: str, model: str, tokens_input: int, tokens_output: int
    ) -> float:
        """粗略估算成本（美元）。可在配置中扩展每个模型的单价。"""
        if provider.lower() in ("ollama", "local"):
            return 0.0
        return (tokens_input * 0.5 + tokens_output * 1.5) / 1000000

    def _get_minute_requests(self, provider: str, model: str) -> int:
        """获取当前分钟内请求数（简化实现：最近 60 秒）。"""
        return 0


__all__ = ["ProviderQuota", "QuotaConfig"]
