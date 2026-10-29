from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.action_guard import ActionGuard
from data.local_storage import LocalDataStore
from repositories import LocalSettingsRepository


class _FakeObservability:
    def __init__(self):
        self.events = []

    def track_event(self, event_name, severity="info", payload=None):
        self.events.append({"event": event_name, "severity": severity, "payload": payload or {}})


def test_action_guard_blocks_action_not_in_whitelist(tmp_path):
    """不在「默认 ∪ 用户追加」中的 action 必须被拒（2026-09-11 起为合并语义）。"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_whitelist", ["tasks", "notes"])

    observability = _FakeObservability()
    guard = ActionGuard(settings_repository=settings_repo, observability_service=observability)

    # 用一个默认白名单里不存在的 action 验证准入拦截
    result = guard.validate("definitely.not.an.action", payload={"source": "pytest"}, source="test")

    assert result.allowed is False
    assert result.reason == "not_in_whitelist"
    assert any(event["event"] == "assistant.action.rejected" for event in observability.events)


def test_action_guard_merges_user_whitelist_with_defaults(tmp_path):
    """用户白名单是「增量追加」——设为小集合不应把默认能力挤掉。

    回归（2026-09-11）：此前是覆盖语义，用户配置会整体替换默认值，
    导致某次保存的旧快照静默锁死此后默认值新增的能力。
    """
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_whitelist", ["tasks"])

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())

    # capture 在默认白名单中；用户只追加了 tasks，不应使其被禁用
    assert guard.validate("capture", payload={}, source="test").allowed is True


def test_action_guard_denylist_disables_default_action(tmp_path):
    """denylist 必须能把默认白名单里的能力显式禁用（2026-09-11 新增能力）。"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_denylist", ["capture"])

    observability = _FakeObservability()
    guard = ActionGuard(settings_repository=settings_repo, observability_service=observability)

    result = guard.validate("capture", payload={"source": "pytest"}, source="test")

    assert result.allowed is False
    assert result.reason == "not_in_whitelist"


def test_action_guard_warns_for_risk_rule_in_warn_only_mode(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_whitelist", ["capture", "tasks"])
    settings_repo.set_setting("assistant_action_risk_warn_only", True)

    observability = _FakeObservability()
    guard = ActionGuard(settings_repository=settings_repo, observability_service=observability)

    result = guard.validate("capture", payload={"source": "pytest"}, source="test")

    assert result.allowed is True
    assert len(result.warnings) >= 1
    assert any(event["event"] == "assistant.action.risk_warning" for event in observability.events)


def test_action_guard_blocks_high_risk_action_in_strict_profile(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_whitelist", ["settings", "tasks"])
    settings_repo.set_setting("assistant_action_guard_profile", "strict")
    settings_repo.set_setting("assistant_action_risk_enforcement", "auto")

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("settings", payload={"source": "pytest"}, source="test")

    assert result.allowed is False
    assert result.reason == "risk_blocked"
    assert result.risk_level == "high"


def test_action_guard_command_respects_command_whitelist(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_command_whitelist", ["detect_nana_pet_process"])

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_command("launch_nana_pet", payload={"command": "os.startfile"}, source="test")

    assert result.allowed is False
    assert result.reason == "not_in_command_whitelist"


def test_action_guard_blocks_open_external_url_when_not_in_command_whitelist(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_command_whitelist", ["launch_nana_pet"])

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_command("open_external_url", payload={"url": "https://example.com"}, source="test")

    assert result.allowed is False
    assert result.reason == "not_in_command_whitelist"


def test_action_guard_blocks_unsafe_shell_command(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_shell_command("echo ok && whoami", payload={"command": "echo ok && whoami"}, source="test")

    assert result.allowed is False
    assert result.reason == "unsafe_shell_syntax_blocked"


def test_action_guard_blocks_dangerous_shell_command(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_shell_command("shutdown /r /t 0", payload={"command": "shutdown /r /t 0"}, source="test")

    assert result.allowed is False
    assert result.reason == "dangerous_command_blocked"


# --- P0: LLM source blocks high-risk actions ---


def test_llm_source_blocks_elevated_action_file_write(tmp_path):
    """llm source blocks elevated action file write"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("file.write", payload={"path": "/etc/passwd"}, source="llm")

    assert result.allowed is False
    assert result.reason == "llm_source_high_risk"
    assert result.risk_level == "high"


def test_agent_source_blocks_shell_run(tmp_path):
    """agent source trying shell.run must be blocked"""

    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("shell.run", source="agent")

    assert result.allowed is False
    assert result.reason == "llm_source_high_risk"


def test_assistant_source_blocks_process_kill(tmp_path):
    """assistant source trying process.kill must be blocked"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("process.kill", source="assistant")

    assert result.allowed is False
    assert result.reason == "llm_source_high_risk"


def test_approved_skips_llm_high_risk_block(tmp_path):
    """approved=True（确认闭环人工授权）应跳过 LLM 高危拦截直接放行。

    复现 bug：用户批准 shell.run 后仍被 llm_source_high_risk 拦截。
    """
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    # 未批准：LLM 来源 shell.run 必须被拦
    blocked = guard.validate("shell.run", payload={"command": "echo hi"}, source="llm")
    assert blocked.allowed is False
    assert blocked.reason == "llm_source_high_risk"
    # 已批准：人工授权是最高授权，直接放行
    result = guard.validate("shell.run", payload={"command": "echo hi"}, source="llm", approved=True)
    assert result.allowed is True


def test_approved_skips_confirm_requirement(tmp_path):
    """approved=True 时不需要再走确认要求（ActionBroker 第 4 步）。"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("file.write", payload={"path": "/tmp/a"}, source="assistant", approved=True)
    assert result.allowed is True


def test_llm_source_allows_non_elevated_action(tmp_path):
    """llm source allows non elevated action"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("news", source="llm")

    assert result.allowed is True


def test_user_source_allows_elevated_action(tmp_path):
    """user source allows elevated action"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    settings_repo.set_setting("assistant_action_risk_warn_only", True)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("file.write", source="user")

    assert result.allowed is True


# --- P0: 空输入与边界条件 ---


def test_empty_action_id_rejected(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("", source="test")

    assert result.allowed is False
    assert result.reason == "empty_action"


def test_empty_command_id_rejected(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_command("", source="test")

    assert result.allowed is False
    assert result.reason == "empty_command"


def test_empty_shell_command_rejected(tmp_path):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_shell_command("", source="test")

    assert result.allowed is False
    assert result.reason == "empty_shell_command"


# --- P0: Default whitelist behavior ---


def test_default_whitelist_allows_known_actions(tmp_path):
    """default whitelist allows known actions"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate("notes", source="user")

    assert result.allowed is True


def test_default_command_whitelist_allows_known_commands(tmp_path):
    """default command whitelist allows known commands"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_command("detect_nana_pet_process", source="user")

    assert result.allowed is True


def test_no_settings_repository_uses_defaults():
    """娌湁 settings_repository 鏃堕回默认白名单。"""
    guard = ActionGuard(settings_repository=None, observability_service=None)
    result = guard.validate("notes", source="user")

    assert result.allowed is True


def test_no_settings_repository_blocks_unknown_action():
    """no settings repository blocks unknown action"""
    guard = ActionGuard(settings_repository=None, observability_service=None)
    result = guard.validate("hack_system", source="user")

    assert result.allowed is False
    assert result.reason == "not_in_whitelist"


# --- P0: Shell 注入边界测试 ---

import pytest


@pytest.mark.parametrize(
    "cmd,reason",
    [
        ("echo hello | grep world", "unsafe_shell_syntax_blocked"),
        ("dir > output.txt", "unsafe_shell_syntax_blocked"),
        ("whoami; id", "unsafe_shell_syntax_blocked"),
        ("echo `whoami`", "unsafe_shell_syntax_blocked"),
        ("echo $(id)", "unsafe_shell_syntax_blocked"),
        ("rm -rf /home/user", "dangerous_command_blocked"),
        ("format C:", "dangerous_command_blocked"),
        ("rd /s /q C:\\Windows", "dangerous_command_blocked"),
        ("del /s /q *", "dangerous_command_blocked"),
        ("reg delete HKLM\\Software", "dangerous_command_blocked"),
        ("net user admin /delete", "dangerous_command_blocked"),
        ("shutdown /s /t 0", "dangerous_command_blocked"),
    ],
)
def test_shell_injection_patterns_blocked(tmp_path, cmd, reason):
    """shell injection patterns blocked"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_shell_command(cmd, source="test")

    assert result.allowed is False, f"Expected blocked for: {cmd}"
    assert result.reason == reason, f"Wrong reason for: {cmd}"


@pytest.mark.parametrize(
    "cmd",
    [
        "echo hello",
        "dir",
        "python --version",
        "git status",
        "docker ps",
    ],
)
def test_safe_shell_commands_allowed(tmp_path, cmd):
    """safe shell commands allowed"""
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)

    guard = ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())
    result = guard.validate_shell_command(cmd, source="test")

    assert result.allowed is True, f"Expected allowed for: {cmd}"


# ── B1（2026-09-11）：权限模型正交两维 ──────────────────────────────────────
# 「能做什么」（sandbox_level）与「要不要问」（approval_policy）应相互独立；
# 默认值必须等同既有行为（full-access / on-risk）。


def _make_guard(tmp_path, **settings):
    data_store = LocalDataStore(base_dir=str(tmp_path))
    settings_repo = LocalSettingsRepository(data_store)
    for key, value in settings.items():
        settings_repo.set_setting(key, value)
    return ActionGuard(settings_repository=settings_repo, observability_service=_FakeObservability())


def test_sandbox_default_full_access_keeps_everything(tmp_path):
    """默认 full-access：不额外限制，等同既有行为。"""
    guard = _make_guard(tmp_path)
    assert guard.validate("shell.run", payload={}, source="test").allowed is True
    assert guard.validate("file.write", payload={}, source="test").allowed is True


def test_sandbox_workspace_write_excludes_dangerous(tmp_path):
    """workspace-write：排除 shell.run / app.launch 等危险能力，保留写类。"""
    guard = _make_guard(tmp_path, assistant_sandbox_level="workspace-write")
    assert guard.validate("shell.run", payload={}, source="test").allowed is False
    assert guard.validate("app.launch", payload={}, source="test").allowed is False
    assert guard.validate("file.write", payload={}, source="test").allowed is True


def test_sandbox_read_only_limits_to_read_actions(tmp_path):
    """read-only：只保留明确列出的只读能力，且与用户白名单取交集。

    注意 memory.recall 不在默认白名单（是迁移/用户追加项），需显式加入
    whitelist 才可用——此用例同时验证 sandbox 与 whitelist 的交集语义。
    """
    guard = _make_guard(
        tmp_path,
        assistant_sandbox_level="read-only",
        assistant_action_whitelist=["memory.recall"],
    )
    assert guard.validate("file.read", payload={}, source="test").allowed is True
    assert guard.validate("memory.recall", payload={}, source="test").allowed is True
    assert guard.validate("file.write", payload={}, source="test").allowed is False
    assert guard.validate("clipboard.write", payload={}, source="test").allowed is False


def test_sandbox_invalid_level_falls_back(tmp_path):
    """非法档位回退 full-access（降级安全，不静默扩权也不误锁）。"""
    guard = _make_guard(tmp_path, assistant_sandbox_level="yolo-mode")
    assert guard.validate("shell.run", payload={}, source="test").allowed is True


def test_approval_policy_default_on_risk_matches_confirms(tmp_path):
    """默认 on-risk：高风险能力需确认，与既有 CONFIRM_TOOLS 语义一致。"""
    guard = _make_guard(tmp_path)
    for action in ("shell.run", "app.launch", "cron.add"):
        assert guard.should_require_confirmation(action) is True, action


def test_approval_policy_never_disables_confirmations(tmp_path):
    """never：无人值守场景显式声明不询问（含高风险）。"""
    guard = _make_guard(tmp_path, assistant_approval_policy="never")
    assert guard.should_require_confirmation("shell.run") is False
    assert guard.should_require_confirmation("app.launch") is False


def test_approval_policy_always_adds_writes(tmp_path):
    """always：写类能力也需确认（在 on-risk 基础上扩大）。"""
    guard = _make_guard(tmp_path, assistant_approval_policy="always")
    assert guard.should_require_confirmation("file.write") is True
    assert guard.should_require_confirmation("shell.run") is True


def test_approval_policy_invalid_falls_back(tmp_path):
    """非法策略回退 on-risk（保守，不静默放开审批）。"""
    guard = _make_guard(tmp_path, assistant_approval_policy="whatever")
    assert guard.should_require_confirmation("shell.run") is True
    assert guard.should_require_confirmation("file.write") is False


# ── B2 收尾（2026-09-11）：「始终允许」能授予必须能收回 ────────────────────


def test_revoke_always_allow_restores_confirmation(tmp_path):
    """授予 → 不再询问；收回 → 恢复询问（对称性核心断言）。"""
    guard = _make_guard(tmp_path)
    assert guard.should_require_confirmation("shell.run") is True
    assert guard.grant_always_allow("shell.run") is True
    assert guard.should_require_confirmation("shell.run") is False
    assert guard.revoke_always_allow("shell.run") is True
    assert guard.should_require_confirmation("shell.run") is True


def test_revoke_always_allow_is_idempotent(tmp_path):
    """移除不在名单中的项：幂等成功，不报错。"""
    guard = _make_guard(tmp_path)
    assert guard.revoke_always_allow("never.granted.action") is True


def test_revoke_always_allow_persists_across_instances(tmp_path):
    """收回写入 settings——共享同一存储的两个 guard 实例视图一致。"""
    data_store = LocalDataStore(base_dir=str(tmp_path / "runtime"))
    repo = LocalSettingsRepository(data_store)
    guard1 = ActionGuard(settings_repository=repo, observability_service=_FakeObservability())
    guard2 = ActionGuard(settings_repository=repo, observability_service=_FakeObservability())
    guard1.grant_always_allow("app.launch")
    assert guard2.should_require_confirmation("app.launch") is False
    guard2.revoke_always_allow("app.launch")
    assert guard1.should_require_confirmation("app.launch") is True


# ── 2026-09-15 修复：命名空间 action_id 被白名单误拦（P0）──────────────────
# 运行时派发的是 notes.list / tasks.search / inbox.list 这类命名空间 id，
# 而判定原先只认裸域名 notes / tasks / inbox → 22 个本地资源工具全量
# not_in_whitelist。以下用例锁定修复后的准入语义，同时锁定不得越权放宽。


@pytest.mark.parametrize("action_id", ["notes.list", "tasks.list", "tasks.search", "knowledge.query", "inbox.list"])
def test_local_resource_actions_allowed_by_default(tmp_path, action_id):
    """核心卖点工具：默认配置下必须放行（source=llm 走 butler 实际路径）。"""
    guard = _make_guard(tmp_path)
    result = guard.validate(action_id, payload={}, source="llm")
    assert result.allowed is True, f"{action_id} 被拦: {result.reason}"


def test_all_local_resource_actions_registered_in_default_whitelist(tmp_path):
    """22 个本地资源工具全部准入（不依赖逐个枚举，防止清单再次漂移）。"""
    from core.action_guard import LOCAL_RESOURCE_ACTIONS

    guard = _make_guard(tmp_path)
    blocked = {a: guard.validate(a, payload={}, source="user").reason for a in LOCAL_RESOURCE_ACTIONS if not guard.validate(a, payload={}, source="user").allowed}
    assert blocked == {}, f"本地资源工具被拦: {blocked}"
    assert len(LOCAL_RESOURCE_ACTIONS) == 22


def test_core_local_resource_list_matches_router_registry():
    """准入清单与执行侧接线必须是同一份（否则「注册了却被白名单拦」复发）。"""
    from assistant.capability._local_resource_tools import LOCAL_RESOURCE_ACTIONS as ROUTER_LIST
    from core.action_guard import LOCAL_RESOURCE_ACTIONS as CORE_LIST

    assert CORE_LIST == ROUTER_LIST


def test_unknown_action_still_blocked_after_namespace_fix(tmp_path):
    """修复不得演变成「什么都能过」：未登记动作仍按 not_in_whitelist 拦截。"""
    guard = _make_guard(tmp_path)
    for action_id in ("rm_rf_everything", "definitely.not.an.action", "hack_system"):
        result = guard.validate(action_id, payload={}, source="user")
        assert result.allowed is False, action_id
        assert result.reason == "not_in_whitelist", action_id


def test_local_resource_delete_still_requires_llm_confirmation(tmp_path):
    """放开命名空间不等于放开破坏性操作：LLM 调用删除仍需人工确认。"""
    guard = _make_guard(tmp_path)
    result = guard.validate("notes.delete", payload={"note_id": 1}, source="llm")
    assert result.allowed is False
    assert result.reason == "llm_source_high_risk"


def test_denylist_bare_namespace_blocks_local_resource_tool(tmp_path):
    """禁用裸域名 notes 必须连带拦住 notes.list（集合减法已不够，需双形态判定）。"""
    guard = _make_guard(tmp_path, assistant_action_denylist=["notes"])
    assert guard.validate("notes.list", payload={}, source="user").allowed is False
    assert guard.validate("tasks.list", payload={}, source="user").allowed is True


def test_denylist_exact_id_blocks_single_local_resource_tool(tmp_path):
    """按完整 id 精确禁用：只摘掉一项，同域其余不受影响。"""
    guard = _make_guard(tmp_path, assistant_action_denylist=["notes.delete"])
    assert guard.validate("notes.delete", payload={}, source="user").allowed is False
    assert guard.validate("notes.list", payload={}, source="user").allowed is True


def test_user_appended_bare_namespace_grants_domain(tmp_path):
    """用户在设置里追加裸域名时，语义仍是「整个域放行」。"""
    guard = _make_guard(tmp_path, assistant_action_whitelist=["memory"])
    assert guard.validate("memory.recall", payload={}, source="user").allowed is True


# ── 2026-09-15 收口：按来源区分匹配粒度 ────────────────────────────────────
# 代码默认组只做全名精确匹配（裸域不再隐式放行整域）；用户显式追加组保留
# 整域放行（设置页既有语义，不能破坏）。


def test_default_bare_domain_does_not_grant_unknown_sub_action(tmp_path):
    """核心验收：默认白名单里的裸域 notes 不得放行未登记的 notes.*。

    修复前（裸域整域放行）该断言为 True，属于代码默认值隐式授权同域未知
    能力。真实可用工具已显式登记在 LOCAL_RESOURCE_ACTIONS，无需裸域兜底。
    """
    guard = _make_guard(tmp_path)
    result = guard.validate("notes.nuke_everything", payload={}, source="user")
    assert result.allowed is False
    assert result.reason == "not_in_whitelist"
    # 对照组：已登记的真实工具不受影响
    assert guard.validate("notes.list", payload={}, source="user").allowed is True


def test_user_appended_bare_domain_still_grants_registered_tool(tmp_path):
    """兼容不破：用户在设置里追加裸域 notes 后，notes.list 仍放行。"""
    guard = _make_guard(tmp_path, assistant_action_whitelist=["notes"])
    assert guard.validate("notes.list", payload={}, source="user").allowed is True


def test_batch_delete_requires_llm_confirmation(tmp_path):
    """批量软删纳入 LLM 高危：LLM 调用必须人工确认，用户直接调用不受影响。"""
    guard = _make_guard(tmp_path)
    result = guard.validate("notes.batch_delete", payload={"note_ids": "1,2,3"}, source="llm")
    assert result.allowed is False
    assert result.reason == "llm_source_high_risk"
    assert guard.validate("notes.batch_delete", payload={}, source="user").allowed is True


def test_read_only_profile_allows_local_read_tools(tmp_path):
    """read-only 档位下本地资源的只读查询必须可用（否则档位形同虚设）。"""
    guard = _make_guard(tmp_path, assistant_sandbox_level="read-only")
    for action_id in ("notes.list", "tasks.list", "inbox.list", "notes.get", "tasks.get", "notes.search", "tasks.search", "info.brief"):
        assert guard.validate(action_id, payload={}, source="user").allowed is True, action_id
    # 写类仍被 read-only 档位掐掉
    assert guard.validate("notes.create", payload={}, source="user").allowed is False
    assert guard.validate("notes.delete", payload={}, source="user").allowed is False
