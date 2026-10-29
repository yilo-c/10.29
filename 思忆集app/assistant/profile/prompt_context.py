"""
助手提示词上下文构建模块
从 ui_main_window.py 中提取出的所有提示词构建、能力列表、降级回答、网络搜索增强等方法。
对外暴露 PromptContext 类，主窗口持有其实例并委托调用。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("siyi.prompt_context")


class PromptContext:
    """助手提示词上下文构建器，负责生成系统提示词、聊天上下文、能力列表等。"""

    # 知识类问题线索
    _KNOWLEDGE_CUES = (
        "推荐",
        "介绍",
        "解释",
        "什么是",
        "有哪些",
        "区别",
        "比较",
        "怎么",
        "如何",
        "为什么",
        "是什么",
        "哪个好",
        "哪些",
        "优缺点",
        "方法",
        "原理",
        "教程",
        "步骤",
        "定义",
        "意思",
        "含义",
        "建议",
        "选择",
        "评价",
        "总结一下",
        "翻译",
        "英文",
        "中文",
    )

    # 本地数据查询线索
    _LOCAL_DATA_CUES = (
        "我的任务",
        "我的笔记",
        "我的待办",
        "我的收件",
        "有多少",
        "汇总",
        "统计",
        "概况",
        "工作台",
    )

    # 需要联网搜索的线索
    _WEB_SEARCH_CUES = (
        "最新",
        "最近",
        "实时",
        "今天",
        "昨天",
        "本周",
        "近期",
        "推荐",
        "排名",
        "榜单",
        "趋势",
        "动态",
        "搜索",
        "搜一下",
        "查一下",
        "了解一下",
        "新闻",
        "报道",
        "发布",
        "更新",
        "版本",
        "价格",
        "多少钱",
        "怎么买",
    )

    # 不需要联网的线索
    _NO_SEARCH_CUES = (
        "我的",
        "打开",
        "启动",
        "运行",
        "执行",
        "设置",
        "剪贴板",
        "任务",
        "待办",
        "笔记",
        "什么是",
        "解释",
        "定义",
        "原理",
    )

    def __init__(self, main_window):
        """
        初始化提示词上下文构建器。

        Args:
            main_window: 主窗口实例，提供对 app_context、settings、chat_history 等的访问
        """
        self._mw = main_window
        self.ASSISTANT_NAME = getattr(main_window, "ASSISTANT_NAME", "小问") or "小问"
        self._HISTORY_TOKEN_BUDGET = getattr(main_window, "_HISTORY_TOKEN_BUDGET", 2400) or 2400
        self._HISTORY_MAX_ENTRIES = getattr(main_window, "_HISTORY_MAX_ENTRIES", 20) or 20
        # 从系统环境获取用户名，可在设置中覆盖
        self.USER_NAME = ""
        try:
            import os

            self.USER_NAME = os.environ.get("SIYIJI_USER_NAME", "")
        except Exception:
            logger.warning("读取环境变量 SIYIJI_USER_NAME 失败")

    # ------------------------------------------------------------------
    # 能力列表构建
    # ------------------------------------------------------------------

    def list_assistant_capabilities(self):
        """列出当前助手的所有能力标签。"""
        labels = self._build_priority_capability_labels()
        registry = getattr(self._mw, "_assistant_tool_registry", None)
        preview_labels = registry.preview_labels(limit=6) if registry is not None else []
        for label in preview_labels or []:
            cleaned = str(label or "").strip()
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
        if labels:
            return labels
        return ["聊天答疑与上下文解释", "任务规划与授权后执行", "生成执行报告与最近工作建议"]

    def _build_priority_capability_labels(self):
        return ["邮件新闻推送", "技术周报", "联网搜索", "浏览器抓取", "自动规则", "收件箱沉淀"]

    def build_capability_summary_for_prompt(self):
        """构建用于系统提示词的简洁能力列表。"""
        lines = [
            "- 可按主题生成技术周报并邮件发送（优先复用当前已配置邮箱与关注主题）",
            "- 可生成每日晨报（例如每天早上推送科技新闻）",
            "- 可用联网搜索补全最新信息，再结合本地记忆生成结果",
            "- 已配置邮箱时可直接发送，未配置时只询问缺的配置",
            "- 打开浏览器/搜索网页（说「帮我搜索 xxx」或「打开浏览器访问 xxx」）",
            "- 启动本地应用程序（说「启动微信」「打开QQ」等）",
            "- 执行系统命令（说「执行命令 xxx」或「帮我查看 xxx」）",
            "- 管理任务/待办（说「打开任务」「整理待办」）",
            "- 管理笔记（说「打开笔记」「同步笔记」「整理笔记」）",
            "- 查看/整理新闻（说「打开新闻」「搜索新闻 xxx」）",
            "- 读写文件（说「读取文件 xxx」「写入文件 xxx」）",
            "- 读写剪贴板（说「读取剪贴板」「复制到剪贴板 xxx」）",
            "- 生成日志/复盘报告（说「生成日志」「每日复盘」）",
            "- 打开翻译、提醒、设置等窗口（说「打开翻译」「打开设置」）",
            "- 分析网页链接（说「分析这个链接 URL」）",
            "- 快速收件（说「收件：内容」）",
            "- 下载文件（说「下载 URL 到本地」）",
            "- 搜索互联网获取实时信息（说「搜一下 xxx」或「帮我查查最新的 xxx」）",
            "- 抓取网页内容（说「帮我读取这个网页 URL」）",
            "- 查看/管理进程（说「列出进程」「结束 xxx 进程」）",
            "- 查看系统信息（说「查看系统信息」「查看CPU/内存/磁盘使用情况」）",
        ]
        return "\n".join(lines)

    def build_capability_reply(self, matched_tools=None):
        """构建能力集介绍回复文本。"""
        tools = list(matched_tools or [])
        lines = [f"我是{self.ASSISTANT_NAME}，当前正式能力集如下："]
        priority_capabilities = [
            ("邮件新闻推送", "可按主题生成邮件快报，已配置邮箱时会直接发送，缺配置时只提示缺口。"),
            ("技术周报", "可汇总关注主题，生成技术周报并邮件发送。"),
            ("联网搜索", "可补全最新信息，不只停留在本地知识。"),
            ("浏览器抓取", "可读取网页、提炼重点并沉淀到本地。"),
            ("自动规则", "可通过每日晨报等规则做持续化推送。"),
            ("收件箱沉淀", "可先收件，再整理成笔记、摘要和后续动作。"),
        ]
        for index, (label, description) in enumerate(priority_capabilities, start=1):
            lines.append(f"{index}. {label}：{description}（可直接回答）")
        if not tools:
            return "\n".join(lines)
        next_index = len(priority_capabilities) + 1
        for tool in tools[:10]:
            confirmation = "需确认" if tool.requires_confirmation or tool.risk_level in {"medium", "high"} else "可直接回答"
            if any(tool.label == item[0] for item in priority_capabilities):
                continue
            lines.append(f"{next_index}. {tool.label}：{tool.description}（{confirmation}）")
            next_index += 1
        if len(tools) > 10:
            lines.append(f"另外还有 {len(tools) - 10} 项能力未展开，你可以继续按关键词追问。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 提示词构建
    # ------------------------------------------------------------------

    def build_assistant_prompt_messages(self, message, snapshot, history_entries, *, at_references=None):
        """构建发送给 LLM 的完整消息列表（含系统提示词、工作上下文、用户偏好、聊天历史）。

        Args:
            message: 当前用户消息
            snapshot: 运行时快照
            history_entries: 聊天历史
            at_references: 可选的 @ 引用列表，每个元素需要实现 resolved_content 属性
        """
        layers = self._build_assistant_memory_layers(snapshot, history_entries)
        history = layers.get("chat_history") or []
        work_context = "\n".join(f"- {line}" for line in (layers.get("work_context") or [])) or "- 当前没有可用工作上下文"
        user_preferences = "\n".join(f"- {line}" for line in (layers.get("user_preferences_summary") or [])) or "- 当前没有显式偏好"
        recent_execution = "\n".join(f"- {line}" for line in (layers.get("recent_execution_results") or [])) or "- 最近没有执行记录"
        capability_summary = self.build_capability_summary_for_prompt()

        messages = [
            {
                "role": "system",
                "content": (
                    "[身份]\n"
                    f"你是{self.ASSISTANT_NAME}，运行在用户本地 PC 上的个人 AI 助手。"
                    f"你不仅是工具，更是用户的伙伴——主动观察、主动建议、主动执行。\n\n"
                    "[行为准则]\n"
                    "1. 先直接回答用户的问题，再视情况结合工作上下文补充。\n"
                    "2. 你可以回答通用知识问题（推荐、解释、比较、翻译、常识等），不要拒绝。\n"
                    "3. 如果问题涉及用户个人数据（待办/笔记/收件），优先引用工作上下文。\n"
                    "4. 不要虚构工具执行结果或声称已执行了未执行的操作。\n"
                    "5. 如果信息不足，说明缺什么，但仍尽量给出最有用的答案。\n"
                    "6. 输出自然中文，不要输出 JSON 或代码块（除非用户明确要求）。\n"
                    "7. 回答应简洁有条理，适合桌面对话窗口阅读。\n"
                    "8. 当用户的请求可以通过你的能力直接执行时，主动提出帮他操作，"
                    "而不是给出手动步骤教程。告诉用户可以怎样说来触发执行。\n"
                    "9. 永远不要说「我无法操作你的浏览器」或「我不能执行」——"
                    "你可以打开浏览器、启动应用、执行命令等，只需用户确认即可。\n"
                    "10. 你有个性、有态度，但始终保持尊重和坦诚。"
                    "用朋友的方式沟通，而不是冷冰冰的机器。\n\n"
                    "[你的可执行能力]\n"
                    f"{capability_summary}\n"
                    "用户说出包含上述关键词的指令后，系统会自动规划步骤并请求确认。\n"
                    "你应当告知用户他可以直接对你说，例如：\n"
                    "- 「帮我打开浏览器搜索 xxx」\n"
                    "- 「启动微信」\n"
                    "- 「帮我同步笔记」\n"
                    "- 「检查你的能力」\n"
                    "- 「扫描当前环境」\n\n"
                    "[运行时信息]\n"
                    f"当前桌面概况：待办 {len(snapshot.get('pending_tasks', []))} 个，"
                    f"笔记 {len(snapshot.get('notes', []))} 条，"
                    f"新闻收藏 {len(snapshot.get('favorite_news_keys', []))} 条，"
                    f"收件 {len(snapshot.get('inbox_entries', []))} 条。"
                ),
            },
            {
                "role": "system",
                "content": (f"[当前工作上下文]\n{work_context}\n\n[用户偏好摘要]\n{user_preferences}\n\n[最近执行结果]\n{recent_execution}"),
            },
        ]

        # 注入长期记忆上下文
        long_term_memory = layers.get("long_term_memory") or ""
        if long_term_memory:
            messages.append(
                {
                    "role": "system",
                    "content": f"[长期记忆与用户画像]\n{long_term_memory}",
                }
            )

        # 注入情绪上下文
        emotion_context = self._build_emotion_context()
        if emotion_context:
            messages.append(
                {
                    "role": "system",
                    "content": f"[角色当前情绪]\n{emotion_context}",
                }
            )

        # @ 引用上下文注入
        at_context = self._build_at_reference_context(at_references)
        if at_context:
            messages.append(
                {
                    "role": "system",
                    "content": f"[用户 @ 引用上下文]\n{at_context}",
                }
            )
            layers["at_reference_context"] = at_context

        # 知识库上下文层
        knowledge_context = self._build_knowledge_base_context(
            str(message or ""),
            snapshot,
        )
        if knowledge_context:
            messages.append(
                {
                    "role": "system",
                    "content": f"[知识库信息]\n{knowledge_context}",
                }
            )
            layers["knowledge_context"] = knowledge_context

        for entry in history:
            role = "assistant" if entry.get("speaker") == "助手" else "user"
            content = str(entry.get("message") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": str(message or "")})
        return messages, layers

    def build_chat_report(self, runtime_snapshot, layers, *, extra_lines=None):
        """构建聊天模式的执行报告。"""
        stage_label = self._format_chain_stage_label((runtime_snapshot or {}).get("last_stage"))
        capability_preview = "、".join(self.list_assistant_capabilities()[:3])
        lines = [
            "本轮未执行任何动作",
            f"回答链路：{stage_label}",
            f"当前可用能力：{capability_preview}",
        ]
        recent_execution = layers.get("recent_execution_results") or []
        if recent_execution:
            lines.append(f"最近执行：{recent_execution[0]}")
        if extra_lines:
            lines.extend(str(item) for item in extra_lines if str(item).strip())
        return {"title": "本轮报告", "lines": lines}

    # ------------------------------------------------------------------
    # 记忆层构建
    # ------------------------------------------------------------------

    def _build_assistant_memory_layers(self, snapshot, history_entries):
        """构建多层记忆上下文——使用 AssistantMemoryIndexService 替代 MemoryManager。"""
        memory_context = ""
        try:
            if hasattr(self._mw, "app_context") and hasattr(self._mw.app_context, "memory_index_service"):
                memory_service = self._mw.app_context.memory_index_service
                if memory_service:
                    memory_context = memory_service.build_recall_context()
        except Exception as exc:
            logger.info("记忆服务不可用: %s", exc, exc_info=True)
        return {
            "chat_history": self._trim_chat_history_by_budget(history_entries),
            "work_context": self._build_work_context_summary(snapshot),
            "user_preferences_summary": self._build_user_preference_summary(),
            "recent_execution_results": self._build_recent_execution_digest(history_entries),
            "long_term_memory": memory_context,
        }

    def _build_knowledge_base_context(
        self,
        message: str,
        snapshot: Any,
    ) -> str:
        """
        根据当前用户消息判断是否需要查询知识库，并返回格式化的上下文文本。

        仅在明确的知识类问题下触发检索，避免每次对话都查询知识库。

        Args:
            message: 用户当前消息
            snapshot: 运行时快照

        Returns:
            知识库检索上下文文本（空字符串 = 不触发）
        """
        normalized = str(message or "").strip()
        if not normalized or len(normalized) < 6:
            return ""

        # 判断是否需要知识库检索：知识类问题
        knowledge_cues = (
            "推荐",
            "介绍",
            "解释",
            "什么是",
            "有哪些",
            "区别",
            "比较",
            "怎么",
            "如何",
            "为什么",
            "是什么",
            "哪个好",
            "哪些",
            "优缺点",
            "方法",
            "原理",
            "教程",
            "步骤",
            "定义",
            "意思",
            "含义",
            "建议",
            "选择",
            "评价",
        )
        # 本地数据查询不触发知识库
        local_cues = ("我的任务", "我的笔记", "我的待办", "我的收件")
        if any(cue in normalized for cue in local_cues):
            return ""
        should_query = any(cue in normalized for cue in knowledge_cues)
        # 短句且有问号也触发
        if not should_query and len(normalized) > 8:
            should_query = "?" in normalized or "？" in normalized

        if not should_query:
            return ""

        # 通过桥接层查询知识库（非阻塞）
        try:
            from knowledge.retrieval.bridge import KnowledgeBaseBridge

            # 优先使用 service_registry 中的共享实例
            bridge = None
            try:
                from app_runtime.service_registry import service_registry as _sr

                bridge = _sr.resolve(KnowledgeBaseBridge)
            except Exception:
                logger.debug("Non-critical error (see details)", exc_info=True)

            if bridge is None:
                bridge = KnowledgeBaseBridge(offline_embedding=False)
                if not bridge.ensure_initialized():
                    logger.debug("Knowledge base unavailable, skipping context query")
                    return ""

            import asyncio

            loop = asyncio.new_event_loop()
            try:
                context = loop.run_until_complete(bridge.query_for_context(normalized, n_results=3))
            finally:
                loop.close()

            if context:
                logger.debug(
                    "Knowledge base context added for message: %.60s",
                    normalized,
                )
                return context
        except Exception as exc:
            logger.info("Knowledge base query failed (non-blocking): %s", exc, exc_info=True)

        return ""

    @staticmethod
    def _build_at_reference_context(at_references):
        """将 @ 引用列表格式化为 LLM 上下文文本。

        Args:
            at_references: AtReference 对象列表，或 None

        Returns:
            格式化的引用上下文字符串，无可用时返回空字符串
        """
        if not at_references:
            return ""
        parts = ["用户通过 @ 引用了以下内容:"]
        for ref in at_references:
            content = getattr(ref, "resolved_content", None) or ""
            if not content:
                continue
            # 计算 token 预算（粗略: ~4000 char 总预算）
            parts.append("---")
            parts.append(content)
        combined = "\n".join(parts)
        # 总长度限制
        if len(combined) > 4000:
            combined = combined[:4000] + "\n... (引用内容已截断)"
        return combined

    def _build_work_context_summary(self, snapshot):
        """构建当前工作上下文摘要。"""
        recent_tasks = [
            str(item.get("name") or "") for item in self._sort_recent(snapshot.get("pending_tasks") or [])[:2] if str(item.get("name") or "").strip()
        ]
        recent_notes = [
            str(item.get("title") or "") for item in self._sort_recent(snapshot.get("notes") or [])[:2] if str(item.get("title") or "").strip()
        ]
        lines = [
            f"待办 {len(snapshot.get('pending_tasks', []))} 个，笔记 {len(snapshot.get('notes', []))} 条，新闻收藏 {len(snapshot.get('favorite_news_keys', []))} 条。",
            f"翻译 {len(snapshot.get('translations', []))} 条，收件 {len(snapshot.get('inbox_entries', []))} 条，提醒 {len(snapshot.get('alarms', []))} 条。",
        ]
        if recent_tasks:
            lines.append("最近待办：" + "、".join(recent_tasks))
        if recent_notes:
            lines.append("最近笔记：" + "、".join(recent_notes))
        return lines

    def _build_user_preference_summary(self):
        """构建用户偏好摘要 — 优先从 UserProfileService 获取实时画像。"""
        try:
            user_profile_service = getattr(getattr(self._mw, "app_context", None), "user_profile_service", None)
            if user_profile_service is not None:
                summary = user_profile_service.build_planner_summary()
                if summary and summary.strip():
                    return [line.strip() for line in summary.split("\n") if line.strip()]
        except Exception:
            logger.debug("UserProfileService 不可用，回退到硬编码偏好摘要")

        # Fallback: 原有硬编码实现
        mw = self._mw
        require_confirm = True
        if hasattr(mw, "_get_settings_repository") and mw._get_settings_repository() is not None:
            require_confirm = bool(mw._get_settings_repository().get_setting("assistant_high_risk_require_confirm", True))
        stream_enabled = getattr(mw, "_cached_stream_enabled", False)
        return [
            "打开模块和执行动作需要明确授权",
            "所有规划步骤会先展示报告再等待确认",
            f"高风险动作确认：{'开启' if require_confirm else '关闭'}",
            f"流式输出：{'开启' if stream_enabled else '关闭'}",
            "默认返回答案、状态、报告和下一步建议",
        ]

    def _build_emotion_context(self) -> str:
        """读取当前情绪状态，返回格式化的情感上下文文本。"""
        try:
            app_context = getattr(self._mw, "app_context", None)
            if app_context is None:
                return ""
            emotion = getattr(app_context, "current_emotion", None)
            if emotion is None:
                return ""
            parts = [
                f"情绪：{emotion.emotion.value}",
                f"强度：{emotion.intensity:.1f}",
            ]
            if emotion.message:
                parts.append(f"描述：{emotion.message}")
            return "\n".join(parts)
        except Exception:
            logger.debug("读取情绪上下文失败")
            return ""

    def _build_recent_execution_digest(self, history_entries, limit=3):
        """构建最近执行摘要。"""
        digests = []
        for entry in reversed(history_entries or []):
            response = entry.get("response") if isinstance(entry, dict) else None
            if not isinstance(response, dict):
                continue
            status = response.get("status") or {}
            report = response.get("report") or {}
            report_lines = report.get("lines") or []
            if not report_lines:
                continue
            digests.append(f"{status.get('label') or status.get('state') or '已处理'}：{report_lines[0]}")
            if len(digests) >= limit:
                break
        return digests

    # ------------------------------------------------------------------
    # Token 预算管理
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_token_count(text):
        """粗略的 token 估算：CJK 字符按 2 token，ASCII 按 0.35。"""
        if not text:
            return 0
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f")
        ascii_chars = len(text) - cjk
        return int(cjk * 2 + ascii_chars * 0.35)

    def _trim_chat_history_by_budget(self, history_entries):
        """按 token 预算裁剪聊天历史，保留 user 消息边界。"""
        if history_entries is None:
            history_entries = getattr(self._mw, "chat_history_entries", None)
        entries = list(history_entries or [])
        if not entries:
            return []

        collected: list[dict[str, Any]] = []
        budget_remaining = self._HISTORY_TOKEN_BUDGET
        for entry in reversed(entries[-self._HISTORY_MAX_ENTRIES :]):
            content = str(entry.get("message") or "").strip()
            if not content:
                continue
            token_cost = self._estimate_token_count(content)
            if budget_remaining - token_cost < 0 and collected:
                break
            budget_remaining -= token_cost
            collected.append(dict(entry))

        collected.reverse()

        # 对齐到 user 边界：确保第一条是 user 消息
        while collected and collected[0].get("speaker") == "助手":
            collected.pop(0)

        return collected

    # ------------------------------------------------------------------
    # 降级回答
    # ------------------------------------------------------------------

    def _classify_degraded_query(self, message):
        """将降级模式下的查询分类为 knowledge、local_data 或 unknown。"""
        normalized = str(message or "").strip().lower()
        if any(cue in normalized for cue in self._LOCAL_DATA_CUES):
            return "local_data"
        if any(cue in normalized for cue in self._KNOWLEDGE_CUES):
            return "knowledge"
        if len(normalized) < 8:
            return "unknown"
        if "?" in message or "？" in message:
            return "knowledge"
        return "unknown"

    def build_smart_degraded_answer(self, message, snapshot, error):
        """根据查询类型生成针对性的降级回答。"""
        query_type = self._classify_degraded_query(message)

        if query_type == "local_data":
            tasks = snapshot.get("pending_tasks") or []
            notes = snapshot.get("notes") or []
            inbox = snapshot.get("inbox_entries") or []
            lines = [f"当前待办 {len(tasks)} 个，笔记 {len(notes)} 条，收件 {len(inbox)} 条。"]
            recent_tasks = [str(t.get("name") or "") for t in tasks[:3] if str(t.get("name") or "").strip()]
            if recent_tasks:
                lines.append("最近待办：" + "、".join(recent_tasks))
            recent_notes = [str(n.get("title") or "") for n in notes[:3] if str(n.get("title") or "").strip()]
            if recent_notes:
                lines.append("最近笔记：" + "、".join(recent_notes))
            return "\n".join(lines)

        if query_type == "knowledge":
            error_hint = self._format_degraded_error_hint(error)
            return (
                f"抱歉，{error_hint}，暂时无法回答这个问题。\n\n"
                "你可以：\n"
                "1. 稍后重试（点击「重试」按钮）\n"
                "2. 把问题先收件，等恢复后我再处理：输入 收件：加上问题内容\n"
                "3. 检查 LLM 服务配置：输入 服务状态"
            )

        # unknown / 通用 fallback
        error_hint = self._format_degraded_error_hint(error)
        tasks_count = len(snapshot.get("pending_tasks") or [])
        notes_count = len(snapshot.get("notes") or [])
        inbox_count = len(snapshot.get("inbox_entries") or [])
        return (
            f"当前 AI 服务{error_hint}，我暂时只能处理本地操作。\n"
            f"工作台概况：待办 {tasks_count} 个，笔记 {notes_count} 条，收件 {inbox_count} 条。\n\n"
            "你可以试试：\n"
            "- 打开任务 / 打开笔记 / 打开翻译（直接执行本地操作）\n"
            "- 收件：<内容>（先存到收件箱，稍后处理）\n"
            "- 输入「能做什么」查看所有可用功能"
        )

    def build_local_degraded_answer(self, message, error, snapshot, runtime_snapshot=None):
        """构建完整的本地降级回答（含能力回复、默认回复等分支）。"""
        normalized = str(message or "").strip().lower()
        mw = self._mw

        if "权限" in normalized or "授权" in normalized or "能做什么" in normalized or normalized == "help":
            answer = self.build_capability_reply()
        elif "服务状态" in message or "后端状态" in message or "运行状态" in message:
            answer = mw._build_runtime_status_reply() if hasattr(mw, "_build_runtime_status_reply") else "当前无法读取运行状态。"
        elif len(normalized) < 15 and ("先做什么" in normalized or "重点" in normalized):
            if hasattr(mw, "_build_default_reply"):
                answer = mw._build_default_reply(snapshot)
            else:
                answer = "你可以从一条笔记开始，也可以先去新闻或翻译模块收集素材。"
        else:
            answer = self.build_smart_degraded_answer(message, snapshot, error)

        report = self.build_chat_report(
            runtime_snapshot or {},
            self._build_assistant_memory_layers(snapshot, []),
            extra_lines=[f"远程 AI 不可用：{error}", "已改用本地降级回答"],
        )
        # 使用 mw 的 _make_assistant_response 或构建兼容字典
        return mw._make_assistant_response(
            answer,
            mode="降级回答",
            state="degraded_answered",
            report=report,
            next_actions=["查看助手自检面板中的模型与最近错误", "补充更具体的问题让我继续回答", "如果要执行功能，请明确说出动作名称"],
            degraded=True,
            can_retry=bool(getattr(error, "transient", False)) if isinstance(error, Exception) else False,
            query=message,
        )

    # ------------------------------------------------------------------
    # 网络搜索增强
    # ------------------------------------------------------------------

    def should_augment_with_web_search(self, message: str) -> bool:
        """判断是否需要联网搜索来增强回答。"""
        normalized = str(message or "").strip().lower()
        if len(normalized) < 4:
            return False
        if any(cue in normalized for cue in self._NO_SEARCH_CUES):
            return False
        if any(cue in normalized for cue in self._WEB_SEARCH_CUES):
            return True
        if ("？" in message or "?" in message) and len(normalized) > 10:
            return any(cue in normalized for cue in ("哪个", "哪些", "有什么", "怎么样"))
        return False

    def maybe_augment_with_web_search(self, message: str, client=None) -> str:
        """按需执行联网搜索，返回格式化后的搜索上下文文本。"""
        try:
            memory_context = self._build_memory_recall_context(message)
            if not self.should_augment_with_web_search(message):
                return memory_context
            from services.web_search import WebSearchService  # P1-B: 原 `web_search_service` 顶层模块不存在

            mw = self._mw
            settings_repo = mw._get_settings_repository() if hasattr(mw, "_get_settings_repository") else None
            svc = WebSearchService(settings_repository=settings_repo)
            queries = self._build_multi_step_search_queries(message, settings_repo)
            combined_lines = [memory_context] if memory_context else []
            unique_urls = set()
            rounds = 0
            for query in queries:
                if rounds >= 3:
                    break
                results = svc.search(query, num_results=4)
                deduped = []
                for item in results:
                    normalized_url = str(item.url or "").strip().lower()
                    if normalized_url in unique_urls:
                        continue
                    unique_urls.add(normalized_url)
                    deduped.append(item)
                if not deduped:
                    continue
                rounds += 1
                combined_lines.append(f"第{rounds}轮联网检索：{query}")
                combined_lines.append(svc.format_results_for_prompt(deduped, query))
            return "\n\n".join(line for line in combined_lines if line).strip()
        except Exception as exc:
            logger.warning("联网搜索增强失败: %s", exc, exc_info=True)
            return ""

    def build_memory_recall_context(self, message: str) -> str:
        """构建本地记忆检索上下文（含知识图谱实体增强）。"""
        app_context = getattr(self._mw, "app_context", None)
        memory_service = getattr(app_context, "memory_index_service", None)
        if memory_service is None:
            return ""
        parts = []
        try:
            result = memory_service.recall(message, top_k=4)
        except Exception as exc:
            logger.warning("本地记忆检索失败: %s", exc, exc_info=True)
            result = {}
        items = list(result.get("items") or [])[:4]
        if items:
            lines = ["以下是本地可检索记忆：", ""]
            for item in items:
                lines.append(f"- [{item.get('source_type')}] {item.get('title')}：{item.get('snippet') or ''}".strip())
            parts.append("\n".join(lines))

        # 知识图谱实体上下文增强
        try:
            kg_extractor = getattr(app_context, "knowledge_graph_extractor", None)
            if kg_extractor is not None:
                entity_ctx = kg_extractor.query_for_prompt_context(message)
                if entity_ctx:
                    parts.append(entity_ctx)
        except Exception as exc:
            logger.info("知识图谱上下文注入失败（非阻塞）: %s", exc, exc_info=True)

        return "\n\n".join(parts)

    def _build_memory_recall_context(self, message: str) -> str:
        """内部：构建本地记忆检索上下文（复用公开方法）。"""
        return self.build_memory_recall_context(message)

    def _build_multi_step_search_queries(self, message: str, settings_repo) -> list:
        """构建多步搜索查询列表。"""
        queries = [str(message or "").strip()]
        normalized = str(message or "").strip().lower()
        focus_topics = []
        if settings_repo is not None:
            topics = settings_repo.get_setting("assistant_focus_topics", [])
            if isinstance(topics, list):
                focus_topics = [str(item).strip() for item in topics if str(item).strip()]
        if any(token in normalized for token in ("最新", "进展", "动态", "趋势", "发布")):
            queries.append(f"{message} 最新进展")
            queries.append(f"{message} 发布 更新 教程")
        matched_topics = [topic for topic in focus_topics if topic.lower() in normalized]
        if matched_topics:
            for topic in matched_topics[:2]:
                queries.append(f"{topic} {message}")
        elif focus_topics:
            queries.append(f"{focus_topics[0]} {message}")
        deduped = []
        seen = set()
        for item in queries:
            query = str(item or "").strip()
            if not query:
                continue
            key = query.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(query)
        return deduped

    # ------------------------------------------------------------------
    # 默认行为构建
    # ------------------------------------------------------------------

    def build_default_next_actions(self, query=""):
        """根据当前查询构建默认的下一步建议列表。"""
        actions = []
        normalized = str(query or "").strip().lower()
        if "权限" in normalized or "能做什么" in normalized:
            actions.append("继续追问某个能力的边界或授权方式")
        if any(kw in normalized for kw in ("搜索", "搜一下", "查找", "github", "百度", "google")):
            actions.append("直接说「帮我搜索 xxx」，我会打开浏览器帮你完成")
        elif any(kw in normalized for kw in ("打开", "启动", "运行")):
            actions.append("直接说「启动 xxx」或「打开 xxx」，我会帮你执行")
        else:
            actions.append("你可以让我执行操作，例如「帮我搜索 xxx」或「启动微信」")
        actions.append("说「帮我整理笔记」或「生成日志」来管理工作台")
        actions.append("说「能做什么」查看我的全部能力")
        return actions[:3]

    # ------------------------------------------------------------------
    # 辅助格式化方法
    # ------------------------------------------------------------------

    @staticmethod
    def format_chain_stage_label(stage_name):
        """格式化链路阶段标签。"""
        mapping = {
            "primary": "主模型",
            "fallback": "备用模型",
            "local": "本地模型",
            "cache": "本地缓存",
            "failed": "降级失败",
            "idle": "空闲",
        }
        return mapping.get(str(stage_name or "idle"), str(stage_name or "idle"))

    @staticmethod
    def _format_chain_stage_label(stage_name):
        """内部格式化链路阶段标签，委托给静态方法。"""
        return PromptContext.format_chain_stage_label(stage_name)

    @staticmethod
    def format_degraded_error_hint(error):
        """将底层错误转换为用户友好的简短消息。"""
        msg = str(error or "").lower()
        if "timeout" in msg or "超时" in msg:
            return "连接超时"
        if "401" in msg or "403" in msg or "auth" in msg or "密钥" in msg:
            return "API 密钥无效或已过期"
        if "429" in msg or "rate" in msg or "频率" in msg:
            return "请求频率超限"
        if "connection" in msg or "连接" in msg or "network" in msg:
            return "网络连接失败"
        if "fallback_exhausted" in msg:
            return "所有 AI 服务均不可用"
        if "未配置" in msg or "unconfigured" in msg:
            return "尚未配置 AI 服务"
        return "暂时不可用"

    @staticmethod
    def _format_degraded_error_hint(error):
        """内部格式化降级错误提示，委托给静态方法。"""
        return PromptContext.format_degraded_error_hint(error)

    @staticmethod
    def normalize_focus_topics(raw_value):
        """标准化关注主题列表。"""
        if isinstance(raw_value, list):
            candidates = [str(item).strip() for item in raw_value]
        else:
            candidates = [line.strip() for line in str(raw_value or "").splitlines()]
        topics = []
        seen = set()
        for item in candidates:
            if not item:
                continue
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            topics.append(item)
        return topics[:12]

    @staticmethod
    def _derive_provider_label(base_url):
        """根据 base_url 推导提供商标签。"""
        if not base_url:
            return "未配置"
        host = (urlparse(str(base_url)).netloc or str(base_url)).lower()
        if "openai" in host:
            return "OpenAI 兼容"
        if "openrouter" in host:
            return "OpenRouter"
        if "anthropic" in host:
            return "Anthropic 兼容"
        if "localhost" in host or "127.0.0.1" in host:
            return "本地模型"
        return host

    @staticmethod
    def _sort_recent(items):
        """按时间排序项目列表（最新的在前）。"""
        return sorted(
            items or [],
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
