"""T1 挖出的三个缺陷的修复测试（2026-10-15）。

1. `sandbox.approve` 假成功 → `_interpret_action_result` 判定器
2. `tasks.delete` 静默失效 → 删后复核（集成层验证见 T1 复测）
3. task/reminder id 冲突 → 生成式唯一化
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._sandbox import _interpret_action_result  # noqa: E402


class TestInterpretActionResult:
    """审批重放结果的语义判定（防止"批准成功但动作没做成"）。"""

    def test_false_is_failure(self) -> None:
        """布尔 False（如 tasks.delete 真失败）→ 必须回报失败。"""
        ok, reason = _interpret_action_result(False)
        assert ok is False
        assert "False" in reason

    def test_success_false_dict_is_failure(self) -> None:
        ok, reason = _interpret_action_result({"success": False, "error": "entry not found"})
        assert ok is False
        assert "entry not found" in reason

    def test_error_dict_is_failure(self) -> None:
        """带 error 且未显式 success=True → 失败。"""
        ok, reason = _interpret_action_result({"error": "not found"})
        assert ok is False
        assert "not found" in reason

    def test_none_is_success(self) -> None:
        """None 表示"动作已完成"（多数破坏性动作的返回）→ 不误报失败。"""
        assert _interpret_action_result(None) == (True, "")

    def test_true_is_success(self) -> None:
        assert _interpret_action_result(True) == (True, "")

    def test_success_true_with_error_still_success(self) -> None:
        """显式 success=True（即使带非致命 error 字段）→ 尊重显式语义。"""
        assert _interpret_action_result({"success": True, "error": "warn"}) == (True, "")

    def test_plain_payload_is_success(self) -> None:
        assert _interpret_action_result({"folders": ["a"]}) == (True, "")
        assert _interpret_action_result([1, 2]) == (True, "")
        assert _interpret_action_result("ok") == (True, "")

    def test_empty_dict_is_success(self) -> None:
        assert _interpret_action_result({}) == (True, "")


class TestTaskIdUniqueness:
    """task/reminder id 生成必须唯一（原式秒级时间戳，同秒创建即撞）。"""

    def test_add_task_uses_ms_and_random(self) -> None:
        src = (APP_ROOT / "data" / "repositories" / "desknotes_task_adapter.py").read_text(encoding="utf-8")
        # add_task 的生成式：毫秒 + 随机后缀
        assert re.search(r"siyi_task_\{now_ms\}_", src), "add_task 的 uuid 应以毫秒时间戳生成"
        assert "_uuid_mod.uuid4().hex" in src, "缺少随机后缀（同毫秒仍可能撞）"
        # 旧的秒级生成式不得复活
        assert "siyi_task_{int(now_ms / 1000)}" not in src, "秒级 id 生成式（同秒冲突源）不得回潮"

    def test_fallback_branch_warns_and_unique(self) -> None:
        """映射缺失的兜底分支：必须留痕（静默降级是 P1 缺陷根因）。"""
        src = (APP_ROOT / "data" / "repositories" / "desknotes_task_adapter.py").read_text(encoding="utf-8")
        assert "任务 id 映射缺失" in src, "兜底分支应 logger.warning 留痕"
