# -*- coding: utf-8 -*-
"""U0 壳循环实测（2026-09-19）三缺陷修复的机器断言。

发现链：隔离后端 + DeepSeek v4 flash 对话层探针（verify_tmp/shell-loop-u0/）——
1. notes.get 严格 == 比较 int id vs str '2' 恒不命中 → 笔记不存在 →
   模型 workaround 螺旋烧光 12 步预算；notes.update 无条件 updated=True
   （str id 静默 no-op = 假成功，P0-D 家族）；notes.delete 同病。
2. UsageLimitExceeded（12 步预算耗尽）落 unknown_error →「生成失败，请重试」。
3. weather_service._build_weather_snapshot KeyError('pending_tasks')——
   chat_context_anchor 每条消息传空 snapshot 刷 Traceback。
"""

from pathlib import Path

import pytest

from services.weather_service import WeatherService

REPO = Path(__file__).resolve().parents[3]


# ── 1. notes 工具 id 契约：规范化比较 + 诚实返回 ─────────────────────────────


class _FakeNoteRepo:
    """最小仓储：模拟 DeskNotesNoteAdapter 的 S0-1 诚实布尔契约——
    str|int 原生解析（执行器透传，不做归一），未知 id 返回 False。"""

    def __init__(self):
        self._notes = [{"id": 2, "title": "壳测A", "content": "x", "tags": []}]
        self.updates: list = []
        self.deletes: list = []

    def list_notes(self):
        return [dict(n) for n in self._notes]

    def update_note(self, note_id, info):
        self.updates.append((note_id, info))
        if str(note_id) == "2":
            self._notes[0].update(info)
            return True
        return False

    def delete_note(self, note_id, soft=True):
        self.deletes.append(note_id)
        if str(note_id) == "2":
            self._notes = [n for n in self._notes if n["id"] != 2]
            return True
        return False


class _Ctx:
    """C-1 步骤 3 重指向：notes.* 实现已从 B 套内联分支迁入 A 套
    ``assistant/capability/_local_resource_tools.py``，故 context 必须挂
    ``capability_router``（构造法同 tests/test_assistant_capability_router.py）。
    断言本身一字不改——U0 三缺陷的修复语义由 A 套继续提供。"""

    def __init__(self):
        import tempfile

        from assistant.capability_router import AssistantCapabilityRouter
        from data.local_storage import LocalDataStore

        self.personal_note_repository = _FakeNoteRepo()
        self.capability_router = AssistantCapabilityRouter(
            data_store=LocalDataStore(base_dir=tempfile.mkdtemp(prefix="siyi-c1-notes-")),
            settings_repository=None,
            observability_service=None,
            note_repository=self.personal_note_repository,
            task_repository=None,
            news_repository=None,
            inbox_repository=None,
        )
        self.capability_router.shell_execution.workspace_root = Path(tempfile.mkdtemp(prefix="siyi-c1-notes-ws-"))


def _run_action(action_id, args, ctx=None):
    from services.react_executor import make_react_executor

    return make_react_executor(ctx or _Ctx())(action_id, args)


def test_notes_get_accepts_string_id():
    out = _run_action("notes.get", {"note_id": "2"})
    assert out.get("result", {}).get("title") == "壳测A", f"str id 应命中: {out}"


def test_notes_get_missing_id_is_honest():
    out = _run_action("notes.get", {"note_id": "999"})
    assert "error" in out and "不存在" in out["error"]


def test_notes_update_string_id_actually_updates():
    ctx = _Ctx()
    from services.react_executor import make_react_executor

    out = make_react_executor(ctx)("notes.update", {"note_id": "2", "title": "壳测B"})
    assert out.get("result", {}).get("updated") is True
    assert ctx.personal_note_repository.updates[0][0] == "2", "id 应原样透传（归一是适配器职责）"
    assert ctx.personal_note_repository.list_notes()[0]["title"] == "壳测B", "更新必须真落盘"


def test_notes_update_missing_id_no_fake_success():
    out = _run_action("notes.update", {"note_id": "999", "title": "x"})
    assert out.get("result", {}).get("updated") is False, f"假成功复发: {out}"


def test_notes_delete_string_id_honest():
    ctx = _Ctx()
    from services.react_executor import make_react_executor
    from services.sandbox_manager import _in_approval_var

    # notes.delete 是破坏性动作：直接调用必须先撞 R4 审批闸（pending 信封）
    out = make_react_executor(ctx)("notes.delete", {"note_id": "2"})
    assert out.get("requires_approval") is True and out.get("pending_id"), f"审批闸失效: {out}"

    # 已批准路径（_in_approval_var 置位）下：str id 真删 + 不存在→诚实报错
    token = _in_approval_var.set(True)
    try:
        out_ok = make_react_executor(ctx)("notes.delete", {"note_id": "2"})
        assert out_ok.get("result", {}).get("deleted") is True, f"批准后应真删: {out_ok}"
        assert ctx.personal_note_repository.deletes == ["2"]

        out2 = make_react_executor(ctx)("notes.delete", {"note_id": "999"})
        assert out2.get("result", {}).get("deleted") is False, "删不存在的笔记不得假成功"
    finally:
        _in_approval_var.reset(token)


# ── 2. 步数预算耗尽分型 ───────────────────────────────────────────────────────


def test_grade_usage_limit_exceeded():
    from backend.handlers.chat_paths.helpers import grade_stream_exception

    class UsageLimitExceeded(Exception):
        pass

    exc = UsageLimitExceeded("The next request would exceed the request_limit of 12")
    payload = grade_stream_exception(exc)
    assert payload["error_kind"] == "budget_exhausted", f"分型漂移: {payload['error_kind']}"
    assert "预算" in payload["friendly"]
    assert payload["transient"] is False

    # 文本兜底形态（无类名特征时）
    payload2 = grade_stream_exception(RuntimeError("exceed the request_limit of 12"))
    assert payload2["error_kind"] == "budget_exhausted"


def test_no_degrade_on_budget_exhaustion_structure():
    """预算耗尽分支必须先于通用降级分支（结构断言，防回潮）。"""
    src = (REPO / "思忆集app" / "backend" / "handlers" / "chat_paths" / "pydantic_agent_path.py").read_text(encoding="utf-8")
    bi = src.index("budget_exhausted")
    di = src.index("pydantic path failed before any output, will degrade")
    assert bi < di, "budget_exhausted 分支必须出现在通用降级说明之前"


# ── 3. weather snapshot 可选性 ────────────────────────────────────────────────


def test_weather_snapshot_empty_dict_no_keyerror():
    import datetime as _dt

    svc = WeatherService(None, "北京", "北京")
    snap = svc.get_snapshot(_dt.datetime(2026, 9, 19, 10, 0), {}, force_refresh=True)
    assert isinstance(snap, dict) and snap, "空 snapshot 应产出兜底快照而非 KeyError"


def test_weather_source_no_direct_keyerror():
    src = (REPO / "思忆集app" / "services" / "weather_service.py").read_text(encoding="utf-8")
    assert 'snapshot["pending_tasks"]' not in src, "不得用硬下标读可选 snapshot"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
