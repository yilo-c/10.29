from __future__ import annotations

import logging
from typing import Any

from ._protocols import *

logger = logging.getLogger("siyi.repositories")


def _coerce_bool(value: Any) -> tuple[bool | None, str | None]:
    """尝试把任意值转换为 bool。

    接受：True/False、"true"/"false"/"1"/"0"/"yes"/"no"（不区分大小写）。
    返回 ``(coerced, error)``：成功时 error 为 None；失败时 coerced 为 None。
    """
    if isinstance(value, bool):
        return value, None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True, None
        if low in ("false", "0", "no", "off", ""):
            return False, None
    if isinstance(value, int) and value in (0, 1):
        return bool(value), None
    return None, f"无法转为 bool: {value!r}"


def _coerce_int(value: Any) -> tuple[int | None, str | None]:
    """尝试把任意值转换为 int。接受 int 或数字字符串。"""
    if isinstance(value, bool):
        # bool 是 int 子类，但语义上不应作为 int 写入
        return None, f"bool 不应作为 int 写入: {value!r}"
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        if value.is_integer():
            return int(value), None
        return None, f"浮点数无法转为 int: {value!r}"
    if isinstance(value, str):
        try:
            return int(value.strip()), None
        except ValueError:
            return None, f"字符串无法转为 int: {value!r}"
    return None, f"无法转为 int: {value!r}"


def _coerce_float(value: Any) -> tuple[float | None, str | None]:
    """尝试把任意值转换为 float。"""
    if isinstance(value, bool):
        return None, f"bool 不应作为 float 写入: {value!r}"
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, str):
        try:
            return float(value.strip()), None
        except ValueError:
            return None, f"字符串无法转为 float: {value!r}"
    return None, f"无法转为 float: {value!r}"


class LocalSettingsRepository:
    # Implements SettingsPort (via data/_protocols.py)

    """本地设置仓储，带缓存，避免重复读盘。

    P2-6: set_setting 现在会对已知 key 做类型校验：
    - 已知 BOOL/INT/FLOAT key：尝试自动类型转换（"true"->True, "60"->60）
    - 转换失败：记录 warning 日志，**仍写入原值**（向后兼容，避免阻塞用户）
    - DEPRECATED_KEYS：记录 warning 但允许写入
    - 未知 key：记录 info 日志，允许写入（支持动态 key 扩展）

    严格校验请用 :meth:`validate_setting`，它返回 ``(ok, error)`` 元组。
    """

    def __init__(self, data_store):
        self.data_store = data_store
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """读取 settings.json 并缓存。"""
        if self._cache is None:
            self._cache = self.data_store.load_json("settings", {}) if self.data_store else {}
        return self._cache

    def reload(self) -> None:
        """清除缓存，下次 get_setting 重新读盘。"""
        self._cache = None

    def get_setting(self, key: str, default: Any) -> Any:
        settings = self._load()
        return settings.get(key, default)

    # ── P2-6: 校验 + 类型转换 ──────────────────────────────

    def validate_setting(self, key: str, value: Any) -> tuple[bool, str | None]:
        """P2-6: 校验 key 存在性与值类型，不写入。

        Args:
            key: 设置键名
            value: 待校验的值

        Returns:
            ``(ok, error)`` — ok=True 表示校验通过（或值已被自动转换到合法类型）；
            ok=False 时 error 描述原因。
        """
        if not key or not isinstance(key, str):
            return False, "key 必须是非空字符串"

        # 延迟导入避免循环依赖
        try:
            from core.settings_keys import (
                BOOL_KEYS, INT_KEYS, FLOAT_KEYS, STRING_KEYS,
                LIST_KEYS, DICT_KEYS, DEPRECATED_KEYS,
                KEY_AGENT_KERNEL, validate_agent_kernel_settings,
            )
        except Exception:
            # schema 不可用时跳过校验（不阻塞写入）
            return True, None

        # 废弃键：警告但允许（向后兼容）
        if key in DEPRECATED_KEYS:
            logger.warning("P2-6 写入废弃 key: %s（已迁移到 credential_store/provider_configs）", key)

        # agent_kernel：一等设置（F/H 项），严格校验 per_request_selection + mcp_tool_policy
        if key == KEY_AGENT_KERNEL:
            ok, err = validate_agent_kernel_settings(value)
            if not ok:
                return False, err
            return True, None

        # 已知 BOOL key：尝试转换
        if key in BOOL_KEYS:
            coerced_bool, err = _coerce_bool(value)
            if err is not None:
                return False, f"key={key} 期望 bool，{err}"
            return True, None

        # 已知 INT key：尝试转换
        if key in INT_KEYS:
            coerced_int, err = _coerce_int(value)
            if err is not None:
                return False, f"key={key} 期望 int，{err}"
            return True, None

        # 已知 FLOAT key：尝试转换
        if key in FLOAT_KEYS:
            coerced_float, err = _coerce_float(value)
            if err is not None:
                return False, f"key={key} 期望 float，{err}"
            return True, None

        # 已知 STRING key：检查可转字符串
        if key in STRING_KEYS:
            if not isinstance(value, (str, int, float)):
                return False, f"key={key} 期望 string，实际 {type(value).__name__}"
            return True, None

        # 已知 LIST key：检查 list
        if key in LIST_KEYS:
            if not isinstance(value, list):
                return False, f"key={key} 期望 list，实际 {type(value).__name__}"
            return True, None

        # 已知 DICT key：检查 dict
        if key in DICT_KEYS:
            if not isinstance(value, dict):
                return False, f"key={key} 期望 dict，实际 {type(value).__name__}"
            return True, None

        # 未知 key：允许但记录 info（支持动态 key 扩展）
        logger.info("P2-6 写入未知 settings key: %s（不在 schema 中，允许但建议注册）", key)
        return True, None

    def _coerce_value(self, key: str, value: Any) -> Any:
        """P2-6: 对已知 key 尝试类型转换，返回转换后的值。

        转换失败时原样返回（向后兼容）。
        """
        try:
            from core.settings_keys import BOOL_KEYS, INT_KEYS, FLOAT_KEYS
        except Exception:
            return value

        try:
            if key in BOOL_KEYS:
                coerced_bool, _ = _coerce_bool(value)
                return coerced_bool if coerced_bool is not None else value
            if key in INT_KEYS:
                coerced_int, _ = _coerce_int(value)
                return coerced_int if coerced_int is not None else value
            if key in FLOAT_KEYS:
                coerced_float, _ = _coerce_float(value)
                return coerced_float if coerced_float is not None else value
        except Exception:
            pass
        return value

    def set_setting(self, key: str, value: Any) -> None:
        if self.data_store is None:
            return

        # P2-6: 校验 + 类型转换
        ok, err = self.validate_setting(key, value)
        if not ok:
            # 严重类型错误：记录 error，但仍写入原值（避免阻塞用户操作）
            # handler 层可通过 validate_setting 获取错误并返回前端
            logger.error("P2-6 settings 类型校验失败: %s（仍写入原值以保持兼容）", err)
        else:
            # 校验通过：尝试类型转换（"true" -> True, "60" -> 60）
            value = self._coerce_value(key, value)

        settings = self._load()
        settings[key] = value
        self.data_store.save_json("settings", settings)
