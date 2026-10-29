"""PatternActionRegistry — 模式化操作引擎。

提供一组内置操作（翻译、总结、润色、保存笔记），
UI 层的右键菜单/剪贴板检测/悬浮窗按钮共享同一执行入口。
"""

from __future__ import annotations

import logging
import re
from typing import Callable, NamedTuple

logger = logging.getLogger("siyi.assistant.pattern_actions")


class PatternAction(NamedTuple):
    action_id: str
    label: str
    icon: str
    fn: Callable[[str], str]


# ── 内置操作实现 ─────────────────────────────────────


def _translate(text: str, target: str = "zh") -> str:
    """通过本地模型（Qwen2.5）翻译。"""
    try:
        from app_runtime import service_registry

        if service_registry is None:
            return "此操作需由后端 AI 引擎处理"
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        lang = "中文" if target == "zh" else "英文"
        messages = [
            {"role": "system", "content": f"你是一个翻译助手。请将以下文本翻译为{lang}，只返回翻译结果，不要解释。"},
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.1, max_tokens=1024, skip_judge=True)
        return result.strip()
    except Exception as exc:
        logger.warning("translate failed: %s", exc)
        if "Service not found" in str(exc) or "No module named" in str(exc) or "NoneType" in str(exc):
            return "此操作需由后端 AI 引擎处理"
        return f"翻译失败：{exc}"


def _summarize(text: str, max_length: int = 300) -> str:
    """AI 摘要：用简洁语言总结内容。"""
    try:
        from app_runtime import service_registry
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        messages = [
            {"role": "system", "content": "你是一个摘要助手。请用简洁的语言总结以下内容（50字以内），只返回摘要结果。"},
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.3, max_tokens=512, skip_judge=True)
        return result.strip()
    except Exception as exc:
        logger.warning("summarize failed: %s", exc)
        return f"总结失败：{exc}"


def _organize(text: str) -> str:
    """AI 整理：重新组织段落、修正语病、保持原始信息（NoteEditor「整理」入口）。"""
    try:
        from app_runtime import service_registry
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个笔记整理助手。请整理以下笔记内容，要求：\n"
                    "1. 重新组织段落顺序，使其逻辑清晰\n"
                    "2. 修正语病和错别字\n"
                    "3. 适当分段，添加空行分隔\n"
                    "4. 保持所有原始信息，不要删减\n"
                    "5. 不要添加原文没有的新内容\n"
                    "只返回整理后的 Markdown 结果。"
                ),
            },
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.3, max_tokens=2048, skip_judge=True)
        return result.strip()
    except Exception as exc:
        # 失败必须走异常通道（handler 转 error 字段）——若当 result 返回，
        # 前端会把失败文案当成整理结果写回笔记（数据破坏，同"未知操作"教训）
        logger.warning("organize failed: %s", exc)
        raise


def _polish(text: str) -> str:
    """AI 润色：使内容更流畅自然。"""
    try:
        from app_runtime import service_registry
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        messages = [
            {"role": "system", "content": "你是一个润色助手。请润色以下内容，使其更流畅自然，只返回润色结果，不要解释。"},
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.3, max_tokens=1024, skip_judge=True)
        return result.strip()
    except Exception as exc:
        logger.warning("polish failed: %s", exc)
        return f"润色失败：{exc}"


def _continue_writing(text: str) -> str:
    """AI 续写：保持风格继续写作。"""
    try:
        from app_runtime import service_registry
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        messages = [
            {"role": "system", "content": "你是一个写作助手。请继续续写以下内容，保持风格一致，只返回续写内容。"},
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.5, max_tokens=1024, skip_judge=True)
        return result.strip()
    except Exception as exc:
        logger.warning("continue_writing failed: %s", exc)
        return f"续写失败：{exc}"


def _explain(text: str) -> str:
    """通过本地模型解释文本含义或用法。"""
    try:
        from app_runtime import service_registry
        from ai.client._judge_aware_client import JudgeAwareClient

        ai_client = service_registry.resolve(JudgeAwareClient)
        messages = [
            {"role": "system", "content": "你是一个解释助手。请简要解释以下内容的核心含义、用途和关键要点。保持简洁，200字以内。"},
            {"role": "user", "content": text},
        ]
        result = ai_client.complete_text(messages, temperature=0.3, max_tokens=512, skip_judge=True)
        return result.strip()
    except Exception as exc:
        logger.warning("explain failed: %s", exc)
        return f"解释失败：{exc}"


def _save_note(text: str) -> str:
    """保存为笔记。"""
    try:
        from app_runtime import get_app_context

        repo = get_app_context().personal_note_repository
        if repo is None:
            return "笔记服务不可用"
        from datetime import datetime

        note = repo.create_note(
            title=f"快捷笔记 {datetime.now():%m-%d %H:%M}",
            content=text,
            tags=["快捷笔记"],
        )
        title = getattr(note, "title", None) or note.get("title", "未命名")
        return f"已保存到笔记「{title}」"
    except Exception as exc:
        logger.warning("save_note failed: %s", exc)
        return f"保存失败：{exc}"


def _fetch_url_title(text: str) -> str:
    """提取文本中的 URL 并获取页面标题。"""
    urls = re.findall(r"https?://[^\s]+", text)
    if not urls:
        return "未检测到链接"
    results = []
    for url in urls[:3]:
        try:
            import urllib.request

            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read(8192).decode("utf-8", errors="ignore")
                m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
                title = m.group(1).strip() if m else url
                results.append(f"• {title}")
        except Exception:
            results.append(f"• {url}")
    return "\n".join(results)


def _word_count(text: str) -> str:
    """字数统计：中文字数、英文词数、总字符。"""
    chinese_chars = len(re.findall(r"[一-鿿]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    total_chars = len(text)
    lines = text.count("\n") + 1
    return f"📊 字数统计\n  中文字数：{chinese_chars}\n  英文词数：{english_words}\n  总字符：{total_chars}\n  行数：{lines}"


def _extract_keywords(text: str, max_keywords: int = 8) -> str:
    """简单关键词提取：按词频排序（中文分词+英文）。"""
    # 提取中文词组（2-4 字）
    chinese_ngrams = re.findall(r"[一-鿿]{2,4}", text)
    # 提取英文单词（>=3 字母）
    english_words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text)]

    from collections import Counter

    cn_freq = Counter(chinese_ngrams)
    en_freq = Counter(english_words)

    # 过滤停用词
    stopwords = {
        "一个",
        "可以",
        "这个",
        "那个",
        "什么",
        "怎么",
        "没有",
        "不是",
        "就是",
        "我们",
        "他们",
        "它们",
        "因为",
        "所以",
        "如果",
        "虽然",
        "但是",
        "而且",
        "或者",
        "还是",
        "已经",
        "之后",
        "以前",
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "any",
        "can",
        "how",
        "why",
        "its",
        "was",
        "had",
        "has",
        "may",
        "get",
        "use",
    }
    for sw in stopwords:
        cn_freq.pop(sw, None)
        en_freq.pop(sw, None)

    top_cn = [f"{w}({c})" for w, c in cn_freq.most_common(max_keywords // 2)]
    top_en = [f"{w}({c})" for w, c in en_freq.most_common(max_keywords // 2)]

    parts = []
    if top_cn:
        parts.append("中文关键词：" + "、".join(top_cn))
    if top_en:
        parts.append("English：" + ", ".join(top_en))
    if not parts:
        return "未提取到足够的关键词"
    return "🏷 关键词提取\n" + "\n".join(parts)


def _json_format(text: str) -> str:
    """JSON 格式美化。"""
    import json

    text = text.strip()
    # 尝试从 markdown 代码块中提取
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if code_match:
        text = code_match.group(1).strip()
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as exc:
        return f"JSON 解析失败：{exc}"


def _base64_encode(text: str) -> str:
    """Base64 编码。"""
    import base64

    try:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        return encoded
    except Exception as exc:
        return f"Base64 编码失败：{exc}"


def _base64_decode(text: str) -> str:
    """Base64 解码。"""
    import base64

    try:
        decoded = base64.b64decode(text.strip()).decode("utf-8")
        return decoded
    except Exception as exc:
        return f"Base64 解码失败：{exc}"


def _date_convert(text: str) -> str:
    """日期格式转换：识别常见格式，输出多种格式。"""
    from datetime import datetime

    # 常见日期格式匹配
    patterns = [
        (r"(\d{4})-(\d{1,2})-(\d{1,2})", "%Y-%m-%d"),
        (r"(\d{4})/(\d{1,2})/(\d{1,2})", "%Y/%m/%d"),
        (r"(\d{4})年(\d{1,2})月(\d{1,2})日", "%Y年%m月%d日"),
        (r"(\d{1,2})月(\d{1,2})日", "%m月%d日"),
        (r"(\d{4})\.(\d{1,2})\.(\d{1,2})", "%Y.%m.%d"),
    ]
    text = text.strip()
    for pattern, fmt in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                if fmt == "%m月%d日":
                    dt = datetime(2000, int(m.group(1)), int(m.group(2)))
                elif fmt == "%Y.%m.%d":
                    dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                else:
                    dt = datetime.strptime(m.group(0), fmt)
                return (
                    f"📅 日期转换\n"
                    f"  原始：{m.group(0)}\n"
                    f"  ISO：{dt.strftime('%Y-%m-%d')}\n"
                    f"  中文：{dt.strftime('%Y年%m月%d日')}\n"
                    f"  英文：{dt.strftime('%B %d, %Y')}\n"
                    f"  星期：{['周一', '周二', '周三', '周四', '周五', '周六', '周日'][dt.weekday()]}\n"
                    f"  时间戳：{int(dt.timestamp())}"
                )
            except (ValueError, IndexError):
                continue
    return "未识别到日期格式。支持格式：2024-01-01、2024/01/01、2024年1月1日"


def _code_format(text: str) -> str:
    """基础代码格式化：检测语言+统一缩进。"""
    text = text.strip()
    # 从 markdown 代码块提取
    code_match = re.search(r"```(\w+)?\s*([\s\S]*?)```", text)
    if code_match:
        lang = code_match.group(1) or ""
        text = code_match.group(2).strip()
    else:
        lang = ""

    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去掉行尾空白
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    lang_label = f" ({lang})" if lang else ""
    return f"📝 格式化后{lang_label}：\n\n{text}"


# ── 注册表 ──────────────────────────────────────────


class PatternActionRegistry:
    """模式化操作注册表。UI 层通过 execute() 统一调用。"""

    def __init__(self):
        self._actions: dict[str, PatternAction] = {}
        self._register_builtin()

    def _register_builtin(self):
        self.register("continue", "续写", "✍️", _continue_writing)
        self.register("explain", "解释", "💡", _explain)
        self.register("translate", "翻译", "🌐", lambda t: _translate(t))
        self.register("translate_en", "译成英文", "🇬🇧", lambda t: _translate(t, target="en"))
        self.register("summarize", "总结", "🔍", _summarize)
        self.register("polish", "润色", "✏️", _polish)
        self.register("organize", "整理", "🧹", _organize)
        self.register("save_note", "保存笔记", "📌", _save_note)
        self.register("fetch_url", "提取链接", "🔗", _fetch_url_title)
        self.register("word_count", "字数统计", "📊", _word_count)
        self.register("keywords", "提取关键词", "🏷", _extract_keywords)
        self.register("json_format", "JSON 美化", "🔧", _json_format)
        self.register("base64_encode", "Base64 编码", "🔐", _base64_encode)
        self.register("base64_decode", "Base64 解码", "🔓", _base64_decode)
        self.register("date_convert", "日期转换", "📅", _date_convert)
        self.register("code_format", "代码格式化", "💻", _code_format)

    def register(self, action_id: str, label: str, icon: str, fn: Callable[[str], str]):
        self._actions[action_id] = PatternAction(action_id, label, icon, fn)

    def execute(self, action_id: str, text: str) -> str:
        action = self._actions.get(action_id)
        if action is None:
            # 未知 action 必须走异常通道（handler 转为 error 字段）——
            # 若当 result 返回，前端会把错误文案当成整理结果写回笔记（数据破坏）
            raise ValueError(f"未知操作: {action_id}")
        try:
            return action.fn(text)
        except Exception as exc:
            logger.warning("pattern action %s failed: %s", action_id, exc)
            return f"操作「{action.label}」失败：{exc}"

    def menu_items(self) -> list[tuple[str, str, str]]:
        """返回 (action_id, label, icon) 列表，供 UI 层构建菜单。"""
        return [(a.action_id, a.label, a.icon) for a in self._actions.values()]


# 模块级单例
_registry: PatternActionRegistry | None = None


def get_registry() -> PatternActionRegistry:
    global _registry
    if _registry is None:
        _registry = PatternActionRegistry()
    return _registry


def execute(action_id: str, text: str) -> str:
    """快捷入口：通过模块级单例执行操作。"""
    return get_registry().execute(action_id, text)
