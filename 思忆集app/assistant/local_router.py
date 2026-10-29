"""本地 AI 默认处理 + 自我判断模块。

根据 complete-architecture-design.md 第 3 章设计：
- 日常任务 (90%+) 由本地 AI 直接处理，成本 $0
- 本地 AI 自我判断能否胜任，不能则升级到 Judge

用法:
    router = LocalAIRouter(ai_client, settings_repository)
    result = router.process(user_input)
    if result.needs_judge:
        judge_result = judge_router.route(result.task, result.context)
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.assistant.local_router")

# 本地 AI 胜任度阈值
CONFIDENCE_AUTO = 0.8      # >= 0.8: 自动处理，不询问用户
CONFIDENCE_SUGGEST = 0.5   # >= 0.5: 本地处理但给出建议，用户可覆盖
# < 0.5: 升级到 Judge

MAX_LOCAL_TOKENS = 4096
LOCAL_TEMPERATURE = 0.3

_SELF_JUDGE_PROMPT = (
    "你是一个轻量级本地 AI，运行在用户电脑上 (Ollama，模型: {model_name})。\n"
    "你的任务是：判断当前任务是否可以在本地完成，还是需要更强大的云端模型。\n\n"
    "你可以本地处理的任务类型：\n"
    "1. 日常对话、闲聊、简单问答\n"
    "2. 信息整理、数据汇总、标签分类\n"
    "3. 本地知识库检索 (你有 RAG 能力)\n"
    "4. 简单决策 (已知方案库中对比推荐)\n"
    "5. 笔记/任务/翻译等本地数据处理\n"
    "6. 文本摘要、格式转换\n\n"
    "翻译任务的边界规则（严格遵循）：\n"
    "  - 以下情况可本地处理：单词/短语翻译、≤5句的短句翻译、常见表达\n"
    "  - 以下情况必须升级：整段/全文翻译、专业领域翻译（法律/医学/技术文档）、需保持文风的文学翻译\n\n"
    "需要升级到 Judge 的情况：\n"
    "1. 复杂代码重构或调试\n"
    "2. 多步骤规划或复杂分析\n"
    "3. 需要最新外部信息 (联网)\n"
    "4. 用户显式要求使用更强模型\n"
    "5. 多方案多维度对比决策\n"
    "6. 整段/全文翻译或专业领域翻译\n\n"
    '请输出 JSON：{{"can_handle": true/false, "confidence": 0.0-1.0, '
    '"reason": "判断理由", "task_type": "chat|organize|retrieve|decision|complex"}}'
)


# 编程任务关键词：命中时路由到 qwen2.5-coder
_CODER_KEYWORDS = (
    "代码", "编程", "写一个", "实现", "调试", "bug",
    "function", "class", "算法", "重构", "优化代码", "代码审查",
    "python", "javascript", "typescript", "rust", "go",
    "shell", "bash", "sql", "git",
    "docker", "api", "json", "yaml", "xml",
    "正则", "regex", "commit", "pr", "merge",
    "deploy", "ci", "cd", "test", "单元测试",
    "code review", "debug", "refactor",
)


@dataclass
class LocalProcessingResult:
    """本地 AI 处理结果。"""
    handled: bool                     # 是否由本地处理完成
    confidence: float                 # 胜任度
    task_type: str                    # 任务类型
    response: str | None = None       # 本地处理的结果文本
    needs_judge: bool = False         # 是否需要升级到 Judge
    judge_context: dict[str, Any] = field(default_factory=dict)  # 传递给 Judge 的上下文
    reason: str = ""


class LocalAIRouter:
    """本地 AI 默认处理 + 自我判断。

    职责：
    1. 接收用户输入，先尝试让本地 AI 处理
    2. 本地 AI 自我评估置信度
    3. 高置信度直接返回结果
    4. 低置信度构造 Judge 上下文并标记升级
    """

    def __init__(self, ai_client, settings_repository=None):
        self._ai_client = ai_client
        self._settings = settings_repository

    @property
    def _local_available(self) -> bool:
        """本地 AI (Ollama) 是否已配置。

        同时检查旧扁平键 (llm_local_api_base + llm_local_model)
        和 provider_configs 中的 is_local 条目。
        """
        if self._settings is None:
            return False
        try:
            base_url = self._settings.get_setting("llm_local_api_base", "")
            model = self._settings.get_setting("llm_local_model", "")
            if base_url and model:
                return True
            # 也检查 provider_configs 中的本地 provider
            try:
                from core.provider_config import load_providers
                providers = load_providers(self._settings)
                return any(p.enabled and p.is_local and bool(p.api_base) for p in providers)
            except Exception as exc:
                # P2 修复：本地模型检测失败无日志，静默返回 False 导致请求都走云端
                logger.warning("local_router failed to detect providers: %s", exc, exc_info=True)
                return False
        except Exception as exc:
            logger.warning("local_router failed to detect local model: %s", exc, exc_info=True)
            return False

    @staticmethod
    def _classify_task_type(text: str) -> str:
        """返回 'code' 或 'general'。"""
        lower = text.lower()
        return "code" if any(kw in lower for kw in _CODER_KEYWORDS) else "general"

    def _get_local_client(self, task_type: str = "general"):
        """根据任务类型选择本地 AI 客户端。无本地模型时返回 None。"""
        ai = self._ai_client
        if task_type == "code":
            coder = getattr(ai, "local_coder", None)
            if coder is not None:
                return coder
            # 未配置 coder 时降级到通用本地模型
            logger.debug("no local_coder configured, falling back to general local model")
        local = getattr(ai, "local", None)
        return local  # 可能为 None，不降级到云端

    def process(self, user_input: str, context: dict[str, Any] | None = None) -> LocalProcessingResult:
        """处理用户输入：先自我判断，再决定本地执行或升级。"""
        text = str(user_input or "").strip()
        if not text:
            return LocalProcessingResult(
                handled=False, confidence=0.0, task_type="",
                reason="empty_input",
            )

        # 第 1 步：自我判断
        judgment = self._self_judge(text)
        if judgment is None:
            return LocalProcessingResult(
                handled=False, confidence=0.0, task_type="complex",
                needs_judge=True,
                judge_context={"input": text, "context": context or {}},
                reason="judge_fallback_no_local",
            )

        can_handle = judgment.get("can_handle", False)
        confidence = float(judgment.get("confidence", 0.0))
        task_type = str(judgment.get("task_type", "chat"))
        reason = str(judgment.get("reason", ""))

        # 第 2 步：根据置信度决策
        if can_handle and confidence >= CONFIDENCE_AUTO:
            # 本地直接处理
            response = self._execute_local(text, context)
            return LocalProcessingResult(
                handled=True,
                confidence=confidence,
                task_type=task_type,
                response=response,
                reason=reason,
            )

        if can_handle and confidence >= CONFIDENCE_SUGGEST:
            # 本地处理，但告知用户可升级
            response = self._execute_local(text, context)
            return LocalProcessingResult(
                handled=True,
                confidence=confidence,
                task_type=task_type,
                response=response,
                judge_context={"input": text, "local_confidence": confidence, "local_response": response},
                reason=reason,
            )

        # 第 3 步：升级到 Judge
        return LocalProcessingResult(
            handled=False,
            confidence=confidence,
            task_type=task_type,
            needs_judge=True,
            judge_context={
                "input": text,
                "context": context or {},
                "local_judgment": judgment,
            },
            reason=reason or "local_confidence_below_threshold",
        )

    def _self_judge(self, text: str) -> dict[str, Any] | None:
        """让本地 AI 自我评估任务的胜任程度。"""
        if not self._local_available or self._ai_client is None:
            return None

        try:
            # Use actual configured model name in prompt
            model_name = (self._settings.get_setting("llm_local_model", "")
                          if self._settings else "")
            prompt = _SELF_JUDGE_PROMPT.format(model_name=model_name or "本地模型")
            task_type = self._classify_task_type(text)
            client = self._get_local_client(task_type)
            if client is None:
                return None
            response = client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                temperature=LOCAL_TEMPERATURE,
                max_tokens=MAX_LOCAL_TOKENS,
            )
        except Exception as exc:
            logger.warning("local self-judge failed: %s", exc, exc_info=True)
            return None

        raw = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                raw = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(response, str):
            raw = response

        if not raw.strip():
            return None

        try:
            cleaned = raw.strip()
            # Strip Qwen <think> tags (including malformed/multiple occurrences)
            cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL).strip()
            # Strip markdown code fences
            cleaned = re.sub(r'```(?:json)?\s*', '', cleaned).strip()
            if not cleaned:
                return None
            # Try direct JSON parse first; fall back to extracting first {…} block
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group())
                else:
                    raise
            return {
                "can_handle": bool(parsed.get("can_handle", False)),
                "confidence": min(1.0, max(0.0, float(parsed.get("confidence", 0.0)))),
                "task_type": str(parsed.get("task_type", "chat")),
                "reason": str(parsed.get("reason", "")),
            }
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("failed to parse self-judge response: %s", exc, exc_info=True)
            return None

    def _execute_local(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        """用本地 AI 执行任务。根据任务类型选择 coder 或通用模型。"""
        if self._ai_client is None:
            return None

        try:
            task_type = self._classify_task_type(text)
            client = self._get_local_client(task_type)
            if client is None:
                return None
            response = client.chat_completion(
                messages=[
                    {"role": "system", "content": "你是一个轻量本地助手，用简洁的中文回答用户问题。"},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
        except Exception as exc:
            logger.warning("local execution failed: %s", exc)
            return None

        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                content = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(response, str):
            content = response
        else:
            return None

        # Strip Qwen <think> tags from output
        if content:
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        return content
        return None

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回当前路由器状态。"""
        info = self._ai_client.describe_config() if hasattr(self._ai_client, "describe_config") else {}
        return {
            "local_available": self._local_available,
            "primary_model": getattr(getattr(self._ai_client, "primary", None), "model", None),
            "local_model": getattr(getattr(self._ai_client, "local", None), "model", None),
            "local_coder_model": getattr(getattr(self._ai_client, "local_coder", None), "model", None),
            "local_base_url": getattr(getattr(self._ai_client, "local", None), "base_url", None),
            "primary_configured": getattr(getattr(self._ai_client, "primary", None), "is_configured", lambda: False)(),
        }
