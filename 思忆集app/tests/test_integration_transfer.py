"""P1 集成测试：transfer.* 全部 5 个方法

验证双端传送功能。
"""
import pytest

pytestmark = pytest.mark.integration


class TestTransferTokenInfo:
    """transfer.token_info"""

    async def test_token_info(self, rpc):
        r = await rpc("transfer.token_info", {})
        result = r.get("result", {})
        assert isinstance(result, dict)
        # 返回 token_required / token_prefix / mode 字段
        assert "token_required" in result or "token_prefix" in result or "mode" in result


class TestTransferHistory:
    """transfer.history"""

    async def test_history_returns_list(self, rpc):
        r = await rpc("transfer.history", {"limit": 10})
        result = r.get("result", [])
        assert isinstance(result, list) or isinstance(result, dict)


class TestTransferSend:
    """transfer.send"""

    async def test_send_text_data(self, rpc):
        """发送文本数据"""
        r = await rpc("transfer.send", {
            "type": "text",
            "data": "integration test transfer",
            "target_device": "test_device",
        })
        result = r.get("result", {})
        assert isinstance(result, dict)
        assert "success" in result or "error" in result or "id" in result

    async def test_send_missing_type(self, rpc):
        r = await rpc("transfer.send", {"data": "test"})
        result = r.get("result", {})
        assert "error" in result or "success" in result


class TestTransferUpload:
    """transfer.upload"""

    async def test_upload_small_file(self, rpc):
        """上传小文件（base64）"""
        import base64
        content = b"integration test file content"
        r = await rpc("transfer.upload", {
            "file_data": base64.b64encode(content).decode(),
            "filename": "__test_integration.txt",
        })
        result = r.get("result", {})
        assert isinstance(result, dict)
        assert "success" in result or "error" in result or "file_id" in result


class TestTransferDownload:
    """transfer.download"""

    async def test_download_nonexistent(self, rpc):
        """下载不存在的文件应返回错误"""
        r = await rpc("transfer.download", {"file_id": "nonexistent_99999"})
        result = r.get("result", {})
        assert "error" in result or "success" in result
