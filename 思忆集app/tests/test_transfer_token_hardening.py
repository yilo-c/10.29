"""七维-安C7：transfer 令牌硬化（P1）。

规范：deliverables/seven-dimension-risk-assessment-2026-08-29.md §1.1 A6、§1.3 C7。

原缺陷（backend/handlers/_transfer.py；兼C4 修复 5b7e59f 后行号漂移）：
1. _check_auth 用 != 明文比较 token —— 非恒时，可被计时侧信道逐字节探测；
2. transfer.token_info 返回 token 前 4 字符（token_prefix）—— 向未鉴权
   调用方泄露前缀，缩小暴力空间并帮助攻击者确认目标配置。

修复：
1. hmac.compare_digest（两侧先 UTF-8 编码：恒时比较且对任意 unicode 输入
   不抛 TypeError —— str/str 形式的 compare_digest 遇非 ASCII 会 raise）；
2. token_info 移除 token_prefix 字段（**有意 RPC 契约变更**：全仓生产
   消费方为零 —— desk-notes methods.ts:44 仅映射方法名，手机端无引用）。

测试两层（FakeContext 风格参考 test_b06_transfer_embedded_cap.py /
test_handlers_transfer.py 的 SimpleNamespace + MethodRegistry，无需活后端）：
- 静态断言：源码含 hmac.compare_digest、不再有 [:4] 切片与 token_prefix；
- 行为断言：错 token 拒绝、对 token 放行、token_info 响应不含任何 token 片段。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._transfer import _check_auth, register_transfer
from backend.method_registry import MethodRegistry

TOKEN = "c7-hardened-secret-token-9f3a"
TRANSFER_SRC = (APP_ROOT / "backend" / "handlers" / "_transfer.py").read_text(encoding="utf-8")


@pytest.fixture
def hardening_env(monkeypatch, tmp_path: Path):
    """secured 模式环境：env token + 隔离 base_dir 的 registry。

    PH-A1（2026-09-17）：settings.transfer_token 配对凭证子系统已整段删除，
    不再需要重置模块级 _TRANSFER_TOKEN —— `_check_auth` 只认 env SIYI_TRANSFER_KEY。
    """
    monkeypatch.setenv("SIYI_TRANSFER_KEY", TOKEN)
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_path)))
    registry = MethodRegistry()
    register_transfer(registry, ctx)
    return registry, tmp_path


# ── 静态断言：源码级硬化 ────────────────────────────────────────────────────


def test_auth_uses_constant_time_compare():
    """_check_auth 必须使用 hmac.compare_digest（恒时比较）。"""
    assert "import hmac" in TRANSFER_SRC, "_transfer.py 未导入 hmac"
    assert "hmac.compare_digest" in TRANSFER_SRC, "七维-安C7：token 比较必须用 hmac.compare_digest（原 != 可被计时侧信道探测）"


def test_no_prefix_leak_in_source():
    """源码（剥离注释后）不得再包含 token 前 4 字符切片与 token 前缀字段。"""
    # 剥离注释：说明性文字（如变更记录）不应触发源码契约断言。
    code_only = re.sub(r"#\s.*$", "", TRANSFER_SRC, flags=re.MULTILINE)
    assert re.search(r"\[:4\]", code_only) is None, "七维-安C7：token 前缀的 [:4] 切片必须移除"
    assert "token_prefix" not in code_only, "七维-安C7：token_info 不再返回 token 前缀字段（有意 RPC 契约变更）"


# ── 组件级行为断言：_check_auth ────────────────────────────────────────────


def test_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("SIYI_TRANSFER_KEY", TOKEN)
    assert _check_auth(_token="totally-wrong") == "auth failed (invalid _token)"


def test_prefix_match_but_wrong_token_rejected(monkeypatch):
    """前缀正确整体不匹配也拒绝（恒时比较不改变精确匹配语义）。"""
    monkeypatch.setenv("SIYI_TRANSFER_KEY", TOKEN)
    assert _check_auth(_token=TOKEN[:4]) == "auth failed (invalid _token)"
    assert _check_auth(_token=TOKEN[:-1]) == "auth failed (invalid _token)"


def test_correct_token_allowed(monkeypatch):
    monkeypatch.setenv("SIYI_TRANSFER_KEY", TOKEN)
    assert _check_auth(_token=TOKEN) is None


def test_non_ascii_token_rejected_without_crash(monkeypatch):
    """任意 unicode 输入不抛 TypeError（bytes 编码后比较，防未处理异常）。"""
    monkeypatch.setenv("SIYI_TRANSFER_KEY", TOKEN)
    assert _check_auth(_token="错误密码") == "auth failed (invalid _token)"


# ── registry 级行为断言（FakeContext + tmp_path，不触真实 data/） ──────────


async def test_send_wrong_token_rejected_via_registry(hardening_env):
    registry, base_dir = hardening_env
    result = await registry.dispatch("transfer.send", {"text": "hi", "_token": "wrong"})
    assert result.get("error") == "auth failed (invalid _token)"
    # 拒绝路径不落盘
    assert not (base_dir / "transfer_history.json").exists()


async def test_send_correct_token_allowed_via_registry(hardening_env):
    registry, base_dir = hardening_env
    result = await registry.dispatch("transfer.send", {"text": "hello c7", "_token": TOKEN})
    assert result.get("success") is True
    history = json.loads((base_dir / "transfer_history.json").read_text("utf-8"))
    assert history[0]["text"] == "hello c7"


async def test_token_info_contains_no_token_fragment(hardening_env):
    """secured 模式：token_info 如实说明单一凭证模型，且不含任何 token 片段。"""
    registry, _ = hardening_env
    result = await registry.dispatch("transfer.token_info", {})
    # PH-A1（2026-09-17）契约变更：新增 auth_model/note 字段，如实说明
    # 「传输鉴权复用 WS auth_token，不另设配对凭证」。
    assert result["auth_model"] == "ws_auth_token"
    assert result["token_required"] is True
    assert result["mode"] == "secured"
    assert "token_prefix" not in result
    blob = json.dumps(result, ensure_ascii=False)
    assert TOKEN not in blob
    assert TOKEN[:4] not in blob


async def test_token_info_lan_mode_contract(monkeypatch, tmp_path: Path):
    """LAN 模式：响应同样不含 token 片段，契约字段精确锁定。"""
    monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
    ctx = SimpleNamespace(local_data_store=SimpleNamespace(base_dir=str(tmp_path)))
    registry = MethodRegistry()
    register_transfer(registry, ctx)
    result = await registry.dispatch("transfer.token_info", {})
    assert result["auth_model"] == "ws_auth_token"
    assert result["token_required"] is False
    assert result["mode"] == "lan"
    assert "token_prefix" not in result
