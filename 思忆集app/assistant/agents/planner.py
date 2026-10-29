from __future__ import annotations

import json
import logging
import re
from typing import Any

from assistant.models import PlannedStep
from core.settings_schema import KEY_ASSISTANT_FOCUS_TOPICS, KEY_ASSISTANT_NEWS_QUERY
from services.react_tool_catalog import REACT_TOOL_CATALOG, to_step_risk

logger = logging.getLogger("siyi.assistant.planner")


class AssistantPlannerLayer:
    """计划层：把自然语言目标拆成可执行步骤（LLM 驱动 + 关键词兜底）。

    集成：
    - UserProfileService：自动注入画像上下文到 LLM prompt
    - IntentRouter：plan_goal 接受预分类结果，跳过纯 chat
    """

    # ── 能力目录（兼容视图）─────────────────────────────────────────────
    # 审计波次② C-1 步骤 4：这里原本硬编码 81 条 planner 私有声明，与
    # services/react_tool_catalog.py 的 REACT_TOOL_CATALOG（49 条）互为孤岛——
    # 同一个动作两份描述、两套 risk 词表，capability_router 只能「先注册
    # planner 目录、再对 REACT 目录 backfill」打补丁维持两边。现在两份声明
    # 已合并为 REACT_TOOL_CATALOG 一份（唯一声明源），planner 视图自此派生；
    # 新增动作只改那一个文件。
    # 字段名与原目录逐一对应（action_id / description / risk / payload_schema /
    # confirmation / rollback / audit / privacy / idempotency / timeout_default），
    # 老消费者不改也能读到。
    # 注意：risk 词表已统一到 registry 现行 read/write/modify/destructive。
    # 需要旧 low/medium/high 词表的消费点（PlannedStep.risk_level、LLM prompt）
    # 一律经 services/react_tool_catalog.to_step_risk() 回落，否则会静默改掉
    # 降级/强确认/告警判定。
    CAPABILITY_CATALOG: list[dict[str, Any]] = [dict(entry) for entry in REACT_TOOL_CATALOG]

    _SYSTEM_PROMPT = (
        "你是思忆集助手的任务规划器。用户会给你一个自然语言目标，你需要将它拆分为一系列可执行步骤。\n"
        "每个步骤必须使用下列可用能力之一。\n\n"
        "## 可用能力\n{capabilities}\n\n"
        "## 输出格式（严格 JSON）\n"
        '返回一个 JSON 对象，包含 "steps" 数组。每个元素包含：\n'
        '- "step_id": 字符串，唯一标识（如 "step_1"）\n'
        '- "action_id": 必须是上述能力之一的 action_id\n'
        '- "payload": 对象，根据该能力的 payload_schema 填写\n'
        '- "risk_level": "low" / "medium" / "high"\n\n'
        "如果用户请求不属于任何能力（纯聊天/闲聊），返回：\n"
        '{{"steps": [], "is_chat": true, "reply": "你的自然语言回复"}}\n\n'
        "仅输出 JSON，不要输出其他内容。"
    )

    def __init__(self, settings_repository=None, ai_client=None, user_profile_service=None, extension_registry=None):
        self.settings_repository = settings_repository
        self._ai_client = ai_client
        self._user_profile_service = user_profile_service
        self._capability_registry = None
        self._extension_registry = extension_registry

    def set_ai_client(self, client) -> None:
        self._ai_client = client

    def set_capability_registry(self, registry: Any) -> None:
        """注入 CapabilityRegistry 实例，planner 从此读取能力目录。"""
        self._capability_registry = registry

    def _get_catalog(self) -> list[dict[str, Any]]:
        """返回当前有效的能力目录：优先从 registry 读取，同时过滤已禁用的联网扩展来源。"""
        if self._capability_registry is not None:
            catalog = self._capability_registry.build_capability_catalog()
            return self._filter_disabled_extensions(catalog)
        return list(self.CAPABILITY_CATALOG)

    def _filter_disabled_extensions(self, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """过滤掉已禁用联网扩展提供的能力。"""
        if self._extension_registry is None:
            return catalog
        disabled_sources = self._extension_registry.get_disabled_source_ids()
        if not disabled_sources:
            return catalog
        return [cap for cap in catalog if cap.get("source", "builtin") not in disabled_sources]

    def build_openai_tools_schema(self) -> list[dict[str, Any]]:
        """从当前有效能力目录构建 OpenAI tool calling 格式。"""
        catalog = self._get_catalog()
        tools: list[dict[str, Any]] = []
        for cap in catalog:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param_name, param_desc in (cap.get("payload_schema") or {}).items():
                prop: dict[str, Any] = {"type": "string", "description": str(param_desc)}
                if "数量" in str(param_desc) or param_name in ("limit", "num_results", "top_n", "max_items", "timeout"):
                    prop["type"] = "integer"
                properties[param_name] = prop
                if not required:
                    required.append(param_name)
            tools.append({
                "type": "function",
                "function": {
                    "name": cap["action_id"].replace(".", "_"),
                    "description": cap["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools

    def _tool_name_to_action_id(self, tool_name: str) -> str:
        catalog = self._get_catalog()
        for cap in catalog:
            if cap["action_id"].replace(".", "_") == tool_name:
                return cap["action_id"]
        if "_" in tool_name:
            parts = tool_name.split("_", 1)
            candidate = f"{parts[0]}.{parts[1]}"
            if any(cap["action_id"] == candidate for cap in catalog):
                return candidate
        return tool_name

    def get_tools_for_llm(self) -> list[dict[str, Any]]:
        """返回 LLM 可消费的 tools 列表（OpenAI tool calling 格式）。

        优先从 CapabilityRegistry 读取；无 registry 时从 CAPABILITY_CATALOG 构建。
        """
        if self._capability_registry is not None:
            return self._capability_registry.build_openai_tools()
        return self.build_openai_tools_schema()

    def _action_id_from_tool_name(self, tool_name: str) -> str:
        """从 tool name（下划线格式）解析 action_id（点号格式）。

        优先匹配 CapabilityRegistry，回退到 CAPABILITY_CATALOG。
        """
        if self._capability_registry is not None:
            valid_ids = self._capability_registry.get_valid_action_ids()
            candidate = tool_name.replace("_", ".", 1)
            if candidate in valid_ids:
                return candidate
            for vid in valid_ids:
                if vid.replace(".", "_") == tool_name:
                    return vid
        return self._tool_name_to_action_id(tool_name)

    def plan_goal(self, goal: str, user_profile: dict[str, Any] | None = None) -> list[PlannedStep]:
        text = str(goal or "").strip()
        if not text:
            return []
        # 优先使用 UserProfileService 提供的画像，兼容旧参数
        resolved_profile = user_profile
        if resolved_profile is None and self._user_profile_service is not None:
            try:
                snapshot = self._user_profile_service.build_routing_context()
                if isinstance(snapshot, dict):
                    resolved_profile = snapshot
            except Exception as exc:
                logger.info("读取 UserProfileService 画像失败，回退空画像: %s", exc, exc_info=True)
        if self._ai_client is not None:
            try:
                tc_steps = self._plan_via_tool_calling(text, user_profile=resolved_profile)
                if tc_steps is not None:
                    return tc_steps
            except Exception as exc:
                logger.info("Tool Calling 规划失败，降级到 LLM JSON: %s", exc, exc_info=True)
        if self._ai_client is not None:
            try:
                llm_steps = self._plan_via_llm(text, user_profile=resolved_profile)
                if llm_steps is not None:
                    return llm_steps
            except Exception as exc:
                logger.warning("LLM 规划失败，降级到关键词匹配: %s", exc)
        return self._plan_via_keywords(text, user_profile=resolved_profile)

    def _plan_via_tool_calling(self, goal: str, user_profile: dict[str, Any] | None = None) -> list[PlannedStep] | None:
        tools = self.get_tools_for_llm()
        profile_summary = self._build_user_profile_summary(user_profile)
        system_prompt = (
            "你是思忆集助手的任务规划器。用户会给你一个自然语言目标。\n"
            "如果目标可以通过可用工具执行，请调用合适的工具。\n"
            "如果是纯聊天/闲聊，请直接回复文本不要调用工具。\n"
            "可以同时调用多个工具来完成复杂任务。"
        )
        if profile_summary:
            system_prompt += f"\n\n## 用户画像摘要\n{profile_summary}"
        try:
            client = self._ai_client
            if hasattr(client, "primary"):
                client = client.primary
            if not hasattr(client, "session") or not hasattr(client, "api_key"):
                return None
            payload: dict[str, Any] = {
                "model": client.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": goal},
                ],
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.1,
            }
            resp = client.session.post(
                f"{client.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {client.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=client.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.info("Tool Calling API 请求失败: %s", exc, exc_info=True)
            return None

        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return None

        catalog = self._get_catalog()
        valid_actions = {cap["action_id"] for cap in catalog}
        steps: list[PlannedStep] = []
        for idx, tc in enumerate(tool_calls):
            func = tc.get("function") or {}
            tool_name = func.get("name", "")
            action_id = self._action_id_from_tool_name(tool_name)
            if action_id not in valid_actions:
                continue
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            risk = "medium"
            for cap in catalog:
                if cap["action_id"] == action_id:
                    risk = to_step_risk(cap.get("risk", "medium"))
                    break
            steps.append(PlannedStep(
                step_id=f"tc_step_{idx + 1}",
                action_id=action_id,
                payload=args,
                risk_level=risk,
                timeout_seconds=20,
                retries=1,
            ))
        return steps if steps else None

    def _plan_via_llm(self, goal: str, user_profile: dict[str, Any] | None = None) -> list[PlannedStep] | None:
        catalog = self._get_catalog()
        capabilities_text = "\n".join(
            f"- {cap['action_id']}: {cap['description']} (risk={to_step_risk(cap.get('risk'))}, payload={json.dumps(cap['payload_schema'], ensure_ascii=False)})"
            for cap in catalog
        )
        system_prompt = self._SYSTEM_PROMPT.format(capabilities=capabilities_text)
        profile_summary = self._build_user_profile_summary(user_profile)
        if profile_summary:
            system_prompt = f"{system_prompt}\n\n## 用户画像摘要\n{profile_summary}"
        try:
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": goal},
                ],
                temperature=0.2,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("LLM chat_completion 调用失败: %s", exc)
            return None

        raw_text = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                raw_text = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(response, str):
            raw_text = response
        if not raw_text.strip():
            return None

        try:
            parsed = self._parse_llm_json(raw_text)
        except Exception:
            logger.warning("LLM 返回内容无法解析为 JSON: %.200s", raw_text)
            return None
        if parsed.get("is_chat"):
            return None
        raw_steps = parsed.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            return None

        valid_actions = {cap["action_id"] for cap in catalog}
        steps: list[PlannedStep] = []
        for idx, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                continue
            action_id = str(raw.get("action_id", "")).strip()
            if action_id not in valid_actions:
                continue
            steps.append(PlannedStep(
                step_id=str(raw.get("step_id", f"llm_step_{idx + 1}")),
                action_id=action_id,
                payload=dict(raw.get("payload") or {}),
                risk_level=str(raw.get("risk_level", "medium")),
                timeout_seconds=20,
                retries=1,
            ))
        return steps if steps else None

    @staticmethod
    def _parse_llm_json(text: str) -> dict[str, Any]:
        try:
            from services.ai_service import _parse_json_object
            return _parse_json_object(text)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    def _plan_via_keywords(self, goal: str, user_profile: dict[str, Any] | None = None) -> list[PlannedStep]:
        text = goal.lower()
        steps: list[PlannedStep] = []
        if any(token in text for token in ("回忆", "检索", "历史", "之前", "过去", "收件箱", "执行记录", "找找")):
            steps.append(PlannedStep(step_id="memory_recall", action_id="memory.recall", payload={"query": goal.strip(), "top_k": 8}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("新闻", "news", "资讯", "情报", "头条", "热点", "快讯")):
            query = self._resolve_news_query(user_profile)
            _category = ""
            _source = "all"
            for cat_kw, cat_val in [("ai", "AI"), ("科技", "科技"), ("国际", "国际"), ("政策", "政策"), ("财经", "财经"), ("互联网", "互联网")]:
                if cat_kw in text:
                    _category = cat_val
                    break
            for src_kw, src_val in [("36氪", "36氪"), ("it之家", "IT之家"), ("央视", "央视新闻"), ("bbc", "BBC World"), ("reuters", "Reuters World"), ("techcrunch", "TechCrunch AI"), ("澎湃", "澎湃新闻")]:
                if src_kw in text:
                    _source = src_val
                    break
            if any(token in text for token in ("简报", "摘要", "总结", "briefing", "digest")):
                steps.append(PlannedStep(step_id="news_briefing", action_id="news.briefing", payload={"topic": query, "max_items": 10}, risk_level="low", timeout_seconds=30, retries=1))
            else:
                steps.append(PlannedStep(step_id="news_lookup", action_id="news.lookup", payload={"query": query, "limit": 8, "source": _source, "category": _category}, risk_level="low", timeout_seconds=20, retries=2))
        if any(token in text for token in ("日志", "日记", "复盘", "周报", "summary")):
            steps.append(PlannedStep(step_id="journal_draft", action_id="journal.daily_draft", payload={"title": "自动日志草稿"}, risk_level="medium", timeout_seconds=12, retries=1))
        if any(token in text for token in ("笔记", "同步", "notion", "obsidian")):
            steps.append(PlannedStep(step_id="notes_sync", action_id="notes.sync", payload={"mode": "bidirectional"}, risk_level="medium", timeout_seconds=20, retries=1))
            steps.append(PlannedStep(step_id="notes_organize", action_id="notes.organize", payload={"max_items": 20}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("监控", "软件", "usage", "使用情况", "专注")):
            steps.append(PlannedStep(step_id="usage_snapshot", action_id="usage.snapshot", payload={"top_n": 12}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("升级", "更新", "自我更新", "self-update")):
            steps.append(PlannedStep(step_id="upgrade_check", action_id="upgrade.check", payload={"channel": "stable"}, risk_level="high", timeout_seconds=8, retries=1))
        has_web_keyword = any(token in text for token in ("浏览器", "网页", "搜索", "百度", "google", "搜一下"))
        has_open_with_url = "打开" in text and any(token in text for token in ("http", "www", ".com", ".cn", ".org"))
        if has_web_keyword or has_open_with_url:
            steps.append(PlannedStep(step_id="browser_open", action_id="browser.open", payload={"search": goal.strip()}, risk_level="medium", timeout_seconds=10, retries=1))
        if any(token in text for token in ("读取文件", "读文件", "打开文件", "查看文件", "目录")):
            steps.append(PlannedStep(step_id="file_read", action_id="file.read", payload={"path": ".", "encoding": "utf-8"}, risk_level="medium", timeout_seconds=10, retries=1))
        if any(token in text for token in ("剪贴板", "粘贴板", "clipboard")):
            steps.append(PlannedStep(step_id="clipboard_read", action_id="clipboard.read", payload={}, risk_level="low", timeout_seconds=5, retries=1))
        if any(token in text for token in ("运行", "执行", "启动", "打开应用", "qq", "微信", "wechat")):
            steps.append(PlannedStep(step_id="app_launch", action_id="app.launch", payload={"command": self._extract_app_name(goal)}, risk_level="high", timeout_seconds=15, retries=1))
        if any(token in text for token in ("下载", "download")):
            steps.append(PlannedStep(step_id="web_download", action_id="web.download", payload={"url": goal.strip()}, risk_level="medium", timeout_seconds=30, retries=1))
        # ── 桌面自动化关键字 ──
        if any(token in text for token in ("截图", "截屏", "屏幕", "screenshot", "看看屏幕")):
            steps.append(PlannedStep(step_id="screen_read", action_id="screen.read", payload={"ocr": True}, risk_level="low", timeout_seconds=30, retries=1))
        if any(token in text for token in ("找窗口", "窗口在哪", "哪个窗口", "window.find", "切换到")):
            steps.append(PlannedStep(step_id="window_find", action_id="window.find", payload={"title": goal.strip()}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("输入", "打字", "键入", "帮我输入", "输入文本")):
            steps.append(PlannedStep(step_id="keyboard_type", action_id="keyboard.type", payload={"text": goal.strip(), "enter": False}, risk_level="high", timeout_seconds=15, retries=1))
        if any(token in text for token in ("搜索文件", "查找文件", "找文件", "file.find", "搜文件")):
            steps.append(PlannedStep(step_id="file_find", action_id="file.find", payload={"pattern": goal.strip(), "max_results": 30}, risk_level="low", timeout_seconds=15, retries=1))
        if any(token in text for token in ("搜一下", "搜索一下", "网上搜", "联网搜", "实时", "最新", "web search")):
            search_query = re.sub(r"^(搜一下|搜索一下|网上搜|联网搜|帮我搜|查一下)", "", text).strip() or goal.strip()
            if not any(step.action_id == "memory.recall" for step in steps):
                steps.append(PlannedStep(step_id="search_memory_context", action_id="memory.recall", payload={"query": search_query, "top_k": 5}, risk_level="low", timeout_seconds=8, retries=1))
            steps.append(PlannedStep(step_id="web_search", action_id="web.search", payload={"query": search_query, "num_results": 5}, risk_level="low", timeout_seconds=15, retries=2))
        url_match = re.search(r"https?://[^\s\"'<>]+", text)
        if url_match and any(token in text for token in ("读取", "抓取", "获取内容", "看看这个", "分析这个")):
            steps.append(PlannedStep(step_id="web_fetch", action_id="web.fetch", payload={"url": url_match.group(0)}, risk_level="low", timeout_seconds=30, retries=1))
        if any(token in text for token in ("进程", "process", "任务管理器")):
            if any(token in text for token in ("结束", "杀", "kill", "关闭")):
                steps.append(PlannedStep(step_id="process_kill", action_id="process.kill", payload={"name": goal.strip()}, risk_level="high", timeout_seconds=10, retries=1))
            else:
                steps.append(PlannedStep(step_id="process_list", action_id="process.list", payload={"filter": ""}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("系统信息", "cpu", "内存", "磁盘", "硬盘", "网络状态", "系统状态")):
            steps.append(PlannedStep(step_id="system_info", action_id="system.info", payload={"category": "all"}, risk_level="low", timeout_seconds=15, retries=1))
        if any(token in text for token in ("定时", "定时任务", "cron", "计划任务", "每天", "自动执行", "定期")):
            if any(token in text for token in ("删除", "移除", "取消")):
                steps.append(PlannedStep(step_id="cron_remove", action_id="cron.remove", payload={"job_id": ""}, risk_level="medium", timeout_seconds=5, retries=1))
            elif any(token in text for token in ("添加", "创建", "设置", "新建", "每天", "每周", "每月")):
                steps.append(PlannedStep(step_id="cron_add", action_id="cron.add", payload={"name": goal.strip(), "action_id": "news.briefing", "repeat": "daily"}, risk_level="medium", timeout_seconds=5, retries=1))
            else:
                steps.append(PlannedStep(step_id="cron_list", action_id="cron.list", payload={}, risk_level="low", timeout_seconds=5, retries=1))
        if any(token in text for token in ("mcp", "github", "notion", "slack", "第三方工具")):
            if any(token in text for token in ("列出", "有哪些", "目录", "工具")):
                steps.append(PlannedStep(step_id="mcp_tools", action_id="mcp.tools.list", payload={}, risk_level="low", timeout_seconds=10, retries=1))
        if any(token in text for token in ("子代理", "后台任务", "异步", "并行执行", "这周的笔记", "整理我这周的笔记")):
            workflow = "weekly_notes_digest" if ("笔记" in text and "这周" in text) else "execute_steps"
            payload = {"workflow": workflow, "label": goal.strip(), "parallel": "并行" in text or "异步" in text}
            if workflow == "weekly_notes_digest":
                payload.update({"days": 7, "instruction": "请整理本周笔记并输出总结、待办与建议。"})
            steps.append(PlannedStep(step_id="subagent_spawn", action_id="subagent.spawn", payload=payload, risk_level="medium", timeout_seconds=5, retries=1))
        if any(token in text for token in ("技术周报", "weekly tech digest", "每周技术", "本周技术", "技术简报")):
            topics = self._get_setting(KEY_ASSISTANT_FOCUS_TOPICS, []) or []
            steps.append(PlannedStep(step_id="weekly_tech_digest", action_id="subagent.spawn", payload={"workflow": "weekly_tech_digest", "label": goal.strip(), "topics": topics}, risk_level="medium", timeout_seconds=5, retries=1))
        if not steps:
            steps.append(PlannedStep(step_id="generic_capture", action_id="journal.daily_draft", payload={"title": "通用任务草稿", "goal": goal}, risk_level="medium", timeout_seconds=10, retries=1))
        return steps

    def _resolve_news_query(self, user_profile: dict[str, Any] | None = None) -> str:
        raw_focus_topics = (user_profile or {}).get("focus_topics")
        focus_topics = self._normalize_string_list(raw_focus_topics)
        safe_topics = [item for item in focus_topics if self._is_safe_query_topic(item)]
        if safe_topics:
            return " OR ".join(safe_topics[:4])
        return str(self._get_setting(KEY_ASSISTANT_NEWS_QUERY, "AI OR 软件工程") or "AI OR 软件工程")

    @staticmethod
    def _build_user_profile_summary(user_profile: dict[str, Any] | None = None) -> str:
        """从画像字典构建摘要文本。"""
        profile = dict(user_profile or {})
        if "summary" in profile:
            return str(profile["summary"])
        lines = []
        current_focus = str(profile.get("current_focus") or "").strip()
        if current_focus:
            lines.append(f"当前关注: {current_focus}")
        active_project = str(profile.get("active_project") or "").strip()
        if active_project:
            lines.append(f"活跃项目: {active_project}")
        focus_topics = AssistantPlannerLayer._normalize_string_list(profile.get("focus_topics"))
        if focus_topics:
            lines.append(f"关注主题: {', '.join(focus_topics[:5])}")
        common_sources = AssistantPlannerLayer._normalize_string_list(profile.get("common_sources"))
        if common_sources:
            lines.append(f"常用来源: {', '.join(common_sources[:5])}")
        preferred_delivery = str(profile.get("preferred_delivery") or "").strip()
        if preferred_delivery:
            lines.append(f"默认交付: {preferred_delivery}")
        quiet_hours = AssistantPlannerLayer._normalize_string_list(profile.get("quiet_hours"))
        if quiet_hours:
            lines.append(f"安静时段: {', '.join(quiet_hours[:3])}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @staticmethod
    def _is_safe_query_topic(topic: str) -> bool:
        normalized = str(topic or "").strip()
        if not normalized:
            return False
        if any(token in normalized for token in ('"', "'", ";", "--", "/*", "*/")):
            return False
        if re.search(r"\s(?:or|and)\s", normalized, flags=re.IGNORECASE):
            return False
        return True

    @staticmethod
    def _extract_app_name(goal: str) -> str:
        text = goal.strip()
        for prefix in ("打开", "启动", "运行", "执行", "帮我打开", "帮我启动", "请打开", "请启动"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        for suffix in ("应用", "软件", "程序", "app"):
            if text.endswith(suffix):
                text = text[:-len(suffix)].strip()
                break
        return text or goal.strip()

    def plan_from_system_event(self, event_name: str, payload: dict[str, Any] | None = None) -> list[PlannedStep]:
        normalized = str(event_name or "").strip().lower()
        payload_map = dict(payload or {})
        if normalized == "startup":
            query = self._get_setting(KEY_ASSISTANT_NEWS_QUERY, "AI OR 软件工程")
            return [PlannedStep(step_id="startup_news", action_id="news.lookup", payload={"query": query, "limit": 5, "source": "startup"}, risk_level="low")]
        if normalized == "meeting_ended":
            return [PlannedStep(step_id="meeting_journal", action_id="journal.daily_draft", payload={"title": "会议纪要草稿", "context": payload_map}, risk_level="medium")]
        if normalized == "off_work":
            return [PlannedStep(step_id="offwork_review", action_id="journal.daily_draft", payload={"title": "下班复盘草稿", "context": payload_map}, risk_level="medium")]
        if normalized == "shutdown":
            return [PlannedStep(step_id="shutdown_usage", action_id="usage.snapshot", payload={"top_n": 15, "source": "shutdown"}, risk_level="low")]
        if normalized == "daily_review":
            return self._build_daily_review_steps(payload_map)
        if normalized == "daily_brief":
            return self._build_daily_brief_steps(payload_map)
        return []

    def _build_daily_review_steps(self, payload: dict[str, Any]) -> list[PlannedStep]:
        """构建每日复盘步骤：活动汇总 + 产出快照。"""
        return [
            PlannedStep(step_id="review_daily", action_id="review.daily",
                        payload={}, risk_level="low"),
        ]

    def _build_daily_brief_steps(self, payload: dict[str, Any]) -> list[PlannedStep]:
        """构建每日简报步骤：新闻 + 建议。"""
        return [
            PlannedStep(step_id="brief_news", action_id="news.briefing",
                        payload={"limit": 5}, risk_level="low"),
            PlannedStep(step_id="brief_suggestion", action_id="suggestion.daily",
                        payload={}, risk_level="low"),
        ]

    def _get_setting(self, key: str, default: Any) -> Any:
        if self.settings_repository is None:
            return default
        return self.settings_repository.get_setting(key, default)
