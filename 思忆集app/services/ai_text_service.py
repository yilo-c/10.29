"""services/ai_text_service.py — 壳内轻 AI 端口（契约 I3 / 决议 D1,D7）

高频小调用（翻译/分类/摘要/嵌入/简单问答）统一入口，模型链由底层
ai_client 提供（deepseek -> moonshot -> 本地 ollama），本端口是稳定区
与 AI 客户端的唯一接缝。services/desktop/memory 经此端口调用，不再直调
ai_client.chat_completion（A 类耦合清零）。

E1/E2（2026-08-31）：新增 extract()（受约束 JSON 抽取，复用 E3 json_mode
基建——支持则 response_format 强约束，不支持则提示词降级；解析失败重试 1
次，最终失败抛 AIServiceError）与 embed()（委托组合
knowledge.retrieval.embedding_service.EmbeddingService，零重复向量逻辑）。
extract/embed 为同步方法：与 complete()/health() 及底层实现一致；消费者
（memory extractor）经 asyncio.to_thread 在工作线程调用，避免 event-loop 陷阱。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

if TYPE_CHECKING:
    from ai.client.base import BaseOpenAIClient

logger = logging.getLogger("siyi.ai_text_service")

CONTENT_KINDS = {
    "note": "笔记 — 需要保存的信息、想法、知识点",
    "task": "任务 — 需要完成的具体事项、待办",
    "reminder": "提醒 — 时间敏感的备忘、约会、日程",
    "idea": "灵感 — 创意、点子、构思",
    "question": "问题 — 需要查找答案的问题",
    "reference": "参考 — 书签、链接、资源引用",
    "uncertain": "不确定 — 无法明确分类",
}


class AiTextService(Protocol):
    async def translate(self, text: str, source: str = "auto", target: str = "zh") -> str: ...
    async def classify(self, content: str, title: str = "") -> dict[str, Any]: ...
    async def summarize(self, text: str, max_chars: int = 200) -> str: ...
    def health(self) -> dict[str, Any]: ...
    # E1：受约束 JSON 抽取（schema 注入提示词，json_mode 强约束 + 解析失败重试 1 次）
    def extract(
        self,
        text: str,
        schema: dict[str, Any],
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = 800,
    ) -> dict[str, Any]: ...
    # E2：批量向量化（委托 knowledge EmbeddingService.get_embeddings）
    def embed(self, texts: list[str]) -> list[list[float]]: ...


# E3 JSON 稳健模式：OpenAI 强约束参数与本地端点降级提示
JSON_OBJECT_FORMAT = {"type": "json_object"}
JSON_ONLY_HINT = "\n\n仅输出合法 JSON：只返回一个 JSON 对象，不要任何解释或 Markdown 代码块。"


def _endpoint_supports_response_format(client: Any) -> bool:
    """沿包装链探测端点的 response_format 能力标记（E3，简单实现）。

    链路形如 JudgeAwareClient._inner -> FallbackOpenAIClient.primary ->
    OpenAICompatibleClient（真正持有 supports_response_format 属性）。
    取链上第一个显式定义该标记的对象为准；整条链都探测不到（如测试用
    FakeClient）则视为支持——发错端点时底层会剥离，解析失败另有
    _extract_json 兜底，不做过度设计。
    """
    node = client
    for _ in range(8):  # 防御性深度上限，包装链实际远小于此
        if node is None:
            break
        marker = getattr(node, "supports_response_format", None)
        if marker is not None:
            return bool(marker)
        node = getattr(node, "_inner", None) or getattr(node, "primary", None)
    return True


def _extract_json(resp: Any) -> str:
    """从 LLM 回复中提取 JSON 字符串（兼容 dict / str 两种返回）。"""
    if isinstance(resp, dict):
        choices = resp.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
        else:
            text = str(resp)
    elif isinstance(resp, str):
        text = resp.strip()
    else:
        text = str(resp)
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return text


# E1 受约束抽取：schema 注入提示词指导输出结构；解析失败时带纠错提醒重试
EXTRACT_SYSTEM_PROMPT = (
    "你是结构化信息抽取引擎。从用户提供的文本中抽取信息，"
    "严格按照给定的 JSON Schema 输出，只输出一个 JSON 对象，"
    "不要任何解释、前后缀或 Markdown 代码块。"
)
EXTRACT_RETRY_HINT = "\n\n上一次输出不是合法的 JSON 对象。请重新生成，严格符合 Schema，只输出一个 JSON 对象本身。"


def _parse_json_object(resp: Any) -> dict[str, Any]:
    """把 LLM 回复解析为 JSON 对象；非 dict / 非法 JSON 抛 ValueError 触发重试。"""
    parsed = json.loads(_extract_json(resp))
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


_embedding_singleton: Any = None
_embedding_lock = threading.Lock()


def _get_default_embedding_service(factory: Optional[Callable[[], Any]] = None) -> Any:
    """E2 默认嵌入服务：进程内懒加载单例（仿 knowledge.embedding._get_service）。

    sentence_transformers 探测/构造全部延迟到首次 embed()，冷启动零成本；
    factory 仅供测试/装配注入构造器，生产路径走 knowledge 既有实现。
    """
    global _embedding_singleton
    if _embedding_singleton is None:
        with _embedding_lock:
            if _embedding_singleton is None:
                if factory is not None:
                    _embedding_singleton = factory()
                else:
                    from knowledge.retrieval.embedding_service import EmbeddingService

                    _embedding_singleton = EmbeddingService()
    return _embedding_singleton


class LegacyLlmTextService:
    """包一层 context.ai_client（现有 JudgeAwareClient/Fallback 链），实现 AiTextService。"""

    def __init__(self, client: "BaseOpenAIClient | None", embedding_service: Any = None):
        self._client = client
        self._embedding_service = embedding_service

    # ── 通用 chat_completion 包装（领域 JSON 类调用走这条）──────────────
    def complete(
        self,
        user_prompt: str,
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = 800,
        json_mode: bool = False,
    ) -> Any:
        """稳定区唯一允许直调 chat_completion 的点（DoD① 余 1 处）。

        json_mode=True（E3 JSON 稳健模式）：端点支持 response_format 时传
        {"type": "json_object"} 强约束输出；不支持（本地 Ollama 等）时降级为
        在提示词尾部追加"仅输出合法 JSON"。两种情况下 _extract_json 仍是兜底。
        默认 False，未开启的调用方行为与改动前完全一致。
        """
        if self._client is None:
            raise RuntimeError("ai_client not available")
        extra: dict[str, Any] = {}
        if json_mode:
            if _endpoint_supports_response_format(self._client):
                extra["response_format"] = dict(JSON_OBJECT_FORMAT)
            else:
                user_prompt = user_prompt + JSON_ONLY_HINT
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        return self._client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra,
        )

    # ── E1：受约束 JSON 抽取 ─────────────────────────────────────────────
    def extract(
        self,
        text: str,
        schema: dict[str, Any],
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        """按 schema 从 text 抽取结构化 JSON 对象（返回 dict）。

        实现复用 complete(json_mode=True)，自动继承 E3 能力探测：端点支持
        response_format 时走 {"type": "json_object"} 强约束路径，不支持（本地
        Ollama 等）时降级为提示词"仅输出合法 JSON"。schema 序列化注入系统
        提示词指导输出结构。解析失败（非 JSON / 非对象）重试 1 次；最终
        失败抛 ai.types.AIServiceError（端口既有错误约定，RuntimeError 子类，
        消费者既有 except Exception 语义兼容）。client 不可用时由 complete()
        抛 RuntimeError("ai_client not available")。
        """
        from ai.types import AIServiceError

        base_system = (system_prompt or EXTRACT_SYSTEM_PROMPT) + "\n\n输出 JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
        user_prompt = text
        last_error: Optional[Exception] = None
        for _ in range(2):  # 首次 + 解析失败重试 1 次
            resp = self.complete(
                user_prompt,
                system_prompt=base_system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            try:
                return _parse_json_object(resp)
            except (ValueError, TypeError) as exc:  # json.JSONDecodeError ⊂ ValueError
                last_error = exc
                user_prompt = text + EXTRACT_RETRY_HINT
        logger.warning("extract 两次解析均失败: %s", last_error)
        raise AIServiceError(f"extract: LLM 输出无法解析为 JSON 对象（已重试 1 次）: {last_error}")

    # ── E2：批量向量化（委托 knowledge EmbeddingService，零重复向量逻辑）──
    def embed(self, texts: list[str]) -> list[list[float]]:
        """委托 EmbeddingService.get_embeddings：批处理/归一化/offline 行为原样保留。

        优先用实例注入的 embedding_service，否则用进程内懒加载单例（默认
        bge-small-zh-v1.5，同 knowledge.embedding 既有默认模型）；整批一次
        调用进 get_embeddings，保持其批处理与内部缓存行为。
        """
        items = list(texts or [])
        if not items:
            return []
        service = self._embedding_service
        if service is None:
            service = _get_default_embedding_service()
        return service.get_embeddings(items)

    async def classify(self, content: str, title: str = "") -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("ai_client not available")
        prompt = (
            "你是一个收件箱分类助手。分析以下内容，判断它最适合归入哪个类别。\n\n"
            f"标题: {title or '（无标题）'}\n"
            f"内容: {content[:500]}\n\n"
            "类别说明:\n" + "\n".join(f"  - {k}: {v}" for k, v in CONTENT_KINDS.items()) + "\n\n请以 JSON 格式回复，包含:\n"
            '  {"kind": "类别key", "confidence": 0.0~1.0, "reason": "简短理由", '
            '"suggested_action": "none|suggest_convert|auto_convert"}\n\n'
            "仅返回 JSON，不要其他文字。"
        )
        # E3: 预期 JSON 输出，启用稳健模式；_extract_json 保留为兜底
        resp = self.complete(prompt, temperature=0.1, max_tokens=200, json_mode=True)
        text = _extract_json(resp)
        result = json.loads(text)
        result.setdefault("kind", "uncertain")
        result.setdefault("confidence", 0.0)
        result.setdefault("reason", "")
        result.setdefault("suggested_action", "none")
        if result["kind"] not in CONTENT_KINDS:
            result["kind"] = "uncertain"
        return result

    async def translate(self, text: str, source: str = "auto", target: str = "zh") -> str:
        if self._client is None:
            return text
        prompt = f"Translate the following {source} text to {target}. Return only the translation, no explanation, no notes.\n\n{text}"
        resp = self.complete(prompt, temperature=0.1, max_tokens=2000)
        if isinstance(resp, dict):
            choices = resp.get("choices") or []
            raw = (choices[0].get("message", {}) or {}).get("content", "") if choices else ""
        elif isinstance(resp, str):
            raw = resp
        else:
            raw = str(resp)
        return (raw or "").strip() or text

    async def summarize(self, text: str, max_chars: int = 200) -> str:
        if self._client is None:
            return ""
        prompt = f"请用不超过 {max_chars} 字总结以下内容，只输出总结本身：\n\n{text[:4000]}"
        resp = self.complete(prompt, temperature=0.2, max_tokens=max_chars + 200)
        if isinstance(resp, dict):
            choices = resp.get("choices") or []
            raw = (choices[0].get("message", {}) or {}).get("content", "") if choices else ""
        elif isinstance(resp, str):
            raw = resp
        else:
            raw = str(resp)
        return (raw or "").strip()

    def health(self) -> dict[str, Any]:
        return {"available": self._client is not None}
