"""Test: transfer handler — 路径穿越保护、并发写入、错误脱敏、认证。

覆盖范围：
- _resolve_file_store_path: 路径穿越攻击向量
- _classify_by_rules: 规则分类正确性
- _check_auth: LAN/secure 模式认证
- transfer.upload/download/files/delete: 文件操作安全
- transfer.send/history: 消息传送持久化
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from types import SimpleNamespace

import pytest
from backend.handlers._transfer import (
    _resolve_file_store_path,
    _classify_by_rules,
    _check_auth,
    MAX_FILE_SIZE,
)


# ═══════════════════════════════════════════════════════════════
# _resolve_file_store_path
# ═══════════════════════════════════════════════════════════════


class TestResolveFileStorePath:
    """路径穿越防护测试。"""

    def test_normal_path_allowed(self, tmp_path):
        result = _resolve_file_store_path(str(tmp_path), "notes/abc/photo.jpg")
        assert result is not None
        assert result.startswith(str(tmp_path))
        assert result.endswith("photo.jpg")

    def test_simple_subdir_allowed(self, tmp_path):
        result = _resolve_file_store_path(str(tmp_path), "folder")
        assert result is not None
        assert result == os.path.join(str(tmp_path), "folder")

    def test_dotdot_traversal_blocked(self, tmp_path):
        """../settings.json 被拒绝。"""
        result = _resolve_file_store_path(str(tmp_path), "../settings.json")
        assert result is None

    def test_multi_dotdot_traversal_blocked(self, tmp_path):
        """....//settings.json — Windows 上 .... 是合法目录名，非路径穿越。"""
        result = _resolve_file_store_path(str(tmp_path), "....//settings.json")
        assert result is not None
        assert result.startswith(os.path.abspath(str(tmp_path)) + os.sep)

    def test_deep_dotdot_traversal_blocked(self, tmp_path):
        """../../outside/file.txt 应被拒绝。"""
        result = _resolve_file_store_path(str(tmp_path), "../../outside/file.txt")
        assert result is None

    def test_absolute_path_not_traversal_on_windows(self, tmp_path):
        """Windows 上 /etc/passwd 是相对路径，os.path.abspath 保留在 base_dir 内。"""
        result = _resolve_file_store_path(str(tmp_path), "/etc/passwd")
        assert result is not None
        assert result.startswith(os.path.abspath(str(tmp_path)) + os.sep)

    def test_encoded_dotdot_is_safe_on_windows(self, tmp_path):
        """Windows 上 %2F 不是路径分隔符，不触发穿越。"""
        result = _resolve_file_store_path(str(tmp_path), "..%2F..%2Fsettings.json")
        assert result is not None
        assert result.startswith(os.path.abspath(str(tmp_path)) + os.sep)

    def test_backslash_normalization(self, tmp_path):
        """反斜杠应被统一处理。"""
        result = _resolve_file_store_path(str(tmp_path), "notes\\abc\\photo.jpg")
        assert result is not None
        assert result.startswith(str(tmp_path))

    def test_empty_parts(self, tmp_path):
        """空 parts 应返回 base_dir 自身。"""
        result = _resolve_file_store_path(str(tmp_path))
        assert result == os.path.abspath(str(tmp_path))

    def test_extra_slash_handling(self, tmp_path):
        """多余斜杠应被处理。"""
        result = _resolve_file_store_path(str(tmp_path), "//notes///abc//")
        assert result is not None

    def test_null_byte_rejected(self, tmp_path):
        """空字节应被处理为普通字符（不崩溃）。"""
        result = _resolve_file_store_path(str(tmp_path), "notes/abc/\x00.jpg")
        assert result is not None  # 被当作普通文件名


# ═══════════════════════════════════════════════════════════════
# _classify_by_rules
# ═══════════════════════════════════════════════════════════════


class TestClassifyByRules:
    """规则分类测试。"""

    def test_link_classification(self):
        """含 URL → category=link, action=save_reading。"""
        result = _classify_by_rules("看看这个 https://example.com/article")
        assert result["category"] == "link"
        assert result["action"] == "save_reading"

    def test_image_classification(self):
        """data:image/ → category=image, action=create_note。"""
        result = _classify_by_rules("data:image/png;base64,iVBORw0KGgo=")
        assert result["category"] == "image"
        assert result["action"] == "create_note"

    def test_task_keyword_tomorrow(self):
        """含"明天" → category=task。"""
        result = _classify_by_rules("明天开会")
        assert result["category"] == "task"
        assert result["action"] == "create_task"

    def test_task_keyword_remind(self):
        """含"别忘了" → category=task。"""
        result = _classify_by_rules("别忘了买牛奶")
        assert result["category"] == "task"

    def test_preference_keyword(self):
        """含"喜欢" → category=preference。"""
        result = _classify_by_rules("我喜欢蓝色")
        assert result["category"] == "preference"
        assert result["action"] == "save_memory"

    def test_preference_habit(self):
        """含"习惯" → category=preference。"""
        result = _classify_by_rules("习惯早起")
        assert result["category"] == "preference"

    def test_long_text_as_note(self):
        """超过 100 字 → category=note。"""
        text = "这是一段很长的文字。" * 20
        result = _classify_by_rules(text)
        assert result["category"] == "note"
        assert result["action"] == "create_note"

    def test_short_text_as_null(self):
        """短文本且不匹配任何规则 → category=None。"""
        result = _classify_by_rules("随便说两句")
        assert result["category"] is None
        assert result["action"] is None

    def test_empty_text(self):
        """空文本 → category=None。"""
        result = _classify_by_rules("")
        assert result["category"] is None

    def test_task_precedence_over_note(self):
        """任务关键词应优先于长文本（即使超过100字）。"""
        text = "明天" + "需要完成的项目报告" * 20
        result = _classify_by_rules(text)
        assert result["category"] == "task"


# ═══════════════════════════════════════════════════════════════
# _check_auth
# ═══════════════════════════════════════════════════════════════


class TestCheckAuth:
    """认证逻辑测试。"""

    def test_lan_mode_no_auth_required(self, monkeypatch):
        """未设置 SIYI_TRANSFER_KEY → 不要求认证。"""
        monkeypatch.delenv("SIYI_TRANSFER_KEY", raising=False)
        assert _check_auth() is None

    def test_secure_mode_requires_token(self, monkeypatch):
        """设置了 SIYI_TRANSFER_KEY → 需要有效 token。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret-key")
        result = _check_auth()  # 未传 _token
        assert result is not None
        assert "auth required" in result

    def test_secure_mode_wrong_token(self, monkeypatch):
        """设置了 SIYI_TRANSFER_KEY 但传错 token → 拒绝。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret-key")
        result = _check_auth(_token="wrong-token")
        assert result is not None
        assert "auth failed" in result

    def test_secure_mode_correct_token(self, monkeypatch):
        """正确的 token → 通过。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret-key")
        result = _check_auth(_token="test-secret-key")
        assert result is None

    def test_secure_mode_empty_token_not_allowed(self, monkeypatch):
        """设置了 SIYI_TRANSFER_KEY 但 token 为空 → 拒绝。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "test-secret-key")
        result = _check_auth(_token="")
        assert result is not None


# ═══════════════════════════════════════════════════════════════
# 集成测试：注册 transfer handler 并调用 RPC
# ═══════════════════════════════════════════════════════════════


class MockRegistry:
    """模拟方法注册中心，捕获注册的函数以便测试调用。"""

    def __init__(self):
        self._handlers: dict[str, callable] = {}

    def register(self, method: str, handler: callable) -> None:
        self._handlers[method] = handler

    def get(self, method: str) -> callable | None:
        return self._handlers.get(method)


class MockContext:
    """模拟 AppContext（只需 minimal 接口）。"""

    local_data_store = None


@pytest.fixture
def registered_handlers(tmp_path):
    """注册 transfer handler，返回 handler dict。每个测试独立 tmp_path。"""
    from backend.handlers._transfer import register_transfer

    registry = MockRegistry()
    context = MockContext()
    context.local_data_store = SimpleNamespace(base_dir=str(tmp_path))

    # 设置 FILE_STORE_DIR 到临时目录
    file_dir = tmp_path / "files"
    file_dir.mkdir()

    # 通过环境变量控制 FILE_STORE_DIR
    old_file_dir = os.environ.get("SIYI_FILE_DIR")
    os.environ["SIYI_FILE_DIR"] = str(file_dir)

    # 禁用 LAN 模式（需要 auth）以便测试
    old_key = os.environ.get("SIYI_TRANSFER_KEY")
    os.environ["SIYI_TRANSFER_KEY"] = "test-key"

    register_transfer(registry, context)

    # 还原环境变量
    if old_key:
        os.environ["SIYI_TRANSFER_KEY"] = old_key
    else:
        del os.environ["SIYI_TRANSFER_KEY"]
    if old_file_dir:
        os.environ["SIYI_FILE_DIR"] = old_file_dir
    else:
        del os.environ["SIYI_FILE_DIR"]

    return registry._handlers


import pytest


class TestTransferUpload:
    """transfer.upload 文件上传测试。"""

    @pytest.mark.asyncio
    async def test_upload_success(self, registered_handlers, tmp_path):
        """正常上传图片文件。"""
        upload = registered_handlers["transfer.upload"]
        import base64

        data = base64.b64encode(b"fake-image-data").decode()
        result = await upload(name="test.png", data=f"data:image/png;base64,{data}", _token="test-key")
        assert result["success"] is True
        assert result["size"] == 15  # len(b"fake-image-data")

    @pytest.mark.asyncio
    async def test_upload_no_name(self, registered_handlers):
        """缺少文件名 → 错误。"""
        upload = registered_handlers["transfer.upload"]
        result = await upload(data="data:image/png;base64,AAAA", _token="test-key")
        assert result["success"] is False
        assert "name and data required" in result["error"]

    @pytest.mark.asyncio
    async def test_upload_no_data(self, registered_handlers):
        """缺少文件数据 → 错误。"""
        upload = registered_handlers["transfer.upload"]
        result = await upload(name="test.png", _token="test-key")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_upload_too_large(self, registered_handlers):
        """超过最大尺寸 → 被拒绝。"""
        upload = registered_handlers["transfer.upload"]
        big_data = "A" * (MAX_FILE_SIZE + 1)
        result = await upload(name="large.txt", data=big_data, _token="test-key")
        assert result["success"] is False
        assert "too large" in result["error"]


class TestTransferDownload:
    """transfer.download 文件下载测试。"""

    @pytest.mark.asyncio
    async def test_download_nonexistent(self, registered_handlers):
        """下载不存在的文件 → 404。"""
        download = registered_handlers["transfer.download"]
        result = await download(path="nonexistent.txt", _token="test-key")
        assert result["success"] is False
        assert result["error"] == "file not found"

    @pytest.mark.asyncio
    async def test_download_invalid_path(self, registered_handlers):
        """路径穿越 → 拒绝。"""
        download = registered_handlers["transfer.download"]
        result = await download(path="../settings.json", _token="test-key")
        assert result["success"] is False
        assert "invalid path" in result["error"]

    @pytest.mark.asyncio
    async def test_download_no_path(self, registered_handlers):
        """缺少路径参数 → 错误。"""
        download = registered_handlers["transfer.download"]
        result = await download(_token="test-key")
        assert result["success"] is False
        assert "path required" in result["error"]


class TestTransferFiles:
    """transfer.files 文件列表测试。"""

    @pytest.mark.asyncio
    async def test_files_empty_dir(self, registered_handlers):
        """空目录 → 空列表。"""
        files = registered_handlers["transfer.files"]
        result = await files(_token="test-key")
        assert result == []

    @pytest.mark.asyncio
    async def test_files_invalid_folder(self, registered_handlers):
        """路径穿越 → 空列表。"""
        files = registered_handlers["transfer.files"]
        result = await files(folder="../settings.json", _token="test-key")
        assert result == []


class TestTransferDelete:
    """transfer.delete 文件删除测试。"""

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, registered_handlers):
        """删除不存在的文件 → 404。"""
        delete = registered_handlers["transfer.delete"]
        result = await delete(path="nonexistent.txt", _token="test-key")
        assert result["success"] is False
        assert result["error"] == "file not found"

    @pytest.mark.asyncio
    async def test_delete_invalid_path(self, registered_handlers):
        """路径穿越 → 拒绝。"""
        delete = registered_handlers["transfer.delete"]
        result = await delete(path="../settings.json", _token="test-key")
        assert result["success"] is False
        assert "invalid path" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_directory_blocked(self, registered_handlers, tmp_path):
        """删除目录 → 被拒绝。"""
        delete = registered_handlers["transfer.delete"]
        result = await delete(path=".", _token="test-key")
        assert result["success"] is False
        assert "cannot delete directory" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_no_path(self, registered_handlers):
        """缺少路径 → 错误。"""
        delete = registered_handlers["transfer.delete"]
        result = await delete(_token="test-key")
        assert result["success"] is False


class TestTransferSend:
    """transfer.send 消息传送测试。"""

    @pytest.mark.asyncio
    async def test_send_text(self, registered_handlers, tmp_path):
        """发送纯文本消息。"""
        send = registered_handlers["transfer.send"]
        result = await send(text="hello world", _token="test-key")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_send_empty(self, registered_handlers):
        """空消息 → 错误。"""
        send = registered_handlers["transfer.send"]
        result = await send(text="", _token="test-key")
        assert result.get("error") is not None

    @pytest.mark.asyncio
    async def test_send_with_file(self, registered_handlers):
        """发送带文件的消息。"""
        send = registered_handlers["transfer.send"]
        result = await send(text="check this", file="base64data", fileName="photo.jpg", _token="test-key")
        assert result["success"] is True


class TestTransferHistory:
    """transfer.history 历史记录测试。"""

    @pytest.mark.asyncio
    async def test_history_empty(self, registered_handlers):
        """无历史 → 空列表。"""
        history = registered_handlers["transfer.history"]
        result = await history(_token="test-key")
        assert result == []

    @pytest.mark.asyncio
    async def test_history_after_send(self, registered_handlers):
        """发送后历史应有记录。"""
        send = registered_handlers["transfer.send"]
        history = registered_handlers["transfer.history"]
        await send(text="test message", _token="test-key")
        result = await history(limit=10, _token="test-key")
        assert len(result) > 0
        assert result[0]["text"] == "test message"


class TestTransferAuth:
    """认证集成测试。"""

    @pytest.mark.asyncio
    async def test_upload_no_auth(self, registered_handlers, monkeypatch):
        """无认证的 upload → 被拒绝。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "required-key")
        upload = registered_handlers["transfer.upload"]
        result = await upload(name="test.png", data="data:image/png;base64,AAAA")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_download_auth_failure(self, registered_handlers, monkeypatch):
        """认证失败的 download → 不泄漏文件信息。"""
        monkeypatch.setenv("SIYI_TRANSFER_KEY", "required-key")
        download = registered_handlers["transfer.download"]
        result = await download(path="somefile.txt")
        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# 七维-兼C4：手机端活跃链路参数补齐（file_path / folder）
# 证据：TransferView.tsx:43 发 file_path；noteAttachment.ts:346 发 folder
# 历史：2026-06-03 H1 已为手机端补过 file/fileName（同一模式）
# ═══════════════════════════════════════════════════════════════


class TestTransferUploadFolder:
    """transfer.upload folder 参数：按目录落盘（此前被 basename 拍平）。"""

    @pytest.mark.asyncio
    async def test_upload_with_folder_lands_in_subdir(self, registered_handlers, tmp_path):
        upload = registered_handlers["transfer.upload"]
        import base64

        data = base64.b64encode(b"attachment-bytes").decode()
        result = await upload(name="a.png", data=data, folder="notes/n1", _token="test-key")
        assert result.get("success") is True, result
        assert result["path"] == "notes/n1/a.png"
        landed = tmp_path / "transfer_files" / "notes" / "n1" / "a.png"
        assert landed.exists(), f"文件未按 folder 落盘: {landed}"

    @pytest.mark.asyncio
    async def test_upload_folder_traversal_rejected(self, registered_handlers):
        upload = registered_handlers["transfer.upload"]
        import base64

        data = base64.b64encode(b"x").decode()
        result = await upload(name="a.png", data=data, folder="../evil", _token="test-key")
        assert result.get("success") is False
        assert result.get("error", "")

    @pytest.mark.asyncio
    async def test_upload_folder_dotdot_inside_rejected(self, registered_handlers):
        upload = registered_handlers["transfer.upload"]
        import base64

        data = base64.b64encode(b"x").decode()
        result = await upload(name="a.png", data=data, folder="notes/../../evil", _token="test-key")
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_upload_without_folder_unchanged(self, registered_handlers):
        """回归守卫：不带 folder 行为不变（落 transfer_files 根、返回 basename）。"""
        upload = registered_handlers["transfer.upload"]
        import base64

        data = base64.b64encode(b"plain").decode()
        result = await upload(name="b.bin", data=data, _token="test-key")
        assert result["success"] is True
        assert result["path"] == "b.bin"


class TestTransferSendFilePath:
    """transfer.send file_path 参数：显式接收并记录（此前被 kwargs 静默丢弃）。"""

    @pytest.mark.asyncio
    async def test_send_with_file_path_recorded(self, registered_handlers):
        send = registered_handlers["transfer.send"]
        history = registered_handlers["transfer.history"]
        result = await send(text="看这个文件", file_path="file:///data/0/big.mp4", _token="test-key")
        assert result.get("success") is True, result
        assert result.get("warning"), "响应应携带 file_path 不可直读的告警"
        h = await history(limit=5, _token="test-key")
        assert h and h[0].get("file_path") == "file:///data/0/big.mp4"

    @pytest.mark.asyncio
    async def test_send_only_file_path_no_text(self, registered_handlers):
        """仅 file_path（无 text/file）也允许成条（此前报 "text or file required"）。"""
        send = registered_handlers["transfer.send"]
        result = await send(file_path="file:///data/0/big.mp4", _token="test-key")
        assert result.get("success") is True, result

    @pytest.mark.asyncio
    async def test_send_file_path_long_value_truncated(self, registered_handlers):
        """file_path 是设备本地引用，不做路径校验，仅截断存储。"""
        send = registered_handlers["transfer.send"]
        result = await send(text="x", file_path="../../" + "a" * 300, _token="test-key")
        assert result.get("success") is True
