# -*- coding: utf-8 -*-
"""L2 集成测试：OCR / 语音转写能力探测 + 边界守卫。

覆盖方法：ocr.status / transcribe.status / ocr.extract_text(缺失文件守卫) /
ocr.extract_base64(空 data 守卫)。

设计：仅做能力探测与入参守卫校验，不依赖 Tesseract/faster-whisper 引擎实际可用，
可在任意后端上稳定通过；能力探测返回结构化字典（available 为真实布尔）。
"""
import pytest

pytestmark = pytest.mark.integration


async def _wrapped(rpc, method, params=None):
    resp = await rpc(method, params or {})
    assert isinstance(resp, dict) and ("result" in resp or "error" in resp), (
        f"{method} 未返回结构化 JSON-RPC 响应: {resp}"
    )
    return resp.get("result"), resp.get("error")


async def test_ocr_status_returns_diagnostic(rpc):
    result, err = await _wrapped(rpc, "ocr.status")
    assert err is None, f"ocr.status 报错: {err}"
    assert isinstance(result, dict), "ocr.status 应返回诊断字典"
    assert "available" in result or "pytesseract" in result or "tesseract" in result or "error" in result


async def test_transcribe_status_reachable(rpc):
    result, err = await _wrapped(rpc, "transcribe.status")
    assert err is None, f"transcribe.status 报错: {err}"
    assert isinstance(result, dict)
    # 真实能力布尔（faster-whisper 已装则为 True）
    assert "available" in result and isinstance(result["available"], bool)
    assert "version" in result and "cached_models" in result


async def test_ocr_extract_text_missing_file_guard(rpc):
    result, err = await _wrapped(rpc, "ocr.extract_text", {"image_path": "/nonexistent/path/to.png"})
    assert err is None, f"ocr.extract_text 报错: {err}"
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "error" in result


async def test_ocr_extract_text_empty_path_guard(rpc):
    result, err = await _wrapped(rpc, "ocr.extract_text", {"image_path": ""})
    assert err is None, f"ocr.extract_text 报错: {err}"
    assert isinstance(result, dict)
    assert result.get("success") is False


async def test_ocr_extract_base64_empty_guard(rpc):
    result, err = await _wrapped(rpc, "ocr.extract_base64", {"data": ""})
    assert err is None, f"ocr.extract_base64 报错: {err}"
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "error" in result
