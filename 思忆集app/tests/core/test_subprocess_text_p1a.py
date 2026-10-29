# -*- coding: utf-8 -*-
"""P1-A 子进程解码基元测试（2026-09-18）。

锁四件事：
  1. decode_output：gbk 中文 / utf-8 正常 / 垃圾字节 replace 兜底 / None→""；
  2. run_text 在 PYTHONUTF8=1 环境下跑真实 chcp 命令，stdout **不得为 None/空**
     （这正是线上炸 'NoneType'.strip 的复现场景）；
  3. run_text 的 returncode 透传正确；
  4. input_text 走 stdin 时不死锁、能回显。
"""

from __future__ import annotations

import os
import sys

import pytest

from core.subprocess_text import decode_output, run_text


def test_decode_gbk_chinese():
    raw = "处理器 英特尔".encode("gbk")
    assert decode_output(raw) == "处理器 英特尔"


def test_decode_prefers_utf8():
    raw = "日本語 テスト".encode("utf-8")
    assert decode_output(raw) == "日本語 テスト"


def test_decode_never_raises_on_garbage():
    raw = bytes([0xFF, 0xFE, 0x81, 0x40, 0x00, 0x20])
    out = decode_output(raw)  # 不抛即通过
    assert isinstance(out, str)


def test_decode_none_returns_empty():
    assert decode_output(None) == ""


def test_decode_passes_through_str():
    assert decode_output("abc") == "abc"


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属复现（GBK 控制台）")
def test_run_text_with_pythonutf8():
    """复现线上事故形态：PYTHONUTF8=1 下取中文控制台输出，不得变 None。"""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    r = run_text(
        ["cmd", "/c", "echo 中文测试&& chcp"],
        timeout=15,
        env=env,
    )
    assert r.returncode == 0
    assert isinstance(r.stdout, str)
    r.stdout.strip()  # 曾在此炸 'NoneType' object has no attribute 'strip'
    assert "936" in r.stdout or "中文" in r.stdout  # chcp 输出代码页或回显中文


def test_run_text_returncode_passthrough():
    r = run_text([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30)
    assert r.returncode == 3


def test_run_text_input_text():
    r = run_text(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        timeout=30,
        input_text="hello 中文",
    )
    assert r.returncode == 0
    assert "HELLO" in r.stdout
