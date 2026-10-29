# -*- coding: utf-8 -*-
"""P1-C 审批 preview 密钥脱敏测试（2026-09-18 实测驱动的修复）。

锁两件事：
  1. redact_preview_args 覆盖扁平 / 嵌套 / list / 值形态凭据；
  2. sandbox_manager.add 在咽喉点脱敏 preview，但 args 原值不动（重放链不污染）。
"""

from __future__ import annotations

import json

import pytest


def test_redact_flat_api_key():
    from services.preview_redact import redact_preview_args

    out = redact_preview_args({"key": "llm_api_key", "value": "sk-9b87c1def1ff4472a38be5e0e219c0c0"})
    # value 命中凭据形态（sk- 前缀）→ 遮
    assert out["value"] != "sk-9b87c1def1ff4472a38be5e0e219c0c0"
    assert "9b87c1def1ff" not in json.dumps(out)


def test_redact_sensitive_key_name_regardless_of_value():
    from services.preview_redact import redact_preview_args

    out = redact_preview_args({"api_key": "hunter2", "note": "hello"})
    assert out["api_key"] != "hunter2"
    assert "hunter2" not in json.dumps(out)
    # 非敏感键原样
    assert out["note"] == "hello"


def test_redact_nested_headers():
    from services.preview_redact import redact_preview_args

    raw = {
        "name": "deepseek",
        "config": {
            "base_url": "https://api.deepseek.com",
            "headers": {"Authorization": "Bearer sk-abcdefghijklmnop1234", "X-Api-Key": "zzztopsecret"},
        },
    }
    out = redact_preview_args(raw)
    dumped = json.dumps(out, ensure_ascii=False)
    assert "abcdefghijklmnop1234" not in dumped
    assert "zzztopsecret" not in dumped
    # 非敏感字段保留可读性（审批 UI 要看得懂在改什么）
    assert out["name"] == "deepseek"
    assert out["config"]["base_url"] == "https://api.deepseek.com"


def test_redact_list_of_secrets():
    from services.preview_redact import redact_preview_args

    out = redact_preview_args({"tokens": ["sk-aaaaaaaaaaaaaaaaa", "sk-bbbbbbbbbbbbbbbbb"]})
    assert "aaaaaaaaaaaaaaaaa" not in json.dumps(out)
    assert "bbbbbbbbbbbbbbbbb" not in json.dumps(out)


def test_redact_does_not_mutate_input():
    from services.preview_redact import redact_preview_args

    src = {"api_key": "sk-abcdefghijklmnop1111", "n": 5}
    snapshot = dict(src)
    redact_preview_args(src)
    assert src == snapshot  # 入参绝不被改（重放依赖原值）


@pytest.mark.parametrize(
    "value,should_keep",
    [
        ("openai", True),
        ("https://api.example.com/v1", True),
        ("gpt-4o", True),
        ("sk-proj-abcdefghijklmnopqrstuvwxyz", False),  # 凭据形态
    ],
)
def test_value_prefix_only_masks_credentials(value, should_keep):
    from services.preview_redact import redact_preview_args

    out = redact_preview_args({"model": value})
    assert (out["model"] == value) is should_keep


# ── 集成：sandbox_manager.add 咽喉点 ──────────────────────────────


def test_sandbox_add_redacts_preview_keeps_args():
    from services.sandbox_manager import SandboxManager

    mgr = SandboxManager()
    secret = "sk-9b87c1def1ff4472a38be5e0e219c0c0"
    pid = mgr.add(
        "settings.set",
        {"key": "llm_api_key", "value": secret},
        {"action": "settings.set", "args": {"key": "llm_api_key", "value": secret}, "summary": "x"},
        kind="rpc",
    )
    entry = mgr.get(pid)
    # args 原值保留（重放用）
    assert entry["args"]["value"] == secret
    # preview 不得含明文密钥
    dumped = json.dumps(entry["preview"], ensure_ascii=False)
    assert secret not in dumped
    assert "9b87c1def1ff" not in dumped
    # args_sha256 仍按原 args 计算，未被脱敏破坏（重放绑定校验依赖它）
    from services.confirmation_store import compute_args_sha256

    assert entry["preview"]["args_sha256"] == compute_args_sha256({"key": "llm_api_key", "value": secret})


# ── 残留补刀（2026-09-18 WS 探针实证）：即时返回信封也不得含明文 ──────────


def test_r4_enqueue_response_envelope_is_redacted(monkeypatch):
    """_enqueue_r4_approval 返回给调用方的 pending 信封必须脱敏。

    背景：add() 只遮存储/事件副本，探针链 B-1 实证返回信封曾带
    settings.set 的 API key 原文 → 三个 enqueue 返回点全部补刀。
    """
    import asyncio

    from services.sandbox_manager import SandboxManager

    fresh = SandboxManager()
    import services.sandbox_manager as sm_mod

    monkeypatch.setattr(sm_mod, "get_sandbox_manager", lambda: fresh)

    from backend.method_registry import MethodRegistry

    secret = "sk-9b87c1def1ff4472a38be5e0e219c0c0"
    reg = MethodRegistry()
    envelope = asyncio.run(reg._enqueue_r4_approval("settings.set", {"key": "llm_api_key", "value": secret}))
    assert envelope["pending"] is True
    dumped = json.dumps(envelope, ensure_ascii=False)
    assert secret not in dumped
    assert "9b87c1def1ff" not in dumped
    # 重放链完整性：队列条目 args 仍是原值
    entry = fresh.get(envelope["pending_id"])
    assert entry["args"]["value"] == secret


def test_no_raw_preview_return_pattern():
    """结构断言：审批入队返回信封禁止再出现裸 `"preview": preview` 写法。

    泄漏模式 = sandbox.add(preview) 后把**同一个未脱敏的局部变量**直接放进
    即时返回信封（探针实证 method_registry / react_executor / _dynamic_tools
    三处同型）。新代码若引入第四处，本断言直接红。
    """
    import re
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[2]
    targets = [
        app_root / "backend" / "method_registry.py",
        app_root / "services" / "react_executor.py",
        app_root / "assistant" / "capability" / "_dynamic_tools.py",
    ]
    pattern = re.compile(r'"preview":\s*preview\s*,')  # 裸 preview 键 = 违规
    offenders = []
    for f in targets:
        src = f.read_text(encoding="utf-8")
        for m in pattern.finditer(src):
            line_no = src.count("\n", 0, m.start()) + 1
            offenders.append(f"{f.name}:{line_no}")
    assert not offenders, f"即时返回信封使用未脱敏的裸 preview: {offenders}"
