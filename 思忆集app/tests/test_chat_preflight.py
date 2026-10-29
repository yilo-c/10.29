"""chat.send 首反馈 fail-fast 预检（V1②，2026-09-13）。

背景：AI 完全未配置（无云端 key、从未配置本地推理）时，chat.send 仍会走完整
回退链，实测 8s 零帧后才吐分型错误——违反 V1②「首反馈 ≤1s + 错误分型可见」。
preflight_ai_or_emit 在进执行路径前做静态判定：完全未配置 → 立即发
not_configured 分型（notice+error）并返回 False；否则放行正常路由。

回归口径（全环境无关，不读真实 os.environ 之外的副作用）：
- 四种「已配置」形态（env key / flat key / provider key / 本地 base）→ True 且不发帧；
- 完全未配置 → False 且 send_chunk 收到 notice(not_configured)+error 两帧；
- settings 仓储缺失 → 防御性放行（True，不阻塞）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.handlers.chat_paths.helpers import preflight_ai_or_emit

KEY_LLM_API_BASE = "llm_api_base"
KEY_LLM_API_KEY = "llm_api_key"
KEY_LLM_LOCAL_API_BASE = "llm_local_api_base"
KEY_PROVIDER_CONFIGS = "provider_configs"


class _FakeSettings:
    """get_setting(key, default) 直查 dict 的最小仓储。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.data = data or {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


class _FakeProvider:
    def __init__(self, api_key: str = "", enabled: bool = True) -> None:
        self.name = "fake"
        self.api_key = api_key
        self.enabled = enabled
        self.is_local = False
        self.is_fallback = False


class _ChunkCollector:
    def __init__(self) -> None:
        self.frames: list[tuple[str, str]] = []

    async def __call__(self, text: str, chunk_type: str | None = None) -> None:
        self.frames.append((chunk_type or "chunk", text))

    @property
    def notice_payloads(self) -> list[dict[str, Any]]:
        out = []
        for ctype, text in self.frames:
            if ctype == "notice":
                try:
                    out.append(json.loads(text))
                except json.JSONDecodeError:
                    pass
        return out


@pytest.fixture(autouse=True)
def _no_ai_env(monkeypatch):
    """隔离环境变量：除用例显式设置外，一律视为未配置。"""
    for k in ("SIYI_LLM_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
              "SIYI_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)


class _FakeContext:
    def __init__(self, settings: Any) -> None:
        self.settings_repository = settings


# ── 已配置形态：放行 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_env_key_configured_passes(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    chunks = _ChunkCollector()
    assert await preflight_ai_or_emit(_FakeContext(_FakeSettings()), chunks) is True
    assert chunks.frames == []


@pytest.mark.asyncio
async def test_flat_key_configured_passes():
    chunks = _ChunkCollector()
    settings = _FakeSettings({KEY_LLM_API_KEY: "sk-flat"})
    assert await preflight_ai_or_emit(_FakeContext(settings), chunks) is True
    assert chunks.frames == []


@pytest.mark.asyncio
async def test_provider_key_configured_passes():
    chunks = _ChunkCollector()
    settings = _FakeSettings({KEY_PROVIDER_CONFIGS: [{"name": "ds", "api_key": "sk-p"}]})
    # load_providers 走真实实现：provider_configs 非空 → 不播种 → 直接用该条
    assert await preflight_ai_or_emit(_FakeContext(settings), chunks) is True
    assert chunks.frames == []


@pytest.mark.asyncio
async def test_local_base_configured_passes():
    chunks = _ChunkCollector()
    settings = _FakeSettings({KEY_LLM_LOCAL_API_BASE: "http://localhost:11434/v1"})
    assert await preflight_ai_or_emit(_FakeContext(settings), chunks) is True
    assert chunks.frames == []


# ── 完全未配置：fail-fast 分型错误 ───────────────────────────


@pytest.mark.asyncio
async def test_unconfigured_fails_fast_with_typed_error():
    chunks = _ChunkCollector()
    settings = _FakeSettings({
        KEY_PROVIDER_CONFIGS: [{"name": "ds", "api_key": ""}],  # 播种形态：无 key
    })
    assert await preflight_ai_or_emit(_FakeContext(settings), chunks) is False
    notices = chunks.notice_payloads
    assert notices and notices[0]["event"] == "chat_error"
    assert notices[0]["error_kind"] == "not_configured"
    # error 帧在场且为纯文本
    assert any(ctype == "error" and "配置" in text for ctype, text in chunks.frames)


@pytest.mark.asyncio
async def test_empty_strings_count_as_unconfigured():
    chunks = _ChunkCollector()
    settings = _FakeSettings({KEY_LLM_API_KEY: "   ", KEY_LLM_LOCAL_API_BASE: ""})
    assert await preflight_ai_or_emit(_FakeContext(settings), chunks) is False
    assert chunks.notice_payloads[0]["error_kind"] == "not_configured"


# ── 防御：仓储缺失仍按未配置 fail-fast（env 为空时不存在隐藏配置源）──


@pytest.mark.asyncio
async def test_missing_settings_repo_fails_fast():
    chunks = _ChunkCollector()
    ctx = _FakeContext(None)
    # 无仓储且 env 无 key：配置来源只剩仓储，仓储都没有 → 如实 not_configured
    assert await preflight_ai_or_emit(ctx, chunks) is False
    assert chunks.notice_payloads[0]["error_kind"] == "not_configured"
