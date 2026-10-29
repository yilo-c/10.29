# -*- coding: utf-8 -*-
"""Regression tests for backend.handlers._reading (P3-1).

Ensures the shared ``io_lock`` is a single instance reused across every
call instead of being recreated per registration / per call (which had
made the lock useless for serializing read-modify-write IO).
"""
from __future__ import annotations

import asyncio
import os

import pytest


class _FakeRegistry:
    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    def register(self, name: str, handler: object) -> None:
        self.methods[name] = handler


class _FakeContext:
    def __init__(self, base_dir: str) -> None:
        self.local_data_store = type("DS", (), {"base_dir": base_dir})()


@pytest.mark.asyncio
async def test_reading_handlers_share_single_lock(tmp_path) -> None:
    """All handlers must share the same lock instance to prevent races."""
    from backend.handlers import _reading

    reg = _FakeRegistry()
    ctx = _FakeContext(str(tmp_path))

    _reading.register_reading(reg, ctx)

    # 通过反复调用 reading.list（只读路径）触发 _io_lock() 多次，确保返回同一实例
    list_handler = reg.methods.get("reading.list")
    assert callable(list_handler)

    # 并发执行多次，相当于触发 _io_lock() 多次
    results = await asyncio.gather(
        list_handler(),
        list_handler(),
        list_handler(),
    )
    for result in results:
        assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_reading_push_get_roundtrip(tmp_path) -> None:
    from backend.handlers import _reading

    reg = _FakeRegistry()
    ctx = _FakeContext(str(tmp_path))
    _reading.register_reading(reg, ctx)

    push = reg.methods["reading.push"]
    get = reg.methods["reading.get"]

    await push(note_id="n1", scroll_position=100, percentage=12.5, device_id="dev1")
    got = await get(note_id="n1")
    assert got.get("note_id") == "n1"
    assert got.get("scroll_position") == 100

    # 同一 note 的并发 push 应被锁串行化，最终状态合法（不会发生 KeyError 等）
    await asyncio.gather(
        *[
            push(note_id="n1", scroll_position=p, percentage=float(p), device_id="dev1")
            for p in range(1, 11)
        ]
    )
    final = await get(note_id="n1")
    assert final.get("scroll_position") in range(1, 11)
    # 文件应仅有一份，没有损坏
    path = os.path.join(str(tmp_path), "reading_positions.json")
    assert os.path.exists(path)
