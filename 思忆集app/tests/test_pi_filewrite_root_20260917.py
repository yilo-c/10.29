# -*- coding: utf-8 -*-
"""P-I 三次复测：file.write 合法根内路径是否也被 broker 拦（区分白名单拦截 vs 写面整体坏死）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_PATH = os.environ.get("PI_FILE_PATH", "D:/h/3.25/pi-filewrite-check.json")
_OUT: dict = {}


@pytest.fixture(scope="session", autouse=True)
def _dump():
    yield
    Path(_PATH).write_text(json.dumps(_OUT, ensure_ascii=False, indent=2), encoding="utf-8")


class TestFileWriteRoot:
    async def test_write_in_root(self, rpc):
        # R2-B6（2026-09-17）后的**预期形态已变**（本探针是语义探针，无硬断言，读输出时按此解释）：
        #   · 根内相对路径 → 返回待审批信封 {"pending": True, "requires_approval": True, "pending_id": ...}
        #     （file.write 已入 _R4_METHODS；批准后由 sandbox.approve 以 skip_approval 重放）
        #   · 越界绝对路径 → 硬拒绝 {"success": False, "reason": "path_outside_allowed_root"}
        #     （路径守卫先于 broker 高危标签执行，这正是 D6 修的判定顺序）
        # 修前旧形态：根内合法路径被 broker 高危标签先行拦下 → 写面整体坏死。
        # 相对路径 → _resolve_write_path 拼到 data_root（隔离临时目录）= 合法 allowed root
        wr = await rpc("file.write", {"path": "pi_probe_ok.txt", "content": "pi"})
        _OUT["write_in_root"] = wr
        # 越界对照
        wo = await rpc("file.write", {"path": "C:/Windows/pi_probe_no.txt", "content": "x"})
        _OUT["write_out_root"] = wo
        # 读回对照（合法根内路径 read_roots 不含 data_root? 含。相对 read 拼 workspace_root）
        rd = await rpc("file.read", {"path": "pi_probe_ok.txt"})
        _OUT["read_back"] = rd
