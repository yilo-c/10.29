# -*- coding: utf-8 -*-
"""P0-D 工具审批闭环测试（2026-09-18 取证）。

事故链（shell-capability-test 报告 P2-C 的完整根因）：
  1. ReAct 引擎遇破坏性动作 → sandbox.add(kind="tool") → 返回 pending_id；
  2. 用户经 sandbox.approve 批准 → mgr.approve(pid)；
  3. approve 里 `if self._tool_executor:` —— **生产从未注入 executor**
     （grep 全仓 set_tool_executor 仅测试调用）→ 走 else 分支：
     弹条目 + 留痕 "approved" + 返回 success=True，**但动作从未执行**。
     = 假成功：用户看到"已批准"，笔记其实没删。

修复 = headless 启动时注入 make_react_executor(context)。本测试锁：
  * 无 executor 时 approve 不得谎报 success（回归守卫）；
  * 有 executor 时 approve 必须真执行（透传 args、_in_approval_var 放行重入）。
"""

from __future__ import annotations


from services.sandbox_manager import SandboxManager, _in_approval_var


def test_tool_approve_with_executor_actually_executes():
    sm = SandboxManager()
    executed: list[tuple] = []
    approval_flag: list[bool] = []

    def executor(action_id, args):
        executed.append((action_id, args))
        approval_flag.append(_in_approval_var.get())  # 重放必须已置审批上下文，防二次拦
        return {"success": True, "executed": action_id}

    sm.set_tool_executor(executor)
    pid = sm.add("notes.delete", {"note_id": "N1"}, {"action": "notes.delete"})
    out = sm.approve(pid)

    assert out["success"] is True
    assert executed == [("notes.delete", {"note_id": "N1"})], "executor 必须被真调用"
    assert approval_flag == [True], "重放必须在 _in_approval_var 上下文内（防二次拦截死循环）"
    # 成功条目留在队列但状态终结（不再被 list_pending 列出，历史可溯源）
    entry = sm.get(pid)
    assert entry is None or entry["status"] == "approved"
    assert sm.list_pending() == []


def test_tool_approve_without_executor_must_not_claim_success():
    """回归守卫：无 executor 时**不得**静默谎报成功（曾经的假成功路径）。"""
    sm = SandboxManager()
    pid = sm.add("notes.delete", {"note_id": "N2"}, {"action": "notes.delete"})
    out = sm.approve(pid)
    # 修复后契约：无执行器 = 无法执行 = success=False + 明确 reason，条目保留可重试
    assert out.get("success") is not True
    assert out.get("reason") or out.get("error")
    entry = sm.get(pid)
    assert entry is not None and entry["status"] == "pending", "无执行器时条目应保留待重试，不得吞掉"


def test_history_injection_would_not_swallow_tool_result():
    """args 中的 dry_run 键在重放前剔除（防把预览态带进真执行）。"""
    sm = SandboxManager()
    seen: list[dict] = []
    sm.set_tool_executor(lambda aid, args: seen.append(args) or {"success": True})
    pid = sm.add("file.write", {"path": "p", "content": "c", "dry_run": True}, {"action": "file.write"})
    sm.approve(pid)
    assert "dry_run" not in seen[0]
