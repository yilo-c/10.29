"""Wordbook and word JSON-RPC handlers."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any

from backend.handlers._list_guard import cap_list, clamp_limit

logger = logging.getLogger("siyi.backend.handlers.wordbook")


async def _find_word_by_id(repo: Any, word_id: str | int) -> dict | None:
    """O(1) 查找单词，优于 O(n) 全量扫描。"""
    # 优先用仓库索引方法
    getter = getattr(repo, "get_word_by_id", None)
    if getter is not None:
        return await asyncio.to_thread(getter, word_id)
    # 回退：全量加载 + 单次遍历
    all_words = await asyncio.to_thread(repo.list_words)
    return next((w for w in all_words if w.get("id") == word_id), None)


def _parse_iso_utc(value: str) -> datetime | None:
    """解析 ISO 时间字符串为 aware datetime；失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except Exception:
        logger.exception("解析词条复习时间失败")
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _word_is_due(word: dict[str, Any], now: datetime) -> bool:
    """判断词条是否到期需复习。

    判定规则：
      - 有 next_review 字段且能解析为时间 → next_review <= now 即到期
      - 无 next_review 或解析失败 → 视为"全新词"，立即需要复习（到期）
    """
    nr = word.get("next_review")
    if not nr:
        return True  # 从未复习的新词，视为到期
    dt = _parse_iso_utc(str(nr))
    if dt is None:
        return True  # 时间格式异常，保守视为到期
    return dt <= now


def add_word_entry(
    repo: Any,
    *,
    wordbook_id: int = 0,
    word: str = "",
    meaning: str = "",
    example: str = "",
    source: str = "",
) -> dict[str, Any]:
    """word.add 的核心实现（同步），供 JSON-RPC handler 与浏览器桥复用。

    单一真源：浏览器桥的 add-word 端点复用本函数，避免两条入口各自实现
    校验/截断/事件语义产生漂移。
    """
    if not repo or not str(word or "").strip():
        return {"success": False, "error": "invalid params"}
    source_trimmed = str(source or "").strip()
    if len(source_trimmed) > 64:
        source_trimmed = source_trimmed[:64]
    word_data: dict[str, Any] = {
        "word": str(word).strip(),
        "meaning": str(meaning or "").strip(),
        "example": str(example or "").strip(),
        "wordbook_id": wordbook_id,
    }
    if source_trimmed:
        word_data["source"] = source_trimmed
    word_id = repo.add_word(word_data)
    # emit 事件：通知词汇新增（供剪贴板联动、统计等订阅）
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(
            AppEvent(
                event_type="vocabulary.word_added",
                payload={
                    "word_id": word_id,
                    "word": str(word).strip(),
                    "source": source_trimmed,
                    "wordbook_id": wordbook_id,
                },
                source="word.add",
            )
        )
    except Exception:
        logger.exception("Failed to emit vocabulary.word_added event")
    return {"success": True, "id": word_id}


def register_wordbook(registry, context) -> None:
    """Register wordbook and word-related JSON-RPC methods."""

    # ── wordbook.list ──
    async def wordbook_list(**kwargs: Any) -> list[dict[str, Any]]:
        repo = context.vocabulary_repository
        if not repo:
            return []
        return await asyncio.to_thread(repo.list_wordbooks)

    registry.register("wordbook.list", wordbook_list)

    # ── wordbook.create ──
    async def wordbook_create(name: str = "", **kwargs: Any) -> dict[str, Any]:
        repo = context.vocabulary_repository
        if not repo:
            return {"success": False, "error": "vocabulary repository unavailable"}
        if not name.strip():
            return {"success": False, "error": "name is required"}
        try:
            new_id = await asyncio.to_thread(repo.add_wordbook, name.strip())
            return {"success": True, "id": new_id}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    registry.register("wordbook.create", wordbook_create)

    # ── wordbook.rename ──
    async def wordbook_rename(wordbook_id: int = 0, name: str = "", **kwargs: Any) -> dict[str, Any]:
        repo = context.vocabulary_repository
        if not repo or not wordbook_id or not name.strip():
            return {"success": False, "error": "invalid params"}
        try:
            await asyncio.to_thread(repo.rename_wordbook, wordbook_id, name.strip())
            return {"success": True}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    registry.register("wordbook.rename", wordbook_rename)

    # ── wordbook.delete ──
    async def wordbook_delete(wordbook_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        repo = context.vocabulary_repository
        if not repo or not wordbook_id:
            return {"success": False, "error": "invalid params"}
        try:
            await asyncio.to_thread(repo.delete_wordbook, wordbook_id)
            return {"success": True}
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    registry.register("wordbook.delete", wordbook_delete)

    # ── word.list ──
    async def word_list(wordbook_id: int = 0, keyword: str = "", **kwargs: Any) -> list[dict[str, Any]]:
        repo = context.vocabulary_repository
        if not repo:
            return []
        # 帧守卫（2026-10-15）：裸 list 契约不变，超 2000 条截断 + 告警
        if wordbook_id:
            if keyword:
                return cap_list(
                    await asyncio.to_thread(repo.search_words_in_wordbook, keyword, wordbook_id),
                    method="word.list",
                )
            return cap_list(
                await asyncio.to_thread(repo.get_words_by_wordbook, wordbook_id),
                method="word.list",
            )
        if keyword:
            return cap_list(
                await asyncio.to_thread(repo.search_words, keyword),
                method="word.list",
            )
        return cap_list(await asyncio.to_thread(repo.list_words), method="word.list")

    registry.register("word.list", word_list)

    # ── word.add ──
    async def word_add(
        wordbook_id: int = 0,
        word: str = "",
        meaning: str = "",
        example: str = "",
        source: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = context.vocabulary_repository
        return await asyncio.to_thread(
            add_word_entry,
            repo,
            wordbook_id=wordbook_id,
            word=word,
            meaning=meaning,
            example=example,
            source=source,
        )

    registry.register("word.add", word_add)

    # ── word.update ──
    async def word_update(
        word_id: int = 0,
        word: str = "",
        meaning: str = "",
        example: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"success": False, "error": "invalid params"}
        updated: dict[str, str] = {}
        if word:
            updated["word"] = word.strip()
        if meaning:
            updated["meaning"] = meaning.strip()
        if example:
            updated["example"] = example.strip()
        await asyncio.to_thread(repo.update_word, word_id, updated)
        return {"success": True}

    registry.register("word.update", word_update)

    # ── word.delete ──
    async def word_delete(word_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"success": False, "error": "invalid params"}
        await asyncio.to_thread(repo.delete_word, word_id)
        return {"success": True}

    registry.register("word.delete", word_delete)

    # ── word.review (SM-2 0-5 quality, dual-writes FSRS) ──
    async def word_review(word_id: int = 0, quality: int = 0, **kwargs: Any) -> dict[str, Any]:
        """Apply a review score (0-5) to a word, dual-writing SM-2 + FSRS fields."""
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"success": False, "error": "invalid params"}
        if not (0 <= quality <= 5):
            return {"success": False, "error": "quality must be 0-5"}
        try:
            result = await asyncio.to_thread(repo.record_review, word_id, quality)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if result is None:
            return {"success": False, "error": "word not found"}
        reps, ef, intv, nr = result
        return {
            "success": True,
            "repetitions": reps,
            "efactor": ef,
            "interval": intv,
            "next_review": nr,
        }

    registry.register("word.review", word_review)

    # ── word.review_fsrs (1-4 rating: Again/Hard/Good/Easy) ──
    _RATING_TO_QUALITY = {1: 1, 2: 3, 3: 4, 4: 5}

    async def word_review_fsrs(word_id: int = 0, rating: int = 0, **kwargs: Any) -> dict[str, Any]:
        """Apply a review rating (1-4 = Again/Hard/Good/Easy) to a word.

        Maps the 4-level FSRS rating to SM-2 quality and dual-writes both schedulers.
        """
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"success": False, "error": "invalid params"}
        if rating not in _RATING_TO_QUALITY:
            return {"success": False, "error": "rating must be 1-4"}
        quality = _RATING_TO_QUALITY[rating]
        try:
            result = await asyncio.to_thread(repo.record_review, word_id, quality)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        if result is None:
            return {"success": False, "error": "word not found"}
        reps, ef, intv, nr = result
        return {
            "success": True,
            "repetitions": reps,
            "efactor": ef,
            "interval": intv,
            "next_review": nr,
        }

    registry.register("word.review_fsrs", word_review_fsrs)

    # ── vocab.rollback_fsrs (strip FSRS fields from all words) ──
    async def vocab_rollback_fsrs(confirm: bool = False, **kwargs: Any) -> dict[str, Any]:
        """Remove all FSRS fields from every word, reverting to pure SM-2.

        Requires ``confirm=True`` to actually perform the rollback.
        """
        repo = context.vocabulary_repository
        if not repo:
            return {"success": False, "error": "vocabulary repository unavailable"}
        if not confirm:
            return {"success": False, "error": "confirmation required"}
        cleaned = await asyncio.to_thread(repo.rollback_all_fsrs)
        return {"success": True, "cleaned": cleaned}

    registry.register("vocab.rollback_fsrs", vocab_rollback_fsrs)

    # ── word.list_due (到期需复习的词汇列表) ──
    # 前端 FlashcardModule / WordbankModule 调用，返回 next_review <= now 的词汇。
    async def word_list_due(
        wordbook_id: int = 0,
        limit: int = 200,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """返回到期需复习的词汇列表（按 next_review 升序，最早到期在前）。

        Args:
            wordbook_id: 限定词本 ID，0 表示全部词本
            limit: 返回数量上限，默认 200
        """
        repo = context.vocabulary_repository
        if not repo:
            return []
        now = datetime.now(timezone.utc)
        # 复用 list_words 的加载逻辑（含软删除过滤）
        words = await asyncio.to_thread(repo.list_words)
        if wordbook_id:
            words = [w for w in words if w.get("wordbook_id", 1) == wordbook_id]
        due = [w for w in words if _word_is_due(w, now)]
        # 按 next_review 升序排序（无 next_review 的全新词排最前）
        due.sort(key=lambda w: _parse_iso_utc(str(w.get("next_review") or "")) or datetime.min.replace(tzinfo=timezone.utc))
        # 帧守卫（2026-10-15）：limit<=0 从"全量"收紧为默认 200，上限 clamp 2000
        due = due[: clamp_limit(limit, default=200)]
        return due

    registry.register("word.list_due", word_list_due)

    # ── word.count_due (到期词汇数量) ──
    async def word_count_due(wordbook_id: int = 0, **kwargs: Any) -> dict[str, Any]:
        """返回到期需复习的词汇数量。

        Args:
            wordbook_id: 限定词本 ID，0 表示全部词本

        Returns:
            {"count": int, "wordbook_id": int}
        """
        repo = context.vocabulary_repository
        if not repo:
            return {"count": 0, "wordbook_id": wordbook_id}
        now = datetime.now(timezone.utc)
        words = await asyncio.to_thread(repo.list_words)
        if wordbook_id:
            words = [w for w in words if w.get("wordbook_id", 1) == wordbook_id]
        count = sum(1 for w in words if _word_is_due(w, now))
        return {"count": count, "wordbook_id": wordbook_id}

    registry.register("word.count_due", word_count_due)

    # ── wordbook.import_batch (批量导入单词) ──
    # 前端 WordbankModule.tsx 调用，期望返回 {imported: int, skipped: int}
    async def wordbook_import_batch(
        wordbook_id: int = 0,
        cards: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """批量导入单词到指定词本，跳过已存在的词。

        Args:
            wordbook_id: 目标词本 ID
            cards: 单词卡片列表，每项形如 {word, meaning, tags?, example?}

        Returns:
            {"imported": int, "skipped": int}
        """
        repo = context.vocabulary_repository
        if not repo or not wordbook_id:
            return {"imported": 0, "skipped": 0, "error": "invalid params"}
        if not cards or not isinstance(cards, list):
            return {"imported": 0, "skipped": 0, "error": "cards list required"}

        # 获取词本中已有单词（小写去重）
        existing = await asyncio.to_thread(repo.get_words_by_wordbook, wordbook_id)
        existing_words = {str(w.get("word", "")).strip().lower() for w in existing}

        imported = 0
        skipped = 0
        for card in cards:
            if not isinstance(card, dict):
                continue
            word = str(card.get("word", "")).strip()
            if not word:
                skipped += 1
                continue
            if word.lower() in existing_words:
                skipped += 1
                continue
            meaning = str(card.get("meaning", "")).strip()
            example = str(card.get("example", "")).strip()
            tags = card.get("tags") if isinstance(card.get("tags"), list) else []
            word_data: dict[str, Any] = {
                "word": word,
                "meaning": meaning,
                "example": example,
                "wordbook_id": wordbook_id,
            }
            if tags:
                word_data["tags"] = tags
            try:
                await asyncio.to_thread(repo.add_word, word_data)
                existing_words.add(word.lower())
                imported += 1
            except Exception as exc:
                logger.warning("wordbook.import_batch: failed to add %r: %s", word, exc)
                skipped += 1
        return {"imported": imported, "skipped": skipped}

    registry.register("wordbook.import_batch", wordbook_import_batch)

    # ── word.enrich_cloud (AI 补充单词详情) ──
    # 前端 WordbankModule.tsx 调用，期望返回 {success: bool, error?: str}
    # 会用 AI 补充音标、同义词、反义词、搭配、例句等，并写回词库
    async def word_enrich_cloud(
        word_id: int = 0,
        word: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """用 AI 补充单词详情（音标、释义、同义词、例句等）并写回词库。

        Args:
            word_id: 词条 ID
            word: 单词文本（word_id 为 0 时用此字段查词库）

        Returns:
            {"success": bool, "error"?: str, "enriched"?: dict}
        """
        repo = context.vocabulary_repository
        if not repo:
            return {"success": False, "error": "vocabulary repository unavailable"}
        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return {"success": False, "error": "ai_client unavailable"}

        # 定位词条
        target = None
        if word_id:
            target = await _find_word_by_id(repo, word_id)
        elif word:
            words = await asyncio.to_thread(repo.list_words)
            for w in words:
                if str(w.get("word", "")).strip().lower() == word.strip().lower():
                    target = w
                    break
        if target is None:
            return {"success": False, "error": "word not found"}

        word_text = str(target.get("word", "")).strip()
        prompt = (
            "请为以下英语单词生成详细的学习信息，返回严格 JSON（不要 markdown 代码块、不要解释）：\n"
            "{\n"
            '  "phonetic": "音标，如 /sɪɡˈnɪfɪkənt/",\n'
            '  "phonetic_us": "美音",\n'
            '  "phonetic_uk": "英音",\n'
            '  "meanings": ["释义1", "释义2"],\n'
            '  "synonyms": ["同义词1"],\n'
            '  "antonyms": ["反义词1"],\n'
            '  "collocations": ["常见搭配1"],\n'
            '  "examples": ["例句1（含中文翻译）"],\n'
            '  "level": "CET4|CET6|考研|GRE|TOEFL|IELTS"\n'
            "}\n\n"
            f"单词：{word_text}"
        )
        try:
            import json as _json

            result = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=800,
            )
            raw = (result or "").strip()
            if raw.startswith("```"):
                raw = raw.removeprefix("```json").removeprefix("```")
                if raw.endswith("```"):
                    raw = raw.removesuffix("```")
                raw = raw.strip()
            brace = raw.find("{")
            if brace < 0:
                return {"success": False, "error": "ai returned non-json"}
            parsed = _json.loads(raw[brace:])
            if not isinstance(parsed, dict):
                return {"success": False, "error": "ai returned invalid structure"}

            # 写回词库（只更新非空字段）
            updates: dict[str, Any] = {}
            for field in ("phonetic", "phonetic_us", "phonetic_uk", "level"):
                val = parsed.get(field)
                if val:
                    updates[field] = str(val).strip()
            for field in ("meanings", "synonyms", "antonyms", "collocations", "examples"):
                val = parsed.get(field)
                if isinstance(val, list) and val:
                    updates[field] = val
            if updates:
                await asyncio.to_thread(repo.update_word, target.get("id"), updates)
            return {"success": True, "enriched": updates}
        except Exception as exc:
            logger.warning("word.enrich_cloud failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("word.enrich_cloud", word_enrich_cloud)

    # ── word.generate_examples (AI 生成例句) ──
    # 前端 WordbankModule.tsx 调用，期望返回 {success: bool, examples: [...], word_id: int}
    async def word_generate_examples(
        word: str = "",
        meaning: str = "",
        source: str = "en",
        word_id: int = 0,
        count: int = 3,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """用 AI 为单词生成例句。

        Args:
            word: 单词文本
            meaning: 单词释义（可选，帮助 AI 生成更贴切的例句）
            source: 源语言代码，默认 'en'
            word_id: 词条 ID（可选，用于回传）
            count: 生成例句数量，默认 3

        Returns:
            {"success": bool, "examples": [str|{sentence, translation}], "word_id": int}
        """
        word_clean = (word or "").strip()
        if not word_clean:
            return {"success": False, "examples": [], "word_id": word_id, "error": "word is required"}
        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return {"success": False, "examples": [], "word_id": word_id, "error": "ai_client unavailable"}

        meaning_hint = f"（释义：{meaning}）" if meaning else ""
        prompt = (
            f"请为英语单词 '{word_clean}'{meaning_hint} 生成 {count} 个高质量例句，"
            "每个例句含英文原句和中文翻译。返回严格 JSON 数组（不要 markdown 代码块、不要解释）：\n"
            '[{"sentence":"英文例句","translation":"中文翻译"},...]\n\n'
            "要求：\n"
            "- 例句自然地道，体现单词的典型用法\n"
            "- 难度适中，适合学习\n"
            "- 语境多样，避免雷同\n"
            f"- 每条例句都要包含单词 '{word_clean}'"
        )
        try:
            import json as _json

            result = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=600,
            )
            raw = (result or "").strip()
            if raw.startswith("```"):
                raw = raw.removeprefix("```json").removeprefix("```")
                if raw.endswith("```"):
                    raw = raw.removesuffix("```")
                raw = raw.strip()
            # 提取 JSON 数组
            bracket = raw.find("[")
            if bracket < 0:
                # 尝试提取对象中的 examples 字段
                brace = raw.find("{")
                if brace >= 0:
                    parsed = _json.loads(raw[brace:])
                    examples = parsed.get("examples") if isinstance(parsed, dict) else None
                else:
                    return {"success": False, "examples": [], "word_id": word_id, "error": "ai returned non-json"}
            else:
                examples = _json.loads(raw[bracket:])
            if not isinstance(examples, list):
                return {"success": False, "examples": [], "word_id": word_id, "error": "ai returned invalid structure"}

            # 规范化：接受 str 或 {sentence, translation} 两种格式
            normalized = []
            for ex in examples:
                if isinstance(ex, str):
                    normalized.append(ex)
                elif isinstance(ex, dict):
                    normalized.append(ex)
            if not normalized:
                return {"success": False, "examples": [], "word_id": word_id, "error": "no examples generated"}
            return {"success": True, "examples": normalized, "word_id": word_id}
        except Exception as exc:
            logger.warning("word.generate_examples failed: %s", exc)
            return {"success": False, "examples": [], "word_id": word_id, "error": str(exc)}

    registry.register("word.generate_examples", word_generate_examples)

    # ── F4: 扩展复习模式 ──────────────────────────────────
    # 3 种新模式：看词选义、看义选词、例句填空
    # 听写模式已由 word.dictation_check 提供，此处不重复

    def _pick_distractors(
        pool: list[dict[str, Any]],
        correct_value: str,
        field: str,
        count: int = 3,
    ) -> list[str]:
        """从词池中随机选 count 个非正确答案的干扰项。"""
        candidates = [
            str(w.get(field, "")).strip()
            for w in pool
            if str(w.get(field, "")).strip() and str(w.get(field, "")).strip().lower() != correct_value.lower()
        ]
        # 去重
        seen: set[str] = set()
        unique = []
        for c in candidates:
            cl = c.lower()
            if cl not in seen:
                seen.add(cl)
                unique.append(c)
        random.shuffle(unique)
        return unique[:count]

    # ── word.quiz_meaning: 看词选义 ──
    async def word_quiz_meaning(
        word_id: int = 0,
        wordbook_id: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """看词选义：显示单词，从 4 个释义中选正确项。

        Args:
          word_id: 目标词条 ID（必填）
          wordbook_id: 词本 ID，用于干扰项来源；0 表示全部

        Returns:
          {word_id, word, phonetic?, options: [4个释义], correct_index}
          失败返回 {error: str}
        """
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"error": "word_id required"}
        target = await _find_word_by_id(repo, word_id)
        if target is None:
            return {"error": "word not found"}
        correct_meaning = str(target.get("meaning", "")).strip()
        if not correct_meaning:
            return {"error": "word has no meaning"}

        # 获取全词列表用作干扰项来源（_find_word_by_id 的内部 all_words 是局部变量，此处需独立获取）
        all_words = await asyncio.to_thread(repo.list_words)

        # 干扰项来源：同词本优先，否则全部
        if wordbook_id:
            pool = [w for w in all_words if w.get("wordbook_id", 1) == wordbook_id and w.get("id") != word_id]
        else:
            pool = [w for w in all_words if w.get("id") != word_id]
        if len(pool) < 3:
            # 词库太少，用全部词池补充
            pool = [w for w in all_words if w.get("id") != word_id]

        distractors = _pick_distractors(pool, correct_meaning, "meaning", count=3)
        if len(distractors) < 3:
            # 不足 3 个干扰项，用占位补充
            while len(distractors) < 3:
                distractors.append(f"（暂无干扰项 {len(distractors) + 1}）")

        options = [correct_meaning] + distractors[:3]
        random.shuffle(options)
        correct_index = options.index(correct_meaning)

        result: dict[str, Any] = {
            "quiz_type": "meaning",
            "word_id": word_id,
            "word": str(target.get("word", "")),
            "options": options,
            "correct_index": correct_index,
        }
        phonetic = str(target.get("phonetic") or target.get("phonetic_us") or "").strip()
        if phonetic:
            result["phonetic"] = phonetic
        return result

    registry.register("word.quiz_meaning", word_quiz_meaning)

    # ── word.quiz_word: 看义选词 ──
    async def word_quiz_word(
        word_id: int = 0,
        wordbook_id: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """看义选词：显示释义，从 4 个单词中选正确项。

        Args:
          word_id: 目标词条 ID（必填）
          wordbook_id: 词本 ID，用于干扰项来源；0 表示全部

        Returns:
          {word_id, meaning, options: [4个单词], correct_index}
          失败返回 {error: str}
        """
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"error": "word_id required"}
        target = await _find_word_by_id(repo, word_id)
        if target is None:
            return {"error": "word not found"}
        correct_word = str(target.get("word", "")).strip()
        meaning = str(target.get("meaning", "")).strip()
        if not meaning:
            return {"error": "word has no meaning"}

        # 获取全词列表用作干扰项来源（同 word_quiz_meaning）
        all_words = await asyncio.to_thread(repo.list_words)

        if wordbook_id:
            pool = [w for w in all_words if w.get("wordbook_id", 1) == wordbook_id and w.get("id") != word_id]
        else:
            pool = [w for w in all_words if w.get("id") != word_id]
        if len(pool) < 3:
            pool = [w for w in all_words if w.get("id") != word_id]

        distractors = _pick_distractors(pool, correct_word, "word", count=3)
        if len(distractors) < 3:
            while len(distractors) < 3:
                distractors.append(f"placeholder{len(distractors) + 1}")

        options = [correct_word] + distractors[:3]
        random.shuffle(options)
        correct_index = options.index(correct_word)

        return {
            "quiz_type": "word",
            "word_id": word_id,
            "meaning": meaning,
            "options": options,
            "correct_index": correct_index,
        }

    registry.register("word.quiz_word", word_quiz_word)

    # ── word.quiz_cloze: 例句填空 ──
    async def word_quiz_cloze(
        word_id: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """例句填空：显示例句（目标词挖空），用户输入答案。

        答案检查可复用 word.dictation_check。

        Args:
          word_id: 目标词条 ID（必填）

        Returns:
          {quiz_type, word_id, sentence_with_blank, word_length, hint}
          失败返回 {error: str}
        """
        repo = context.vocabulary_repository
        if not repo or not word_id:
            return {"error": "word_id required"}
        target = await _find_word_by_id(repo, word_id)
        if target is None:
            return {"error": "word not found"}
        word_text = str(target.get("word", "")).strip()
        if not word_text:
            return {"error": "word is empty"}

        # 优先用存储的 example，其次 examples 列表
        example = str(target.get("example", "")).strip()
        if not example:
            examples_list = target.get("examples")
            if isinstance(examples_list, list) and examples_list:
                first = examples_list[0]
                if isinstance(first, dict):
                    example = str(first.get("sentence") or first.get("text") or "").strip()
                else:
                    example = str(first).strip()

        if not example:
            return {"error": "word has no example sentence for cloze"}

        # 在例句中挖空目标词（大小写不敏感，整词匹配 \b 防止子串误匹配）
        pattern = re.compile(r"\b" + re.escape(word_text) + r"\b", re.IGNORECASE)
        match = pattern.search(example)
        if not match:
            return {"error": "word not found in example sentence"}

        # 用下划线替换目标词
        blank = "_" * len(word_text)
        sentence_with_blank = example[: match.start()] + blank + example[match.end() :]

        # 提示：首字母 + 长度
        hint = f"{word_text[0].upper()}{'.' * (len(word_text) - 1)} ({len(word_text)} 字母)"

        return {
            "quiz_type": "cloze",
            "word_id": word_id,
            "sentence_with_blank": sentence_with_blank,
            "word_length": len(word_text),
            "hint": hint,
        }

    registry.register("word.quiz_cloze", word_quiz_cloze)
