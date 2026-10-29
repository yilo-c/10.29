"""audit.list 快照摘要化测试（2026-10-15）。

背景（U1 实测）：``before_snapshot``/``after_snapshot`` 存完整变更前后内容
（生产实测单条最大 66KB、平均 5.1KB），audit.list 默认 100 条 → **712KB 响应**，
逼近 WS 1MB 帧上限。前端 AuditEntry 接口零引用这两个字段，详情走 audit.get，
故列表压成预览（保留结构 + 标记截断）。
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._audit import _SNAPSHOT_PREVIEW_CHARS, _summarize_for_list  # noqa: E402


class TestSummarizeForList:
    def test_large_snapshot_truncated_with_markers(self) -> None:
        """超长快照 → 截断 + *_truncated + *_chars（信息不丢，只压体积）。"""
        big = {"content": "x" * 5000}
        entry = {"entry_id": "e1", "action_id": "notes.update", "before_snapshot": big}
        out = _summarize_for_list(entry)
        assert len(out["before_snapshot"]) == _SNAPSHOT_PREVIEW_CHARS
        assert out["before_snapshot_truncated"] is True
        assert out["before_snapshot_chars"] > 5000  # JSON 包装后更大
        assert out["entry_id"] == "e1"  # 小字段原样

    def test_small_snapshot_untouched(self) -> None:
        """小快照不截断、不加标记。"""
        entry = {"entry_id": "e1", "before_snapshot": {"a": 1}}
        out = _summarize_for_list(entry)
        assert out["before_snapshot"] == {"a": 1}
        assert "before_snapshot_truncated" not in out

    def test_none_snapshot_preserved(self) -> None:
        """None 快照保持 None（不误标记）。"""
        entry = {"after_snapshot": None}
        out = _summarize_for_list(entry)
        assert out["after_snapshot"] is None
        assert "after_snapshot_truncated" not in out

    def test_string_snapshot_handled(self) -> None:
        """字符串形态的快照同样处理。"""
        entry = {"before_snapshot": "y" * 400}
        out = _summarize_for_list(entry)
        assert len(out["before_snapshot"]) == _SNAPSHOT_PREVIEW_CHARS
        assert out["before_snapshot_truncated"] is True

    def test_non_dict_passthrough(self) -> None:
        """非 dict 条目原样返回（不炸）。"""
        assert _summarize_for_list("oops") == "oops"
        assert _summarize_for_list(None) is None

    def test_does_not_mutate_input(self) -> None:
        """摘要走副本，不改调用方数据。"""
        entry = {"before_snapshot": {"content": "z" * 5000}}
        _summarize_for_list(entry)
        assert isinstance(entry["before_snapshot"], dict)
        assert len(entry["before_snapshot"]["content"]) == 5000

    def test_volume_reduction_estimate(self) -> None:
        """100 条各 5KB 快照 → 摘要后总量下降一个数量级（防回归的量级断言）。"""
        import json

        entries = [{"entry_id": f"e{i}", "action_id": "notes.update", "before_snapshot": {"content": "a" * 5000}} for i in range(100)]
        raw = len(json.dumps(entries, ensure_ascii=False))
        summarized = len(json.dumps([_summarize_for_list(e) for e in entries], ensure_ascii=False))
        assert summarized < raw / 10, f"摘要后仅降到 {summarized}/{raw}，未达一个数量级"
