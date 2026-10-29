"""认证工具模块。

提供 Token 生成、哈希、验证等功能。
"""

from __future__ import annotations
import hashlib
import secrets


def hash_token(token: str) -> str:
    """计算 token 的 SHA-256 哈希值。
    
    Args:
        token: 原始 token 字符串
        
    Returns:
        十六进制哈希字符串
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token() -> str:
    """生成安全的随机 token。
    
    Returns:
        URL 安全的随机字符串
    """
    return secrets.token_urlsafe(32)
