"""共享路径安全策略：敏感文件黑名单。

审计波次②（C-1 执行器收敛）步骤 2：B 套 ``services/react_executor.py`` 的
``_SENSITIVE_PATTERNS`` / ``_is_sensitive_path`` 三样安全策略之一，逐字迁移到此，
供 A 套 ``assistant/capability/_file_ops.py`` 的 file.read/write/copy 入口复用，
使两套执行器在收敛前共享同一份敏感路径判定。

注意：本模块**不改动** ``react_executor.py``——B 套自身内联实现由后续代理在收缩
步骤删除。此处只做「新增可共用真源」，语义（resolve 后小写子串匹配）与 B 套逐字一致。
"""
from __future__ import annotations

from pathlib import Path

# 敏感文件黑名单：AI 不应读取/写入/复制这些文件（含凭据/密钥/系统配置）。
# 逐字对齐 services/react_executor.py 的 _SENSITIVE_PATTERNS（收敛期两套共用同一判定）。
SENSITIVE_PATTERNS: tuple[str, ...] = (
    ".siyi_key",  # Fernet 主密钥
    "settings.json",  # 含加密 API Key
    "credentials.json",  # 凭据存储
    ".env",  # 环境变量密钥
    ".ssh/id_",  # SSH 私钥
    ".ssh/authorized_keys",
    ".ssh/known_hosts",
    ".gitconfig",
    ".npmrc",
    ".pypirc",
    ".aws/credentials",
    ".aws/config",
    ".kube/config",
    "cookies.db",  # 浏览器 Cookie
    "cookies.sqlite",
    "Login Data",  # Chrome 密码库
    "key4.db",  # Firefox 密码库
    "key3.db",
    "NTUSER.DAT",  # Windows 注册表
    "SAM",  # Windows SAM
    "SYSTEM",
)


def is_sensitive_path(path: Path | str) -> bool:
    """检查路径是否命中敏感文件黑名单（大小写不敏感）。

    与 B 套 ``_is_sensitive_path`` 语义逐字一致：先 ``resolve(strict=False)``，
    转小写、把反斜杠归一为正斜杠，再对每个模式做子串匹配（``pat.lower() in resolved``）。
    """
    resolved = str(Path(path).resolve(strict=False)).lower().replace("\\", "/")
    return any(pat.lower() in resolved for pat in SENSITIVE_PATTERNS)
