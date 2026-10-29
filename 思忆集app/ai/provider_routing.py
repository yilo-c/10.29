"""多模型提供商路由表。

从 ai_service.py 提取，职责：
- _PROVIDER_ROUTING_PRESETS 预设模板
- ProviderRoutingTable 按查询类型自动选择提供商
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ai.client.base import OpenAICompatibleClient
from core.settings_schema import KEY_LLM_PROVIDER_ROUTING

logger = logging.getLogger("siyi.ai.routing")

# ── 预设提供商模板 ────────────────────────────────────

_PROVIDER_ROUTING_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "display_name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        # 官方模型名：deepseek-v4-flash（2026-07-24 起 deepseek-chat / deepseek-reasoner 已退役）
        "default_model": "deepseek-v4-flash",
        "best_for": ("reasoning", "coding", "analysis"),
        "env_key": "DEEPSEEK_API_KEY",
    },
    "glm": {
        "display_name": "GLM-4 (智谱)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4",
        "best_for": ("chinese_knowledge", "general_chat"),
        "env_key": "GLM_API_KEY",
    },
    "moonshot": {
        "display_name": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "best_for": ("general_chat", "summarization"),
        "env_key": "MOONSHOT_API_KEY",
    },
    "qwen": {
        "display_name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-turbo",
        "best_for": ("chinese_knowledge", "general_chat"),
        "env_key": "DASHSCOPE_API_KEY",
    },
}


# ── 路由表 ────────────────────────────────────────────


class ProviderRoutingTable:
    """根据查询类型自动选择最佳模型提供商。

    在 config.yaml 或设置中配置 llm_provider_routing 为 JSON 字符串，格式：
    {"reasoning": "deepseek", "chinese_knowledge": "glm", "default": "primary"}
    或留空使用默认（主服务处理一切）。
    """

    _QUERY_TYPE_CUES: dict[str, tuple[str, ...]] = {
        "reasoning": ("推理", "分析", "为什么", "原因", "逻辑", "证明", "推导", "计算"),
        "coding": ("代码", "编程", "函数", "bug", "debug", "重构", "实现", "算法", "python", "javascript"),
        "chinese_knowledge": ("中国", "中文", "历史", "古诗", "成语", "文言", "诗词"),
        "summarization": ("总结", "摘要", "概括", "归纳", "提炼"),
    }

    def __init__(self, settings_repository=None, environ=None):
        self._settings = settings_repository
        self._env = environ if environ is not None else os.environ
        self._clients: dict[str, OpenAICompatibleClient] = {}
        self._routing: dict[str, str] = {}
        self._load_routing()

    def _load_routing(self):
        """从设置或环境变量加载路由配置。"""
        raw = ""
        if self._settings:
            raw = self._settings.get_setting(KEY_LLM_PROVIDER_ROUTING, "")
        if not raw:
            raw = self._env.get("SIYI_LLM_PROVIDER_ROUTING", "")
        if raw and isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    self._routing = parsed
                else:
                    logger.warning("llm_provider_routing 必须是字典")
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("llm_provider_routing 解析失败: %s", exc.__class__.__name__)

    def classify_query(self, message: str) -> str:
        """将用户消息分类为查询类型。"""
        normalized = str(message or "").lower()
        for query_type, cues in self._QUERY_TYPE_CUES.items():
            if any(cue in normalized for cue in cues):
                return query_type
        return "general_chat"

    def get_client_for_query(
        self,
        message: str,
        primary: OpenAICompatibleClient,
    ) -> OpenAICompatibleClient:
        """根据查询类型返回最佳客户端。如果无匹配或未配置，返回主客户端。"""
        if not self._routing:
            return primary
        query_type = self.classify_query(message)
        provider_name = self._routing.get(query_type) or self._routing.get("default", "primary")
        if provider_name == "primary":
            return primary
        return self._get_or_create_client(provider_name, primary)

    def _get_or_create_client(
        self,
        provider_name: str,
        fallback: OpenAICompatibleClient,
    ) -> OpenAICompatibleClient:
        """懒初始化提供商客户端。"""
        if provider_name in self._clients:
            return self._clients[provider_name]
        preset = _PROVIDER_ROUTING_PRESETS.get(provider_name)
        if not preset:
            return fallback
        api_key = self._env.get(preset["env_key"], "")
        if not api_key:
            logger.debug("提供商 %s 的 API Key 未配置 (%s)", provider_name, preset["env_key"])
            return fallback
        client = OpenAICompatibleClient(
            api_key=api_key,
            api_base=preset["base_url"],
            model=preset["default_model"],
            timeout=60,
        )
        self._clients[provider_name] = client
        return client

    def list_available_providers(self) -> list[dict[str, Any]]:
        """列出所有已配置的提供商。"""
        available = []
        for name, preset in _PROVIDER_ROUTING_PRESETS.items():
            api_key = self._env.get(preset["env_key"], "")
            available.append({
                "provider": name,
                "display_name": preset["display_name"],
                "configured": bool(api_key),
                "best_for": list(preset["best_for"]),
                "model": preset["default_model"],
            })
        return available

    # ── P2.6 动态路由决策 ────────────────────────────────

    def get_routing_config(self) -> dict[str, str]:
        """返回当前路由配置（已解析的 dict）。"""
        return dict(self._routing)

    def is_enabled(self) -> bool:
        """是否启用了路由表（配置非空）。"""
        return bool(self._routing)

    def decide(
        self,
        message: str,
        health_monitor: Any = None,
        available_provider_names: list[str] | None = None,
    ) -> tuple[str, str] | None:
        """P2.6 动态路由决策：根据查询类型选择 provider。

        Args:
            message: 用户输入文本
            health_monitor: 可选的 ``ProviderHealthMonitor``，若指定则跳过不可用 provider
            available_provider_names: 可选的 provider 名称白名单
                （如 ``provider_configs`` 中已启用的 provider 名）

        Returns:
            ``(provider_name, model_name)`` 或 ``None``（表示走默认回退链）。
            ``model_name`` 可能为空字符串，由调用方按 provider 配置解析默认模型。
        """
        if not self._routing:
            return None
        query_type = self.classify_query(message)
        provider_name = self._routing.get(query_type) or self._routing.get("default", "")
        if not provider_name or provider_name == "primary":
            return None

        # 健康检查：若 provider 不可用则回退到默认链
        if health_monitor is not None:
            try:
                if not health_monitor.is_available(provider_name):
                    logger.info(
                        "dynamic routing: provider '%s' unavailable, falling back to default chain",
                        provider_name,
                    )
                    return None
            except Exception as exc:
                logger.debug(
                    "dynamic routing health check failed for '%s': %s",
                    provider_name, exc,
                )

        # 白名单检查（如来自 provider_configs）
        # 注意：preset provider（如 deepseek/glm/moonshot/qwen）有独立的 env_key 配置，
        # 不需要在 provider_configs 白名单中。只有非 preset 的自定义 provider 才需要白名单校验。
        if (
            available_provider_names is not None
            and available_provider_names  # 非空列表才校验
            and provider_name not in available_provider_names
            and provider_name not in _PROVIDER_ROUTING_PRESETS  # preset 豁免
        ):
            logger.info(
                "dynamic routing: provider '%s' not in available list, skipping",
                provider_name,
            )
            return None

        # 解析 model：preset 默认模型，否则空字符串（由调用方解析）
        preset = _PROVIDER_ROUTING_PRESETS.get(provider_name)
        model_name = preset["default_model"] if preset else ""
        return (provider_name, model_name)

    def preview(self, message: str, health_monitor: Any = None, available_provider_names: list[str] | None = None) -> dict[str, Any]:
        """预览路由决策（诊断用）。

        返回 dict 包含 ``query_type``、``matched_provider``、``decision``、
        ``available_providers`` 等字段，便于前端展示。
        """
        query_type = self.classify_query(message)
        decision = self.decide(message, health_monitor, available_provider_names)
        return {
            "query_type": query_type,
            "routing_config": self.get_routing_config(),
            "matched_provider": decision[0] if decision else None,
            "matched_model": decision[1] if decision else None,
            "will_use_dynamic_routing": decision is not None,
            "fallback_reason": None if decision else (
                "routing_disabled" if not self._routing
                else "provider_unavailable_or_not_listed"
            ),
            "available_presets": list(_PROVIDER_ROUTING_PRESETS.keys()),
        }


# ── P2.6 辅助：从 provider_configs 提取可用 provider 名称 ─────────


def list_enabled_provider_names(settings_repository: Any) -> list[str]:
    """从 ``provider_configs`` 读取所有已启用的 provider 名称。

    供 ``ProviderRoutingTable.decide`` 的 ``available_provider_names`` 参数使用，
    以避免路由到一个已被用户禁用的 provider。
    """
    if settings_repository is None:
        return []
    try:
        from core.provider_config import load_providers
        providers = load_providers(settings_repository)
        return [p.name for p in providers if p.enabled]
    except Exception as exc:
        logger.debug("list_enabled_provider_names failed: %s", exc)
        return []


def parse_routing_config(raw: str) -> dict[str, str]:
    """解析 ``llm_provider_routing`` 配置字符串。

    接受 JSON 字符串或空字符串。返回 ``dict[query_type -> provider_name]``。
    无效输入返回空 dict 并记录警告。
    """
    if not raw or not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning("llm_provider_routing 必须是字典")
            return {}
        # 值必须为字符串
        cleaned: dict[str, str] = {}
        for k, v in parsed.items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                cleaned[k] = v.strip()
        return cleaned
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("llm_provider_routing 解析失败: %s", exc.__class__.__name__)
        return {}


def validate_routing_config(config: dict[str, Any]) -> tuple[bool, str]:
    """校验路由配置 dict 是否合法。

    合法条件：
    - 顶层是 dict
    - 所有 key/value 都是字符串
    - value 非空
    - value 为 "primary" 或在 ``_PROVIDER_ROUTING_PRESETS`` 中（preset）
      （provider_configs 中的 provider 名运行时校验，这里放行）
    """
    if not isinstance(config, dict):
        return False, "配置必须是字典"
    if not config:
        return True, "空配置（禁用动态路由）"
    known_preset_names = set(_PROVIDER_ROUTING_PRESETS.keys()) | {"primary"}
    for k, v in config.items():
        if not isinstance(k, str) or not k.strip():
            return False, f"键 '{k}' 必须是非空字符串"
        if not isinstance(v, str) or not v.strip():
            return False, f"键 '{k}' 的值必须是字符串"
    # 仅警告未知 provider 名（可能是 provider_configs 中的自定义 provider）
    unknown = [v for v in config.values() if v not in known_preset_names]
    if unknown:
        logger.info(
            "routing config references non-preset providers (will validate against provider_configs at runtime): %s",
            unknown,
        )
    return True, ""
