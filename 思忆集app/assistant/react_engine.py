"""ReAct (Reasoning + Acting) 引擎 — 让 AI 拥有工具调用能力。

在聊天流中插入一个 ReAct 循环层：
  用户消息 → 启发式判断（是否需要工具？）
             ├─ 否 → 返回 None（走原聊天流）
             └─ 是 → ReAct 循环
                      ├─ LLM(消息 + 工具定义) → tool_call
                      ├─ _execute_tool(tool_call)
                      ├─ 结果反馈 LLM
                      └─ 重复直到 LLM 返回最终文本
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from services.capability_types import CapabilityRegistry
from assistant.capability_router import AssistantCapabilityRouter
from assistant.models import PlannedStep
from services.react_tool_catalog import SAFE_TOOL_IDS_BASE


def _get_trace_id_safe() -> str:
    """P1.8: 安全读取当前 trace_id（避免循环导入，失败返回空串）。"""
    try:
        from core.agent_context_vars import get_trace_id

        return get_trace_id()
    except Exception:
        return ""


logger = logging.getLogger("siyi.assistant.react_engine")

# ── 工具 allowlist（安全子集，排除 shell.run / process.kill / app.launch 等高危操作）──
#
# 统一引用 services.react_tool_catalog.SAFE_TOOL_IDS_BASE 作为基准，
# 避免三份独立白名单漂移（历史 bug：notes.get/update/delete 在基准中已注册，
# 但此处副本遗漏，导致 LLM 看不到这些笔记工具）。
# 本引擎额外允许的工具在此补充。

_REACT_ENGINE_EXTRA_TOOLS: frozenset[str] = frozenset(
    {
        "news.lookup",
        "news.briefing",
        "usage.snapshot",
        "weather.current",
        "knowledge.search",
        # ── Trae IDE 控制 ──
        "trae.open",
        "trae.open_file",
        "trae.diff",
        "trae.list_extensions",
        "trae.install_extension",
        "trae.uninstall_extension",
        # ── 动态工具白名单（文档化声明） ──
        # 注：所有 tool.* 前缀的 action_id 在 _build_tool_defs 中通过
        # action_id.startswith("tool.") 前缀匹配自动允许，无需加入
        # TOOL_ALLOWLIST 静态集合。此处显式列出常用动态工具仅作文档化
        # 目的，便于维护者快速了解项目当前可用的动态工具集。
        # 新增动态工具无需修改此列表 — 直接编辑
        # data/tool_specs.json 或调用 tool.register 即可生效。
        "tool.copilot.suggest",
        "tool.copilot.explain",
        "tool.claude_code.run",
        "tool.git.status",
        "tool.git.log",
        "tool.git.diff",
    }
)

TOOL_ALLOWLIST: frozenset[str] = SAFE_TOOL_IDS_BASE | _REACT_ENGINE_EXTRA_TOOLS

# ── 关键词触发规则 ──

_TRIGGER_KEYWORDS: list[str] = [
    # 文件操作
    "文件",
    "桌面",
    "搜索",
    "查找",
    "读取",
    "打开",
    "写入",
    "保存",
    "删除",
    "复制",
    "移动",
    # 网络
    "web",
    "网络",
    "网页",
    "url",
    "http",
    "下载",
    "上传",
    "抓取",
    "爬取",
    # 笔记/任务
    "笔记",
    "同步",
    "整理",
    "任务",
    "待办",
    "todo",
    "标记",
    "完成",
    # 记忆
    "记忆",
    "回忆",
    "记住",
    "想起",
    # 系统操作
    "剪贴板",
    "粘贴",
    "复制",
    "新闻",
    "简报",
    "资讯",
    "翻译",
    "translate",
    "天气",
    "weather",
    "信息",
    "状态",
    "系统",
    "进程",
    "服务",
    "收件箱",
    "收集",
    "inbox",
    # 浏览器自动化触发词
    "浏览器",
    "登录",
    "填表",
    "截图",
    "点击",
    "输入框",
    "browse",
    "browser",
    "click",
    "form",
    "login",
    "screenshot",
    # Trae IDE 触发词
    "trae",
    "ide",
    "编辑器",
    "扩展",
    # P1-4: 补充命令式动词
    "启动",
    "运行",
    "执行",
    "关闭",
    "检查",
    "扫描",
    "创建",
    "修改",
    "更新",
    "添加",
    "导出",
    "导入",
    "分析",
    "总结",
    "生成",
    "发送",
    "安装",
    # P1-4: 补充英文动作词
    "run",
    "exec",
    "start",
    "stop",
    "check",
    "scan",
    "create",
    "update",
    "delete",
    "remove",
    "export",
    "import",
    "analyze",
    "summarize",
    "generate",
    # P1-4: 补充数据操作
    "数据",
    "数据库",
    "备份",
    "恢复",
    "清理",
    "统计",
    "报表",
]


class ReActEngine:
    """ReAct 循环引擎。"""

    def __init__(
        self,
        primary_client: Any,
        capability_router: AssistantCapabilityRouter,
        capability_registry: CapabilityRegistry,
        max_iterations: int = 8,
    ) -> None:
        self._client = primary_client
        self._router = capability_router
        self._registry = capability_registry
        self._max_iterations = max_iterations
        # P0/C4: OpenAI tool name → action_id 精确映射，避免 replace fallback 误解析
        self._tool_name_map: dict[str, str] = {}
        # P1-4: _needs_tools 缓存（容量上限 128，超限时全量重建）
        self._needs_tools_cache: dict[str, bool] = {}

    # ── 启发式预判 ──────────────────────────────────────────

    def _needs_tools(self, text: str) -> bool:
        """快速判断是否需要工具调用。短消息/问候直接跳过。

        P1-4: 增加缓存（128 条），避免对相同消息重复判断。
        """
        stripped = text.strip()
        if not stripped or len(stripped) < 4:
            return False

        # P1-4: 缓存检查
        cache_key = stripped[:200]  # 截断避免超长 key
        cached = self._needs_tools_cache.get(cache_key)
        if cached is not None:
            return cached
        # 缓存上限控制（超过 128 条时全量清空重建）
        if len(self._needs_tools_cache) > 128:
            self._needs_tools_cache.clear()

        # 问候/闲聊快速跳过
        greetings = {"你好", "嗨", "hello", "hi", "hey", "在吗", "在不在", "再见", "拜拜", "晚安", "谢谢", "好的", "嗯"}
        lower = stripped.lower()
        if lower in greetings or lower.rstrip("！!？?。.,") in greetings:
            self._needs_tools_cache[cache_key] = False
            return False

        for kw in _TRIGGER_KEYWORDS:
            if kw.lower() in lower:
                self._needs_tools_cache[cache_key] = True
                return True

        self._needs_tools_cache[cache_key] = False
        return False

    # ── 工具定义生成 ────────────────────────────────────────

    def _build_tool_defs(self) -> list[dict[str, Any]]:
        """从 CapabilityRegistry 提取工具定义，按 allowlist 过滤。

        tool.* 前缀的动态工具（ToolSpec 配置注册）自动允许，无需加入静态白名单。
        P0/C4: 同时构建 openai_name → action_id 精确映射。
        """
        all_tools = self._registry.build_openai_tools()
        filtered: list[dict[str, Any]] = []
        name_map: dict[str, str] = {}
        for tool in all_tools:
            func = tool.get("function", {})
            # OpenAI 格式中 name 的 "." 已被替换为 "_"
            openai_name: str = func.get("name", "")
            # 还原 action_id：file_read → file.read
            action_id = openai_name.replace("_", ".", 1) if "_" in openai_name else openai_name
            # 静态白名单 OR 动态工具前缀（tool.*）自动允许
            if action_id in TOOL_ALLOWLIST or action_id.startswith("tool."):
                filtered.append(tool)
                # P0/C4: 记录精确映射，避免后续 fallback 误解析
                name_map[openai_name] = action_id
        self._tool_name_map = name_map
        return filtered

    # ── 工具执行 ────────────────────────────────────────────

    def _tool_name_to_action_id(self, tool_name: str) -> str:
        """OpenAI tool name → action_id（file_read → file.read）。

        P0/C4: 优先用精确映射，映射未命中才用 split fallback。
        """
        # 精确映射优先
        mapped = self._tool_name_map.get(tool_name)
        if mapped is not None:
            return mapped
        # fallback：只替换第一个 _，保留 action_id 内可能的 _
        if "_" in tool_name:
            parts = tool_name.split("_", 1)
            action_id = f"{parts[0]}.{parts[1]}"
            logger.debug("tool name %r not in map, fallback to %r", tool_name, action_id)
            return action_id
        return tool_name

    def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行单个工具调用。"""
        action_id = self._tool_name_to_action_id(tool_name)
        logger.info("ReAct executing: %s args=%s", action_id, args)
        step = PlannedStep(
            step_id=f"react_{action_id}",
            action_id=action_id,
            payload=args,
            description=f"ReAct auto: {action_id}",
        )
        return self._router.execute(step)

    # ── 结果格式化 ──────────────────────────────────────────

    def _format_tool_result(self, result: dict[str, Any]) -> str:
        """将工具结果格式化为字符串，大内容截断。"""
        if not result:
            return "（空结果）"

        # 优先用 content/result/data 字段
        text = json.dumps(result, ensure_ascii=False, default=str)

        # 截断长文本（8000 chars）
        if len(text) > 8000:
            text = text[:8000] + f"\n... (truncated, total {len(text)} chars)"

        return text

    # ── 主入口 ──────────────────────────────────────────────

    def process_message(
        self,
        user_message: str,
        system_context: str | None = None,
    ) -> str | None:
        """处理用户消息，返回 AI 回复文本。不需要工具时返回 None。"""
        if not self._needs_tools(user_message):
            return None

        tool_defs = self._build_tool_defs()
        if not tool_defs:
            logger.info("ReAct: no available tools, skipping")
            return None

        messages: list[dict[str, str]] = []
        if system_context:
            messages.append({"role": "system", "content": system_context})
        messages.append({"role": "user", "content": user_message})

        for iteration in range(1, self._max_iterations + 1):
            logger.info("ReAct iteration %d/%d | trace_id=%s", iteration, self._max_iterations, _get_trace_id_safe())

            try:
                # B6 修复：同步 chat_completion 会阻塞事件循环。
                # 如果在异步上下文中（有运行中的事件循环），用线程池执行避免阻塞；
                # 否则直接同步调用（保持向后兼容）。
                def _call_llm():
                    return self._client.chat_completion(
                        messages=messages,
                        temperature=0.3,
                        max_tokens=4096,
                        tools=tool_defs,
                        tool_choice="auto",
                    )

                try:
                    asyncio.get_running_loop()
                    # 在事件循环中：用线程池避免阻塞
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        resp = pool.submit(_call_llm).result(timeout=120)
                except RuntimeError:
                    # 无事件循环：直接同步调用
                    resp = _call_llm()
            except Exception as exc:
                logger.warning("ReAct LLM call failed at iteration %d: %s", iteration, exc)
                # 有已有内容则返回，否则返回 None 让主流程处理
                tool_calls_in_messages = any("tool_calls" in m for m in messages)
                if tool_calls_in_messages:
                    return self._extract_last_assistant_content(messages) or "（AI 处理中断，请重试）"
                return None

            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message", {})
            content: str | None = msg.get("content")
            tool_calls: list[dict] | None = msg.get("tool_calls")

            if not tool_calls:
                # LLM 返回纯文本 — 完成
                return content or "（AI 未能生成回复）"

            # 追加 assistant 消息（含 tool_calls）
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
            # 序列化 tool_calls 为可 JSON 序列化的格式
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                }
                for tc in tool_calls
            ]
            messages.append(assistant_msg)

            # 执行每个 tool_call
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name: str = func.get("name", "")
                raw_args = func.get("arguments", "{}")
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                try:
                    result = self._execute_tool(tool_name, args)
                    formatted = self._format_tool_result(result)
                except Exception as exc:
                    logger.warning("ReAct tool %s failed: %s", tool_name, exc)
                    formatted = f"（工具执行失败: {exc}）"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": formatted,
                    }
                )

        # 达到最大迭代次数
        last_content = self._extract_last_assistant_content(messages)
        if last_content:
            return last_content + "\n\n[已达最大迭代次数，结果可能不完整]"
        return f"已达到最大迭代次数（{self._max_iterations} 轮），模型始终未返回最终文本，请简化问题或稍后重试。"

    @staticmethod
    def _extract_last_assistant_content(
        messages: list[dict[str, Any]],
    ) -> str | None:
        """从消息列表中提取最后一个 assistant 文本。"""
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return None
