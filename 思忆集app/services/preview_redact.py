# -*- coding: utf-8 -*-
"""审批预览脱敏（P1-C，2026-09-18）。

背景（实测取证 deliverables/shell-capability-test-2026-09-18.md §3 P1-C）：
settings.set("llm_api_key", …) 与 provider.update 被 R4 拦成 pending 时，
审批 preview 的 args 整包原样回显 —— API Key 明文经 WS 泄漏到任何能列
pending 的面（list_pending / sandbox.pending_added 事件）。

设计：
  * 只脱敏 **preview**（纯展示面）；sandbox.add 单独存的 args 不动
    （approve 后 skip_approval 重放必须用原值）。
  * 命中敏感键名 → 值替换为 mask_key 形态（sk-9b87…c0c0）；非字符串值
    命中键名也遮（可能整体是凭据对象）。
  * 递归处理嵌套 dict/list，返回深拷贝，绝不改动入参。
  * 另兜一层值形态：短前缀+高熵串（sk-/ghp_/xoxb-/AIza…）即便键名不敏感也遮。

判据（棘轮教训）：敏感值泄漏类 bug 再出现第二次 → 升级为全局出参过滤。
本模块是唯一真源，三个构造点（method_registry / react_executor /
_dynamic_tools）一律经 redact_preview_args。
"""

from __future__ import annotations

import re
from typing import Any

from services.api_key_service import mask_key

# 键名含这些片段即视为敏感（小写比对，兼容 snake/kebab/camel）
SENSITIVE_KEY_HINTS: tuple[str, ...] = (
    "apikey",
    "api_key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "privatekey",
    "accesskey",
    "authorization",
)

# 值形态兜底：常见凭据前缀 + 足够长度（避免误伤 "auth_mode" 之类短值）
_VALUE_PREFIX_RE = re.compile(r"^(?:sk|pk|ghp|gho|github_pat|xox[baprs]-|AIza|ya29\.|api:)[A-Za-z0-9_\-]{12,}")
# 去分隔符后用于键名匹配
_SEP_RE = re.compile(r"[_\-\s]")


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    compact = _SEP_RE.sub("", lowered)
    return any(h in lowered or h in compact for h in SENSITIVE_KEY_HINTS)


def redact_value_str(value: str) -> str:
    """字符串值：命中凭据形态则脱敏，否则原样返回。"""
    if _VALUE_PREFIX_RE.match(value.strip()):
        return mask_key(value.strip())
    return value


def redact_preview_args(args: Any) -> Any:
    """返回脱敏深拷贝：dict 的敏感键值 + 任意层级的凭据形态字符串。

    注意：不修改入参（重放链依赖原始 args）。
    """
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for k, v in args.items():
            if _key_is_sensitive(str(k)):
                out[k] = mask_key(v) if isinstance(v, str) and v.strip() else "***"
            else:
                out[k] = redact_preview_args(v)
        return out
    if isinstance(args, list):
        return [redact_preview_args(x) for x in args]
    if isinstance(args, str):
        return redact_value_str(args)
    return args


__all__ = ["redact_preview_args", "SENSITIVE_KEY_HINTS"]
