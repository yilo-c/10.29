"""平台发布服务 — 发布内容到外部平台（WordPress/Medium 等）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.services.platform_publisher")

SUPPORTED_PLATFORMS = {"wordpress", "medium"}


class PlatformPublisherService:
    """发布内容到外部平台。

    每个平台从 settings_repository 读取对应凭证，通过平台 API 发布。
    """

    def __init__(self, settings_repository: Any = None) -> None:
        self._settings = settings_repository

    async def publish(
        self,
        platform: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        """发布内容到指定平台。

        Args:
            platform: 目标平台（wordpress / medium）
            content: {
                "title": str,
                "body": str (markdown),
                "image_urls": list[str],
                "tags": list[str],
                "status": "draft" | "publish" (默认 draft)
            }

        Returns:
            {"success": bool, "url": str, "error": str}
        """
        platform = platform.lower().strip()
        if platform not in SUPPORTED_PLATFORMS:
            return {
                "success": False,
                "error": f"不支持的平台: {platform}，支持: {', '.join(sorted(SUPPORTED_PLATFORMS))}",
            }

        title = str(content.get("title", "")).strip()
        body = str(content.get("body", "")).strip()
        if not title or not body:
            return {"success": False, "error": "标题和正文不能为空"}

        dispatch = {
            "wordpress": self._publish_wordpress,
            "medium": self._publish_medium,
        }
        return await dispatch[platform](content)

    async def _publish_wordpress(
        self, content: dict[str, Any]
    ) -> dict[str, Any]:
        """通过 WordPress XML-RPC 发布。"""
        wp_url = self._get_credential("wordpress", "url")
        wp_user = self._get_credential("wordpress", "username")
        wp_pass = self._get_credential("wordpress", "password")

        if not all([wp_url, wp_user, wp_pass]):
            return {
                "success": False,
                "error": "WordPress 凭证不完整，请设置 wordpress_url / wordpress_username / wordpress_password",
            }

        import aiohttp

        # 用 aiohttp ClientSession 兼容 XML-RPC 调用
        # WordPress 新版也支持 REST API，优先尝试 REST
        rest_url = wp_url.rstrip("/") + "/wp-json/wp/v2/posts"
        app_pass = wp_pass  # 应用密码

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {__import__('base64').b64encode(f'{wp_user}:{app_pass}'.encode()).decode()}",
        }

        image_ids: list[int] = []
        for img_url in content.get("image_urls", []):
            try:
                img_id = await self._upload_image(rest_url, headers, img_url)
                if img_id:
                    image_ids.append(img_id)
            except Exception as exc:
                logger.warning("WordPress image upload failed: %s", exc)

        # 构建文章 body — 图片嵌入
        body = str(content.get("body", ""))
        for img_url in content.get("image_urls", []):
            if img_url not in body:
                body = f"![]({img_url})\n\n" + body

        tags = content.get("tags", [])
        status = content.get("status", "draft")

        payload = {
            "title": content.get("title", ""),
            "content": body,
            "status": status,
            "tags": tags if isinstance(tags, list) else [],
        }
        if image_ids:
            payload["featured_media"] = image_ids[0]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    rest_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()

            if resp.status in (200, 201):
                post_url = result.get("link", "")
                logger.info("WordPress publish success: %s", post_url)
                return {"success": True, "url": post_url}
            else:
                err_msg = result.get("message", str(result))
                logger.error("WordPress publish failed: %s", err_msg)
                return {"success": False, "error": err_msg}

        except Exception as exc:
            logger.error("WordPress request failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def _upload_image(
        self, rest_url: str, headers: dict[str, str], image_url: str
    ) -> int | None:
        """上传远程图片到 WordPress 媒体库。"""
        import aiohttp

        media_url = rest_url.rstrip("/posts").rstrip("/wp/v2") + "/media"
        async with aiohttp.ClientSession() as session:
            # 下载图片
            async with session.get(
                image_url, timeout=aiohttp.ClientTimeout(total=15)
            ) as img_resp:
                if img_resp.status != 200:
                    return None
                img_data = await img_resp.read()
                content_type = img_resp.headers.get("Content-Type", "image/png")

            # 上传
            filename = image_url.rsplit("/", 1)[-1] or "image.png"
            form = aiohttp.FormData()
            form.add_field(
                "file",
                img_data,
                filename=filename,
                content_type=content_type,
            )
            async with session.post(
                media_url, headers=headers, data=form, timeout=aiohttp.ClientTimeout(total=30)
            ) as media_resp:
                media_result = await media_resp.json()
                return media_result.get("id")

    async def _publish_medium(
        self, content: dict[str, Any]
    ) -> dict[str, Any]:
        """通过 Medium API 发布。"""
        token = self._get_credential("medium", "token")

        if not token:
            return {
                "success": False,
                "error": "Medium token 未配置，请设置 medium_token",
            }

        import aiohttp

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            # 获取用户 ID
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.medium.com/v1/me",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    user_data = await resp.json()

            user_id = (
                user_data.get("data", {}).get("id")
                if user_data.get("data")
                else None
            )
            if not user_id:
                return {"success": False, "error": "获取 Medium 用户信息失败"}

            tags = content.get("tags", [])
            if isinstance(tags, list):
                tags = tags[:5]  # Medium 最多 5 个标签

            body = str(content.get("body", ""))
            # Medium API 接受 markdown
            content_str = body
            for img_url in content.get("image_urls", []):
                if img_url not in content_str:
                    content_str = f"![]({img_url})\n\n" + content_str

            payload = {
                "title": content.get("title", ""),
                "contentFormat": "markdown",
                "content": content_str,
                "tags": tags,
                "publishStatus": content.get("status", "draft"),
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.medium.com/v1/users/{user_id}/posts",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    post_data = await resp.json()

            if resp.status in (200, 201):
                post_url = post_data.get("data", {}).get("url", "")
                logger.info("Medium publish success: %s", post_url)
                return {"success": True, "url": post_url}
            else:
                err_msg = post_data.get("errors", [{}])[0].get("message", str(post_data))
                logger.error("Medium publish failed: %s", err_msg)
                return {"success": False, "error": err_msg}

        except Exception as exc:
            logger.error("Medium request failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_credential(self, platform: str, key: str) -> str:
        """从 settings_repository 读取平台凭证。"""
        if not self._settings:
            return ""
        setting_key = f"{platform}_{key}"
        try:
            val = self._settings.get_setting(setting_key)
            return str(val) if val else ""
        except Exception:
            return ""
