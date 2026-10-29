# -*- coding: utf-8 -*-
"""认证主 token 的持久化存储与 ACL 管理（V2.1 批次1 #1）。

背景（P0-1 / N4）：此前 `WebSocketServer._auth_token` 为纯内存态，重启即换，
无法持久分发到 Electron / 诊断脚本 / 集成测试，也无轮换/吊销能力。

本模块提供：
  - load_or_create(data_dir)：读取既有 token，不存在则生成并持久化（含 ACL）
  - persist(data_dir, token)：写入 data/auth_token 并收紧 ACL
  - Windows 上 chmod(0o600) 无意义，改用 icacls 收紧 ACL；失败可降级。

生产路径不使用 secrets 落盘明文？—— token 本身就是密钥，落盘是不可避免的，
关键在磁盘权限。调用方（headless_main）负责启动后即时加载进内存。
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("siyi.backend.auth_token_store")

#: token 文件相对数据根目录的路径
AUTH_TOKEN_FILE = "auth_token"

#: icacls 的 ACE 行形态：``<主体>:(权限)``（如 ``BUILTIN\Users:(R)`` /
#: ``NT AUTHORITY\SYSTEM:(I)(F)``）。用于把真正的访问控制项与
#: 「已成功处理 1 个文件」这类汇总行区分开。
_RE_ACE_LINE = re.compile(r":\s*\(")


def _secure_file_permissions(path: Path) -> bool:
    """在 Windows 上收紧 token 文件 ACL；POSIX 用 chmod 0600。

    Returns:
        True  = 已成功收紧
        False = 无法收紧（调用方决定是否降级继续）
    """
    try:
        if sys.platform == "win32":
            # chmod 0600 在 Windows 上是 no-op，必须用 NTFS ACL。
            # icacls：移除继承 + 仅当前用户完全控制。
            import getpass

            user = getpass.getuser()
            proc = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{user}:F",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if proc.returncode != 0:
                logger.warning(
                    "icacls ACL hardening failed for %s: %s",
                    path,
                    proc.stderr.strip() or proc.stdout.strip(),
                )
                return False
            logger.info("Hardened ACL for %s (icacls, user=%s)", path, user)
            return True
        # POSIX
        os.chmod(path, 0o600)
        return True
    except Exception as exc:
        logger.warning("File permission hardening failed for %s: %s", path, exc)
        return False


def get_token_path(data_dir: str | os.PathLike[str]) -> Path:
    """返回 auth_token 文件的绝对路径。"""
    return Path(data_dir) / AUTH_TOKEN_FILE


def load_or_create(
    data_dir: str | os.PathLike[str],
    *,
    length: int = 32,
) -> tuple[str, bool]:
    """读取 data/auth_token；不存在则生成、持久化并收紧 ACL。

    Returns:
        (token, created)  created=True 表示本次新生成
    """
    path = get_token_path(data_dir)
    if path.exists():
        try:
            token = path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            # AV/读锁等瞬时失败：重试一次后降级为「要求重新认证」
            logger.warning("auth_token read failed, retrying: %s", exc)
            try:
                token = path.read_text(encoding="utf-8").strip()
            except Exception as exc2:
                logger.error("auth_token unreadable: %s", exc2)
                return "", False
        if token:
            return token, False
    token = secrets.token_urlsafe(length)
    persist(data_dir, token)
    return token, True


def persist(data_dir: str | os.PathLike[str], token: str) -> bool:
    """将 token 写入数据根（原子写 + ACL），返回是否成功。

    使用临时文件 + os.replace 原子替换，避免 Windows Defender 扫描与
    半写文件导致的 PermissionError/读破损（对应用内 _write_atomic 教训）。
    """
    import uuid as _uuid

    path = get_token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".auth_token.{os.getpid()}.{_uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(token, encoding="utf-8")
        _secure_file_permissions(tmp)
        os.replace(tmp, path)
        # 替换目标后目标 ACL 可能丢失，需对最终文件再收紧一次
        _secure_file_permissions(path)
        return True
    except Exception as exc:
        logger.error("Failed to persist auth_token to %s: %s", path, exc)
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def rotate(data_dir: str | os.PathLike[str], *, length: int = 32) -> str:
    """生成新 token 并持久化（旧 token 由调用方使其失效）。返回新 token。"""
    new_token = secrets.token_urlsafe(length)
    persist(data_dir, new_token)
    return new_token


def acl_is_hardened(path: Path) -> bool:
    """V2.1 批次1#5：验证 token 文件的 ACL 已收紧（通电自检断言用）。

    仅 Windows 需要真正检查；POSIX 侧 chmod 0600 已由 persist 执行，这里返回 True。
    检查方式：运行 ``icacls <file>``，断言输出中**不再出现** Everyone / BUILTIN\\Users /
    NT AUTHORITY\\Authenticated Users 等粗粒度 ACE（这些会放大读取面）。
    文件不存在或 icacls 不可用时返回 False（保守）。

    ★ 2026-09-18 修复（先红后绿实证）：旧实现对**整段输出**做子串匹配，而 token
    文件必然位于 ``C:\\Users\\<name>\\...``（``%LOCALAPPDATA%`` 下），
    路径里的 ``\\Users\\`` 会被 ``r"\\users"`` 这个 marker 命中 →
    **任何 Windows 机器上本函数恒返回 False**，ACL 自检形同虚设（自己骗自己）。
    修法：**只扫每行中文件路径之后的 ACE 部分**，路径本体不参与匹配。
    """
    if sys.platform != "win32":
        return True
    if not path.exists():
        return False
    try:
        proc = subprocess.run(
            ["icacls", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:
        logger.warning("icacls query failed during ACL check: %s", exc)
        return False
    if proc.returncode != 0:
        logger.warning("icacls query returned non-zero for %s", path)
        return False
    output = (proc.stdout or "") + (proc.stderr or "")
    # 粗粒度可读 ACE 清单（宽松匹配），出现任一即视为未收紧
    coarse_markers = (
        "everyone",
        "authenticated users",
        r"\users",
        "creators owner",
    )
    # ★ 只扫 ACE 部分：icacls 每行形如 ``<路径> <主体>:(权限)``，
    # 而 <路径> 本身可能含 ``\Users\``（本机的 %LOCALAPPDATA% 就是），
    # 若连路径一起匹配会把**路径**误判成粗粒度 ACE。
    path_text = str(path).lower()
    saw_ace_line = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        idx = lowered.find(path_text)
        if idx != -1:
            # 首行形如 ``<路径> <主体>:(权限)`` —— 路径本体不参与匹配
            ace_part = lowered[idx + len(path_text) :]
        else:
            # ★ 续行 ACE：icacls 把同一文件的**其余 ACE** 缩进排到后续行，
            # 这些行不含路径，但**同样是真 ACE**（实测本机输出即如此：首行
            # 只挂第一条 ACE，SYSTEM/Administrators/OWNER RIGHTS 分列后续行）。
            # 首版修复对不含路径的行一律 continue → 粗粒度 ACE 若排在续行会被
            # 漏判（**假阴性**：明明没收紧却报"已收紧"，比原 bug 更危险）。
            ace_part = lowered
        # 用 ``<主体>:(权限)`` 形态筛掉「已成功处理 N 个文件」这类汇总行，
        # 也筛掉只含路径、看不到任何 ACE 的异常行。
        if not _RE_ACE_LINE.search(ace_part):
            continue
        saw_ace_line = True
        for marker in coarse_markers:
            if marker in ace_part:
                return False
    # 至少要看到一条真 ACE 行，避免空输出/异常输出被误判为"已收紧"
    return saw_ace_line


__all__ = [
    "AUTH_TOKEN_FILE",
    "get_token_path",
    "load_or_create",
    "persist",
    "rotate",
    "acl_is_hardened",
]
