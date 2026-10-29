# -*- coding: utf-8 -*-
"""沙箱审批管理器：破坏性操作的"先审批、后执行"队列。

历史说明：本文件源文件曾丢失（仅残留 legacy .pyc），现按字节码反汇编
逐行重建，行为与原版一致：

- ``SandboxManager.add`` 把破坏性操作挂起为 pending 条目（secrets 生成 ID）；
- ``approve`` 经 ``_tool_executor`` 真正执行（期间 ``_in_approval_var``
  置 True，让 react_executor 跳过二次审批）；
- 全程通过 EventBus 发 ``sandbox.pending_added / approved / rejected``
  事件供前端刷新审批面板。

P3-8：executor 由宿主经 ``set_tool_executor`` 注入；全局单例
``get_sandbox_manager()`` 供 backend handler 与 services 共用。
"""

from __future__ import annotations

import contextvars
import logging
import os
import secrets
import threading
import time
from collections import deque
from typing import Any

from core.events import AppEvent, event_bus
from services.confirmation_store import compute_args_sha256

logger = logging.getLogger("siyi.services.sandbox_manager")

# AG-C1③：审批历史（轻量环形）。pending 条目在 approve/reject/timeout 时即被
# 从 _pending 弹出（_sandbox.py:105 的 rpc 重放路径同样 pop），此前无任何留存，
# 前端审批面板刷新后"批过什么"彻底不可追溯。补一条 maxlen=200 的环形历史：
# 有 data_store 时落 kv 键（跨重启可见），无 store（纯单测/内核早周期）时退化为
# 进程内环形，两条路径同构、都只存摘要不存 args/preview 原文（避免把待写内容
# 留在历史里）。
SANDBOX_HISTORY_KEY = "sandbox_approval_history"
SANDBOX_HISTORY_CAP = 200
_SANDBOX_HISTORY_EVENTS = ("approved", "rejected", "timeout", "failed")

# 审批执行流标记：approve → executor 期间置 True，
# react_executor 的破坏性拦截据此跳过二次入队。
_in_approval_var: contextvars.ContextVar[bool] = contextvars.ContextVar("sandbox_in_approval", default=False)

# ── M2.1 G1：连接持有者上下文（审批不得由请求方连接自我批准）─────────────
# WS 帧处理入口设置当前连接指纹与"可信 UI 持有者"标记；R4 入队时把请求方 conn
# 记入 pending，approve 时比对。非 WS 路径（内部/handler 直接调用）为 None，
# 不触发自我批准拦截，向后兼容既有审批闭环。
_current_conn_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("sandbox_current_conn_id", default=None)
_current_conn_trusted: contextvars.ContextVar[bool] = contextvars.ContextVar("sandbox_current_conn_trusted", default=False)


def set_request_connection(conn_id: str | None, trusted: bool = False) -> tuple:
    """进入某连接的 RPC 处理前调用；返回 token 供 reset_request_connection 复原。"""
    t1 = _current_conn_id.set(conn_id)
    t2 = _current_conn_trusted.set(bool(trusted))
    return (t1, t2)


def reset_request_connection(tokens: tuple) -> None:
    t1, t2 = tokens
    _current_conn_id.reset(t1)
    _current_conn_trusted.reset(t2)


def get_request_connection() -> tuple[str | None, bool]:
    """返回当前请求连接 (conn_id, trusted)。非 WS 路径为 (None, False)。"""
    return _current_conn_id.get(), _current_conn_trusted.get()


class SandboxManager:
    """破坏性操作待审批队列（实例级 _pending，禁止类属性共享）。"""

    _MAX_PENDING = 200

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, Any]] = {}
        self._tool_executor = None
        # P1-5：超时策略状态（防止用户离线时高危审批永久挂起）
        self._timeout_loop_started = False
        # AG-C1③：审批历史（kv 可选后端 + 进程内环形兜底）
        self._history_store: Any = None
        self._history: deque[dict[str, Any]] = deque(maxlen=SANDBOX_HISTORY_CAP)

    # ── AG-C1③：审批历史 ──────────────────────────────────────

    def set_history_store(self, data_store: Any) -> None:
        """注入 LocalDataStore，使审批历史落 kv 键跨重启可见（None=仅进程内环形）。"""
        self._history_store = data_store

    def record_history(
        self,
        event: str,
        entry: dict[str, Any] | None,
        *,
        status: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """把一条审批处置记入环形历史，返回写入的条目（便于测试与 RPC 回显）。

        event ∈ approved/rejected/timeout/failed；只存摘要字段，args 以指纹留痕
        （SEC-1 同款 sha256），不落原文——历史里塞待写文件内容等于新开一个泄漏面。
        """
        entry = entry or {}
        item = {
            "pending_id": str(entry.get("pending_id") or ""),
            "action_id": str(entry.get("action_id") or ""),
            "kind": str(entry.get("kind") or "tool"),
            "event": str(event or ""),
            "status": str(status or entry.get("status") or event or ""),
            "args_sha256": compute_args_sha256(entry.get("args") or {}),
            "requested_at": entry.get("created_at"),
            "resolved_at": time.time(),
        }
        if error:
            item["error"] = str(error)[:300]
        self._history.append(item)
        store = self._history_store
        if store is not None:
            try:

                def _append(current: Any, _item=item) -> Any:
                    items = current if isinstance(current, list) else []
                    return (items + [dict(_item)])[-SANDBOX_HISTORY_CAP:]

                store.update_json(SANDBOX_HISTORY_KEY, [], _append)
            except Exception:  # noqa: BLE001 —— 历史失败不得影响审批主流程
                logger.debug("sandbox history persist failed", exc_info=True)
        return item

    def list_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """返回审批历史（新的在前）。有 kv 后端时以 kv 为准，否则回退进程内环形。"""
        cap = max(1, min(int(limit or 50), SANDBOX_HISTORY_CAP))
        store = self._history_store
        items: Any = None
        if store is not None:
            try:
                raw = store.load_json(SANDBOX_HISTORY_KEY, [])
                if isinstance(raw, list):
                    items = raw
            except Exception:  # noqa: BLE001
                logger.debug("sandbox history load failed", exc_info=True)
        if items is None:
            items = list(self._history)
        clean = [dict(i) for i in items if isinstance(i, dict)]
        clean.reverse()
        return clean[:cap]

    def clear_history(self) -> None:
        """清空历史（测试隔离 / 用户主动清理）。"""
        self._history.clear()
        store = self._history_store
        if store is not None:
            try:
                store.save_json(SANDBOX_HISTORY_KEY, [])
            except Exception:  # noqa: BLE001
                logger.debug("sandbox history clear failed", exc_info=True)

    # ── P1-5：审批超时策略 ──

    def _timeout_seconds(self) -> float:
        """审批超时（秒）；<=0 表示禁用。默认 30 分钟。"""
        try:
            return float(os.environ.get("SIYI_SANDBOX_TIMEOUT_SECONDS", "1800"))
        except ValueError:
            return 1800.0

    def _timeout_policy(self) -> str:
        """超时处置策略：reject（默认拒绝）/ dry_run（降级为只读预演）。"""
        policy = os.environ.get("SIYI_SANDBOX_TIMEOUT_POLICY", "reject").strip().lower()
        return policy if policy in ("reject", "dry_run") else "reject"

    def _ensure_timeout_loop(self) -> None:
        """懒启动超时扫描线程（首个审批条目入队时）。"""
        if self._timeout_loop_started or self._timeout_seconds() <= 0:
            return
        self._timeout_loop_started = True
        thread = threading.Thread(target=self._timeout_loop, daemon=True, name="sandbox-timeout")
        thread.start()

    def _timeout_loop(self) -> None:
        interval = min(30.0, max(1.0, self._timeout_seconds() / 10))
        while True:
            time.sleep(interval)
            try:
                self.check_timeouts()
            except Exception:  # noqa: BLE001
                logger.debug("sandbox timeout check failed", exc_info=True)

    def check_timeouts(self) -> list[dict[str, Any]]:
        """扫描过期/临期 pending 条目并按策略处置。返回处置摘要列表。

        - 剩余 ≤ 60s：发 ``sandbox.timeout_warning`` 提醒（每条目一次）
        - 已过期：按策略 reject（移除）或 dry_run（降级预演后移除），
          发 ``sandbox.timeout`` 事件（进通知白名单，手机端可收）
        """
        timeout = self._timeout_seconds()
        if timeout <= 0:
            return []
        now = time.time()
        policy = self._timeout_policy()
        processed: list[dict[str, Any]] = []
        for pid, entry in list(self._pending.items()):
            if entry.get("status") != "pending":
                continue
            age = now - float(entry.get("created_at", now))
            if age >= timeout:
                status = self._apply_timeout(pid, entry, policy, int(age))
                processed.append(
                    {
                        "pending_id": pid,
                        "action_id": entry["action_id"],
                        "policy": policy,
                        "status": status,
                    }
                )
            elif timeout - age <= 60 and not entry.get("_timeout_warned"):
                entry["_timeout_warned"] = True
                try:
                    event_bus.emit(
                        AppEvent(
                            "sandbox.timeout_warning",
                            payload={
                                "pending_id": pid,
                                "action_id": entry["action_id"],
                                "remaining_seconds": int(timeout - age),
                            },
                        )
                    )
                except Exception:
                    logger.debug("failed to emit sandbox.timeout_warning", exc_info=True)
        return processed

    def _apply_timeout(self, pid: str, entry: dict[str, Any], policy: str, expired_seconds: int) -> str:
        """对单个过期条目执行超时策略，返回最终状态。"""
        status = "timed_out_rejected"
        if policy == "dry_run" and self._tool_executor:
            try:
                token = _in_approval_var.set(True)
                try:
                    result = self._tool_executor(entry["action_id"], dict(entry["args"], dry_run=True))
                finally:
                    _in_approval_var.reset(token)
                entry["result"] = result
                status = "timed_out_dryrun"
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
                status = "timed_out_failed"
        self._pending.pop(pid, None)
        entry["status"] = status
        self.record_history("timeout", entry, status=status)
        try:
            event_bus.emit(
                AppEvent(
                    "sandbox.timeout",
                    payload={
                        "pending_id": pid,
                        "action_id": entry["action_id"],
                        "policy": policy,
                        "status": status,
                        "expired_seconds": expired_seconds,
                    },
                )
            )
        except Exception:
            logger.debug("failed to emit sandbox.timeout", exc_info=True)
        logger.warning(
            "sandbox approval timed out: pid=%s action=%s policy=%s -> %s",
            pid,
            entry["action_id"],
            policy,
            status,
        )
        return status

    # ── P1-5：批量审批 ──

    def batch_approve(self, pids: list[str]) -> dict[str, Any]:
        """批量批准待审批条目（并发任务审批不逐条点）。"""
        results = [self.approve(pid) for pid in pids]
        approved = sum(1 for r in results if r.get("success"))
        return {
            "success": True,
            "approved": approved,
            "total": len(pids),
            "results": results,
        }

    def set_tool_executor(self, executor) -> None:
        """设置工具执行器。

        P3-8: 同一全局 SandboxManager 可能被重复注入 executor，
        静默覆盖前者会导致跨 context 泄漏，
        这里对不同 executor 的覆盖行为打 warning，便于发现是否需要拆分实例。
        """
        if self._tool_executor is not None and executor is not None and self._tool_executor is not executor:
            logger.warning(
                "SandboxManager.set_tool_executor: replacing existing executor "
                "(previous=%r, new=%r); pending entries from the previous "
                "executor will be approved against the new one.",
                self._tool_executor,
                executor,
            )
        self._tool_executor = executor

    def unset_tool_executor(self) -> None:
        """显式清空当前 executor（用于测试/重建 context 场景）。"""
        self._tool_executor = None

    # ── 队列操作 ──────────────────────────────────────────────

    def add(self, action_id: str, args: dict, preview: dict, *, kind: str = "tool") -> str:
        """添加破坏性操作到待审批队列。

        Args:
            action_id: 操作标识（tool 为 action_id，RPC 为方法名）
            args: 操作参数
            preview: 预览信息
            kind: 来源类型——"tool"（react_executor 工具）/ "rpc"（dispatch 层 R4 方法）。
                  approve 时按 kind 分流执行（rpc 走 registry 重放，tool 走 _tool_executor）。

        Returns:
            pending_id: 待审批条目的唯一标识
        """
        if len(self._pending) >= self._MAX_PENDING:
            sorted_pids = sorted(
                self._pending.items(),
                key=lambda kv: kv[1].get("created_at", 0),
            )
            evicted: list[str] = []
            for old_pid, old_entry in sorted_pids[: self._MAX_PENDING // 5]:
                self._pending.pop(old_pid, None)
                evicted.append(old_entry["action_id"])
            # V2.1 批次2：队列满驱逐时通知前端（防静默丢失待审批项）
            try:
                event_bus.emit(
                    AppEvent(
                        "sandbox.evicted",
                        payload={
                            "count": len(evicted),
                            "action_ids": evicted,
                            "reason": "queue_full",
                            "max_pending": self._MAX_PENDING,
                        },
                    )
                )
            except Exception:
                logger.debug("failed to emit sandbox.evicted event", exc_info=True)
        pid = secrets.token_hex(6)
        # P1-C（2026-09-18 实测）：审批 preview 曾明文回显 API Key
        # （settings.set / provider.update 被拦成 pending 时整包 args 泄漏）。
        # 统一在此咽喉点对 **preview** 递归脱敏；entry["args"] 保留原值，
        # 重放走 skip_approval + args_sha256 绑定校验，脱敏不得污染重放链。
        from services.preview_redact import redact_preview_args

        redacted_preview = redact_preview_args(preview)
        # M2.1 SEC-1：preview 增记参数指纹（与 confirmation_store 同款），
        # 供审批 UI / 重放路径绑定校验 args_sha256（**用原 args 算，非脱敏值**）。
        preview = {**redacted_preview, "args_sha256": compute_args_sha256(args)}
        self._pending[pid] = {
            "pending_id": pid,
            "action_id": action_id,
            "args": dict(args),
            "preview": dict(preview),
            "created_at": time.time(),
            "status": "pending",
            "kind": kind,
            # M2.1 G1：记录发起该待审批操作的连接指纹（approve 时禁止同连接自批）。
            "requester_conn": _current_conn_id.get(),
        }
        try:
            event_bus.emit(
                AppEvent(
                    "sandbox.pending_added",
                    payload={"pending_id": pid, "action_id": action_id, "preview": preview},
                )
            )
        except Exception:
            logger.debug("failed to emit sandbox.pending_added event", exc_info=True)
        self._ensure_timeout_loop()
        return pid

    def get(self, pid: str) -> dict[str, Any] | None:
        """获取单个待审批条目。"""
        return self._pending.get(pid)

    def list_pending(self) -> list[dict[str, Any]]:
        """列出所有待审批条目。"""
        return [v for v in self._pending.values() if v["status"] == "pending"]

    def approve(self, pid: str) -> dict[str, Any]:
        """批准待审批条目并执行。"""
        entry = self._pending.get(pid)
        if not entry or entry["status"] != "pending":
            return {"error": "invalid or not pending"}
        entry["status"] = "approved"
        if self._tool_executor:
            try:
                token = _in_approval_var.set(True)
                try:
                    result = self._tool_executor(
                        entry["action_id"],
                        {k: v for k, v in entry["args"].items() if k != "dry_run"},
                    )
                finally:
                    _in_approval_var.reset(token)
                entry["result"] = result
                # AG-C1③：approve 成功也留痕（与 timeout/reject 同构）
                self.record_history("approved", entry, status="approved")
                try:
                    event_bus.emit(
                        AppEvent(
                            "sandbox.approved",
                            payload={"pending_id": pid, "action_id": entry["action_id"], "success": True},
                        )
                    )
                except Exception:
                    logger.debug("failed to emit sandbox.approved event", exc_info=True)
                return {"success": True, "pending_id": pid, "result": result}
            except Exception as exc:
                entry["status"] = "failed"
                # AG-C1③：approve 执行失败单独留痕（event=failed，含错误摘要）
                self.record_history("failed", entry, status="failed", error=str(exc))
                try:
                    event_bus.emit(
                        AppEvent(
                            "sandbox.approved",
                            payload={
                                "pending_id": pid,
                                "action_id": entry["action_id"],
                                "success": False,
                                "error": str(exc),
                            },
                        )
                    )
                except Exception:
                    logger.debug("failed to emit sandbox.approved event", exc_info=True)
                return {"success": False, "pending_id": pid}
        # ⚠ P0-D（2026-09-18 取证，勿改回谎报成功）：无 _tool_executor 时动作无法执行。
        # 此前这条路径 pop 条目 + 留痕 "approved" + 返回 success=True，而动作从未跑过
        # = 假成功（用户见"已批准"，破坏性动作其实没做）。生产 executor 现由
        # backend/headless_main.py 注入（make_react_executor）；走到这里说明注入缺失/
        # 被 unset —— 保留条目（回退 pending 可重试），记 failed 留痕，明确报失败。
        logger.error(
            "sandbox approve(%s) 无可用 tool_executor，动作 %s 未执行（条目保留待重试）",
            pid,
            entry.get("action_id"),
        )
        entry["status"] = "pending"
        self.record_history("failed", entry, status="failed", error="tool_executor_not_injected")
        return {
            "success": False,
            "pending_id": pid,
            "reason": "tool_executor_not_injected",
            "error": "沙箱工具执行器未就绪，动作未执行；条目保留可重试。",
        }

    def reject(self, pid: str) -> dict[str, Any]:
        """拒绝待审批条目。"""
        entry = self._pending.pop(pid, None)
        action_id = entry["action_id"] if entry else None
        # AG-C1③：拒绝留痕（条目不存在时无处置可记，跳过）
        if entry is not None:
            self.record_history("rejected", entry, status="rejected")
        try:
            event_bus.emit(
                AppEvent(
                    "sandbox.rejected",
                    payload={"pending_id": pid, "action_id": action_id},
                )
            )
        except Exception:
            logger.debug("failed to emit sandbox.rejected event", exc_info=True)
        return {"success": True, "pending_id": pid, "action": "rejected"}

    def clear_all(self) -> None:
        """清空所有待审批条目。"""
        self._pending.clear()


_sandbox_manager = SandboxManager()


def get_sandbox_manager() -> SandboxManager:
    """获取全局 SandboxManager 单例。"""
    return _sandbox_manager
