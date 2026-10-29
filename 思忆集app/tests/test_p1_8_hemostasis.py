"""批次1 快速止血三件回归测试（B-14 补回，原 test_p1_8_hemostasis.py 从未入库）。

#8-3 settings 敏感键脱敏：core.env_loader._is_sensitive_key / mask_secret
     对 api_key / token 等敏感键返回掩码值，非敏感键明文。
#8-1 url_learning 目录白名单：_is_within_learn_whitelist 仅放行应用数据目录
     及 Documents / Downloads / Desktop，拒绝白名单外路径与 .. 遍历。
（#8-2 另由 test_url_safety_ssrf.py 20 例兜住，本文件不重复。）
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from core.env_loader import _is_sensitive_key, mask_secret


class Test8_3SettingsMasking:
    def test_is_sensitive_key_detects_secrets(self):
        assert _is_sensitive_key("openai_api_key") is True
        assert _is_sensitive_key("anthropic_api_key") is True
        assert _is_sensitive_key("SIYI_SYNC_TOKEN") is True

    def test_is_sensitive_key_allows_normal_keys(self):
        assert _is_sensitive_key("SIYI_WS_PORT") is False
        assert _is_sensitive_key("SIYI_DATA_DIR") is False

    def test_mask_secret_hides_value(self):
        masked = mask_secret("sk-1234567890")
        assert masked != "sk-1234567890"
        assert "***" in masked
        assert masked.startswith("sk-1")

    def test_mask_secret_short_value_fully_masked(self):
        assert mask_secret("ab") == "***"

    def test_settings_get_masks_sensitive_key(self):
        # 端到端：settings.get RPC 对敏感 key 返回脱敏 dict，而非明文
        from backend.handlers._system_settings import register_system_settings
        from backend.method_registry import MethodRegistry
        from unittest.mock import MagicMock

        import asyncio

        reg = MethodRegistry()
        ctx = SimpleNamespace(
            settings_repository=MagicMock(),
            recreate_ai_client=MagicMock(),
            agent_gateway=MagicMock(),
            local_data_store=MagicMock(),
        )
        ctx.settings_repository.get_setting = lambda k, d=None: "sk-secret-value-123"
        register_system_settings(reg, ctx, 18765, MagicMock())
        handler = reg._handlers["settings.get"]
        result = asyncio.run(handler(key="openai_api_key"))
        assert isinstance(result, dict)
        assert result.get("masked") is True
        assert result.get("value") != "sk-secret-value-123"
        assert "***" in result.get("value", "")


class Test8_1UrlLearningWhitelist:
    def test_within_data_dir_allowed(self, tmp_path: Path):
        from backend.handlers._url_learning import _is_within_learn_whitelist

        ctx = SimpleNamespace(
            local_data_store=SimpleNamespace(base_dir=str(tmp_path))
        )
        inside = tmp_path / "notes" / "a.md"
        inside.parent.mkdir(parents=True, exist_ok=True)
        assert _is_within_learn_whitelist(str(inside), ctx) is True

    def test_outside_path_rejected(self, tmp_path: Path):
        from backend.handlers._url_learning import _is_within_learn_whitelist

        ctx = SimpleNamespace(
            local_data_store=SimpleNamespace(base_dir=str(tmp_path))
        )
        assert _is_within_learn_whitelist("/etc/passwd", ctx) is False

    def test_traversal_rejected(self, tmp_path: Path):
        from backend.handlers._url_learning import _is_within_learn_whitelist

        ctx = SimpleNamespace(
            local_data_store=SimpleNamespace(base_dir=str(tmp_path))
        )
        trap = os.path.join(str(tmp_path), "..", "secret", "x.md")
        assert _is_within_learn_whitelist(trap, ctx) is False
