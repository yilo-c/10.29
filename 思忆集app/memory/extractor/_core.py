"""Core memory extraction logic — MemoryExtractor class.

Extracted from memory/extractor.py — MemoryExtractor (main extraction class).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from ..types import (
    AUTO_WRITE_THRESHOLD,
    CONFIRM_THRESHOLD,
    CONFIDENCE_MIN,
    CONFIDENCE_MAX,
    CONFLICT_CONFIDENCE_GAP,
    MemoryCandidate,
    ExtractionResult,
    PendingMemory,
    ConflictRecord,
)
from ._privacy import PrivacyFilter
from ._store import PendingMemoryStore, DedupEngine, ConflictTracker

logger = logging.getLogger("siyi.memory.extractor.core")

# 领域指引（E1 消费者切换：extract 端口路径与兼容数组路径共用）
_MEMORY_DOMAIN_GUIDE = (
    "You are a memory extractor. Analyze the following conversation and extract "
    "information worth remembering.\n\n"
    "Memory types to extract:\n"
    '1. factual - factual statements ("my NAS address is...", "I am learning Rust")\n'
    '2. preference - preference changes ("I now prefer...", "I decided to stop...")\n'
    "3. decision - key decisions (chosen solutions, rejected options)\n"
    "4. project - project progress (started/completed/paused projects)\n"
    "5. relationship - people info (family, colleagues, friends)\n"
    "6. intent - planned actions or to-do items\n"
    "7. procedural - workflows, processes, how-to knowledge\n\n"
    "Consider existing memories to avoid duplicates (marked [EXISTING MEMORIES]).\n\n"
    "Evaluate confidence for each memory candidate (factors: clarity, repetition, consistency):\n"
    "- 0.8-1.0: clear, important, unambiguous -> auto-write\n"
    "- 0.5-0.8: useful but uncertain -> ask user next time\n"
    "- 0.0-0.5: uncertain or irrelevant -> discard\n\n"
)

# 兼容路径（无端口直调 chat_completion）沿用原 JSON 数组输出要求，行为不变
_EXTRACT_PROMPT = _MEMORY_DOMAIN_GUIDE + (
    "Output JSON array ONLY:\n"
    '[{"type": "factual|preference|decision|project|relationship|intent",\n'
    '  "content": "memory content summary",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "source": "user|ai",\n'
    '  "tags": ["tag1", "tag2"],\n'
    '  "reason": "why this should be remembered"}]'
)

# E1 消费者切换：AiTextService.extract() 用 schema 包一层对象（json_object 模式
# 要求顶层为对象），候选条目字段与原数组路径完全一致，输出数据结构不变。
_MEMORY_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "factual",
                            "preference",
                            "decision",
                            "project",
                            "relationship",
                            "intent",
                            "procedural",
                        ],
                    },
                    "content": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "source": {"type": "string", "enum": ["user", "ai"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["type", "content", "confidence", "source"],
            },
        }
    },
    "required": ["memories"],
}


class MemoryExtractor:
    """Post-conversation memory extraction with full MCP-10 capabilities.

    Usage:
        extractor = MemoryExtractor(ai_client, data_store)
        result = extractor.extract(conversation_messages)
        for mem in result.auto_write:
            memory_index_service.add_memory(mem)
    """

    def __init__(
        self,
        ai_client,
        data_store=None,
        memory_index_service=None,
        knowledge_graph_extractor=None,
        ai_text_service=None,
    ):
        self._ai_client = ai_client
        self._data_store = data_store
        self._memory_index = memory_index_service
        self._pending_store = PendingMemoryStore(data_store) if data_store else None
        self._conflict_tracker = ConflictTracker(data_store) if data_store else None
        self._dedup_engine = DedupEngine()
        self._privacy_filter = PrivacyFilter()
        self._kg_extractor = knowledge_graph_extractor
        self._ai_service = ai_text_service

        self._extraction_enabled = True
        self._per_conversation_settings: dict[str, bool] = {}

    # ── Settings ──────────────────────────────────────────────────────

    def is_extraction_enabled(self, conversation_id: str = "") -> bool:
        """Check if extraction is enabled (global + per-conversation override)."""
        if not self._extraction_enabled:
            return False
        if conversation_id and conversation_id in self._per_conversation_settings:
            return self._per_conversation_settings[conversation_id]
        return True

    def set_extraction_enabled(self, enabled: bool) -> None:
        """Set global extraction toggle."""
        self._extraction_enabled = enabled
        logger.info("Memory extraction enabled: %s", enabled)

    def set_conversation_extraction(self, conversation_id: str, enabled: bool) -> None:
        """Set per-conversation extraction override."""
        if enabled:
            self._per_conversation_settings.pop(conversation_id, None)
        else:
            self._per_conversation_settings[conversation_id] = False
        logger.info(
            "Memory extraction for conversation %s: %s",
            conversation_id,
            enabled,
        )

    def get_extraction_notice(self) -> str:
        """Return notice text shown at conversation start."""
        return "本次对话内容将用于记忆提取，以便我更好地记住你的偏好和重要信息。你可以在设置中随时关闭此功能。敏感信息（如密码、密钥）不会被提取。"

    def get_recent_extractions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent extraction history."""
        if self._data_store is None:
            return []
        items = self._data_store.load_json("assistant_memory_extraction_log", [])
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items[-limit:]]

    def delete_extraction(self, index: int) -> bool:
        """Delete an extraction log entry."""
        if self._data_store is None:
            return False

        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            if 0 <= index < len(normalized):
                normalized.pop(index)
            return normalized

        self._data_store.update_json("assistant_memory_extraction_log", [], _updater)
        return True

    # ── Core extraction ────────────────────────────────────────────

    def extract(
        self,
        messages: list[dict[str, str]],
        conversation_id: str = "",
        existing_memories: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        """Extract candidate memories from conversation messages."""
        if not messages or self._ai_client is None:
            return ExtractionResult()

        if not self.is_extraction_enabled(conversation_id):
            logger.info("Memory extraction disabled for conversation %s", conversation_id)
            return ExtractionResult()

        recent = self._get_conversation_context(messages)
        if not recent:
            return ExtractionResult()

        candidates = self._llm_extract(recent, existing_memories or [])
        if not candidates:
            return ExtractionResult()

        safe_candidates, sensitive_candidates = self._privacy_filter.filter_candidates(candidates)
        if sensitive_candidates:
            logger.info("Privacy filter blocked %d sensitive candidates", len(sensitive_candidates))

        result = ExtractionResult(candidates=safe_candidates)

        for c in safe_candidates:
            if existing_memories:
                dedup_result = self._dedup_engine.check(c, existing_memories)
                if dedup_result["status"] == "duplicate":
                    result.duplicates.append(c)
                    continue
                elif dedup_result["status"] == "merge" and dedup_result["matched_entry"]:
                    merged = self._dedup_engine.merge(c, dedup_result["matched_entry"])
                    result.merged.append(merged)
                    self._adjust_confidence_for_merge(c)

            if c.confidence >= AUTO_WRITE_THRESHOLD:
                if existing_memories:
                    conflict = self._detect_conflict_with_history(c, existing_memories)
                    if conflict:
                        result.conflicts.append(conflict)
                        resolution = self._resolve_conflict_strategy(
                            c,
                            conflict["existing_entry"],
                        )
                        if resolution == "keep_new":
                            result.auto_write.append(c)
                        elif resolution == "keep_old":
                            result.discarded.append(c)
                        else:
                            result.needs_confirm.append(c)
                        continue

                result.auto_write.append(c)

            elif c.confidence >= CONFIRM_THRESHOLD:
                result.needs_confirm.append(c)
                if self._pending_store:
                    self._pending_store.add(c, conversation_id=conversation_id)
            else:
                result.discarded.append(c)

        self._log_extraction(result, conversation_id)

        if self._kg_extractor is not None and result.auto_write:
            # KG-CH（2026-09-21）：显式传 use_llm=True。记忆候选是聊天/叙述体，
            # 正则通道会将其切成句子碎片噪声实体（批3 X1 的契约本意即禁止
            # 此路，但当时只堵「回落」未堵「默认」）。此处不依赖函数默认值，
            # 接线意图就地可读；无 AI 内核时抽取器自身返回 via="skipped"。
            try:
                for mem in result.auto_write:
                    self._kg_extractor.extract_from_memory(
                        mem.to_dict() if hasattr(mem, "to_dict") else {},
                        use_llm=True,
                    )
            except Exception as exc:
                logger.info("知识图谱提取异常（非阻塞）: %s", exc, exc_info=True)

        return result

    def _get_conversation_context(
        self,
        messages: list[dict[str, str]],
        max_exchanges: int = 5,
    ) -> list[dict[str, str]]:
        """Get the full conversation context (multiple exchanges, not just last)."""
        result: list[dict[str, str]] = []
        exchange_count = 0
        for msg in reversed(messages):
            role = msg.get("role", "")
            if role in ("user", "assistant", "ai"):
                result.append(msg)
                if role == "user":
                    exchange_count += 1
                    if exchange_count >= max_exchanges:
                        break

        result.reverse()
        return result

    def _llm_extract(
        self,
        conversation: list[dict[str, str]],
        existing_memories: list[dict[str, Any]] | None = None,
    ) -> list[MemoryCandidate]:
        """Call AI to extract memory candidates with existing memory context."""
        existing_memories = existing_memories or []
        if self._ai_service is not None:
            return self._llm_extract_via_port(conversation, existing_memories)

        try:
            system_prompt = self._build_system_prompt(_EXTRACT_PROMPT, existing_memories)
            if self._ai_client is None:
                logger.warning("memory extraction failed: ai unavailable")
                return []
            # ← 无端口时的兼容路径（M5 删）
            response = self._ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(conversation, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=800,
            )
        except Exception as exc:
            logger.warning("memory extraction failed: %s", exc, exc_info=True)
            return []

        raw = ""
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                raw = (choices[0].get("message") or {}).get("content", "")
        elif isinstance(response, str):
            raw = response

        if not raw.strip():
            return []

        try:
            parsed = json.loads(raw.strip().strip("`").strip())
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("memory extraction parse failed: %s", exc, exc_info=True)
            return []

        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return []

        return self._candidates_from_items(parsed, existing_memories)

    def _llm_extract_via_port(
        self,
        conversation: list[dict[str, str]],
        existing_memories: list[dict[str, Any]],
    ) -> list[MemoryCandidate]:
        """E1 消费者切换：经 AiTextService.extract() 受约束抽取（json_mode 强约束、
        解析失败端口内重试 1 次）；输出数据结构（list[MemoryCandidate]）不变。"""
        system_prompt = self._build_system_prompt(_MEMORY_DOMAIN_GUIDE, existing_memories)
        try:
            data = self._ai_service.extract(
                text=json.dumps(conversation, ensure_ascii=False),
                schema=_MEMORY_EXTRACT_SCHEMA,
                system_prompt=system_prompt,
                temperature=0.1,
                max_tokens=800,
            )
        except Exception as exc:
            logger.warning("memory extraction failed: %s", exc, exc_info=True)
            return []

        items = data.get("memories") if isinstance(data, dict) else None
        if not isinstance(items, list):
            logger.warning("memory extraction parse failed: no memories array in extract result")
            return []
        return self._candidates_from_items(items, existing_memories)

    def _build_system_prompt(
        self,
        base_prompt: str,
        existing_memories: list[dict[str, Any]],
    ) -> str:
        """Append [EXISTING MEMORIES] context to a base system prompt."""
        if existing_memories:
            existing_summary = self._summarize_existing_memories(existing_memories)
            if existing_summary:
                return base_prompt + "\n\n[EXISTING MEMORIES]\n" + existing_summary
        return base_prompt

    def _candidates_from_items(
        self,
        items: list[Any],
        existing_memories: list[dict[str, Any]],
    ) -> list[MemoryCandidate]:
        """Parse candidate dicts (both extract and legacy array paths) to MemoryCandidate."""
        candidates = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue

            candidate = MemoryCandidate(
                type=str(item.get("type", "factual")),
                content=content,
                confidence=min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, float(item.get("confidence", 0.0)))),
                source=str(item.get("source", "user")),
                tags=[str(t).strip() for t in (item.get("tags") or []) if str(t).strip()],
                reason=str(item.get("reason") or ""),
            )

            candidate.confidence = self._compute_adjusted_confidence(
                candidate,
                existing_memories or [],
            )
            candidates.append(candidate)

        return candidates

    # ── Confidence computation ─────────────────────────────────────

    @staticmethod
    def _compute_adjusted_confidence(
        candidate: MemoryCandidate,
        existing_memories: list[dict[str, Any]],
    ) -> float:
        """Adjust confidence based on multi-factor analysis."""
        confidence = candidate.confidence
        content_lower = candidate.content.lower()

        repetition_count = 0
        for mem in existing_memories:
            mem_content = str(mem.get("content") or "").lower()
            if mem_content and (content_lower in mem_content or mem_content in content_lower):
                repetition_count += 1

        if repetition_count > 0:
            confidence += min(0.1, repetition_count * 0.05)

        consistent_count = 0
        for mem in existing_memories:
            mem_content = str(mem.get("content") or "").lower()
            if mem_content:
                shared = set(content_lower.split()) & set(mem_content.split())
                if len(shared) >= 3:
                    consistent_count += 1

        if consistent_count >= 2:
            confidence += 0.05
        elif consistent_count == 0 and repetition_count == 0 and existing_memories:
            pass

        if candidate.source == "user":
            confidence += 0.05
        elif candidate.source == "ai":
            confidence -= 0.02

        return min(CONFIDENCE_MAX, max(CONFIDENCE_MIN, confidence))

    @staticmethod
    def _adjust_confidence_for_merge(candidate: MemoryCandidate) -> None:
        """Slightly boost confidence for merged candidates."""
        candidate.confidence = min(
            CONFIDENCE_MAX,
            candidate.confidence + 0.05,
        )

    # ── Conflict detection ─────────────────────────────────────────

    def _detect_conflict_with_history(
        self,
        candidate: MemoryCandidate,
        existing_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Detect if candidate conflicts with existing memories."""
        for entry in existing_memories:
            content = str(entry.get("content") or "")
            if not content:
                continue

            if self._is_contradictory(candidate.content, content):
                conflict = {
                    "new": candidate.to_dict(),
                    "existing_entry": dict(entry),
                    "new_confidence": candidate.confidence,
                    "existing_confidence": float(entry.get("confidence", 0.5)),
                }

                if self._conflict_tracker:
                    record = ConflictRecord(
                        new_content=candidate.content,
                        existing_content=content,
                        new_confidence=candidate.confidence,
                        existing_confidence=float(entry.get("confidence", 0.5)),
                        detected_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._conflict_tracker.record_conflict(record)

                return conflict

        return None

    @staticmethod
    def _is_contradictory(new: str, existing: str) -> bool:
        """Check if two memory contents contradict each other."""
        new_lower = new.lower()
        exist_lower = existing.lower()
        negation_words = {"not", "no", "never", "stop", "quit", "abandon", "reject", "不是", "没有", "不要", "不再", "从未"}
        has_negation = any(w in new_lower for w in negation_words)
        has_negation_existing = any(w in exist_lower for w in negation_words)

        new_tokens = set(new_lower.split())
        exist_tokens = set(exist_lower.split())
        shared = new_tokens & exist_tokens

        if has_negation != has_negation_existing:
            return len(shared) >= 3

        conflict_patterns = [
            (r"(?:住在|位于|在|地址|地址是)\s*(\S+)", "location"),
            (r"(?:叫|是|姓名|名字)\s*(\S{2,4})", "name"),
            (r"(?:手机|电话|手机号|电话号)\s*(\S+)", "phone"),
        ]

        for pattern, _ in conflict_patterns:
            new_match = re.search(pattern, new)
            exist_match = re.search(pattern, existing)
            if new_match and exist_match:
                if new_match.group(1).lower() != exist_match.group(1).lower():
                    return True

        return False

    @staticmethod
    def _resolve_conflict_strategy(
        candidate: MemoryCandidate,
        existing_entry: dict[str, Any],
    ) -> str:
        """Determine conflict resolution strategy: keep_new | keep_old | keep_both."""
        existing_confidence = float(existing_entry.get("confidence", 0.5))
        confidence_diff = candidate.confidence - existing_confidence

        if confidence_diff > CONFLICT_CONFIDENCE_GAP:
            return "keep_new"
        elif confidence_diff < -CONFLICT_CONFIDENCE_GAP:
            return "keep_old"
        else:
            return "keep_both"

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _summarize_existing_memories(memories: list[dict[str, Any]], limit: int = 10) -> str:
        """Create a concise summary of existing memories for LLM context."""
        if not memories:
            return ""

        lines: list[str] = []
        for mem in memories[:limit]:
            content = str(mem.get("content") or "").strip()
            mem_type = str(mem.get("type") or "factual")
            if content:
                lines.append(f"- [{mem_type}] {content[:120]}")

        return "\n".join(lines) if lines else ""

    def _log_extraction(self, result: ExtractionResult, conversation_id: str) -> None:
        """Log extraction results for user review."""
        if self._data_store is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        log_entry = {
            "timestamp": now,
            "conversation_id": conversation_id,
            "total_candidates": len(result.candidates),
            "auto_write": len(result.auto_write),
            "needs_confirm": len(result.needs_confirm),
            "discarded": len(result.discarded),
            "conflicts": len(result.conflicts),
            "duplicates": len(result.duplicates),
            "merged": len(result.merged),
            "summary": (f"提取了 {len(result.auto_write)} 条自动写入, {len(result.needs_confirm)} 条待确认, {len(result.conflicts)} 条冲突"),
        }

        def _updater(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            normalized = list(items) if isinstance(items, list) else []
            normalized.append(log_entry)
            return normalized[-500:]

        self._data_store.update_json("assistant_memory_extraction_log", [], _updater)

    # ── Public API ─────────────────────────────────────────────────

    def get_pending_store(self) -> PendingMemoryStore | None:
        return self._pending_store

    def get_conflict_tracker(self) -> ConflictTracker | None:
        return self._conflict_tracker

    def get_pending_confirmations(self) -> list[PendingMemory]:
        if self._pending_store is None:
            return []
        return self._pending_store.get_pending()

    def get_extraction_status(self) -> dict[str, Any]:
        return {
            "extraction_enabled": self._extraction_enabled,
            "per_conversation_overrides": dict(self._per_conversation_settings),
            "pending_count": self._pending_store.get_pending_count() if self._pending_store else 0,
        }


__all__ = [
    "MemoryExtractor",
]
