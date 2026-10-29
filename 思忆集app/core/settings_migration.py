from __future__ import annotations

import copy
import logging
from typing import Any

from core.action_guard import (
    KEY_ACTION_GUARD_PROFILE,
    KEY_ACTION_RISK_ENFORCEMENT,
    KEY_ACTION_RISK_WARN_ONLY,
    KEY_ACTION_WHITELIST,
    KEY_COMMAND_WHITELIST,
    normalize_command_whitelist,
    normalize_enforcement,
    normalize_profile,
    normalize_whitelist,
)
from core.settings_keys import (
    AGENT_KERNEL_PER_REQUEST_SELECTION,
    BOOL_KEYS,
    DICT_KEYS,
    FLOAT_KEYS,
    INT_KEYS,
    KEY_AGENT_KERNEL,
    KEY_AI_AUTO_TAG_ENABLED,
    KEY_ASSISTANT_FOCUS_TOPICS,
    KEY_INBOX_AUTO_CLASSIFY,
    KEY_PROVIDER_CONFIGS,
    LIST_KEYS,
    SETTINGS_DEFAULTS,
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SCHEMA_VERSION_KEY,
    STRING_KEYS,
)

logger = logging.getLogger("siyi.settings_migration")


def _to_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _to_str_list(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    result.append(stripped)
            else:
                result.append(item)  # 保留非字符串元素（如 dict）不变
        return result
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if value is None:
        return list(default)
    text = str(value).strip()
    return [text] if text else list(default)


def _to_dict(value: Any, default: dict) -> dict:
    """将值规范化为 dict。

    处理 JSON 字符串 → dict、None → 默认值、其他类型 → 默认值。
    非空 dict 直接返回（深拷贝以避免共享引用）。
    """
    if isinstance(value, dict):
        return dict(value)  # 浅拷贝避免外部修改影响存储
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return dict(default)
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return dict(default)
    if value is None:
        return dict(default)
    return dict(default)


def migrate_settings_repository(settings_repository) -> dict[str, Any]:
    data_store = getattr(settings_repository, "data_store", None)
    if data_store is None:
        return {"changed": False, "version": SETTINGS_SCHEMA_VERSION}

    data_dir = getattr(data_store, "base_dir", None)

    def _encrypt(value: str) -> str:
        if not value or not data_dir:
            return value
        try:
            from core.crypto_utils import encrypt

            return encrypt(value, data_dir)
        except Exception:
            return value

    settings: dict[str, Any] = data_store.load_json("settings", {})
    if not isinstance(settings, dict):
        settings = {}

    # P2-1: 迁移前保存快照，失败时回滚
    import copy as _copy

    _snapshot = _copy.deepcopy(settings)

    changed = False

    current_version = int(settings.get(SETTINGS_SCHEMA_VERSION_KEY, 0) or 0)

    # 修复已损坏的 provider_configs：_to_str_list 之前错误地将 dict 转为 str
    provider_configs = settings.get(KEY_PROVIDER_CONFIGS)
    if isinstance(provider_configs, list) and provider_configs:
        has_dict = any(isinstance(item, dict) for item in provider_configs)
        if not has_dict:
            logger.warning("provider_configs 已损坏（dict→str），重置为空列表")
            settings[KEY_PROVIDER_CONFIGS] = []
            changed = True

    # 迁移扁平 LLM 键 → provider_configs（如果 provider_configs 为空但有旧键）
    if not settings.get(KEY_PROVIDER_CONFIGS):
        llm_api_key = settings.get("llm_api_key", "") or ""
        llm_api_base = settings.get("llm_api_base", "") or ""
        llm_model = settings.get("llm_model", "") or ""
        if llm_api_key or llm_api_base or llm_model:
            provider_config: dict[str, Any] = {
                "name": "默认 Provider（从旧配置迁移）",
                "api_key": _encrypt(llm_api_key),
                "api_base": llm_api_base or "https://api.deepseek.com/v1",
                "model": llm_model or "deepseek-v4-flash",
                "enabled": True,
                "is_fallback": False,
                "is_local": False,
                "description": "自动从旧版 llm_api_key/base/model 迁移",
            }
            settings[KEY_PROVIDER_CONFIGS] = [provider_config]
            changed = True
            logger.info("已从扁平 LLM 键迁移至 provider_configs: %s", llm_api_base or "https://api.deepseek.com/v1")

    # F-1: 旧的 inbox_auto_classify 迁移到新的 ai_auto_tag_enabled，避免已启用用户被默认 false 关闭
    if KEY_INBOX_AUTO_CLASSIFY in settings and KEY_AI_AUTO_TAG_ENABLED not in _snapshot:
        settings[KEY_AI_AUTO_TAG_ENABLED] = _to_bool(settings[KEY_INBOX_AUTO_CLASSIFY], False)
        changed = True
        logger.info("已从 inbox_auto_classify 迁移到 ai_auto_tag_enabled: %s", settings[KEY_AI_AUTO_TAG_ENABLED])

    for key, default_value in SETTINGS_DEFAULTS.items():
        if key not in settings:
            # B2 修复：深拷贝可变默认值，避免污染全局 SETTINGS_DEFAULTS
            settings[key] = copy.deepcopy(default_value)
            changed = True

    for key in BOOL_KEYS:
        old = settings.get(key, SETTINGS_DEFAULTS[key])
        new: bool | int | float | str | list[str] | dict[Any, Any] = _to_bool(old, SETTINGS_DEFAULTS[key])
        if old != new:
            settings[key] = new
            changed = True

    for key in INT_KEYS:
        old = settings.get(key, SETTINGS_DEFAULTS[key])
        new = _to_int(old, SETTINGS_DEFAULTS[key])
        if old != new:
            settings[key] = new
            changed = True

    for key in FLOAT_KEYS:
        old = settings.get(key, SETTINGS_DEFAULTS[key])
        new = _to_float(old, SETTINGS_DEFAULTS[key])
        if old != new:
            settings[key] = new
            changed = True

    for key in STRING_KEYS:
        old = settings.get(key, SETTINGS_DEFAULTS[key])
        new = _to_str(old, SETTINGS_DEFAULTS[key])
        if old != new:
            settings[key] = new
            changed = True

    if current_version < SETTINGS_SCHEMA_VERSION:
        for key in LIST_KEYS:
            old = settings.get(key, SETTINGS_DEFAULTS[key])
            new = _to_str_list(old, SETTINGS_DEFAULTS[key])
            if old != new:
                settings[key] = new
                changed = True

    # DICT_KEYS：保证 dict 类型字段的存储值始终是 dict（兼容 JSON 字符串 / None / 损坏值）
    for key in DICT_KEYS:
        old = settings.get(key, SETTINGS_DEFAULTS[key])
        new = _to_dict(old, SETTINGS_DEFAULTS[key])
        if old != new:
            settings[key] = new
            changed = True

    # A1① 存量补键：老配置持久化的 agent_kernel 不含 per_request_selection 键时，
    # routing 的 cfg.get(..., False) 兜底会让"默认翻转 True"只对新装生效。
    # 此处一次性显式补 True；用户显式保存过该键（含 False）的尊重其选择不覆盖。
    agent_kernel_cfg = settings.get(KEY_AGENT_KERNEL)
    if isinstance(agent_kernel_cfg, dict) and AGENT_KERNEL_PER_REQUEST_SELECTION not in agent_kernel_cfg:
        settings[KEY_AGENT_KERNEL] = {
            **agent_kernel_cfg,
            AGENT_KERNEL_PER_REQUEST_SELECTION: True,
        }
        changed = True
        logger.info("agent_kernel 存量补键: per_request_selection=True")

    old_whitelist = settings.get(KEY_ACTION_WHITELIST, SETTINGS_DEFAULTS[KEY_ACTION_WHITELIST])
    new_whitelist = normalize_whitelist(old_whitelist)
    for required_action in (
        "news.lookup",
        "news.briefing",
        "notes.sync",
        "notes.organize",
        "journal.daily_draft",
        "usage.snapshot",
        "upgrade.check",
        "memory.recall",
        "browser.open",
        "file.read",
        "file.write",
        "clipboard.read",
        "clipboard.write",
        "app.launch",
        "shell.run",
        "web.search",
        "web.fetch",
        "cron.list",
        "cron.add",
        "cron.remove",
        # P0/C2: 补充破坏性数据操作到白名单（仍受 _LLM_ELEVATED_ACTIONS 拦截）
        "notes.delete",
        "tasks.delete",
        "inbox.delete",
        # 工具依赖的动作：旧白名单缺失会导致 agent 调用时
        # 报 not_in_whitelist（如 system_info），白白浪费推理步数
        "system.info",
        "translate.text",
        "file.find",
        "file.copy",
        "vocab.quiz_in_chat",
        "vocab.review_status",
        "browser.navigate",
        "browser.click",
        "browser.type",
        "browser.extract",
    ):
        if required_action not in new_whitelist:
            new_whitelist.append(required_action)
    if old_whitelist != new_whitelist:
        settings[KEY_ACTION_WHITELIST] = new_whitelist
        changed = True

    old_command_whitelist = settings.get(KEY_COMMAND_WHITELIST, SETTINGS_DEFAULTS[KEY_COMMAND_WHITELIST])
    new_command_whitelist = normalize_command_whitelist(old_command_whitelist)
    if old_command_whitelist != new_command_whitelist:
        settings[KEY_COMMAND_WHITELIST] = new_command_whitelist
        changed = True

    old_profile = settings.get(KEY_ACTION_GUARD_PROFILE, SETTINGS_DEFAULTS[KEY_ACTION_GUARD_PROFILE])
    new_profile = normalize_profile(old_profile)
    if old_profile != new_profile:
        settings[KEY_ACTION_GUARD_PROFILE] = new_profile
        changed = True

    old_enforcement = settings.get(KEY_ACTION_RISK_ENFORCEMENT, SETTINGS_DEFAULTS[KEY_ACTION_RISK_ENFORCEMENT])
    new_enforcement = normalize_enforcement(old_enforcement)
    if old_enforcement != new_enforcement:
        settings[KEY_ACTION_RISK_ENFORCEMENT] = new_enforcement
        changed = True

    # P0-4: warn_only 安全收敛 — 旧配置中 True 值仅在 dev profile 下保留，
    # 其余 profile 一律重置为 False（与 settings_keys 默认值对齐）
    old_warn_only = settings.get(KEY_ACTION_RISK_WARN_ONLY, SETTINGS_DEFAULTS[KEY_ACTION_RISK_WARN_ONLY])
    if _to_bool(old_warn_only, False) and new_profile != "dev":
        settings[KEY_ACTION_RISK_WARN_ONLY] = False
        changed = True
        logger.info(
            "warn_only 安全收敛: profile=%s, 旧值=%s → 新值=False",
            new_profile,
            old_warn_only,
        )

    normalized_focus_topics = _to_str_list(
        settings.get(KEY_ASSISTANT_FOCUS_TOPICS),
        SETTINGS_DEFAULTS[KEY_ASSISTANT_FOCUS_TOPICS],
    )[:12]
    if settings.get(KEY_ASSISTANT_FOCUS_TOPICS) != normalized_focus_topics:
        settings[KEY_ASSISTANT_FOCUS_TOPICS] = normalized_focus_topics
        changed = True

    if current_version < SETTINGS_SCHEMA_VERSION:
        settings[SETTINGS_SCHEMA_VERSION_KEY] = SETTINGS_SCHEMA_VERSION
        changed = True

    if changed:
        try:
            data_store.save_json("settings", settings)
            logger.info("设置迁移完成: version=%s, changed=%s", SETTINGS_SCHEMA_VERSION, changed)
        except Exception as exc:
            # P2-1: 保存失败时回滚到快照
            logger.error("设置保存失败，回滚到迁移前快照: %s", exc)
            try:
                data_store.save_json("settings", _snapshot)
            except Exception:
                pass  # 回滚也失败，只能记录
            return {"changed": False, "version": int(_snapshot.get(SETTINGS_SCHEMA_VERSION_KEY, 0) or 0), "error": str(exc)}

    from workbench.layout_config import migrate_workbench_layout_config

    _config, layout_changed = migrate_workbench_layout_config(settings_repository)
    if layout_changed:
        changed = True

    return {"changed": changed, "version": SETTINGS_SCHEMA_VERSION}
