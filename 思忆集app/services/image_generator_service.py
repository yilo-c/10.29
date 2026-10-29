"""图片生成服务 — AI 图片生成，回退到 OpenAI DALL-E 3 API。"""

from __future__ import annotations

import base64
import logging
from typing import Any

logger = logging.getLogger("siyi.services.image_generator")


class ImageGeneratorService:
    """AI 图片生成。

    策略：
    1. 如果 ai_client 有 generate_image 方法 → 优先使用
    2. 否则回退到 OpenAI DALL-E 3 API（从 settings 读取 key）
    """

    def __init__(self, ai_client: Any = None, settings_repository: Any = None) -> None:
        self._ai_client = ai_client
        self._settings = settings_repository

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
    ) -> dict[str, Any]:
        """生成图片。

        Args:
            prompt: 图片描述
            size: 尺寸（1024x1024 / 1792x1024 / 1024x1792）
            n: 生成数量（1-4）

        Returns:
            {"success": bool, "images": list[{b64_data, format}], "error": str}
        """
        if not prompt:
            return {"success": False, "error": "缺少 prompt 参数"}

        n = max(1, min(4, n))

        # 策略 1: ai_client 直接支持图片生成
        if self._ai_client and hasattr(self._ai_client, "generate_image"):
            try:
                result = await self._ai_client.generate_image(
                    prompt=prompt, size=size, n=n
                )
                if result and isinstance(result, dict):
                    return result
            except Exception as exc:
                logger.warning("ai_client.generate_image failed, falling back: %s", exc)

        # 策略 2: OpenAI DALL-E 3 API
        return await self._fallback_openai(prompt, size, n)

    @staticmethod
    def _decrypt_if_secure(value: Any) -> Any:
        from core.secure_key_storage import SecureKeyStorage

        if not isinstance(value, str):
            return value
        secure = SecureKeyStorage(namespace="com.siyi.apikey")
        if secure.is_secure_token(value):
            return secure.decrypt(value)
        return value

    async def _fallback_openai(
        self, prompt: str, size: str, n: int
    ) -> dict[str, Any]:
        api_key = None
        api_base = "https://api.openai.com/v1"

        if self._settings:
            try:
                api_key = self._settings.get_setting("openai_api_key", "")
                api_key = self._decrypt_if_secure(api_key)
                stored_base = self._settings.get_setting("openai_api_base", "")
                if stored_base:
                    api_base = stored_base.rstrip("/")
            except Exception:
                pass

        if not api_key:
            from os import environ

            api_key = environ.get("OPENAI_API_KEY", "")

        if not api_key:
            return {
                "success": False,
                "error": "未配置 OpenAI API Key（设置 openai_api_key 或环境变量 OPENAI_API_KEY）",
            }

        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_base}/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "dall-e-3",
                        "prompt": prompt,
                        "n": n,
                        "size": size,
                        "response_format": "b64_json",
                    },
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    data = await resp.json()

            if "error" in data:
                err_msg = data["error"].get("message", str(data["error"]))
                logger.error("DALL-E API error: %s", err_msg)
                return {"success": False, "error": err_msg}

            images = []
            for img in data.get("data", []):
                b64 = img.get("b64_json", "")
                images.append(
                    {"b64_data": b64, "format": "png"}
                )

            return {"success": True, "images": images}

        except Exception as exc:
            logger.error("DALL-E request failed: %s", exc)
            return {"success": False, "error": str(exc)}
