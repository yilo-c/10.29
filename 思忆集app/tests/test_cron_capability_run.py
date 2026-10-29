"""回归测试：cron 通用能力执行入口 capability.run（2026-09-11，B1/B2 落地）。

设计要点：
- 权限链与 chat 内执行同源：白名单（默认∪追加−禁用）+ sandbox_level 档位
- 无人值守的审批语义是 **fail-fast**：需要人工确认时返回 skipped（进死信
  可查、带处置提示），而不是像对话场景那样弹卡等待——cron 没有人可等。
- 递归调用（capability.run → capability.run）显式拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.action_guard import ActionGuard
from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository
from services.cron_actions import execute_cron_action


def _make_context(tmp_path, *, whitelist=None, denylist=None, sandbox=None, approval=None, always_allow=None):
    data_store = LocalDataStore(base_dir=str(tmp_path / "runtime"))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_whitelist", list(whitelist or []))
    if denylist is not None:
        settings_repo.set_setting("assistant_action_denylist", list(denylist))
    if sandbox is not None:
        settings_repo.set_setting("assistant_sandbox_level", sandbox)
    if approval is not None:
        settings_repo.set_setting("assistant_approval_policy", approval)
    if always_allow:
        settings_repo.set_setting("assistant_confirm_always_allow", list(always_allow))

    guard = ActionGuard(settings_repository=settings_repo, observability_service=None)
    executed: list[tuple[str, dict]] = []

    async def _execute_direct(action_id, args):
        executed.append((action_id, dict(args or {})))
        return {"success": True, "echo_action": action_id}

    router = SimpleNamespace(action_guard=guard, execute_direct=_execute_direct)
    ctx = SimpleNamespace(capability_router=router)
    return ctx, executed


def _make_inbox():
    calls: list[dict] = []

    def _deliver(**kwargs):
        calls.append(dict(kwargs))
        return True

    return SimpleNamespace(deliver_suggestion=_deliver), calls


def test_capability_run_registered():
    from services.cron_actions import list_cron_actions

    assert any(a["action_id"] == "capability.run" for a in list_cron_actions())


def test_missing_action_id_fails(tmp_path):
    ctx, _ = _make_context(tmp_path)
    result = execute_cron_action(ctx, "capability.run", {})
    assert result is not None and result["success"] is False
    assert result["error"] == "missing action_id"


def test_recursive_call_rejected(tmp_path):
    ctx, _ = _make_context(tmp_path)
    result = execute_cron_action(ctx, "capability.run", {"action_id": "capability.run"})
    assert result["success"] is False
    assert "recursive" in result["error"]


def test_target_not_in_whitelist_rejected(tmp_path):
    """目标能力不在白名单（默认∪追加）→ 准入拒绝（action_rejected）。

    注意不能拿 shell.run 之类默认能力测——合并语义下它们恒在白名单
    （这正是 A1 修复的效果）；须用默认清单外的能力名。
    """
    ctx, executed = _make_context(tmp_path, whitelist=["notes.list"])
    result = execute_cron_action(ctx, "capability.run", {"action_id": "nonexistent.capability", "args": {}})
    assert result["success"] is False
    assert result["error"].startswith("action_rejected")
    assert executed == [], "被拒绝的能力不得执行"


def test_denylist_blocks_target_even_if_default_allows(tmp_path):
    """denylist 优先：即使目标是默认能力，被禁用后仍拒绝。"""
    ctx, executed = _make_context(
        tmp_path,
        whitelist=["file.read"],
        denylist=["file.read"],
    )
    result = execute_cron_action(ctx, "capability.run", {"action_id": "file.read", "args": {}})
    assert result["success"] is False
    assert result["error"].startswith("action_rejected")
    assert executed == []


def test_confirmation_required_skips_without_blocking(tmp_path):
    """无人值守审批语义（核心）：目标需人工确认且不在「始终允许」名单
    → fail-fast 返回 skipped（带处置提示），不阻塞、不执行。"""
    # shell.run 是默认白名单能力，但也是高风险（需要确认）
    ctx, executed = _make_context(tmp_path, whitelist=[])
    result = execute_cron_action(ctx, "capability.run", {"action_id": "shell.run", "args": {}})
    assert result["success"] is False
    assert result["status"] == "skipped"
    assert result["reason"] == "confirmation_required_unattended"
    assert "hint" in result
    assert executed == []


def test_confirmation_skip_delivers_inbox_hint(tmp_path):
    """反馈闭环（2026-09-11）：skipped 必须投递收件箱指引——否则 hint 躺在
    死信里无人知晓，用户不知道该去点「始终允许」，流程静默卡死。"""
    inbox, calls = _make_inbox()
    ctx, executed = _make_context(tmp_path, whitelist=[])
    ctx.assistant_inbox_service = inbox
    result = execute_cron_action(ctx, "capability.run", {"action_id": "shell.run", "args": {}})
    assert result["status"] == "skipped"
    assert len(calls) == 1
    assert "shell.run" in calls[0]["text"]
    assert calls[0]["reason"].startswith("capability.run skipped")


def test_confirmation_skip_tolerates_missing_inbox(tmp_path):
    """inbox 缺失时不得抛异常（降级安全，死信里仍有 hint）。"""
    ctx, _ = _make_context(tmp_path, whitelist=[])
    result = execute_cron_action(ctx, "capability.run", {"action_id": "shell.run", "args": {}})
    assert result["status"] == "skipped"


def test_always_allow_bypasses_confirmation_and_executes(tmp_path):
    """「始终允许」名单优先于全局审批策略：加入后 cron 可直接执行。"""
    ctx, executed = _make_context(tmp_path, whitelist=[], always_allow=["shell.run"])
    result = execute_cron_action(ctx, "capability.run", {"action_id": "shell.run", "args": {"command": "start workbuddy"}})
    assert result["success"] is True, result
    assert result["status"] == "completed"
    assert executed and executed[0][0] == "shell.run"
    assert executed[0][1].get("command") == "start workbuddy"


def test_approval_policy_never_executes_unattended(tmp_path):
    """approval_policy=never（无人值守显式声明）：高风险能力也直接执行。"""
    ctx, executed = _make_context(tmp_path, whitelist=[], approval="never")
    result = execute_cron_action(ctx, "capability.run", {"action_id": "app.launch", "args": {"command": "workbuddy"}})
    assert result["success"] is True, result
    assert executed and executed[0][0] == "app.launch"


def test_sandbox_read_only_blocks_launch_even_whitelisted(tmp_path):
    """sandbox 档位是独立上限：read-only 下即使 app.launch 可确认也拒绝。"""
    ctx, executed = _make_context(
        tmp_path,
        whitelist=["app.launch"],
        sandbox="read-only",
        always_allow=["app.launch"],
    )
    result = execute_cron_action(ctx, "capability.run", {"action_id": "app.launch", "args": {}})
    assert result["success"] is False
    assert result["error"].startswith("action_rejected")
    assert executed == []
