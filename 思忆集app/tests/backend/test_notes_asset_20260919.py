# -*- coding: utf-8 -*-
"""U0 方案 A（2026-09-19）：notes.asset.put/get 图片资产目录 RPC 的机器断言。

资产落 <desknotes-root>/assets/，md 写标准相对路径 assets/<name>（Typora 兼容）。
防护面：basename 规范化（.. / 分隔符 / 非法字符）+ 扩展名白名单 + 10MB 上限。
"""

import base64

import pytest


@pytest.fixture()
def notes_root(tmp_path, monkeypatch):
    """隔离 desknotes root：SIYI_DESKNOTES_DATA_DIR 显式权威（resolve_isolated_dir 契约）。
    目录必须自带 notes/ 子目录，否则 fail-closed 拒启语义生效。"""
    root = tmp_path / "desknotes"
    (root / "notes").mkdir(parents=True)
    monkeypatch.setenv("SIYI_DESKNOTES_DATA_DIR", str(root))
    return root


def _registry():
    from backend.method_registry import MethodRegistry
    from backend.handlers._notes import register_notes

    registry = MethodRegistry()
    ctx = type("Ctx", (), {"personal_note_repository": None})()
    register_notes(registry, ctx)
    return registry


def _call(registry, method, params):
    import asyncio

    handler = registry._handlers.get(method)
    assert handler is not None, f"{method} 未注册"
    if asyncio.iscoroutinefunction(handler):
        return asyncio.run(handler(**params))
    return handler(**params)


PNG_1PX = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )
).decode("ascii")


def test_put_then_get_roundtrip(notes_root):
    reg = _registry()
    put = _call(reg, "notes.asset.put", {"name": "shot-abc.png", "data_base64": PNG_1PX})
    assert put["success"] is True and put["rel_path"] == "assets/shot-abc.png"
    assert (notes_root / "assets" / "shot-abc.png").is_file()
    got = _call(reg, "notes.asset.get", {"name": "shot-abc.png"})
    assert got["success"] is True and got["mime"] == "image/png"
    assert base64.b64decode(got["data"]) == base64.b64decode(PNG_1PX)


def test_put_rejects_path_traversal(notes_root):
    reg = _registry()
    # `..` 族：必须拒绝（真正的遍历风险面）
    for bad in ["../evil.png", "a/../../x.png", "..\\evil.png", "", ".", ".."]:
        out = _call(reg, "notes.asset.put", {"name": bad, "data_base64": PNG_1PX})
        assert out.get("success") is False, f"路径遍历放行: {bad!r}"
    assert not (notes_root / "evil.png").exists()
    assert not (notes_root.parent / "evil.png").exists()

    # 纯目录前缀（/abs.png、a/b.png）：basename 规范化剥掉目录成分，构造上安全——
    # 只要求落点在 assets/ 之内且文件名扁平化
    out = _call(reg, "notes.asset.put", {"name": "/abs.png", "data_base64": PNG_1PX})
    assert out.get("success") is True and out["name"] == "abs.png"
    assert (notes_root / "assets" / "abs.png").is_file()
    assert not (notes_root / "abs.png").exists()


def test_put_rejects_non_image_and_invalid_base64(notes_root):
    reg = _registry()
    out = _call(reg, "notes.asset.put", {"name": "x.exe", "data_base64": PNG_1PX})
    assert out.get("success") is False
    out2 = _call(reg, "notes.asset.put", {"name": "y.png", "data_base64": "!!!not-base64!!!"})
    assert out2.get("success") is False


def test_get_missing_asset_is_honest(notes_root):
    reg = _registry()
    out = _call(reg, "notes.asset.get", {"name": "nope.png"})
    assert out.get("success") is False and "not found" in out.get("error", "")
