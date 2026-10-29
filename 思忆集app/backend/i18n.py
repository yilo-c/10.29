"""Backend message i18n — translation table for error/status strings returned to clients.

Design goals (lightweight, no heavy dependencies):
- A flat dict keyed by message id, with one entry per supported locale.
- `t(key, **params)` returns a formatted string in the current locale.
- Current locale is resolved via a registered provider (defaults to `zh-CN`).
  The provider is set once by `app_runtime` to read `ui_language` from the
  settings repository; tests can override it directly.
- Missing key → returns the key itself (caller still gets a stable, greppable string).
- Missing locale entry → falls back to `zh-CN`.

This is intentionally **not** a generic gettext-style framework — backend
error messages are small in count and Chinese is the default surface.
"""

from __future__ import annotations

from typing import Callable, Mapping

DEFAULT_LOCALE = "zh-CN"
SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en")

# Translation table: { message_key: { locale: template } }
# Templates use `str.format(**params)` placeholders, e.g. "{agent_id}".
_TABLE: dict[str, dict[str, str]] = {
    # ── Common / shared error messages ────────────────────────────
    "error.missing_param": {
        "zh-CN": "缺少 {name} 参数",
        "en": "Missing parameter: {name}",
    },
    "error.invalid_param": {
        "zh-CN": "参数 {name} 无效",
        "en": "Invalid parameter: {name}",
    },
    # ── Memory 管理（U3，S3-5） ───────────────────────────────────
    "memory.service_unavailable": {
        "zh-CN": "记忆服务未初始化",
        "en": "Memory service not initialized",
    },
    "memory.entry_not_found": {
        "zh-CN": "未找到记忆条目 {entry_id}",
        "en": "Memory entry {entry_id} not found",
    },
    "memory.entry_not_recoverable": {
        "zh-CN": "记忆条目 {entry_id} 不存在或未被撤销，无法恢复",
        "en": "Memory entry {entry_id} not found or not revoked; nothing to restore",
    },
    # ── AgentDirectory / CronService bootstrap ────────────────────
    "agent.directory_unavailable": {
        "zh-CN": "AgentDirectory 未初始化",
        "en": "AgentDirectory not initialized",
    },
    "agent.cron_unavailable": {
        "zh-CN": "CronService 未初始化",
        "en": "CronService not initialized",
    },
    "agent.not_registered": {
        "zh-CN": "Agent '{agent_id}' 未注册",
        "en": "Agent '{agent_id}' is not registered",
    },
    "agent.unavailable": {
        "zh-CN": "Agent '{agent_id}' 当前不可用",
        "en": "Agent '{agent_id}' is currently unavailable",
    },
    # ── Remote capability domains ─────────────────────────────────
    "remote.desktop_domain_unavailable": {
        "zh-CN": "desktop capability domain 不可用",
        "en": "desktop capability domain unavailable",
    },
    "remote.shell_domain_unavailable": {
        "zh-CN": "shell capability domain 不可用",
        "en": "shell capability domain unavailable",
    },
    # ── Provider management ──────────────────────────────────────
    "provider.added": {
        "zh-CN": "已添加 {name}",
        "en": "Added {name}",
    },
    "provider.removed": {
        "zh-CN": "已删除 {name}",
        "en": "Removed {name}",
    },
    "provider.updated": {
        "zh-CN": "已更新 {name}",
        "en": "Updated {name}",
    },
    "provider.not_found": {
        "zh-CN": "提供商 '{name}' 未找到",
        "en": "Provider '{name}' not found",
    },
    "kiro.not_running": {
        "zh-CN": "Kiro-gateway 未运行",
        "en": "Kiro-gateway not running",
    },
}


# Locale provider: a no-arg callable returning the current locale string.
_locale_provider: Callable[[], str] = lambda: DEFAULT_LOCALE


def register_locale_provider(provider: Callable[[], str]) -> None:
    """Register the function used to resolve the current locale.

    Wired up by `app_runtime` during AppContext setup to read `ui_language`
    from the settings repository (with caching at the caller's discretion).
    """
    global _locale_provider
    _locale_provider = provider


def get_current_locale() -> str:
    """Return the current locale, falling back to DEFAULT_LOCALE on error."""
    try:
        loc = _locale_provider() or DEFAULT_LOCALE
    except Exception:
        loc = DEFAULT_LOCALE
    if loc not in SUPPORTED_LOCALES:
        # Permissive: accept short prefixes like 'zh', 'en-US'
        if loc.startswith("zh"):
            return "zh-CN"
        if loc.startswith("en"):
            return "en"
        return DEFAULT_LOCALE
    return loc


def t(key: str, /, **params: object) -> str:
    """Translate `key` into the current locale.

    Falls back to `zh-CN` if the requested locale is missing for this key,
    and to the key itself if the key is unknown. Unfilled placeholders are
    preserved verbatim (so callers can still debug missing params).
    """
    entry = _TABLE.get(key)
    if entry is None:
        return key
    locale = get_current_locale()
    template = entry.get(locale) or entry.get(DEFAULT_LOCALE) or key
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


# Convenience accessor — handy for tests / introspection.
def all_keys() -> Mapping[str, dict[str, str]]:
    return _TABLE


__all__ = [
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "register_locale_provider",
    "get_current_locale",
    "t",
    "all_keys",
]
