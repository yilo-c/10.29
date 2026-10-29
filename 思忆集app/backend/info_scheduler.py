"""后台资讯定时采集调度器。

管理定时抓取外部资讯（HN/Dev.to/GitHub/Solidot/Oschina/Juejin）的后台任务。
支持配置启停、间隔和兴趣话题。采集结果通过 EventBus 推送。
P2: 定时抓取后自动生成 Top5 摘要并推送到收件箱。
P3: 封装为 InfoScheduler 类，消除模块级可变全局变量。
"""

from __future__ import annotations

import asyncio
import logging

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.backend.info_scheduler")


class InfoScheduler:
    """资讯定时采集调度器，封装所有运行时状态。

    替代原模块级全局变量 (_enabled / _interval / _topics / ...)，
    提升可测试性并支持多实例。
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._enabled: bool = False
        self._interval: int = 3600  # seconds
        self._topics: list[str] | None = None
        self._collector: object | None = None
        self._context: object | None = None  # AppContext，用于 inbox 推送
        self._settings: object | None = None  # settings_repository，configure 落盘用

    # ── 依赖注入 ──

    def set_collector(self, collector: object | None) -> None:
        """设置 info collector 实例（由 async_main 在启动时注入）。"""
        self._collector = collector

    def set_context(self, ctx: object | None) -> None:
        """设置 AppContext（用于 inbox 推送和 AI 摘要）。"""
        self._context = ctx

    def set_settings(self, settings: object | None) -> None:
        """注入 settings_repository（headless_main 启动时接线），供 configure 落盘。"""
        self._settings = settings

    # ── 状态查询 ──

    def is_enabled(self) -> bool:
        return self._enabled

    def get_config(self) -> dict:
        return {
            "enabled": self._enabled,
            "interval": self._interval,
            "topics": self._topics,
        }

    # ── 生命周期 ──

    def restart(self) -> None:
        """重启定时采集（根据当前开关状态）。"""
        self.cancel()
        if self._enabled:
            self._task = asyncio.create_task(self._loop())

    def cancel(self) -> None:
        """取消正在运行的定时采集任务。"""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def configure(
        self,
        enabled: bool,
        interval: int = 3600,
        topics: list[str] | None = None,
    ) -> dict:
        """配置并重启调度器。返回当前状态 dict。

        Args:
            enabled: 是否启用定时采集
            interval: 采集间隔（秒，最小 300）
            topics: 兴趣话题列表
        """
        self._enabled = enabled
        self._interval = max(300, interval)
        self._topics = topics
        # 持久化（2026-10-16 用户拍板修复）：此前 configure 只改内存，前端开关
        # （info.auto_config）跨后端重启即丢失，恢复成启动默认 enabled=True。
        # 键名与 headless_main 启动恢复一致（info_scheduler_*）。
        if self._settings is not None:
            try:
                self._settings.set_setting("info_scheduler_enabled", bool(enabled))
                self._settings.set_setting("info_scheduler_interval", int(self._interval))
                self._settings.set_setting("info_scheduler_topics", topics)
            except Exception:
                logger.warning("info_scheduler 配置落盘失败（运行时状态不受影响）", exc_info=True)
        self.restart()
        logger.info(
            "info auto-collect %s (interval=%ds, topics=%s)",
            "enabled" if enabled else "disabled",
            self._interval,
            self._topics or "default",
        )
        return {
            "success": True,
            "enabled": self._enabled,
            "interval": self._interval,
            "topics": self._topics,
        }

    # ── 内部 ──

    async def _loop(self) -> None:
        """后台定时采集资讯，完成后通过 EventBus 推送。"""
        # 任务1修复：原逻辑先 sleep(_interval) 再采集，导致后端启动后 1 小时内无任何数据。
        # 改为先立即采集一次，再进入定时循环，让前端启动后能尽快看到抓取信息。
        _first_run = True
        while self._enabled:
            try:
                if not _first_run:
                    await asyncio.sleep(self._interval)
                    if not self._enabled:
                        break
                _first_run = False
                collector = self._collector
                if collector is None:
                    continue
                # 按启用板块所关联信息源并集抓取；空列表则退回抓全源
                from services.info_constants import get_active_source_names

                result = await collector.collect(topics=self._topics, sources=get_active_source_names(), generate_brief=True)
                event_bus.emit(AppEvent("info.collected", payload=result))

                # 周期抓取时即把大致信息写入知识体系（structured_memory_entries）
                if result.get("items"):
                    await self._persist_to_memory(result)

                # P2: 推送简报到收件箱（仅当有 items 时）
                if result.get("items"):
                    await self._post_to_inbox(
                        result.get("brief", ""),
                        result["items"],
                        result.get("errors", []),
                    )

                # 云同步：简报推送到 Worker（静默，失败不影响主流程）
                if result.get("overview") or result.get("full_brief"):
                    await self._push_brief_to_cloud(result)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("periodic info collect failed: %s", exc)

    async def _persist_to_memory(self, result: dict) -> None:
        """周期抓取后把大致信息写入知识体系（structured_memory_entries）。

        复用 cron info.collect 的落库逻辑（dsh 内核整理 + 当日 digest 幂等去重），
        保证周期抓取与每日 info.collect 写入同一处、不重复。
        """
        ctx = self._context
        items = result.get("items") or []
        if ctx is None or not items:
            return
        try:
            # 惰性 import：避免与 services.cron_actions 形成启动期循环依赖
            from services.cron_actions import _persist_info_memory

            await asyncio.to_thread(
                _persist_info_memory,
                ctx,
                items,
                result.get("brief") or "",
            )
        except Exception as exc:
            logger.warning("periodic info persist to memory failed: %s", exc)

    async def _post_to_inbox(self, brief: str, items: list[dict], errors: list[dict]) -> None:
        """将简报 + Top5 摘要推送到收件箱。

        - 取 score 最高的 5 条 items
        - 对每条调 AI 生成 1-2 句摘要
        - 拼成一条 inbox entry 推送
        """
        ctx = self._context
        if ctx is None:
            return
        inbox_repo = getattr(ctx, "personal_inbox_repository", None)
        if not inbox_repo:
            return

        # 取 Top 5 by score
        top_items = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]

        ai_client = getattr(ctx, "ai_client", None)

        async def _gen_one_summary(item: dict) -> str:
            """为单条资讯生成一句话摘要。"""
            title = item.get("title_zh") or item.get("title") or ""
            url = item.get("url", "")
            existing_summary = item.get("summary_zh") or item.get("summary") or ""
            if ai_client:
                try:
                    prompt = f"请用一句话（30字以内）概括以下资讯的核心内容：\n标题：{title}\n摘要：{existing_summary[:200]}"
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            ai_client.complete_text,
                            [{"role": "user", "content": prompt}],
                            max_tokens=80,
                        ),
                        timeout=15,
                    )
                    one_line = (result or "").strip()
                    if one_line:
                        return f"- [{title}]({url})\n  {one_line}"
                except Exception as exc:
                    logger.debug("inbox summary for '%s' failed: %s", title[:30], exc)
            # Fallback: 用已有 summary
            return f"- [{title}]({url})\n  {existing_summary[:80]}"

        # P2 优化：并行生成 5 条摘要，替代串行逐条调用
        summaries = await asyncio.gather(*[_gen_one_summary(item) for item in top_items])

        # 拼接 inbox 内容
        content_parts = []
        if brief:
            content_parts.append(brief)
        if summaries:
            content_parts.append("\n### Top 5 摘要\n\n" + "\n\n".join(summaries))

        content = "\n\n---\n\n".join(content_parts) if content_parts else ""
        if not content:
            return

        try:
            await asyncio.to_thread(
                inbox_repo.add_entry,
                {
                    "content": content,
                    "title": f"资讯简报 - {len(items)} 条",
                    "source": "info_scheduler",
                },
            )
            logger.info("info scheduler pushed brief to inbox (%d items)", len(items))
        except Exception as exc:
            logger.warning("info scheduler inbox push failed: %s", exc)

    async def _push_brief_to_cloud(self, result: dict) -> None:
        """将最新简报推送到 Cloudflare Worker（best-effort）。

        受云同步总开关门控（§2-③）：注入 settings_repository，令
        ``cloud_sync_enabled=False`` 时 push_info_brief_standalone 直接不推。
        """
        try:
            settings = getattr(self._context, "settings_repository", None)
            from sync.cloud_sync_client import push_info_brief_standalone

            await push_info_brief_standalone(
                result.get("overview", ""),
                result.get("section_briefs", {}),
                result.get("full_brief", result.get("brief", "")),
                len(result.get("items", [])),
                settings=settings,
            )
        except Exception:
            logger.debug("cloud push info_brief failed (non-critical)", exc_info=True)


# ── 模块级单例（向后兼容现有调用方）──
# headless_main.py / _inbox_data.py / tests 均通过模块级函数访问，
# 保持 API 不变的同时，状态封装在实例中。
_scheduler = InfoScheduler()

# 向后兼容的模块级 API
set_collector = _scheduler.set_collector
set_context = _scheduler.set_context
set_settings = _scheduler.set_settings
is_enabled = _scheduler.is_enabled
get_config = _scheduler.get_config
configure = _scheduler.configure
restart = _scheduler.restart
cancel = _scheduler.cancel
