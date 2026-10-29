"""测试：低打扰建议策略 — 建议应解释原因、支持关闭、可转换动作。"""

from __future__ import annotations

from domain.suggestion_models import (
    SUGGESTION_STATUS_ACTIVE,
    SUGGESTION_STATUS_DISMISSED,
    SuggestionItem,
)


class TestSuggestionItemModel:
    """SuggestionItem 数据模型测试。"""

    def test_create_with_default_id(self):
        item = SuggestionItem(text="测试建议", action_key="tasks")
        assert item.suggestion_id
        assert len(item.suggestion_id) == 12
        assert item.text == "测试建议"
        assert item.action_key == "tasks"
        assert item.status == SUGGESTION_STATUS_ACTIVE

    def test_create_with_explicit_id(self):
        item = SuggestionItem(suggestion_id="abc123", text="测试", action_key="notes")
        assert item.suggestion_id == "abc123"

    def test_default_convertible_actions(self):
        """默认有转笔记和转任务两个可转换动作。"""
        item = SuggestionItem(text="测试", action_key="capture")
        assert len(item.convertible_actions) >= 2
        actions = {a["action"] for a in item.convertible_actions}
        assert "notes" in actions
        assert "tasks" in actions

    def test_dismiss(self):
        item = SuggestionItem(text="测试", action_key="notes")
        assert item.status == SUGGESTION_STATUS_ACTIVE
        item.dismiss()
        assert item.status == SUGGESTION_STATUS_DISMISSED

    def test_to_dict_roundtrip(self):
        original = SuggestionItem(
            text="收件箱有内容",
            action_key="organize_current",
            reason="收件箱有 3 条未整理",
            convertible_actions=[{"label": "转笔记", "action": "notes"}],
        )
        data = original.to_dict()
        restored = SuggestionItem.from_dict(data)
        assert restored.suggestion_id == original.suggestion_id
        assert restored.text == original.text
        assert restored.action_key == original.action_key
        assert restored.reason == original.reason
        assert restored.convertible_actions == original.convertible_actions

    def test_from_dict_missing_id(self):
        data = {"text": "测试", "action_key": "tasks"}
        restored = SuggestionItem.from_dict(data)
        assert restored.suggestion_id
        assert restored.text == "测试"

    def test_custom_convertible_actions(self):
        """自定义可转换动作。"""
        custom = [{"label": "打开新闻", "action": "news"}]
        item = SuggestionItem(text="测试", action_key="news", convertible_actions=custom)
        assert len(item.convertible_actions) == 1
        assert item.convertible_actions[0]["action"] == "news"


class TestWorkbenchServiceSuggestions:
    """验证 workbench_service 输出的建议字段完整性。"""

    def _make_snapshot(self, **overrides):
        return {
            "pending_tasks": [],
            "inbox_entries": [],
            "notes": [],
            "translations": [],
            "favorite_news_keys": [],
            "improvement_proposals": [],
            **overrides,
        }

    def test_suggestions_include_reason(self):
        """每条建议都应包含 reason 字段，解释为什么现在展示。"""
        from workbench.service import PersonalWorkbenchService

        service = PersonalWorkbenchService(None, None, None, None)
        snapshot = self._make_snapshot(inbox_entries=[{"id": "1"}], notes=[{"id": "1"}])
        suggestions = service.build_context_suggestions(snapshot)
        assert len(suggestions) > 0
        for s in suggestions:
            assert "reason" in s, f"建议缺少 reason: {s.get('text', '')}"
            assert s["reason"], f"reason 不能为空: {s.get('text', '')}"

    def test_suggestions_include_convertible_actions(self):
        from workbench.service import PersonalWorkbenchService

        service = PersonalWorkbenchService(None, None, None, None)
        snapshot = self._make_snapshot(inbox_entries=[{"id": "1"}])
        suggestions = service.build_context_suggestions(snapshot)
        for s in suggestions:
            assert "convertible_actions" in s, f"建议缺少 convertible_actions: {s.get('text', '')}"
            assert isinstance(s["convertible_actions"], list)
            if s["convertible_actions"]:
                assert "label" in s["convertible_actions"][0]
                assert "action" in s["convertible_actions"][0]

    def test_suggestions_capped_at_6(self):
        from workbench.service import PersonalWorkbenchService

        service = PersonalWorkbenchService(None, None, None, None)
        # 触发所有建议条件
        snapshot = self._make_snapshot(
            inbox_entries=[{"id": str(i)} for i in range(3)],
            pending_tasks=[{"id": str(i)} for i in range(3)],
            notes=[{"id": "n1"}],
            translations=[{"id": "t1"}],
            favorite_news_keys=["k1"],
            improvement_proposals=[{"status": "pending_review"}],
        )
        suggestions = service.build_context_suggestions(snapshot)
        assert len(suggestions) <= 6

    def test_suggestions_without_data(self):
        """没有输入数据时，仍应给出至少一条建议（如"收件"建议）。"""
        from workbench.service import PersonalWorkbenchService

        service = PersonalWorkbenchService(None, None, None, None)
        snapshot = self._make_snapshot()
        suggestions = service.build_context_suggestions(snapshot)
        assert len(suggestions) >= 1

    def test_reason_matches_context(self):
        """reason 应反映建议触发的原因。"""
        from workbench.service import PersonalWorkbenchService

        service = PersonalWorkbenchService(None, None, None, None)
        # 有 3 条待办时，"待办"的建议 reason 应提到数量
        snapshot = self._make_snapshot(pending_tasks=[{"id": str(i)} for i in range(3)])
        suggestions = service.build_context_suggestions(snapshot)
        task_suggestions = [s for s in suggestions if s.get("action") == "tasks"]
        if task_suggestions:
            reason = task_suggestions[0].get("reason", "")
            assert "待办" in reason or "注意力" in reason


class TestSuggestionPlanner:
    """验证 AssistantSuggestionPlanner 输出完整性。"""

    def _make_snapshot(self, **overrides):
        return {
            "pending_tasks": [],
            "inbox_entries": [],
            "notes": [],
            "translations": [],
            "favorite_news_keys": [],
            "improvement_proposals": [],
            "tasks": [],
            "news_cache_items": [],
            "news_records": [],
            **overrides,
        }

    def test_suggestions_have_ids(self):
        """planner 输出的每条建议应带 suggestion_id。"""
        from assistant.suggestion_planner import AssistantSuggestionPlanner

        planner = AssistantSuggestionPlanner()
        snapshot = self._make_snapshot(pending_tasks=[{"id": "1"}])
        suggestions = planner.build_suggestions(snapshot)
        assert len(suggestions) > 0
        for s in suggestions:
            assert "suggestion_id" in s, f"建议缺少 suggestion_id: {s.get('text', '')}"
            assert s["suggestion_id"], f"suggestion_id 不能为空"

    def test_suggestions_have_reason(self):
        from assistant.suggestion_planner import AssistantSuggestionPlanner

        planner = AssistantSuggestionPlanner()
        snapshot = self._make_snapshot(pending_tasks=[{"id": "1"}])
        suggestions = planner.build_suggestions(snapshot)
        for s in suggestions:
            assert "reason" in s, f"建议缺少 reason: {s.get('text', '')}"

    def test_suggestions_deduplicated_and_capped(self):
        from assistant.suggestion_planner import AssistantSuggestionPlanner

        planner = AssistantSuggestionPlanner()
        snapshot = self._make_snapshot(
            pending_tasks=[{"id": "1"}],
            notes=[{"id": "1"}],
        )
        suggestions = planner.build_suggestions(snapshot)
        assert len(suggestions) <= 6
        # 检查无重复
        seen = set()
        for s in suggestions:
            key = (s.get("text"), s.get("action"))
            assert key not in seen, f"重复建议: {key}"
            seen.add(key)

    def test_planner_passes_convertible_actions(self):
        from assistant.suggestion_planner import AssistantSuggestionPlanner

        planner = AssistantSuggestionPlanner()
        snapshot = self._make_snapshot(pending_tasks=[{"id": "1"}])
        suggestions = planner.build_suggestions(snapshot)
        for s in suggestions:
            assert "convertible_actions" in s

    def test_planner_with_workbench_service(self):
        """通过 workbench_service 的建议应保留 reason 和 convertible_actions。"""
        from assistant.suggestion_planner import AssistantSuggestionPlanner
        from workbench.service import PersonalWorkbenchService

        planner = AssistantSuggestionPlanner(PersonalWorkbenchService(None, None, None, None))
        snapshot = {
            "pending_tasks": [{"id": "1"}],
            "inbox_entries": [{"id": "1"}],
            "notes": [{"id": "1"}],
            "translations": [{"id": "1"}],
            "favorite_news_keys": ["k1"],
            "improvement_proposals": [],
        }
        suggestions = planner.build_suggestions(snapshot)
        for s in suggestions:
            assert s.get("reason"), f"通过 workbench 的建议缺少 reason: {s.get('text')}"
            assert s.get("convertible_actions"), f"通过 workbench 的建议缺少 convertible_actions: {s.get('text')}"
