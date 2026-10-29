# -*- coding: utf-8 -*-
"""环境变量安全加载工具（P2-3）。

提供：
1. :func:`load_env_file` — 安全加载 .env 文件，不打印 value 明文
2. :func:`mask_secret` — 脱敏显示密钥（用于日志中的安全显示）

与 :mod:`core.exception_handler` 的 :func:`sanitize_text` 区别：
- ``sanitize_text`` 用于"完全脱敏"日志中的敏感值（替换为 ***REDACTED***）
- ``mask_secret`` 用于"有意显示密钥前缀"的场景（如 "sk-ab***"），便于排查 key 配置问题

使用示例：
    from core.env_loader import load_env_file, mask_secret

    loaded = load_env_file(Path(".env"))
    print(f"[env] loaded {len(loaded)} keys from .env")

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    print(f"key prefix: {mask_secret(key)}")  # sk-ab***
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

#: mask_secret 默认可见前缀长度
_DEFAULT_VISIBLE_PREFIX: Final = 4
#: 值长度低于该值时全部脱敏（前缀太短没有排查价值，反而泄露比例过高）
_MIN_LENGTH_FOR_PREFIX: Final = 12
#: 敏感 key 关键词（小写匹配）
_SENSITIVE_KEY_WORDS: Final = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def _is_sensitive_key(key: str) -> bool:
    """判断 key 是否为敏感 key（需要脱敏显示）。"""
    key_lower = key.lower()
    return any(word in key_lower for word in _SENSITIVE_KEY_WORDS)


def mask_secret(value: str, visible_prefix: int = _DEFAULT_VISIBLE_PREFIX) -> str:
    """脱敏显示密钥值，保留前几位便于排查。

    Args:
        value: 原始密钥值
        visible_prefix: 保留的前缀长度（默认 4）

    Returns:
        脱敏后的字符串，如 "sk-a***"

    - 空值返回 "<empty>"
    - 值长度 < _MIN_LENGTH_FOR_PREFIX 时全部脱敏为 "***"
    - 否则保留前 visible_prefix 位 + "***"
    """
    if not value:
        return "<empty>"
    s = str(value)
    if len(s) < _MIN_LENGTH_FOR_PREFIX:
        return "***"
    return f"{s[:visible_prefix]}***"


def load_env_file(env_path: str | Path, *, override: bool = False) -> list[str]:
    """安全加载 .env 文件到 ``os.environ``。

    Args:
        env_path: .env 文件路径
        override: 是否覆盖已存在的环境变量（默认 False，使用 setdefault 语义）

    Returns:
        成功加载的 key 列表（不包含 value，避免泄露）

    解析规则：
    - 跳过空行和 ``#`` 注释行
    - ``KEY=VALUE`` 格式，value 去除首尾空白和引号
    - 使用 ``os.environ.setdefault``（除非 override=True）

    安全保证：
    - 不打印 value 明文
    - 返回值只包含 key 列表
    - 加载日志只输出 key 数量和路径
    """
    path = Path(env_path)
    if not path.is_file():
        return []

    loaded_keys: list[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"\'').strip()
                if not key:
                    continue
                if override:
                    os.environ[key] = val
                else:
                    os.environ.setdefault(key, val)
                loaded_keys.append(key)
    except OSError:
        return []
    return loaded_keys


def get_env_masked(key: str, default: str = "") -> str:
    """读取环境变量并返回脱敏版本（用于日志）。

    注意：此函数返回脱敏后的值，**不能**用于实际密钥使用场景。
    实际使用密钥时应直接调用 ``os.environ.get(key)`` 或 settings 仓储层。
    """
    if key not in os.environ:
        return default
    value = os.environ[key]
    if not value:
        return default
    return mask_secret(value)


__all__ = [
    "load_env_file",
    "mask_secret",
    "get_env_masked",
    "_is_sensitive_key",
]
