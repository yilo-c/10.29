# -*- coding: utf-8 -*-
"""P-I 补充复测：dictation 正确参数（上次 good 槽被二次调用覆盖）+ reminder 仓储直调验证。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_PATH = os.environ.get("PI_RECHECK_PATH", "D:/h/3.25/pi-longtail-recheck.json")
_OUT: dict = {}


@pytest.fixture(scope="session", autouse=True)
def _dump():
    yield
    Path(_PATH).write_text(json.dumps(_OUT, ensure_ascii=False, indent=2), encoding="utf-8")


class TestRecheck:
    async def test_dictation_good(self, rpc):
        # 单次调用，expected 正确给出
        r = await rpc("word.dictation_check", {"expected": "hello", "actual": "helo", "word_id": 0})
        _OUT["dictation_good"] = r
        # 全对
        r2 = await rpc("word.dictation_check", {"expected": "apple", "actual": "apple"})
        _OUT["dictation_exact"] = r2

    async def test_reminder_then_tasks_create(self, rpc, rpc_approved):
        # reminder.create 是否真的死（对照 tasks.create 走 add_task 存活）
        past = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
        rr = await rpc("reminder.create", {"title": "__pi_rc__", "reminder_time": past})
        _OUT["reminder_create"] = rr
        tr = await rpc("tasks.create", {"title": "__pi_rc_task__"})
        _OUT["tasks_create"] = tr
        # 清理 tasks.create 产物（R4 delete → approve）
        res = tr.get("result", {}) if isinstance(tr, dict) else {}
        tid = res.get("id") if isinstance(res, dict) else None
        if tid:
            _OUT["tasks_delete"] = await rpc_approved("tasks.delete", {"task_id": tid})
