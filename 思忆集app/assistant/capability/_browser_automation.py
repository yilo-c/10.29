"""全功能浏览器自动化能力域（基于 Playwright）。

提供完整的浏览器操作能力：导航、点击、输入、提取、截图、滚动、多标签等。
补齐思忆集 Agent 在浏览器全自动化方面的能力缺口，使其可对标 OpenClaw 等
全功能 Agent 框架。

设计要点：
1. 懒初始化 — 仅在首次调用浏览器工具时启动 Playwright，避免启动开销
2. 会话保持 — 单个 BrowserContext 跨多次工具调用复用，保持登录态/cookie
3. 有头模式 — 思忆集是桌面应用，浏览器窗口对用户可见，符合 foreground_visible 语义
4. 安全限制 — 默认超时 300 秒，单页内容截断 50KB，禁止访问内网地址
5. 同步接口 — capability_router 的 _handlers 是同步的，通过 asyncio.run 桥接
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("siyi.assistant.capability.browser_automation")

# Playwright 是否可用（懒检测）
_PLAYWRIGHT_AVAILABLE: bool | None = None

# 内网/危险地址黑名单（防止 SSRF）
_BLOCKED_HOST_PATTERNS = [
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^localhost$", re.IGNORECASE),
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^::1$"),
    re.compile(r"^fe80:", re.IGNORECASE),  # IPv6 link-local
]

# 单次提取内容上限（字符数）
_MAX_EXTRACT_CHARS = 50000
# 单个 Agent 任务超时（秒）
_AGENT_TASK_TIMEOUT = 300
# 单个简单操作超时（秒）
_SIMPLE_OP_TIMEOUT = 60


def _check_playwright() -> bool:
    """检测 Playwright 是否可用（懒检测，结果缓存）。"""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is None:
        try:
            import playwright  # noqa: F401
            _PLAYWRIGHT_AVAILABLE = True
        except ImportError:
            _PLAYWRIGHT_AVAILABLE = False
            logger.info("Playwright 未安装，浏览器自动化能力将不可用")
    return _PLAYWRIGHT_AVAILABLE


def _is_url_safe(url: str) -> tuple[bool, str]:
    """检查 URL 是否安全（非内网地址）。返回 (是否安全, 原因)。"""
    if not url:
        return False, "URL 为空"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False, "仅允许 http/https 协议"
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        for pattern in _BLOCKED_HOST_PATTERNS:
            if pattern.match(host):
                return False, f"禁止访问内网地址: {host}"
        return True, ""
    except Exception as exc:
        return False, f"URL 解析失败: {exc}"


class BrowserAutomationDomain:
    """浏览器自动化能力域：导航、点击、填表、提取、截图等。"""

    def __init__(self, router) -> None:
        self.router = router
        # Playwright 会话状态（懒初始化）
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._init_lock = asyncio.Lock() if _check_playwright() else None

    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """获取（或创建）一个事件循环用于异步 Playwright 调用。"""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run_async(self, coro):
        """在同步上下文中运行异步协程。"""
        loop = self._get_event_loop()
        return loop.run_until_complete(coro)

    async def _ensure_session(self):
        """懒初始化 Playwright 会话（BrowserContext + Page）。"""
        if self._page is not None:
            return self._page
        if not _check_playwright():
            raise RuntimeError("Playwright 未安装，无法启动浏览器自动化")

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        # 有头模式 — 思忆集是桌面应用，用户可见浏览器窗口
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        # 独立 context，不共享用户主 profile 的 cookie（沙箱隔离）
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        logger.info("BrowserAutomation 会话已启动")
        return self._page

    async def _close_session(self):
        """关闭 Playwright 会话，释放资源。"""
        if self._playwright:
            try:
                if self._context:
                    await self._context.close()
                if self._browser:
                    await self._browser.close()
                await self._playwright.stop()
            except Exception:
                logger.debug("关闭 Playwright 会话异常", exc_info=True)
            finally:
                self._playwright = None
                self._browser = None
                self._context = None
                self._page = None

    # ── 工具实现 ──────────────────────────────────────────────

    def browser_navigate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """打开一个网页 URL。"""
        url = str(payload.get("url") or "").strip()
        if not url:
            return {"success": False, "error": "缺少 url 参数"}

        safe, reason = _is_url_safe(url)
        if not safe:
            return {"success": False, "error": reason}

        async def _go():
            page = await self._ensure_session()
            await page.goto(url, timeout=_SIMPLE_OP_TIMEOUT * 1000, wait_until="domcontentloaded")
            return {
                "success": True,
                "url": page.url,
                "title": await page.title(),
            }

        try:
            return self._run_async(_go())
        except Exception as exc:
            logger.warning("browser.navigate 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_click(self, payload: dict[str, Any]) -> dict[str, Any]:
        """点击页面元素（通过 CSS 选择器、文本或索引）。"""
        selector = str(payload.get("selector") or "").strip()
        text = str(payload.get("text") or "").strip()

        if not selector and not text:
            return {"success": False, "error": "需要 selector 或 text 参数"}

        async def _click():
            page = await self._ensure_session()
            if text:
                # 通过文本查找元素（优先 button/a/input[type=submit]）
                locator = page.get_by_text(text, exact=False).first
                try:
                    await locator.click(timeout=_SIMPLE_OP_TIMEOUT * 1000)
                    return {"success": True, "clicked": text}
                except Exception:
                    # 回退到 role=button
                    locator = page.get_by_role("button", name=text).first
                    await locator.click(timeout=_SIMPLE_OP_TIMEOUT * 1000)
                    return {"success": True, "clicked": text}
            else:
                await page.click(selector, timeout=_SIMPLE_OP_TIMEOUT * 1000)
                return {"success": True, "clicked": selector}

        try:
            return self._run_async(_click())
        except Exception as exc:
            logger.warning("browser.click 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_type(self, payload: dict[str, Any]) -> dict[str, Any]:
        """在输入框中输入文本。"""
        selector = str(payload.get("selector") or "").strip()
        text = str(payload.get("text") or "")
        clear_first = bool(payload.get("clear", True))

        if not selector:
            return {"success": False, "error": "缺少 selector 参数"}

        async def _type():
            page = await self._ensure_session()
            if clear_first:
                await page.fill(selector, "", timeout=_SIMPLE_OP_TIMEOUT * 1000)
            await page.type(selector, text, delay=50, timeout=_SIMPLE_OP_TIMEOUT * 1000)
            return {"success": True, "selector": selector, "typed_length": len(text)}

        try:
            return self._run_async(_type())
        except Exception as exc:
            logger.warning("browser.type 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        """提取页面内容（文本、HTML 或通过 CSS 选择器提取特定元素）。"""
        selector = str(payload.get("selector") or "").strip()
        extract_type = str(payload.get("type") or "text").strip().lower()

        async def _extract():
            page = await self._ensure_session()
            if selector:
                # 提取特定元素
                elements = await page.query_selector_all(selector)
                results = []
                for el in elements[:50]:  # 最多 50 个元素
                    if extract_type == "html":
                        results.append(await el.inner_html())
                    elif extract_type == "attr":
                        attr_name = str(payload.get("attr") or "href")
                        results.append(await el.get_attribute(attr_name))
                    else:  # text
                        results.append(await el.inner_text())
                content = "\n---\n".join(results)
            else:
                # 提取整页
                if extract_type == "html":
                    content = await page.content()
                else:  # text
                    content = await page.inner_text("body")
            # 截断防止超长
            truncated = len(content) > _MAX_EXTRACT_CHARS
            if truncated:
                content = content[:_MAX_EXTRACT_CHARS]
            return {
                "success": True,
                "content": content,
                "truncated": truncated,
                "length": len(content),
                "url": page.url,
                "title": await page.title(),
            }

        try:
            return self._run_async(_extract())
        except Exception as exc:
            logger.warning("browser.extract 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        """截取当前页面的截图，保存到文件。"""
        # 默认保存到 agent_outputs 目录
        output_path = str(payload.get("path") or "").strip()
        full_page = bool(payload.get("full_page", True))

        if not output_path:
            # 自动生成路径
            ds = getattr(self.router, "context", None)
            ds = getattr(ds, "local_data_store", None) if ds else None
            base_dir = getattr(ds, "base_dir", None) if ds else None
            if not base_dir:
                return {"success": False, "error": "无法确定输出目录，请指定 path 参数"}
            os.makedirs(os.path.join(base_dir, "agent_outputs"), exist_ok=True)
            import time as _t
            output_path = os.path.join(
                base_dir, "agent_outputs",
                f"browser_screenshot_{int(_t.time())}.png",
            )

        async def _screenshot():
            page = await self._ensure_session()
            await page.screenshot(path=output_path, full_page=full_page)
            return {
                "success": True,
                "path": output_path,
                "full_page": full_page,
                "size_bytes": os.path.getsize(output_path) if os.path.exists(output_path) else 0,
            }

        try:
            return self._run_async(_screenshot())
        except Exception as exc:
            logger.warning("browser.screenshot 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_scroll(self, payload: dict[str, Any]) -> dict[str, Any]:
        """滚动页面（向下/向上/到顶部/到底部）。"""
        direction = str(payload.get("direction") or "down").strip().lower()
        amount = int(payload.get("amount", 500))

        async def _scroll():
            page = await self._ensure_session()
            if direction == "down":
                await page.mouse.wheel(0, amount)
            elif direction == "up":
                await page.mouse.wheel(0, -amount)
            elif direction == "top":
                await page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                return {"success": False, "error": f"未知方向: {direction}"}
            await page.wait_for_timeout(300)  # 等待渲染
            scroll_y = await page.evaluate("window.scrollY")
            scroll_h = await page.evaluate("document.body.scrollHeight")
            return {
                "success": True,
                "direction": direction,
                "scroll_y": scroll_y,
                "scroll_height": scroll_h,
            }

        try:
            return self._run_async(_scroll())
        except Exception as exc:
            logger.warning("browser.scroll 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_get_tabs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """获取当前浏览器中所有打开的标签页。"""
        async def _get_tabs():
            await self._ensure_session()
            pages = self._context.pages
            tabs = []
            for i, p in enumerate(pages):
                tabs.append({
                    "index": i,
                    "url": p.url,
                    "title": await p.title(),
                })
            return {"success": True, "tabs": tabs, "count": len(tabs)}

        try:
            return self._run_async(_get_tabs())
        except Exception as exc:
            logger.warning("browser.get_tabs 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_switch_tab(self, payload: dict[str, Any]) -> dict[str, Any]:
        """切换到指定索引的标签页。"""
        index = int(payload.get("index", 0))

        async def _switch():
            await self._ensure_session()
            pages = self._context.pages
            if index < 0 or index >= len(pages):
                return {"success": False, "error": f"标签索引超出范围: {index} (共 {len(pages)} 个)"}
            self._page = pages[index]
            await self._page.bring_to_front()
            return {
                "success": True,
                "index": index,
                "url": self._page.url,
                "title": await self._page.title(),
            }

        try:
            return self._run_async(_switch())
        except Exception as exc:
            logger.warning("browser.switch_tab 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_wait(self, payload: dict[str, Any]) -> dict[str, Any]:
        """等待指定时间（毫秒）或等待元素出现。"""
        ms = int(payload.get("ms", 1000))
        selector = str(payload.get("selector") or "").strip()

        async def _wait():
            page = await self._ensure_session()
            if selector:
                await page.wait_for_selector(selector, timeout=min(ms * 10, 30000))
                return {"success": True, "waited_for": selector}
            else:
                await page.wait_for_timeout(ms)
                return {"success": True, "waited_ms": ms}

        try:
            return self._run_async(_wait())
        except Exception as exc:
            logger.warning("browser.wait 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        """关闭浏览器会话，释放资源。"""
        try:
            self._run_async(self._close_session())
            return {"success": True, "message": "浏览器会话已关闭"}
        except Exception as exc:
            logger.warning("browser.close 失败: %s", exc)
            return {"success": False, "error": str(exc)}

    def browser_automate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """自动执行多步浏览器任务（由上层 Agent 编排，本工具为单步执行入口）。

        注意：此方法不实现完整的 AI Agent 循环（那需要 LLM 决策每一步）。
        完整的 Agent 循环由 SubAgentManager + ReAct 引擎在上层实现。
        此处仅提供一个"任务描述"入口，由 LLM 在 ReAct 循环中分解为具体步骤。
        """
        task = str(payload.get("task") or "").strip()
        if not task:
            return {"success": False, "error": "缺少 task 参数"}
        return {
            "success": True,
            "message": (
                "browser.automate 是一个元工具入口。请在 ReAct 循环中分解任务为具体步骤，"
                "使用 browser.navigate / browser.click / browser.type / browser.extract 等单步工具执行。"
            ),
            "task": task,
            "suggested_steps": [
                "1. browser.navigate 打开起始页面",
                "2. browser.extract 提取当前页面内容",
                "3. browser.click / browser.type 进行交互",
                "4. browser.extract 提取最终结果",
            ],
        }
