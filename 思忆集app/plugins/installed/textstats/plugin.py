from __future__ import annotations

from typing import Any

from plugins.base import ToolPlugin
from plugins.manifest import PluginManifest


def _count_cjk(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


class TextStatsPlugin(ToolPlugin):
    """文本统计工具插件。

    统计给定文本的：总字符数、可见字符数、CJK 字数、拉丁词数、总词数
    （CJK 字 + 拉丁词）、段落数、以及按阅读速度估算的阅读时长（分钟）。
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        if manifest is None:
            manifest = PluginManifest(
                manifest_version="1.0",
                id="com.siyi.textstats",
                name="文本统计",
                version="0.1.0",
                type="tool",
                entry_point="plugins.installed.textstats.plugin:TextStatsPlugin",
                description="统计文本字数、字符数、段落数与预计阅读时间。",
                permissions=(),
                capabilities=("text.stats",),
                config_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "待统计的文本"},
                        "reading_wpm": {
                            "type": "integer",
                            "description": "阅读速度（字/分钟），默认 300",
                            "default": 300,
                        },
                    },
                    "required": ["text"],
                },
            )
        super().__init__(manifest)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        text = params.get("text", "") or ""
        if not isinstance(text, str):
            text = str(text)

        reading_wpm = params.get("reading_wpm") or 300
        try:
            reading_wpm = int(reading_wpm)
        except (TypeError, ValueError):
            reading_wpm = 300
        if reading_wpm <= 0:
            reading_wpm = 300

        import re

        stripped = text.strip()
        chars = len(text)
        whitespace = sum(1 for ch in text if ch in " \t\n\r\f\v")
        visible_chars = chars - whitespace
        cjk = _count_cjk(text)
        # latin 词：仅统计含 ASCII 字母的空白分词，避免把 CJK 词串误计入
        latin_words = len([w for w in stripped.split() if re.search(r"[A-Za-z]", w)])
        word_count = cjk + latin_words
        paragraphs = [p for p in stripped.split("\n") if p.strip()]
        paragraph_count = len(paragraphs)
        minutes = word_count / reading_wpm if reading_wpm else 0.0
        # 空文本/零词不报阅读时长；+0.05 仅为向上取整避免 0.0 显示过于跳动
        reading_minutes = 0.0 if word_count == 0 else round(minutes + 0.05, 1)

        return {
            "chars": chars,
            "visible_chars": visible_chars,
            "cjk_chars": cjk,
            "latin_words": latin_words,
            "word_count": word_count,
            "paragraphs": paragraph_count,
            "reading_wpm": reading_wpm,
            "reading_minutes": reading_minutes,
        }


Plugin = TextStatsPlugin
