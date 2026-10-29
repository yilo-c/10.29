"""Behavior archive scheduler unit tests — 行为日志每日归档调度器（T5.3）。

覆盖：run_once 触发归档 + 归档目录上限清理、按日期去重、归档目录清理只保留最近 N 个。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend import behavior_archive_scheduler


class _FakeDataStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._store: dict[str, Any] = {}

    def save_json(self, key: str, data: Any) -> None:
        self._store[key] = data

    def load_json(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)


class _FakeBehaviorLogger:
    """模拟 BehaviorLogger：记录归档调用，并提供可操作的归档目录。"""

    def __init__(self, archive_dir: str):
        self._archive_dir = archive_dir
        self.archive_calls = 0

    def archive_old_events(self) -> int:
        self.archive_calls += 1
        return 3  # 模拟归档了 3 条


def _make_context(archive_dir: str, data_dir: str) -> object:
    logger = _FakeBehaviorLogger(archive_dir)
    ctx = type(
        "FakeContext",
        (),
        {
            "local_data_store": _FakeDataStore(data_dir),
            "behavior_logger": logger,
        },
    )()
    ctx._behavior_logger = logger  # 便于断言
    return ctx


def _make_archive_files(archive_dir: Path, n: int) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        # 日期戳按 i 递增，最新（最大）排后面
        (archive_dir / f"behavior_events_{20260101 + i}.jsonl").write_text(
            json.dumps({"ts": i}), encoding="utf-8",
        )


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_once_archives_and_cleans(tmp_path: Path) -> None:
    archive_dir = tmp_path / "behavior_archives"
    _make_archive_files(archive_dir, n=40)  # 40 个归档文件，超上限 30
    ctx = _make_context(str(archive_dir), str(tmp_path))

    result = await behavior_archive_scheduler.run_once(ctx)

    assert result["archived"] == 3
    assert result["cleaned"] == 10  # 40 - 30
    # 只剩最近 30 个
    remaining = list(archive_dir.glob("*.jsonl"))
    assert len(remaining) == 30
    # 保留了最新（日期最大）的 30 个：20260111..20260140，最旧的 20260101 已删
    names = sorted(f.name for f in remaining)
    assert names[0] == "behavior_events_20260111.jsonl"
    assert names[-1] == "behavior_events_20260140.jsonl"


@pytest.mark.asyncio
async def test_run_once_no_context_returns_error(tmp_path: Path) -> None:
    behavior_archive_scheduler.set_context(None)
    result = await behavior_archive_scheduler.run_once(None)
    assert "error" in result


@pytest.mark.asyncio
async def test_run_once_dedup_same_day(tmp_path: Path) -> None:
    """同一天第二次 run_once 不应重复归档（幂等去重）。"""
    archive_dir = tmp_path / "behavior_archives"
    _make_archive_files(archive_dir, n=5)
    ctx = _make_context(str(archive_dir), str(tmp_path))

    # 第一次：归档 3 条，持久化今日
    first = await behavior_archive_scheduler.run_once(ctx)
    assert first["archived"] == 3
    assert ctx.local_data_store.load_json(behavior_archive_scheduler._PERSIST_KEY) is not None

    # 第二次：同一天，应去重跳过（不再归档）
    second = await behavior_archive_scheduler.run_once(ctx)
    assert second.get("deduped") is True
    assert second["archived"] == 0
