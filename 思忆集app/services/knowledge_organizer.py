"""KnowledgeOrganizer — 主动整理个人知识体系。

从记忆索引、结构化记忆（@知识领域 / @个人档案）中读取当前知识状态，
调用 AI 生成整理报告：领域分布、知识缺口、建议行动。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.services.knowledge_organizer")


class KnowledgeOrganizer:
    """主动整理服务：分析记忆与结构化知识，输出整理报告。"""

    def __init__(
        self,
        *,
        ai_client=None,
        memory_index_service=None,
        structured_memory_store=None,
        user_profile_service=None,
        ai_text_service=None,
    ) -> None:
        self._ai = getattr(ai_client, "_inner", ai_client)
        self._memory_index = memory_index_service
        self._structured_store = structured_memory_store
        self._profile_service = user_profile_service
        self._ai_service = ai_text_service

    def organize(
        self,
        *,
        memory_limit: int = 120,
        days: int | None = 30,
    ) -> dict[str, Any]:
        """执行一次知识整理，返回报告。

        Args:
            memory_limit: 从记忆索引召回的最大条目数。
            days: 只分析最近 N 天的记忆，None 表示全部。
        """
        if self._memory_index is None:
            return {"error": "memory index service unavailable"}

        try:
            recall = self._memory_index.recall(
                query="",
                top_k=memory_limit,
                days=days,
            )
            memories = list(recall.get("items") or [])
        except Exception as exc:
            logger.warning("knowledge organizer recall failed: %s", exc, exc_info=True)
            memories = []

        domains = []
        profiles = []
        if self._structured_store is not None:
            try:
                domains = self._structured_store.list_entries(supertag_type="@知识领域", limit=100)
                profiles = self._structured_store.list_entries(supertag_type="@个人档案", limit=100)
            except Exception as exc:
                logger.warning("knowledge organizer structured store failed: %s", exc, exc_info=True)

        focus_topics = []
        if self._profile_service is not None:
            try:
                focus_topics = self._profile_service.get_focus_topics()
            except Exception as exc:
                logger.debug("knowledge organizer profile topics failed: %s", exc)

        memory_texts = []
        for m in memories[:memory_limit]:
            title = str(m.get("title") or "").strip()
            snippet = str(m.get("snippet") or "").strip()
            source = str(m.get("source_type") or "").strip()
            parts = [p for p in (title, snippet, f"[{source}]") if p]
            if parts:
                memory_texts.append(" | ".join(parts))

        domain_texts = []
        for d in domains:
            fv = d.get("field_values") or {}
            name = str(fv.get("name") or "").strip()
            maturity = str(fv.get("maturity") or "").strip()
            area = str(fv.get("area") or "").strip()
            if name:
                domain_texts.append(f"{name} (maturity={maturity}, area={area})")

        profile_texts = []
        for p in profiles:
            fv = p.get("field_values") or {}
            key = str(fv.get("key") or "").strip()
            value = str(fv.get("value") or "").strip()
            category = str(fv.get("category") or "").strip()
            if key and value:
                profile_texts.append(f"[{category}] {key}: {value}")

        report = self._analyze(
            memory_texts=memory_texts,
            domain_texts=domain_texts,
            profile_texts=profile_texts,
            focus_topics=focus_topics,
        )

        report["metadata"] = {
            "memory_count": len(memories),
            "domain_count": len(domains),
            "profile_count": len(profiles),
            "focus_topics": focus_topics[:12],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        return report

    def _analyze(
        self,
        *,
        memory_texts: list[str],
        domain_texts: list[str],
        profile_texts: list[str],
        focus_topics: list[str],
    ) -> dict[str, Any]:
        """调用 AI 生成整理报告。"""
        if self._ai is None:
            return {"error": "ai client unavailable"}

        system_prompt = (
            "你是一位个人知识管理顾问。请根据用户最近的记忆、已建立的知识领域、个人档案和关注主题，生成一份 JSON 格式的知识整理报告。只输出合法 JSON，不要任何解释或 markdown 代码块。"
        )

        sections = []
        sections.append("## 近期记忆样本（每条一行）")
        sections.extend(memory_texts or ["（无）"])
        sections.append("\n## 已建立的知识领域")
        sections.extend(domain_texts or ["（无）"])
        sections.append("\n## 个人档案摘要")
        sections.extend(profile_texts or ["（无）"])
        sections.append("\n## 用户关注主题")
        sections.extend(focus_topics or ["（无）"])

        user_prompt = (
            '请分析以下个人知识数据，输出 JSON：\n'
            '{\n'
            '  "summary": "一段 200 字以内的整体分析",\n'
            '  "domain_distribution": [{"domain": "领域名", "count": 估计相关记忆数, "examples": ["记忆标题1", "记忆标题2"]}],\n'
            '  "suggested_domains": [{"name": "建议新增领域", "reason": "理由", "seed_memories": ["相关记忆"]}],\n'
            '  "gaps": [{"area": "缺口领域", "suggestion": "如何补充"}],\n'
            '  "action_items": ["可执行建议1", "可执行建议2"]\n'
            '}\n\n'
        ) + "\n".join(sections)

        try:
            if self._ai_service is not None:
                resp = self._ai_service.complete(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    temperature=0.3,
                    max_tokens=2500,
                )
            elif self._ai is None:
                return {"error": "ai unavailable"}
            else:
                # ← 无端口时的兼容路径（M5 删）
                resp = self._ai.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=2500,
                )
            content = resp.get("content") if isinstance(resp, dict) else str(resp)
            if not content:
                return {"error": "empty ai response"}

            text = str(content).strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"error": "invalid report format", "raw": text}
        except json.JSONDecodeError as exc:
            logger.warning("knowledge organizer parse failed: %s", exc)
            return {"error": f"failed to parse ai report: {exc}"}
        except Exception as exc:
            logger.warning("knowledge organizer analyze failed: %s", exc, exc_info=True)
            return {"error": f"ai analyze failed: {exc}"}
