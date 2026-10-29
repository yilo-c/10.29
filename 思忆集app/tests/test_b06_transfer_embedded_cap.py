"""B-06 回归测试：transfer.send 内嵌 file 独立小上限。

审计（audit-report-2026-08-27 B-06，P2）：transfer.send 的 file 参数（base64 全量）
内嵌 transfer_history.json，单条上限 50MB × 50 条历史最坏 ~2.5GB，且每次 send
全量读写 + fsync 该文件 → 传几次大文件后每次 send/transfer.history 全量 I/O 卡顿。

修复：新增 MAX_EMBEDDED_FILE = 1MB（base64）——超限明确拒绝并引导走
transfer.upload 大文件通道（transfer_files/ 目录 + 引用），不静默截断
（P2-13 已证实截断导致文件损坏）。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.handlers._transfer import MAX_EMBEDDED_FILE, register_transfer
from backend.method_registry import MethodRegistry

# 沙箱内 tempfile/pytest basetemp 目录不可写（.pytest-tmp-root ACL 拒绝），
# 用 workspace 内目录（tests 旁），测试后清理。
_TMP_ROOT = Path(__file__).resolve().parent / ".b06-tmp"


@pytest.fixture
def transfer_env():
    """临时 base_dir + 注册 transfer.* 的 registry。"""
    tmp_dir = _TMP_ROOT / "case"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_dir)))
    registry = MethodRegistry()
    register_transfer(registry, ctx)
    yield registry, ctx, tmp_dir
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


async def _send(registry, **params):
    return await registry.dispatch("transfer.send", params)


def test_send_oversized_file_rejected_b06(transfer_env):
    """B-06: >1MB base64 file → 明确拒绝（引导 transfer.upload），不落盘。"""
    registry, _, base_dir = transfer_env
    big = "A" * (MAX_EMBEDDED_FILE + 1)
    result = asyncio.run(_send(registry, text="hello", file=big, fileName="big.bin"))
    assert result.get("success") is not True  # 返回 error 形态
    assert "transfer.upload" in str(result.get("error", "")), (
        f"超限拒绝必须提示走 transfer.upload，实际: {result}"
    )
    # 不落盘：history 文件不存在
    assert not (base_dir / "transfer_history.json").exists()


def test_send_small_file_persisted_b06(transfer_env):
    """B-06: ≤1MB base64 file → 正常落盘（history 含 fileName）。"""
    registry, _, base_dir = transfer_env
    small = "A" * 100
    result = asyncio.run(_send(registry, text="hello", file=small, fileName="a.txt"))
    assert result.get("success") is not False
    hist_path = base_dir / "transfer_history.json"
    assert hist_path.exists()
    history = json.loads(hist_path.read_text(encoding="utf-8"))
    assert history[0]["fileName"] == "a.txt"
    assert history[0]["file"] == small


def test_send_text_only_still_works_b06(transfer_env):
    """B-06: 纯文本 send 不受影响。"""
    registry, _, base_dir = transfer_env
    result = asyncio.run(_send(registry, text="just text"))
    assert result.get("success") is not False
    history = json.loads((base_dir / "transfer_history.json").read_text(encoding="utf-8"))
    assert history[0]["text"] == "just text"
    assert "file" not in history[0]
