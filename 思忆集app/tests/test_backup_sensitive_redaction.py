"""七维-数C2/安C6：备份产物敏感键剥离回归测试。

背景（七维报告 §2，干净会话复核属实）：BackupService 将 settings 原样写进
备份（backup_service.py:319-330 无任何脱敏），settings 中的 API key/token
明文（gzip 仅压缩非加密）随备份落盘/外传。

修复语义：settings 进备份前递归剥离敏感键（值为空串占位，保持结构），
键名计入日志；恢复后需重新录入凭据（不向备份传播旧凭据）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.backup_service import BackupConfig, BackupService, _strip_sensitive_settings


def _sensitive_settings() -> dict:
    return {
        "theme": "dark",
        "llm_api_key": "sk-secret-top",
        "smtp_password": "mail-pass",
        "provider_configs": {"deepseek": {"api_key": "sk-nested", "model": "deepseek-v4-flash"}},
        "sync": {"token": "sync-token"},
    }


def _seed(data_dir: Path) -> None:
    (data_dir / "notes.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
    (data_dir / "settings.json").write_text(json.dumps(_sensitive_settings(), ensure_ascii=False), encoding="utf-8")


class TestStripSensitiveSettings:
    def test_nested_and_top_level_stripped(self):
        out, names = _strip_sensitive_settings(_sensitive_settings())
        blob = json.dumps(out, ensure_ascii=False)
        for secret in ("sk-secret-top", "sk-nested", "mail-pass", "sync-token"):
            assert secret not in blob, f"敏感值泄漏: {secret}"
        assert out["theme"] == "dark"
        assert out["provider_configs"]["deepseek"]["model"] == "deepseek-v4-flash"
        assert out["provider_configs"]["deepseek"].get("api_key") == ""
        assert "llm_api_key" in names and "smtp_password" in names
        assert any("provider_configs" in n and "api_key" in n for n in names)

    def test_original_not_mutated(self):
        s = _sensitive_settings()
        _strip_sensitive_settings(s)
        assert s["llm_api_key"] == "sk-secret-top"


@pytest.mark.asyncio
async def test_backup_file_contains_no_secrets(tmp_path: Path) -> None:
    _seed(tmp_path)
    svc = BackupService(
        data_dir=str(tmp_path),
        config=BackupConfig(compress=False, max_backups=3),
        data_store=None,
    )
    res = await svc.create_backup()
    assert res.success, res.error
    blob = Path(res.file_path).read_text(encoding="utf-8")
    for secret in ("sk-secret-top", "sk-nested", "mail-pass", "sync-token"):
        assert secret not in blob, "备份产物泄漏敏感值"
