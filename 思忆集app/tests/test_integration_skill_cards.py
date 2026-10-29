"""L2 集成测试：M4 内核技能卡（cron → kernel.submit 直走 dsh）

在真实后端（conftest 自启临时后端）加载后，校验：
1. 后端存活（经 RPC ping）；
2. 4 张技能卡已注册进 cron_actions 稳定区注册表；
3. 4 张技能卡数据文件 front-matter 解析正确、成本档→dsh profile 映射正确；
4. 渲染 prompt 形态正确（内核执行入口）。

注：注册发生在 `services.cron_actions` 模块加载期的 `_register_skill_cards()`，
与后端启动路径一致；本测试在同一进程加载该模块，等价于校验后端启动后注册结果。
单元层行为（gateway 快/深档分发、缺卡跳过）已由 `tests/test_cron_actions.py` 覆盖。
"""
import pytest

from services.cron_actions import list_cron_actions
from services.skill_cards import (
    KERNEL_TO_DSH_PROFILE,
    load_skill_card,
    render_skill_prompt,
)

pytestmark = pytest.mark.integration

SKILL_CARDS = ("daily-news-digest", "knowledge-digest", "inbox-triage", "memory-anchor")


class TestSkillCardBackendLive:
    """后端存活冒烟（确认集成 harness 连上真实后端）。"""

    async def test_backend_reachable(self, rpc):
        r = await rpc("ping", {})
        # ping 返回 {"result": "pong"}
        assert r.get("result") == "pong" or r.get("result") is not None


class TestSkillCardRegistration:
    """4 张技能卡已注册进稳定区 cron_actions 注册表。"""

    async def test_all_four_registered(self):
        ids = {a["action_id"] for a in list_cron_actions()}
        for sid in SKILL_CARDS:
            assert sid in ids, f"技能卡未注册: {sid}"

    async def test_no_unexpected_skill_card_count(self):
        """注册表应至少包含 4 张技能卡（防止误删）。"""
        ids = {a["action_id"] for a in list_cron_actions()}
        registered = ids & set(SKILL_CARDS)
        assert len(registered) == len(SKILL_CARDS)


class TestSkillCardDataShape:
    """技能卡数据文件形态：front-matter 解析 + 成本档→profile 映射。"""

    @pytest.mark.parametrize("skill_id", SKILL_CARDS)
    async def test_card_loads_and_has_name(self, skill_id):
        card = load_skill_card(skill_id)
        assert card is not None, f"技能卡文件缺失或损坏: {skill_id}"
        assert card.name, f"技能卡缺 name: {skill_id}"

    async def test_fast_card_maps_to_siyi_fast(self):
        fast = load_skill_card("daily-news-digest")
        assert fast is not None
        assert fast.kernel == "fast"
        assert fast.resolve_profile() == KERNEL_TO_DSH_PROFILE["fast"] == "siyi-fast"

    async def test_deep_card_maps_to_siyi_deep(self):
        deep = load_skill_card("memory-anchor")
        assert deep is not None
        assert deep.kernel == "deep"
        assert deep.resolve_profile() == KERNEL_TO_DSH_PROFILE["deep"] == "siyi-deep"

    @pytest.mark.parametrize(
        "skill_id,expected_kernel",
        [
            ("daily-news-digest", "fast"),
            ("knowledge-digest", "fast"),
            ("inbox-triage", "fast"),
            ("memory-anchor", "deep"),
        ],
    )
    async def test_kernel_field_consistent(self, skill_id, expected_kernel):
        card = load_skill_card(skill_id)
        assert card is not None
        assert card.kernel == expected_kernel


class TestSkillCardPromptRender:
    """render_skill_prompt 产出内核执行形态。"""

    async def test_prompt_contains_kernel_entry_marker(self):
        card = load_skill_card("daily-news-digest")
        prompt = render_skill_prompt(card, {})
        assert "你是思忆集的执行内核" in prompt
        assert card.name in prompt

    async def test_prompt_includes_resolved_profile(self):
        card = load_skill_card("memory-anchor")
        prompt = render_skill_prompt(card, {})
        assert "siyi-deep" in prompt

    async def test_prompt_includes_payload_context(self):
        card = load_skill_card("inbox-triage")
        prompt = render_skill_prompt(card, {"context": "集成测试上下文"})
        assert "集成测试上下文" in prompt
