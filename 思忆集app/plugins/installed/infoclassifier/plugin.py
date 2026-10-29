from __future__ import annotations

from typing import Any

from plugins.base import ToolPlugin
from plugins.manifest import PluginManifest


class InfoClassifierPlugin(ToolPlugin):
    """信息分类工具插件。

    封装信息收集的核心纯函数（来自 services.info_collector_service）：
    - classify_item：关键词分类，返回 (category, score)
    - has_cjk：检测文本是否含中日韩字符
    - section_keywords：返回 effective 关键词映射（默认 + 用户覆盖 + 自定义板块）

    该插件是「核心能力渐进 plugin 化」的 PoC：业务侧通过 plugins.capability_router
    以「plugin 优先、原生兜底」策略调用，plugin 不可用时自动回退到 services 原实现。
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        if manifest is None:
            manifest = PluginManifest(
                manifest_version="1.0",
                id="com.siyi.infoclassifier",
                name="信息分类器",
                version="0.1.0",
                type="tool",
                entry_point="plugins.installed.infoclassifier.plugin:InfoClassifierPlugin",
                description="信息条目关键词分类、CJK 检测与板块关键词查询。",
                permissions=(),
                capabilities=("info.classify", "info.has_cjk", "info.section_keywords"),
                config_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "调用动作：classify_item | has_cjk | section_keywords",
                            "enum": ["classify_item", "has_cjk", "section_keywords"],
                        },
                        "item": {
                            "type": "object",
                            "description": "classify_item 用的条目（含 title/title_zh/summary/summary_zh/tags）",
                        },
                        "text": {"type": "string", "description": "has_cjk 用的文本"},
                    },
                    "required": ["action"],
                },
            )
        super().__init__(manifest)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "")
        # 延迟导入，避免插件在未安装 services 的环境被加载时硬失败
        from services.info_collector_service import (
            categorize_item,
            get_section_keywords,
            has_cjk,
        )

        if action == "classify_item":
            item = params.get("item") or {}
            if not isinstance(item, dict):
                item = {}
            category, score = categorize_item(item)
            return {"category": category, "score": score}
        if action == "has_cjk":
            text = params.get("text", "") or ""
            return {"has_cjk": bool(has_cjk(text))}
        if action == "section_keywords":
            return {"keywords": get_section_keywords()}
        return {"error": f"unknown action: {action}"}


Plugin = InfoClassifierPlugin
