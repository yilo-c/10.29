from __future__ import annotations

from typing import Any

from plugins.base import ToolPlugin
from plugins.manifest import PluginManifest


class DedupPlugin(ToolPlugin):
    """信息去重工具插件。

    封装信息收集的去重纯函数（来自 services.info_collector_service）：
    - dedup_items：基于 URL 精确去重 + 标题相似度模糊去重，返回去重后列表
    - title_similarity：两段标题的相似度（0~1，基于字符集合 Jaccard）

    与 infoclassifier 同属「核心能力渐进 plugin 化」家族：业务侧通过
    plugins.capability_router 以「plugin 优先、原生兜底」策略调用，plugin
    不可用时自动回退到 services 原实现，零行为回归。
    """

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        if manifest is None:
            manifest = PluginManifest(
                manifest_version="1.0",
                id="com.siyi.dedup",
                name="信息去重器",
                version="0.1.0",
                type="tool",
                entry_point="plugins.installed.dedup.plugin:DedupPlugin",
                description="信息条目 URL/标题相似度去重与标题相似度计算。",
                permissions=(),
                capabilities=("info.dedup", "info.title_similarity"),
                config_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "调用动作：dedup_items | title_similarity",
                            "enum": ["dedup_items", "title_similarity"],
                        },
                        "items": {
                            "type": "array",
                            "description": "dedup_items 用的条目列表（每项含 url/title）",
                        },
                        "a": {"type": "string", "description": "title_similarity 的标题 A"},
                        "b": {"type": "string", "description": "title_similarity 的标题 B"},
                    },
                    "required": ["action"],
                },
            )
        super().__init__(manifest)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "")
        # 延迟导入，避免插件在未安装 services 的环境被加载时硬失败
        from services.info_collector_service import (
            DEDUP_SIMILARITY_THRESHOLD,
            dedup_items,
            title_similarity,
        )

        if action == "dedup_items":
            items = params.get("items") or []
            if not isinstance(items, list):
                items = []
            # T3.6：改用公共别名 dedup_items（不再依赖私有静态方法）
            return {"items": dedup_items(list(items))}
        if action == "title_similarity":
            a = params.get("a", "") or ""
            b = params.get("b", "") or ""
            return {
                "similarity": float(title_similarity(str(a), str(b))),
                "threshold": float(DEDUP_SIMILARITY_THRESHOLD),
            }
        return {"error": f"unknown action: {action}"}


Plugin = DedupPlugin
