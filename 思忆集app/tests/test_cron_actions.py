"""M-cron 契约测试：cron 任务经 gateway.submit 直走内核（而非旧 subprocess）。

验证：
- ``kernel.cron_demo`` 已注册且走稳定区注册表。
- 经 ``execute_cron_action`` 调用后，确实通过 ``context.agent_gateway.submit`` 路径
  （无真实内核时返回 unavailable，仍证明路径接通，不回退旧 router）。
"""
from __future__ import annotations

import types
from typing import Any

from services.agent_gateway.adapters import NoneGateway
from services.cron_actions import execute_cron_action, list_cron_actions


def _ctx() -> Any:
    # 假上下文（duck-typing）：handler 全部经 getattr 防御访问，Any 表达"运行期即真身"
    return types.SimpleNamespace(agent_gateway=NoneGateway())


def test_kernel_cron_demo_registered() -> None:
    ids = {a["action_id"] for a in list_cron_actions()}
    assert "kernel.cron_demo" in ids
    assert "kernel.submit" in ids


def test_kernel_cron_demo_routes_through_gateway() -> None:
    res = execute_cron_action(_ctx(), "kernel.cron_demo", {})
    assert res is not None
    assert res.get("demo") is True
    # 走的是 gateway.submit（NoneGateway → unavailable），不是旧 router
    assert res["kernel"] == "none"
    assert res["status"] == "unavailable"
    assert res["success"] is False
    assert res["error_kind"] == "unavailable"


def test_kernel_cron_demo_uses_provided_prompt() -> None:
    res = execute_cron_action(_ctx(), "kernel.cron_demo", {"prompt": "do something"})
    assert res is not None
    assert res.get("demo") is True
    assert res["kernel"] == "none"


def test_kernel_cron_demo_unknown_action_still_none() -> None:
    # 回归保护：未知 action 仍返回 None（过渡期白名单语义不变）
    assert execute_cron_action(_ctx(), "no.such.action", {}) is None


# --- M4 技能卡：cron → 内核（gateway.submit 直走 dsh） -------------------------------------


def _ctx_with_gateway(gw) -> Any:
    return types.SimpleNamespace(agent_gateway=gw)


def _capturing_dsh_gateway():
    """模拟 DshGateway.submit，记录实际传入的 profile。"""
    captured: dict = {}

    def submit(prompt, timeout_s=None, profile=None):
        captured["prompt"] = prompt
        captured["timeout_s"] = timeout_s
        captured["profile"] = profile
        return types.SimpleNamespace(ok=True, status="succeeded", result="ok", error=None, raw={})

    gw = types.SimpleNamespace(name="dsh", submit=submit)
    return gw, captured


def test_skill_cards_registered() -> None:
    ids = {a["action_id"] for a in list_cron_actions()}
    for sid in ("daily-news-digest", "knowledge-digest", "inbox-triage", "memory-anchor"):
        assert sid in ids


def test_load_skill_card_frontmatter_and_profile() -> None:
    from services.skill_cards import load_skill_card

    fast = load_skill_card("daily-news-digest")
    assert fast is not None
    assert fast.name == "每日资讯简报"
    assert fast.kernel == "fast"
    assert fast.resolve_profile() == "siyi-fast"

    deep = load_skill_card("memory-anchor")
    assert deep is not None
    assert deep.kernel == "deep"
    assert deep.resolve_profile() == "siyi-deep"


def test_skill_card_runs_through_gateway_fast_profile() -> None:
    gw, cap = _capturing_dsh_gateway()
    res = execute_cron_action(_ctx_with_gateway(gw), "daily-news-digest", {})
    assert res is not None
    assert res["skill_id"] == "daily-news-digest"
    assert res["profile"] == "siyi-fast"
    assert cap["profile"] == "siyi-fast"
    assert res["success"] is True
    assert "你是思忆集的执行内核" in cap["prompt"]


def test_skill_card_memory_anchor_uses_deep_profile() -> None:
    gw, cap = _capturing_dsh_gateway()
    res = execute_cron_action(_ctx_with_gateway(gw), "memory-anchor", {})
    assert res is not None
    assert res["skill_id"] == "memory-anchor"
    assert cap["profile"] == "siyi-deep"
    assert res["profile"] == "siyi-deep"


def test_skill_card_missing_returns_skipped(monkeypatch) -> None:
    import services.skill_cards as sc

    monkeypatch.setattr(sc, "load_skill_card", lambda sid: None)
    gw, _ = _capturing_dsh_gateway()
    res = execute_cron_action(_ctx_with_gateway(gw), "daily-news-digest", {})
    assert res is not None
    assert res["status"] == "skipped"
    assert res["reason"] == "skill_card_not_found"


# --- debt-v2 A-1：proactive.tick 缺 assistant_inbox_service 不得失败（回归保护） -------------


def _proactive_ctx() -> Any:
    """最小 context：模拟"旧代码引用不存在的属性"场景的回归保护。

    2026-08-29 修正：夹具此前提供 task_repository / note_repository，而 AppContext
    真实注入名是 personal_task_repository / personal_note_repository —— 用错误契约
    的 mock 验证，导致生产代码属性漂移时测试依然全绿（主动管家失效数月无人发现）。
    现一律使用 AppContext 真实属性名。
    """
    return types.SimpleNamespace(
        settings_repository=types.SimpleNamespace(get_setting=lambda key, default=None: default),
        local_data_store=None,
        user_profile_service=None,
        personal_task_repository=None,
        personal_note_repository=None,
        assistant_inbox_service=None,
        # environment_probe 未提供：它只存在于 assistant/capability_router，
        # AppContext 从不注入 → 生产里 getattr(context,"environment_probe") 恒为 None
        # （已登记为 2026-08-29 审查 L9 死接线）。夹具如实反映生产，不做美化。
    )


def test_proactive_ctx_uses_real_appcontext_attribute_names() -> None:
    """夹具属性名必须全部是 AppContext 真实注入名。

    反例（2026-08-29 H1）：夹具用 task_repository / note_repository，而真实注入名是
    personal_*，于是"生产漂移"与"夹具漂移"互相掩盖，测试永远绿而主动管家一直死。
    """
    from tests.test_context_attr_consistency import _builder_injected

    injected = _builder_injected()
    provided = set(vars(_proactive_ctx()))
    bogus = sorted(provided - injected)
    assert not bogus, (
        "proactive.tick 夹具含 AppContext 未注入的属性名，会掩盖生产接线漂移："
        f"{bogus}（真实注入集示例：{sorted(injected)[:8]}）"
    )


def test_proactive_tick_missing_inbox_service_completes() -> None:
    # 修复前：cron_actions.py 引用 context.inbox_service（死属性）→ AttributeError → status=failed
    # 修复后：改走 assistant_inbox_service，缺失时正常 completed（不投递建议但不得失败）
    res = execute_cron_action(_proactive_ctx(), "proactive.tick", {})
    assert res is not None
    assert res.get("status") == "completed"
    assert "inbox_service" not in str(res.get("error", ""))
