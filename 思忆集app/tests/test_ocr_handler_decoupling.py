"""P1-2 + P2.2: OCR handler 解耦、依赖打包与诊断能力测试。

历史 bug（P1-2）：
- backend/handlers/_ocr.py:34 + :55 用 `from ui.translate.ocr import ...`
- 违反"无头后端不依赖 PyQt6/UI"契约；UI 模块若顶层 import PyQt6，headless 启动崩
- tempfile.NamedTemporaryFile 异常路径不删 tmp，永久泄漏到 TEMP

修复后：
- 实际实现移到 services/ocr_service.py
- backend handler 改 import services.ocr_service
- ui/translate/ocr.py 变重导出 shim 保持向后兼容
- tempfile 走 try/finally 确保异常路径也删

P2.2 增强（与 P2.1 transcription 严格对称）：
- ``ocr.status`` RPC 返回 pytesseract/tesseract 二进制可用性诊断
- ``extract_text_from_image()`` 支持 language 参数 + settings_repository 注入
- ``_resolve_tesseract_binary()`` 在 settings/env/PATH/Windows 默认路径间探测
- pyproject.toml 添加 ``[ocr]`` optional-dependencies 分组
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_handlers_ocr_does_not_import_ui_layer():
    """grep 源代码：_ocr.py 不能再有 from ui.translate.ocr import 语句。"""
    src = (APP_ROOT / "backend" / "handlers" / "_ocr.py").read_text(encoding="utf-8")
    assert "from ui.translate.ocr" not in src, (
        "P1-2 回归：backend/handlers/_ocr.py 不能反向 import ui.translate.ocr，"
        "应该用 services.ocr_service。"
    )
    assert "from services.ocr_service import" in src, (
        "应通过 services.ocr_service.extract_text_from_image 调用 OCR。"
    )


def test_services_ocr_service_module_imports_cleanly():
    """services.ocr_service 必须能在不安装 pytesseract 时导入（lazy import）。"""
    import importlib
    mod = importlib.import_module("services.ocr_service")
    assert hasattr(mod, "extract_text_from_image")


def test_ocr_extract_text_returns_error_on_missing_path():
    """no path → 错误，不应抛异常。"""
    import asyncio
    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ocr(registry, MagicMock())

    result = asyncio.run(
        registry.dispatch("ocr.extract_text", {"image_path": ""})
    )
    assert result["success"] is False
    assert "image_path" in result["error"]


def test_ocr_extract_text_returns_error_on_nonexistent_file():
    import asyncio
    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ocr(registry, MagicMock())

    result = asyncio.run(
        registry.dispatch("ocr.extract_text", {"image_path": "/no/such/file.png"})
    )
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_ocr_extract_base64_cleans_up_tmp_on_error(tmp_path, monkeypatch):
    """tempfile 异常路径必须删 tmp（P1-2 顺手修）。"""
    import asyncio
    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ocr(registry, MagicMock())

    # 让 extract_text_from_image 抛异常
    import services.ocr_service
    created_tmp_paths: list[str] = []

    def fake_extract(path: str, *, language=None, settings_repository=None):
        created_tmp_paths.append(path)
        raise RuntimeError("simulated OCR failure")

    monkeypatch.setattr(services.ocr_service, "extract_text_from_image", fake_extract)

    # 一个最小 PNG（4 字节的 base64 占位符）
    import base64
    fake_image_b64 = base64.b64encode(b"\x89PNG\r\n").decode()

    result = asyncio.run(
        registry.dispatch("ocr.extract_base64", {"data": fake_image_b64})
    )

    assert result["success"] is False
    # 临时文件应在 finally 中被删除
    assert created_tmp_paths, "extract_text_from_image 应被调用过（拿到 tmp 路径）"
    import os
    assert not os.path.exists(created_tmp_paths[0]), (
        f"tmp 文件 {created_tmp_paths[0]} 应在异常 finally 中被删除"
    )


# ═══════════════════════════════════════════════════════════════════
# P2.2: ocr.status RPC（与 transcribe.status 严格对称）
# ═══════════════════════════════════════════════════════════════════


def test_ocr_status_returns_dict_with_available_flag():
    """ocr.status 必须返回 {available: bool, ...} 结构，不抛异常。"""
    import asyncio

    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry

    registry = MethodRegistry()
    register_ocr(registry, MagicMock())

    result = asyncio.run(registry.dispatch("ocr.status", {}))
    assert isinstance(result, dict)
    assert "available" in result
    assert isinstance(result["available"], bool)
    # 其余字段即使为 None 也必须存在（前端按 key 读取）
    for key in (
        "pytesseract_version",
        "tesseract_binary_path",
        "tesseract_version",
        "languages",
        "tessdata_prefix",
    ):
        assert key in result, f"ocr.status 缺少字段 {key}"


def test_ocr_status_handler_does_not_import_pytesseract_at_module_top():
    """handler 模块顶层不能 import pytesseract（必须 lazy import 在函数体内）。"""
    src = (APP_ROOT / "backend" / "handlers" / "_ocr.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("import pytesseract") or stripped.startswith("from pytesseract"):
            assert line.startswith(" ") or line.startswith("\t"), (
                f"pytesseract 必须在函数内 lazy import: {line!r}"
            )


def test_ocr_status_passes_settings_repository_to_service(monkeypatch):
    """ocr.status 必须把 context.settings_repository 透传给 get_ocr_status。"""
    import asyncio

    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry
    import services.ocr_service as svc

    captured: dict[str, object] = {}

    def fake_get_ocr_status(*, env=None, settings_repository=None):
        captured["settings_repository"] = settings_repository
        return {
            "available": True,
            "pytesseract_version": "0.3.10",
            "tesseract_binary_path": "/fake/tesseract",
            "tesseract_version": "tesseract v5.3.4",
            "languages": ["eng", "chi_sim"],
            "tessdata_prefix": None,
        }

    monkeypatch.setattr(svc, "get_ocr_status", fake_get_ocr_status)

    registry = MethodRegistry()
    ctx = MagicMock()
    fake_repo = MagicMock()
    ctx.settings_repository = fake_repo
    register_ocr(registry, ctx)

    result = asyncio.run(registry.dispatch("ocr.status", {}))
    assert result["available"] is True
    assert result["pytesseract_version"] == "0.3.10"
    assert captured["settings_repository"] is fake_repo


# ═══════════════════════════════════════════════════════════════════
# P2.2: _resolve_tesseract_binary 探测优先级
# ═══════════════════════════════════════════════════════════════════


def test_resolve_tesseract_binary_settings_overrides_everything(monkeypatch, tmp_path):
    """settings 中配置的 ocr_tesseract_path 优先级最高。"""
    from services.ocr_service import _resolve_tesseract_binary

    # 准备一个真实文件作为"二进制"
    fake_bin = tmp_path / "custom-tesseract.exe"
    fake_bin.write_bytes(b"FAKE")

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = str(fake_bin)

    # 即使 env 和 PATH 都没有，settings 路径也应被使用
    result = _resolve_tesseract_binary(env={}, settings_repository=fake_repo)
    assert result == str(fake_bin)


def test_resolve_tesseract_binary_env_var_when_no_settings(monkeypatch, tmp_path):
    """settings 未配置时，TESSERACT_CMD 环境变量生效。"""
    from services.ocr_service import _resolve_tesseract_binary

    fake_bin = tmp_path / "env-tesseract.exe"
    fake_bin.write_bytes(b"FAKE")

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = ""

    result = _resolve_tesseract_binary(
        env={"TESSERACT_CMD": str(fake_bin)},
        settings_repository=fake_repo,
    )
    assert result == str(fake_bin)


def test_resolve_tesseract_binary_path_lookup_when_no_settings_no_env(monkeypatch):
    """settings/env 都没配置时，回退到 shutil.which。"""
    from services.ocr_service import _resolve_tesseract_binary

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = ""

    monkeypatch.setattr(
        "services.ocr_service.shutil.which",
        lambda name: "/usr/bin/tesseract" if name == "tesseract" else None,
    )

    result = _resolve_tesseract_binary(env={}, settings_repository=fake_repo)
    assert result == "/usr/bin/tesseract"


def test_resolve_tesseract_binary_returns_none_when_nothing_found(monkeypatch):
    """探测全部失败时返回 None（不抛异常）。"""
    from services.ocr_service import _resolve_tesseract_binary

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = ""

    monkeypatch.setattr("services.ocr_service.shutil.which", lambda name: None)
    # 强制非 Windows 路径
    monkeypatch.setattr("services.ocr_service.os.name", "posix")

    result = _resolve_tesseract_binary(env={}, settings_repository=fake_repo)
    assert result is None


def test_resolve_tesseract_binary_settings_path_nonexistent_falls_through(
    monkeypatch, tmp_path
):
    """settings 中配置的路径不存在时，应降级到下一级（env / which）。"""
    from services.ocr_service import _resolve_tesseract_binary

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = "/no/such/tesseract.exe"

    monkeypatch.setattr("services.ocr_service.shutil.which", lambda name: None)
    monkeypatch.setattr("services.ocr_service.os.name", "posix")

    result = _resolve_tesseract_binary(env={}, settings_repository=fake_repo)
    assert result is None


# ═══════════════════════════════════════════════════════════════════
# P2.2: get_ocr_status 在不同安装状态下的返回
# ═══════════════════════════════════════════════════════════════════


def test_get_ocr_status_when_pytesseract_missing(monkeypatch):
    """pytesseract 未安装时，available=False 且其它字段降级。"""
    from services.ocr_service import get_ocr_status

    import sys
    # 确保 pytesseract 不在 sys.modules 缓存中
    monkeypatch.delitem(sys.modules, "pytesseract", raising=False)

    # 模拟 import pytesseract 抛 ImportError
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pytesseract":
            raise ImportError("simulated: pytesseract not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = get_ocr_status(env={})
    assert result["available"] is False
    assert result["pytesseract_version"] is None
    assert result["tesseract_binary_path"] is None
    assert result["languages"] == []


def test_get_ocr_status_when_binary_not_found(monkeypatch):
    """pytesseract 装了但二进制找不到时，available=False 但保留 pytesseract_version。"""
    from services.ocr_service import get_ocr_status

    # 构造一个 fake pytesseract 模块
    fake_pytesseract = MagicMock()
    fake_pytesseract.__version__ = "0.3.10"

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    result = get_ocr_status(env={})
    assert result["available"] is False
    assert result["pytesseract_version"] == "0.3.10"
    assert result["tesseract_binary_path"] is None
    assert result["languages"] == []


def test_get_ocr_status_when_binary_found(monkeypatch):
    """pytesseract + 二进制都可用时，available=True 且填充完整字段。"""
    from services.ocr_service import get_ocr_status

    fake_pytesseract = MagicMock()
    fake_pytesseract.__version__ = "0.3.10"

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: "/usr/bin/tesseract",
    )
    monkeypatch.setattr(
        "services.ocr_service._query_tesseract_version",
        lambda binary: "tesseract v5.3.4",
    )
    monkeypatch.setattr(
        "services.ocr_service._query_tesseract_languages",
        lambda binary: ["chi_sim", "eng", "osd"],
    )

    result = get_ocr_status(env={"TESSDATA_PREFIX": "/usr/share/tessdata"})
    assert result["available"] is True
    assert result["pytesseract_version"] == "0.3.10"
    assert result["tesseract_binary_path"] == "/usr/bin/tesseract"
    assert result["tesseract_version"] == "tesseract v5.3.4"
    assert result["languages"] == ["chi_sim", "eng", "osd"]
    assert result["tessdata_prefix"] == "/usr/share/tessdata"


# ═══════════════════════════════════════════════════════════════════
# P2.2: extract_text_from_image 的 language 参数与 settings 注入
# ═══════════════════════════════════════════════════════════════════


def test_extract_text_uses_explicit_language_argument(monkeypatch, tmp_path):
    """显式 language 参数应优先于 settings 与默认值。"""
    from services.ocr_service import extract_text_from_image

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "hello"

    fake_pil = MagicMock()
    fake_image = MagicMock()
    fake_pil.Image.open.return_value = fake_image

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil.Image)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    # 显式传 "eng"，settings 中是 "chi_sim+eng"（不应被用）
    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = "chi_sim+eng"

    result = extract_text_from_image(
        str(img_path), language="eng", settings_repository=fake_repo
    )
    assert result == "hello"
    # 验证 image_to_string 用的是显式参数
    _, kwargs = fake_pytesseract.image_to_string.call_args
    assert kwargs.get("lang") == "eng"


def test_extract_text_falls_back_to_settings_language(monkeypatch, tmp_path):
    """无显式 language 时，从 settings 读取 ocr_language。"""
    from services.ocr_service import extract_text_from_image

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "你好"

    fake_pil = MagicMock()
    fake_image = MagicMock()
    fake_pil.Image.open.return_value = fake_image

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil.Image)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    fake_repo = MagicMock()
    fake_repo.get_setting.return_value = "eng"

    result = extract_text_from_image(str(img_path), settings_repository=fake_repo)
    assert result == "你好"
    _, kwargs = fake_pytesseract.image_to_string.call_args
    assert kwargs.get("lang") == "eng"


def test_extract_text_falls_back_to_default_language(monkeypatch, tmp_path):
    """无显式 language 且 settings 为空时，使用默认 chi_sim+eng。"""
    from services.ocr_service import extract_text_from_image

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "txt"

    fake_pil = MagicMock()
    fake_image = MagicMock()
    fake_pil.Image.open.return_value = fake_image

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil.Image)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    # settings_repository=None，无 settings 提供
    result = extract_text_from_image(str(img_path), settings_repository=None)
    assert result == "txt"
    _, kwargs = fake_pytesseract.image_to_string.call_args
    assert kwargs.get("lang") == "chi_sim+eng"


def test_extract_text_returns_none_when_pytesseract_missing(monkeypatch, tmp_path):
    """pytesseract 未安装时，返回 None（不抛 ImportError 给调用方）。"""
    from services.ocr_service import extract_text_from_image

    import sys
    # 清除缓存，确保 import 真正走 __import__
    monkeypatch.delitem(sys.modules, "pytesseract", raising=False)
    monkeypatch.delitem(sys.modules, "PIL", raising=False)
    monkeypatch.delitem(sys.modules, "PIL.Image", raising=False)

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("pytesseract", "PIL"):
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    result = extract_text_from_image(str(img_path))
    assert result is None


def test_extract_text_returns_none_on_empty_result(monkeypatch, tmp_path):
    """OCR 返回空字符串时，service 应返回 None。"""
    from services.ocr_service import extract_text_from_image

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.return_value = "   \n  "  # 全空白

    fake_pil = MagicMock()
    fake_image = MagicMock()
    fake_pil.Image.open.return_value = fake_image

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil.Image)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    result = extract_text_from_image(str(img_path))
    assert result is None


def test_extract_text_returns_none_on_exception(monkeypatch, tmp_path):
    """pytesseract.image_to_string 抛异常时，service 应回收为 None。"""
    from services.ocr_service import extract_text_from_image

    fake_pytesseract = MagicMock()
    fake_pytesseract.image_to_string.side_effect = RuntimeError("tesseract crashed")

    fake_pil = MagicMock()
    fake_image = MagicMock()
    fake_pil.Image.open.return_value = fake_image

    import sys
    monkeypatch.setitem(sys.modules, "pytesseract", fake_pytesseract)
    monkeypatch.setitem(sys.modules, "PIL", fake_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", fake_pil.Image)

    monkeypatch.setattr(
        "services.ocr_service._resolve_tesseract_binary",
        lambda *, env=None, settings_repository=None: None,
    )

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    result = extract_text_from_image(str(img_path))
    assert result is None


# ═══════════════════════════════════════════════════════════════════
# P2.2: handler 透传 language 参数到 service
# ═══════════════════════════════════════════════════════════════════


def test_ocr_extract_text_handler_passes_language_to_service(monkeypatch, tmp_path):
    """handler 必须把 language 参数透传给 service。"""
    import asyncio

    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry
    import services.ocr_service as svc

    captured: dict[str, object] = {}

    def fake_extract(path, *, language=None, settings_repository=None):
        captured["language"] = language
        captured["settings_repository"] = settings_repository
        return "extracted"

    monkeypatch.setattr(svc, "extract_text_from_image", fake_extract)

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"FAKE")

    registry = MethodRegistry()
    ctx = MagicMock()
    ctx.settings_repository = MagicMock()
    register_ocr(registry, ctx)

    result = asyncio.run(
        registry.dispatch(
            "ocr.extract_text",
            {"image_path": str(img_path), "language": "eng"},
        )
    )
    assert result["success"] is True
    assert result["text"] == "extracted"
    assert captured["language"] == "eng"


def test_ocr_extract_base64_handler_passes_language_to_service(monkeypatch):
    """base64 入参的 handler 也必须透传 language。"""
    import asyncio
    import base64 as b64mod

    from backend.handlers._ocr import register_ocr
    from backend.method_registry import MethodRegistry
    import services.ocr_service as svc

    captured: dict[str, object] = {}

    def fake_extract(path, *, language=None, settings_repository=None):
        captured["language"] = language
        return "b64-text"

    monkeypatch.setattr(svc, "extract_text_from_image", fake_extract)

    registry = MethodRegistry()
    ctx = MagicMock()
    ctx.settings_repository = MagicMock()
    register_ocr(registry, ctx)

    fake_image_b64 = b64mod.b64encode(b"\x89PNG\r\n").decode()
    result = asyncio.run(
        registry.dispatch(
            "ocr.extract_base64",
            {"data": fake_image_b64, "language": "chi_sim"},
        )
    )
    assert result["success"] is True
    assert captured["language"] == "chi_sim"


# ═══════════════════════════════════════════════════════════════════
# P2.2: pyproject.toml optional-dependencies 分组
# ═══════════════════════════════════════════════════════════════════


def test_pyproject_has_ocr_optional_dependency():
    """pyproject.toml 必须声明 [ocr] 分组（与 [transcription] 对称）。"""
    pyproject_path = APP_ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        pytest.skip("pyproject.toml 不存在（部署变体）")
    content = pyproject_path.read_text(encoding="utf-8")
    assert "ocr = " in content and "pytesseract" in content, (
        "pyproject.toml 应在 [project.optional-dependencies] 添加 "
        "`ocr = [\"pytesseract>=0.3\"]`"
    )
    assert "transcription = " in content and "faster-whisper" in content, (
        "transcription 分组应同时存在（P2.1）"
    )
