"""ConfirmationStore — 用户确认请求的跨进程共享状态存储（M2a，契约 §4.2）。

设计要点
========
* 纯状态管理，不执行任何动作（动作执行留给调用方：butler 的 resolve_confirmation
  或内核轮询后重新下发）。
* 以 ``<data_dir>/confirmations.json`` 为文件后端，因此后端进程（``confirmation.resolve``
  经 _confirmations handler）与内核子进程（``siyi_confirm_request`` 经 MCP）可共享同一队列。
* 进程内单例（``get_confirmation_store``）：同进程复用同一实例；跨进程经文件对齐。

条目形状（与 butler 现状兼容 + 扩展）
====================================
``{confirmation_id, action_id, args, args_sha256, status, source, title, detail, created_at, expires_at}``
（M2.1 SEC-1：``args_sha256`` 为参数指纹，审批批准时绑定校验，防换参数重放）
* ``status`` ∈ pending / approved / rejected / expired
* ``source`` ∈ butler / mcp:<kernel> / tool:<name>
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from core import clock
from data._atomic_json import atomic_write_json, quarantine_corrupt_file

logger = logging.getLogger("siyi.confirmation_store")

_DEFAULT_TTL_SECONDS = 300.0


def _is_expired(entry: dict[str, Any], now: float) -> bool:
    """统一 TTL 判据（含墙钟倒退检测）——判据本体在 :func:`core.clock.deadline_expired`。

    P0-1 治本（2026-09-18，env-drill T2-D）：本函数只做条目字段适配，不再自带
    一套判据——否则同一病灶会在 cron / task_queue 等处继续各写一遍。

    此处**不用** ``time.monotonic()``：条目持久化到 ``confirmations.json`` 且跨进程
    共享（后端进程 ↔ 内核子进程），monotonic 在进程重启后归零，无法承载 TTL 语义；
    故采用"墙钟 + ``created_at`` 倒退检测"双轨护栏。
    """
    return clock.deadline_expired(
        now,
        entry.get("expires_at"),
        reference=entry.get("created_at"),
    )


def compute_args_sha256(args: Optional[dict[str, Any]]) -> str:
    """SEC-1：审批条目参数指纹（sort_keys 稳定序列化 → sha256 hex）。"""
    return hashlib.sha256(json.dumps(args or {}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _resolve_default_data_dir() -> str:
    # 优先 env（测试隔离用），否则与 data.local_storage.LocalDataStore 默认生产数据目录一致：<app>/data/data
    env = os.environ.get("SIYI_DATA_DIR")
    if env:
        return env
    here = Path(__file__).resolve()
    app_root = here.parents[1]  # services/confirmation_store.py -> 思忆集app
    return str(app_root / "data" / "data")


class ConfirmationStore:
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or _resolve_default_data_dir()
        self.path = Path(self.data_dir) / "confirmations.json"
        self._lock = threading.Lock()
        # 容错三件套·③：损坏文件隔离失败时为 True，_save 拒写保留现场。
        self._write_blocked = False

    # ── 持久化（文件后端，跨进程共享）─────────────────────────────
    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except Exception as exc:
            # 容错三件套：损坏的 confirmations.json 先 os.rename 为
            # <name>.corrupt-<ts> 保全现场（①）；改名成功以空队列续用（②）；
            # 改名失败则封锁 _save 拒写并 error 告警（③），禁 warning-only 覆写。
            quarantined = quarantine_corrupt_file(str(self.path))
            if quarantined:
                logger.error("confirmations.json 损坏，已隔离保全至 %s：%s", quarantined, exc)
                self._write_blocked = False
            else:
                logger.error("confirmations.json 损坏且隔离失败，封锁写入以保留现场：%s", exc)
                self._write_blocked = True
            return {}
        return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        if self._write_blocked:
            msg = "confirmations.json 损坏现场未能隔离：拒绝覆写（待人工处理）"
            logger.error(msg)
            raise RuntimeError(msg)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(str(self.path), data, indent=2)

    # ── 公共 API（契约 §4.2）─────────────────────────────────────
    def create(
        self,
        action_id: str,
        args: dict[str, Any],
        *,
        source: str,
        title: str = "",
        detail: str = "",
        ttl_s: float = _DEFAULT_TTL_SECONDS,
    ) -> str:
        confirmation_id = uuid.uuid4().hex
        now = clock.wall_ts()
        entry = {
            "confirmation_id": confirmation_id,
            "action_id": action_id,
            "args": dict(args or {}),
            "args_sha256": compute_args_sha256(args),
            "status": "pending",
            "source": source,
            "title": title or action_id,
            "detail": detail,
            "created_at": now,
            "expires_at": now + ttl_s,
        }
        with self._lock:
            data = self._load()
            data[confirmation_id] = entry
            self._save(data)
        return confirmation_id

    def resolve(
        self,
        confirmation_id: str,
        approved: bool,
        reason: str = "",
        args_sha256: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """一次性（非 pending 返回 None）。翻转 status 后返回条目。

        ``reason``（可选，缺省空串，向后兼容既有调用）：审批理由，批准/拒绝时
        写入条目（审计标记，status 查询与 list 均可回显）。

        M2.1 锁内三查（SEC-1 + QUA-6，与 ``get()`` 同款 TTL 判定）：
        ① 非 pending 拒（一次性消费，防重放）；② 已过 ``expires_at`` 拒且翻
        expired；③ 调用方传入 ``args_sha256``（可选，缺省 None=不校验，向后
        兼容既有调用）与条目指纹不符拒——批准必须绑定当初预览的参数指纹。
        """
        with self._lock:
            data = self._load()
            entry = data.get(confirmation_id)
            if entry is None or entry["status"] != "pending":
                return None
            expires_at = entry.get("expires_at")
            if expires_at is None:
                # QUA-6/E4：旧队列/伪造条目缺 expires_at → 保守拒批（不得无限期批准），
                # 优雅返回 None 而非抛 KeyError。
                logger.warning("confirmation %s missing expires_at; approval rejected", confirmation_id)
                return None
            if _is_expired(entry, clock.wall_ts()):
                entry["status"] = "expired"
                self._save(data)
                logger.warning(
                    "confirmation %s expired before resolve; approval rejected (created_at=%s expires_at=%s)",
                    confirmation_id,
                    entry.get("created_at"),
                    expires_at,
                )
                return None
            if args_sha256 is not None and entry.get("args_sha256") != args_sha256:
                logger.warning(
                    "confirmation %s args_sha256 mismatch (expected=%s got=%s); approval rejected",
                    confirmation_id,
                    entry.get("args_sha256"),
                    args_sha256,
                )
                return None
            entry["status"] = "approved" if approved else "rejected"
            if reason:
                entry["reason"] = str(reason)
            self._save(data)
            return entry

    def set_result(self, confirmation_id: str, result: Any) -> None:
        """回填执行结果（butler 批准后执行动作的结果），供 status 查询回显。"""
        with self._lock:
            data = self._load()
            entry = data.get(confirmation_id)
            if entry is None:
                return
            entry["result"] = result
            self._save(data)

    def get(self, confirmation_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            data = self._load()
            entry = data.get(confirmation_id)
            if entry is None:
                return None
            if entry["status"] == "pending" and _is_expired(entry, clock.wall_ts()):
                entry["status"] = "expired"
                self._save(data)
            return entry

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
            now = clock.wall_ts()
            changed = False
            for e in data.values():
                if e["status"] == "pending" and _is_expired(e, now):
                    e["status"] = "expired"
                    changed = True
            if changed:
                self._save(data)
            items = list(data.values())
        items.sort(key=lambda e: (0 if e["status"] == "pending" else 1, e["created_at"]))
        return items

    def purge_expired(self) -> int:
        with self._lock:
            data = self._load()
            now = clock.wall_ts()
            before = len(data)
            data = {c: e for c, e in data.items() if not (e["status"] == "pending" and _is_expired(e, now))}
            removed = before - len(data)
            if removed:
                self._save(data)
            return removed

    def clear(self) -> None:
        """清空所有确认（主要用于测试隔离；生产环境慎用）。"""
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass


_store_singleton: Optional[ConfirmationStore] = None
_store_singleton_lock = threading.Lock()


def get_confirmation_store(data_dir: Optional[str] = None) -> ConfirmationStore:
    """进程内单例访问器（契约 §4.2）。优先复用已存在的单例。"""
    global _store_singleton
    if _store_singleton is None or (data_dir and _store_singleton.data_dir != data_dir):
        with _store_singleton_lock:
            if _store_singleton is None or (data_dir and _store_singleton.data_dir != data_dir):
                _store_singleton = ConfirmationStore(data_dir)
    return _store_singleton
