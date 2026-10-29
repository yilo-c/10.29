"""L6 安全 —— 沙箱权限边界（破坏性操作审批队列 + 超时处置策略）。

组件级（确定性，独立实例化避免单例污染）：
1. 破坏性操作必须进入待审批队列（pending），绝不直接执行；
2. 批准时才经 tool_executor 执行（且剥离 dry_run 标记）；无 executor 时仅出队；
3. 拒绝/批量批准/清空；
4. 队列上限（_MAX_PENDING=200）超限淘汰最早条目；
5. P1-5 超时策略：禁用(0) / reject（默认拒绝）/ dry_run（只读预演）。
"""

from __future__ import annotations

import time

import pytest

from services.sandbox_manager import SandboxManager


@pytest.fixture()
def sm() -> SandboxManager:
    """每个用例独立实例（队列/executor 隔离）。"""
    return SandboxManager()


class TestApprovalQueue:
    def test_destructive_add_queues_pending(self, sm: SandboxManager) -> None:
        pid = sm.add("file.delete", {"path": "/x"}, {"preview": "删除 /x"})
        assert pid and isinstance(pid, str)
        pending = sm.list_pending()
        assert len(pending) == 1
        assert pending[0]["action_id"] == "file.delete"
        assert pending[0]["status"] == "pending"
        assert sm.get(pid)["args"] == {"path": "/x"}

    def test_add_never_executes_immediately(self, sm: SandboxManager) -> None:
        """入队即 pending：不触发任何执行（executor 不被调用）。"""
        calls: list = []
        sm.set_tool_executor(lambda aid, args: calls.append((aid, args)))
        sm.add("fs.rm", {"path": "/data"}, {})
        assert calls == []

    def test_reject_removes_pending(self, sm: SandboxManager) -> None:
        pid = sm.add("file.delete", {}, {})
        r = sm.reject(pid)
        assert r["success"] is True and r["action"] == "rejected"
        assert sm.list_pending() == []
        assert sm.get(pid) is None

    def test_approve_without_executor_keeps_pending(self, sm: SandboxManager) -> None:
        """P0-D（2026-09-18）契约变更：无 executor 不再谎报成功/静默出队。

        旧断言 `success is True + 出队` 正是"批了但从未执行"的假成功锁死点。
        新契约：success=False + reason=tool_executor_not_injected + 条目保留
        pending 可重试（注入 executor 后再批即真执行）。
        """
        pid = sm.add("cmd.run", {"cmd": "rm -rf /"}, {"preview": "危险命令"})
        r = sm.approve(pid)
        assert r["success"] is False
        assert r["reason"] == "tool_executor_not_injected"
        assert [p["pending_id"] for p in sm.list_pending()] == [pid]

    def test_approve_executes_via_executor(self, sm: SandboxManager) -> None:
        """批准时经 executor 执行，且参数剥离 dry_run 标记、result 透传。"""
        captured: list[dict] = []

        def executor(action_id: str, args: dict) -> dict:
            captured.append({"aid": action_id, "args": args})
            return {"ok": True, "n": 1}

        sm.set_tool_executor(executor)
        pid = sm.add("fs.remove", {"path": "/tmp/a", "dry_run": True}, {})
        r = sm.approve(pid)
        assert r["success"] is True
        assert len(captured) == 1
        assert captured[0]["aid"] == "fs.remove"
        assert captured[0]["args"] == {"path": "/tmp/a"}  # dry_run 被剥离
        assert sm.get(pid)["result"] == {"ok": True, "n": 1}
        assert sm.list_pending() == []

    def test_approve_invalid_twice(self, sm: SandboxManager) -> None:
        # P0-D：无 executor 时首次批准已不终结条目，故本用例显式注入 executor，
        # 验证的是"批准后条目终结、重复批准被拒"这一原意。
        sm.set_tool_executor(lambda aid, args: {"ok": True})
        pid = sm.add("file.delete", {}, {})
        assert sm.approve(pid)["success"] is True
        r2 = sm.approve(pid)
        assert "success" not in r2  # 失败响应无 success 键（带 error）
        assert r2.get("error") == "invalid or not pending"

    def test_approve_unknown_pid(self, sm: SandboxManager) -> None:
        r = sm.approve("does-not-exist")
        assert "success" not in r
        assert r.get("error") == "invalid or not pending"

    def test_batch_approve(self, sm: SandboxManager) -> None:
        sm.set_tool_executor(lambda aid, args: {"ok": True})
        pid1 = sm.add("a", {}, {})
        pid2 = sm.add("b", {}, {})
        out = sm.batch_approve([pid1, pid2, "nope"])
        assert out["approved"] == 2
        assert sm.list_pending() == []

    def test_clear_all(self, sm: SandboxManager) -> None:
        sm.add("a", {}, {})
        sm.add("b", {}, {})
        sm.clear_all()
        assert sm.list_pending() == []

    def test_max_pending_eviction(self, sm: SandboxManager) -> None:
        """超过 200 上限时淘汰最早条目，队列始终 ≤ 上限。"""
        for i in range(201):
            sm.add(f"op-{i}", {"i": i}, {})
        pending = sm.list_pending()
        assert len(pending) <= SandboxManager._MAX_PENDING
        # 最早一批被淘汰（i=0 已不在）
        pids = [p["action_id"] for p in pending]
        assert "op-0" not in pids


class TestTimeoutPolicy:
    def test_timeout_disabled_when_zero(self, sm: SandboxManager, monkeypatch) -> None:
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0")
        assert sm._timeout_seconds() == 0.0
        pid = sm.add("file.delete", {}, {})
        assert sm.check_timeouts() == []
        assert sm.list_pending() == [sm.get(pid)]  # 未被处置

    def test_timeout_reject_policy_removes_expired(self, sm: SandboxManager, monkeypatch) -> None:
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_POLICY", "reject")
        pid = sm.add("file.delete", {}, {})
        time.sleep(0.15)
        processed = sm.check_timeouts()
        assert len(processed) == 1
        assert processed[0]["status"] == "timed_out_rejected"
        assert sm.list_pending() == []  # 过期条目被移除（拒绝）

    def test_timeout_dry_run_policy_runs_readonly(self, sm: SandboxManager, monkeypatch) -> None:
        """dry_run 策略：过期条目降级为只读预演（dry_run=True）后移除。"""
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0.05")
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_POLICY", "dry_run")
        captured: list[dict] = []

        def executor(action_id: str, args: dict) -> dict:
            captured.append({"aid": action_id, "args": args})
            return {"preview": True}

        sm.set_tool_executor(executor)
        pid = sm.add("fs.remove", {"path": "/x"}, {})
        time.sleep(0.15)
        processed = sm.check_timeouts()
        assert len(processed) == 1
        assert processed[0]["status"] == "timed_out_dryrun"
        assert captured == [{"aid": "fs.remove", "args": {"path": "/x", "dry_run": True}}]
        assert sm.list_pending() == []

    def test_timeout_policy_invalid_defaults_reject(self, sm: SandboxManager, monkeypatch) -> None:
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_POLICY", "exec")
        assert sm._timeout_policy() == "reject"

    def test_reject_does_not_touch_executor(self, sm: SandboxManager, monkeypatch) -> None:
        """超时 reject 策略不调用 executor（高危操作被静默丢弃，不执行）。"""
        monkeypatch.setenv("SIYI_SANDBOX_TIMEOUT_SECONDS", "0.05")
        calls: list = []
        sm.set_tool_executor(lambda aid, args: calls.append((aid, args)))
        sm.add("file.delete", {}, {})
        time.sleep(0.15)
        sm.check_timeouts()
        assert calls == []
