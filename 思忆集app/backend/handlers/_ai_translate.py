"""AI intent-parsing and translation JSON-RPC handlers.

从原 _ai.py 拆分而来（意图与翻译域）：
  - ai.parse_intent
  - translate.text / translate.full / translate.languages / translate.word_detail
  - translations.save / translations.list

各方法依赖的 AppContext 字段（ai_client / vocabulary_repository /
translation_history_repository）均通过 getattr 惰性访问，保持与原实现一致。
函数内延迟 import 随方法迁移，避免循环依赖。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.handlers._async_rpc import POLLER_SCHEMA
from backend.handlers._async_rpc import poll_job_result as _poll_job_result
from backend.handlers._list_guard import clamp_limit

logger = logging.getLogger("siyi.backend.handlers.ai_translate")


async def run_translate_text(
    context: Any,
    *,
    text: str = "",
    source: str = "auto",
    target: str = "zh",
) -> dict[str, Any]:
    """快速翻译真实现 —— cron worker 与 RPC 降级共用（S3-2）。

    返回 ``{"original", "translated", "source", "target"}``（异常时附 ``error``）。
    """
    try:
        lang_map = {
            "zh": "中文",
            "en": "英语",
            "ja": "日语",
            "ko": "韩语",
            "fr": "法语",
            "de": "德语",
            "es": "西班牙语",
            "ru": "俄语",
        }
        target_name = lang_map.get(target, target)
        source_name = lang_map.get(source, source) if source != "auto" else "auto"

        style_hint = ""
        if target == "zh" and source in ("en", "auto"):
            style_hint = (
                "- 技术术语：首次出现时保留英文并在括号内注中文，如「CPU（中央处理器）」\n"
                "- 句式：英文长句拆分为符合中文表达习惯的短句\n"
                "- 风格：避免翻译腔，使用自然平实的中文"
            )
        elif target == "en" and source in ("zh", "auto"):
            style_hint = "- 技术术语：直接使用标准英文术语\n- 句式：按英文习惯整合短句为复合句\n- 风格：简洁正式的技术文档风格"
        else:
            style_hint = "- 准确传达原文含义，符合目标语言表达习惯"

        prompt = (
            f"你是一个专业翻译助手。请将以下内容从{source_name}翻译成{target_name}。\n\n"
            "要求：\n"
            f"{style_hint}\n"
            "- 保持原文格式：换行、列表缩进、代码块、空行不变\n"
            "- 代码、命令行、文件路径、URL、版本号、变量名不翻译\n"
            "- 专有名词（产品名、人名、品牌）保留原文\n"
            "- 只返回翻译结果，不需要解释\n\n"
            f"内容：\n{text[:4000]}"
        )
        ai_client = getattr(context, "ai_client", None)
        if ai_client is None:
            return {"original": text, "translated": "", "source": source, "target": target, "error": "ai_client unavailable"}
        # 保留对 ai_client 的单次网络超时（防 worker 任务永久挂死）。废止的是
        # 「同步 RPC 层」的等待上限：异步化后 RPC 秒回，慢的部分挪进队列，
        # 客户端轮询不受前端 call timeout 约束。预算同步放宽到 60s（原 30s
        # 是 RPC 层限制，本地模型长文经常一次跑不完 → 反复「timed out」空结果）。
        result = await asyncio.wait_for(
            asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=3000,
            ),
            timeout=60,
        )
        return {
            "original": text,
            "translated": (result or "").strip(),
            "source": source,
            "target": target,
        }
    except asyncio.TimeoutError:
        return {"original": text, "translated": "", "source": source, "target": target, "error": "translation timed out"}
    except Exception as exc:
        logger.warning("translate.text failed: %s", exc)
        return {"original": text, "translated": "", "source": source, "target": target, "error": str(exc)}


async def run_translate_full(
    context: Any,
    *,
    text: str = "",
    source: str = "auto",
    target: str = "zh",
    mode: str = "learn",
) -> dict[str, Any]:
    """深度翻译真实现 —— cron worker 与 RPC 降级共用（S3-2）。

    一次 AI 调用产出译文 + 生词卡 + 语法分析。前端 TranslationModule 在
    learn/deep 模式调用（原超时预算 90s，是「慢到像坏」最典型的一处）。
    返回 ``{"translated": str, "words": [...], "grammar": {...}}``。
    """
    import json as _json

    if not text or not text.strip():
        return {"translated": "", "words": [], "grammar": None, "error": "text is required"}

    ai_client = getattr(context, "ai_client", None)
    if not ai_client:
        return {"translated": "", "words": [], "grammar": None, "error": "ai_client unavailable"}

    source_name = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文", "auto": "自动检测"}.get(source, source)
    target_name = {"zh": "中文", "en": "英文", "ja": "日文", "ko": "韩文"}.get(target, target)

    grammar_depth = "详细分析句子结构、时态、语态、从句、语法难点" if mode == "deep" else "分析核心句子结构和关键语法点"

    prompt = (
        f"你是专业翻译助手。请将以下{source_name}文本翻译成{target_name}，"
        "并提取生词和语法分析。返回严格 JSON（不要 markdown 代码块、不要解释）：\n"
        "{\n"
        '  "translated": "完整译文",\n'
        '  "words": [\n'
        '    {"word":"原词","phonetic":"音标","meaning":"中文释义","context":"在原文中的用法","examples":["例句1"]}\n'
        "  ],\n"
        '  "grammar": {\n'
        '    "tenses": ["时态"],\n'
        '    "structure": "句子结构简述",\n'
        '    "grammarPoints": [{"pattern":"语法点","explanation":"解释","example":"例句"}]\n'
        "  }\n"
        "}\n\n"
        f"要求：\n"
        f"- 译文准确自然，保持原文格式\n"
        f"- 生词最多 8 个，选难度较高的词\n"
        f"- 语法分析：{grammar_depth}\n"
        f"- 只返回 JSON\n\n"
        f"文本：\n{text[:3000]}"
    )
    try:
        # 注意：这里保留对 ai_client 的单次网络超时（防 worker 任务永久挂死），
        # 废止的是「同步 RPC 层」的等待上限 —— 异步化后 RPC 本身秒回，
        # 慢的部分挪进队列，客户端轮询不受前端 call timeout 约束。
        result = await asyncio.wait_for(
            asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=3000,
            ),
            timeout=90,
        )
        raw = (result or "").strip()
        # 去除可能的 markdown 代码块包裹
        if raw.startswith("```"):
            raw = raw.removeprefix("```json").removeprefix("```")
            if raw.endswith("```"):
                raw = raw.removesuffix("```")
            raw = raw.strip()
        # 提取第一个 JSON 对象
        brace = raw.find("{")
        if brace < 0:
            return {"translated": raw or "", "words": [], "grammar": None, "error": "ai returned non-json"}
        try:
            parsed = _json.loads(raw[brace:])
        except _json.JSONDecodeError:
            # 尝试找到匹配的右大括号
            depth = 0
            end = -1
            for i, ch in enumerate(raw[brace:], brace):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end < 0:
                return {"translated": raw or "", "words": [], "grammar": None, "error": "ai returned invalid json"}
            parsed = _json.loads(raw[brace:end])

        if not isinstance(parsed, dict):
            return {"translated": "", "words": [], "grammar": None, "error": "ai returned invalid structure"}

        translated = str(parsed.get("translated") or "").strip()
        words = parsed.get("words") or []
        if not isinstance(words, list):
            words = []
        # 规范化 words 字段
        normalized_words: list[dict[str, Any]] = []

        for w in words:
            if not isinstance(w, dict):
                continue
            normalized_words.append(
                {
                    "word": str(w.get("word") or ""),
                    "phonetic": str(w.get("phonetic") or ""),
                    "meaning": str(w.get("meaning") or ""),
                    "context": str(w.get("context") or ""),
                    "examples": w.get("examples") if isinstance(w.get("examples"), list) else [],
                }
            )
        grammar = parsed.get("grammar") if isinstance(parsed.get("grammar"), dict) else None
        return {"translated": translated, "words": normalized_words, "grammar": grammar}
    except asyncio.TimeoutError:
        return {"translated": "", "words": [], "grammar": None, "error": "translation timed out"}
    except Exception as exc:
        logger.warning("translate.full failed: %s", exc)
        return {"translated": "", "words": [], "grammar": None, "error": str(exc)}


def register_ai_translate(registry, context) -> None:
    """Register AI intent-parsing and translation JSON-RPC methods."""

    # ── ai.parse_intent ──
    async def ai_parse_intent(text: str = "", **kwargs: Any) -> dict[str, Any]:
        """Parse natural language intent, return structured result."""
        # 2026-09-18（类型边界普查）：JSON-RPC 不做类型校验，客户端传 int 时
        # `text.strip()` 直接 AttributeError → -32603。入口归一为 str。
        text = str(text or "")
        if not text or not text.strip():
            return {"type": "unknown", "original": text}
        try:
            prompt = (
                f"解析以下用户输入，判断用户意图并提取信息。\n\n"
                f"可能的意图：\n"
                f"- create_note：创建笔记（记录内容、想法、知识）\n"
                f"- create_todo：创建待办事项（需要完成的事）\n"
                f"- translate：翻译文本（中英互译或其他语言）\n"
                f"- search：网络搜索（查找资料、最新信息）\n"
                f"- schedule：安排提醒/日程（时间相关）\n"
                f"- summarize：总结/摘要（概括长文本）\n"
                f"- query：查询/提问（其他一般性问题）\n"
                f"- unknown：无法判断\n\n"
                f"以 JSON 格式返回（不要其他文字）：\n"
                f'{{"type":"...","title":"...","content":"...","tags":[...],"dueDate":null,"query":""}}\n'
                f"（query 字段仅 translate/search 类型需要，其他类型可留空）\n\n"
                f"输入：{text}"
            )
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    context.ai_client.complete_text,
                    [{"role": "user", "content": prompt}],
                    max_tokens=300,
                ),
                timeout=15,
            )
            if result:
                import json as _json

                result = result.strip()
                if result.startswith("```"):
                    body = result.removeprefix("```json").removeprefix("```")
                    if body.endswith("```"):
                        body = body.removesuffix("```")
                    result = body.strip()
                brace_start = result.find("{")
                if brace_start >= 0:
                    parsed = _json.loads(result[brace_start:])
                    if isinstance(parsed, dict):
                        return parsed
        except asyncio.TimeoutError:
            return {"type": "unknown", "original": text, "error": "intent parsing timed out"}
        except Exception as exc:
            logger.warning("ai.parse_intent failed: %s", exc)
        return {"type": "unknown", "original": text}

    registry.register("ai.parse_intent", ai_parse_intent)

    # ── translate.text / translate.languages ──
    async def translate_text(
        text: str = "",
        source: str = "auto",
        target: str = "zh",
        **kwargs: Any,
    ) -> dict[str, str]:
        """AI translation with format preservation and tech terminology strategy.

        空文本短路直返，其余同步调 run_translate_text 返回终局
        ``{original, translated, source, target}``。

        S3-2 回退（2026-09-16 架构复审，audit §1）：入队信封版秒回 job_id，
        但前端 TranslationModule.tsx:156 读 ``transResp?.translated``——信封无
        此字段 → **快速翻译永远返回空**（且因秒回不再超时，症状从「慢」劣化
        为「静默空结果」）。消费面未适配轮询 → 回退同步直调。异步骨架
        （worker/.status/completed 事件）保留待 S3-2b 先建前端轮询消费层。
        """
        # 2026-09-18（类型边界普查）：JSON-RPC 不做类型校验，客户端传 int 时
        # `text.strip()` 直接 AttributeError → -32603。入口归一为 str。
        text = str(text or "")
        if not text or not text.strip():
            return {"original": "", "translated": "", "source": source, "target": target}
        return await run_translate_text(context, text=text, source=source, target=target)

    async def translate_text_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 translate.text 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="translate.text",
            wait_seconds=wait_seconds,
        )

    registry.register("translate.text", translate_text)
    registry.register_with_schema("translate.text.status", translate_text_status, schema=POLLER_SCHEMA)

    # ── translate.full (深度翻译：译文 + 生词 + 语法分析) ──
    # 前端 TranslationModule.tsx 在 learn/deep 模式调用，期望返回：
    #   {translated: str, words?: [{word,phonetic,meaning,context,examples?}], grammar?: {...}}
    async def translate_full(
        text: str = "",
        source: str = "auto",
        target: str = "zh",
        mode: str = "learn",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """深度翻译：一次 AI 调用同时产出译文、生词卡、语法分析。

        空文本短路直返，其余同步调 run_translate_full 返回终局
        ``{translated, words, grammar}``。

        S3-2 回退（2026-09-16 架构复审，audit §1）：这是 6 处「慢到像坏」里
        最重的一处（单次 LLM max_tokens=3000，前端 call timeout 已给到 90s）。
        入队信封版秒回 job_id，但前端 TranslationModule.tsx:159 读
        ``fullResp?.translated/words/grammar``——信封无这些字段 → **深度翻译
        （生词卡+语法）整块功能坏死**。消费面未适配轮询 → 回退同步直调。
        异步骨架保留待 S3-2b 先建前端轮询消费层再翻转。
        """
        # 2026-09-18（类型边界普查）：JSON-RPC 不做类型校验，客户端传 int 时
        # `text.strip()` 直接 AttributeError → -32603。入口归一为 str。
        text = str(text or "")
        if not text or not text.strip():
            return {"translated": "", "words": [], "grammar": None, "error": "text is required"}
        return await run_translate_full(context, text=text, source=source, target=target, mode=mode)

    async def translate_full_status(
        job_id: str = "",
        task_id: str = "",
        wait_seconds: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询 translate.full 异步任务的状态与结果。"""
        return await _poll_job_result(
            context,
            job_id=job_id,
            task_id=task_id,
            capability_id="translate.full",
            wait_seconds=wait_seconds,
        )

    registry.register("translate.full", translate_full)
    registry.register_with_schema("translate.full.status", translate_full_status, schema=POLLER_SCHEMA)

    async def translate_languages(**kwargs: Any) -> list[dict[str, str]]:
        """Return supported translation languages."""
        return [
            {"code": "zh", "name": "中文"},
            {"code": "en", "name": "English"},
            {"code": "ja", "name": "日本語"},
            {"code": "ko", "name": "한국어"},
            {"code": "fr", "name": "Français"},
            {"code": "de", "name": "Deutsch"},
            {"code": "es", "name": "Español"},
            {"code": "ru", "name": "Русский"},
        ]

    registry.register("translate.languages", translate_languages)

    # ── translate.word_detail (单词深度查询) ──
    async def translate_word_detail(
        word: str = "",
        source: str = "en",
        target: str = "zh",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """查询单词的详细信息：音标、释义、同义词、反义词、搭配、例句、等级。

        优先从词库查已 enrich 的记录；词库未命中时调 AI 现场分析。
        """
        word_clean = str(word or "").strip()
        if not word_clean:
            return {"error": "word is required"}

        # 1) 词库命中且已 enriched → 直接返回
        vocab_repo = getattr(context, "vocabulary_repository", None)
        if vocab_repo is not None:
            try:
                existing = await asyncio.to_thread(vocab_repo.list_words)
                for w in existing or []:
                    if str(w.get("word", "")).strip().lower() == word_clean.lower():
                        # 已 enriched 的判定：有 phonetic 或 synonyms
                        if w.get("phonetic") or w.get("synonyms"):
                            return {
                                "word": w.get("word", word_clean),
                                "phonetic": w.get("phonetic", ""),
                                "phonetic_us": w.get("phonetic_us", ""),
                                "phonetic_uk": w.get("phonetic_uk", ""),
                                "meanings": w.get("meanings") or ([w["meaning"]] if w.get("meaning") else []),
                                "synonyms": w.get("synonyms", []),
                                "antonyms": w.get("antonyms", []),
                                "collocations": w.get("collocations", []),
                                "examples": w.get("examples", []),
                                "level": w.get("level", ""),
                            }
                        break
            except Exception as exc:
                logger.debug("vocab lookup failed: %s", exc)

        # 2) 词库未命中 → 调 AI
        ai_client = getattr(context, "ai_client", None)
        if not ai_client:
            return {"error": "ai_client unavailable"}

        try:
            prompt = (
                f'请分析英文单词 "{word_clean}"，返回 JSON（不要任何解释、不要 markdown 代码块）：\n'
                '{"phonetic":"IPA 音标","phonetic_us":"美音","phonetic_uk":"英音",'
                '"level":"CEFR 等级如 B1/C1","meanings":["中文释义1","释义2"],'
                '"synonyms":[{"word":"近义词","similarity":"high/medium/low","nuance":"差异说明"}],'
                '"antonyms":[{"word":"反义词","note":"说明"}],'
                '"collocations":[{"phrase":"常用搭配","meaning":"含义"}],'
                '"examples":["例句1","例句2"],"etymology":"词源简述"}'
            )
            resp = await asyncio.to_thread(
                ai_client.complete_text,
                [{"role": "user", "content": prompt}],
                max_tokens=800,
            )
            content = (resp or "").strip()
            # 剥离 markdown 代码块
            if content.startswith("```"):
                content = content.removeprefix("```json").removeprefix("```")
                if content.endswith("```"):
                    content = content.removesuffix("```")
                content = content.strip()
            import json as _json

            brace = content.find("{")
            if brace < 0:
                return {"error": "ai returned non-json"}
            parsed = _json.loads(content[brace:])
            if not isinstance(parsed, dict):
                return {"error": "ai returned invalid json"}
            # 补 word 字段，保证前端 WordDetail.word 存在
            parsed.setdefault("word", word_clean)
            # 兜底字段
            parsed.setdefault("phonetic", "")
            parsed.setdefault("phonetic_us", "")
            parsed.setdefault("phonetic_uk", "")
            parsed.setdefault("meanings", [])
            parsed.setdefault("synonyms", [])
            parsed.setdefault("antonyms", [])
            parsed.setdefault("collocations", [])
            parsed.setdefault("examples", [])
            parsed.setdefault("level", "")
            return parsed
        except Exception as exc:
            logger.warning("translate.word_detail ai failed: %s", exc)
            return {"error": str(exc)}

    registry.register("translate.word_detail", translate_word_detail)

    # ── translations.save (保存翻译历史到后端，跨设备同步) ──
    async def translations_save(
        source_text: str = "",
        translated_text: str = "",
        source_lang: str = "",
        target_lang: str = "",
        mode: str = "quick",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """持久化一条翻译记录到 translation_history_repository。"""
        if not source_text or not source_text.strip():
            return {"success": False, "error": "source_text required"}
        repo = getattr(context, "translation_history_repository", None)
        if not repo:
            return {"success": False, "error": "translation repository unavailable"}
        try:
            new_id = await asyncio.to_thread(
                repo.add_translation_record,
                source_text,
                translated_text,
                source_lang or "auto",
                target_lang or "zh",
            )
            return {"success": True, "id": new_id}
        except Exception as exc:
            logger.warning("translations.save failed: %s", exc)
            return {"success": False, "error": str(exc)}

    registry.register("translations.save", translations_save)

    # ── translations.list (拉取翻译历史，供手机端跨设备同步) ──
    async def translations_list(limit: int = 50, **kwargs: Any) -> list[dict[str, Any]]:
        """返回翻译历史列表，字段映射为手机端期望的 {source, target, sourceText, translatedText, timestamp}。"""
        from datetime import datetime as _dt

        repo = getattr(context, "translation_history_repository", None)
        if not repo:
            return []
        try:
            records = await asyncio.to_thread(repo.get_translation_history)
        except Exception as exc:
            logger.warning("translations.list failed: %s", exc)
            return []
        items: list[dict[str, Any]] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            source_text = r.get("source_text") or r.get("text") or ""
            translated_text = r.get("translated_text") or r.get("result") or ""
            source_lang = r.get("from_lang") or r.get("from") or ""
            target_lang = r.get("to_lang") or r.get("to") or ""
            ts = r.get("created_at") or r.get("updated_at")
            # timestamp 统一为毫秒整数（手机端 localStorage 用 number 存储）
            if isinstance(ts, str):
                try:
                    ts = int(_dt.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
                except Exception:
                    logger.exception("解析翻译历史时间戳失败")
                    ts = 0
            elif isinstance(ts, (int, float)):
                # 若已是秒级则转毫秒
                if ts < 1e12:
                    ts = int(ts * 1000)
                else:
                    ts = int(ts)
            else:
                ts = 0
            items.append(
                {
                    "source": source_lang,
                    "target": target_lang,
                    "sourceText": source_text,
                    "translatedText": translated_text,
                    "timestamp": ts,
                }
            )
        # 帧守卫（2026-10-15）：limit<=0 从"全量"收紧为默认 50，上限 clamp 2000
        items = items[: clamp_limit(limit, default=50)]
        return items

    registry.register("translations.list", translations_list)
