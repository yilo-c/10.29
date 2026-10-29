"""Cloud Sync Crypto — 从 cloud_sync_client.py 抽离的加密/冲突/时间戳纯逻辑层。

本模块只依赖标准库 + cryptography，不依赖 core/aiohttp，便于独立测试
（push 辅助路径按需懒加载 aiohttp，见 push() 内注释）。
所有公开符号由 sync/cloud_sync_client.py 透明 re-export，外部 import 不变。
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import logging

logger = logging.getLogger("siyi.sync.cloud")


# ── 加密常量（必须与手机端 cloudSync.ts 完全一致）──
CRYPTO_SALT_V1 = "siyi-sync-v1"  # legacy
CRYPTO_SALT_V2_PREFIX = "siyi-sync-v2:"
CRYPTO_SALT_V4_PREFIX = "siyi-sync-v4:"  # P0.1: 同步组 salt 前缀
# 迭代次数：v1/v2 用 100K（历史值，仅解密兼容），v3/v4 用 600K（OWASP 2023）。
CRYPTO_ITERATIONS_V1V2 = 100_000
CRYPTO_ITERATIONS_V3 = 600_000
CRYPTO_ITERATIONS_V4 = 600_000  # v4 同 v3，但值写入信封头，未来可调整
# B-08 硬上限：iterations 从信封头读（4 字节 BE），恶意/被篡改密文可把其设为
# 任意值（如 0xFFFFFFFF），使 PBKDF2 无界长跑占死解密线程（CPU DoS）。
# 正常值 600K，留 ~3.3x 余量后设 2M 硬上限；超限直接拒绝，不进入派生。
MAX_CRYPTO_ITERATIONS = 2_000_000
# 向后兼容别名：旧代码/测试引用 CRYPTO_ITERATIONS（指 v1/v2 的 100K）。
CRYPTO_ITERATIONS = CRYPTO_ITERATIONS_V1V2
VERSION_BYTE_V2 = 0x02
VERSION_BYTE_V3 = 0x03  # 历史写入版本（per-device salt，600K 迭代）
VERSION_BYTE_V4 = 0x04  # P0.1: 当前写入版本（group salt + 信封头自带 KDF 参数）

# ── HTTP 超时 ──
FETCH_TIMEOUT_SECONDS = 15

# ── 同步资源域 ──
RESOURCE_NOTES = "notes"
RESOURCE_TODOS = "todos"
RESOURCE_SETTINGS = "settings"
RESOURCE_INFO_BRIEF = "info_brief"
RESOURCE_INFO_SECTIONS = "info_sections"

# T7 缺口 2（2026-08-30）：同步客户端版本号——upload/download 请求都必须携带。
# Worker 端 min_client_version 协商对缺失/过低的 upload 一律 426
# （SYNC_VERSION_TOO_OLD，见 cloudflare/worker/src/sync.ts rejectStaleVersion）。
# 语义化版本格式（支持 1 / 1.2 / 1.2.3），须与手机端 cloudSync.ts 的
# SYNC_CLIENT_VERSION 保持一致；cloud_sync_client.py re-export 本常量作门面。
SYNC_CLIENT_VERSION = "1.2.0"

# ── 独立推送函数（无需 CloudSyncClient 实例，供 info_scheduler 等模块调用）──


def _cloud_sync_enabled(settings: Any) -> bool:
    """云同步总开关（§2-③ / PP-3）：读 settings 的 ``cloud_sync_enabled``。

    调用方（info_scheduler）须显式注入 settings；未注入（settings is None）时
    保留旧的"env 三键在就推"语义，不改变既有 standalone 调用方行为。
    """
    if settings is None:
        return True
    try:
        return bool(settings.get_setting("cloud_sync_enabled", False))
    except Exception:
        logger.debug("cloud_sync_enabled read failed; treating as disabled", exc_info=True)
        return False


async def push_info_brief_standalone(
    overview: str,
    section_briefs: dict,
    full_brief: str,
    source_count: int,
    settings: Any = None,
) -> bool:
    """将简报推送到 Cloudflare Worker（从 settings service 读取配置）。

    云同步总开关门控：显式注入 settings 且 ``cloud_sync_enabled=False`` 时直接不推。
    """
    if not _cloud_sync_enabled(settings):
        return False
    worker_url, sync_token, register_secret = _get_worker_config(settings)
    if not worker_url or not sync_token:
        return False
    import time

    data = {
        "overview": overview,
        "section_briefs": section_briefs,
        "full_brief": full_brief,
        "generated_at": int(time.time()),
        "source_count": source_count,
    }
    return await _push_standalone(
        worker_url,
        sync_token,
        RESOURCE_INFO_BRIEF,
        data,
        register_secret,
    )


async def push_info_sections_standalone(
    user_sections: list[dict],
    settings: Any = None,
) -> bool:
    """将板块配置推送到 Cloudflare Worker（从 settings service 读取配置）。

    云同步总开关门控：同 push_info_brief_standalone，关同步后不再上云。
    """
    if not _cloud_sync_enabled(settings):
        return False
    worker_url, sync_token, register_secret = _get_worker_config(settings)
    if not worker_url or not sync_token:
        return False
    import time

    data = {
        "user_sections": user_sections,
        "updated_at": int(time.time()),
    }
    return await _push_standalone(
        worker_url,
        sync_token,
        RESOURCE_INFO_SECTIONS,
        data,
        register_secret,
    )


def _get_worker_config(settings: Any = None) -> tuple[str, str, str]:
    """从 settings service 或环境变量读取 Worker 配置。

    P0.1: 同时返回 register_secret，用于派生 sync_group_id。
    """
    worker_url = os.environ.get("SIYI_WORKER_URL", "")
    sync_token = os.environ.get("SIYI_SYNC_TOKEN", "")
    register_secret = os.environ.get("SIYI_REGISTER_SECRET", "")
    if settings is not None:
        try:
            w = settings.get_setting("cloud_sync_worker_url", "")
            t = settings.get_setting("cloud_sync_token", "")
            rs = settings.get_setting("cloud_sync_register_secret", "")
            if w:
                worker_url = w
            if t:
                sync_token = t
            if rs:
                register_secret = rs
        except Exception:
            pass
    return worker_url, sync_token, register_secret


async def _push_standalone(
    worker_url: str,
    sync_token: str,
    resource: str,
    data: dict,
    register_secret: str = "",
) -> bool:
    import json as _json

    worker_url = worker_url.rstrip("/")
    if not worker_url or not sync_token:
        return False

    plaintext = _json.dumps(data, ensure_ascii=False)
    checksum = hashlib.sha256(plaintext.encode()).hexdigest()

    # P0.1: 优先用 v4（group salt），让其他设备能解密；缺 register_secret 时降级 v3
    try:
        sync_group_id = _derive_sync_group_id(register_secret) if register_secret else ""
        if sync_group_id:
            encrypted = _encrypt_v4(sync_token, plaintext, sync_group_id)
        else:
            encrypted = _encrypt_v3(sync_token, plaintext)
    except Exception as exc:
        logger.warning("encrypt %s failed: %s", resource, exc)
        return False

    payload = {
        "resource": resource,
        "data_encrypted": encrypted,
        "checksum": checksum,
        # T7 缺口 2：补发 client_version——Worker 对缺失该字段的 upload
        # 恒 426（rejectStaleVersion 的 !client 短路），standalone 推送
        # （info_brief / info_sections）此前因此被全部拒绝。
        "client_version": SYNC_CLIENT_VERSION,
    }

    try:
        # 懒导入 aiohttp：保持模块"导入期不依赖 aiohttp"的纯逻辑契约（便于独立单测）；
        # 仅 push 实际调用时才加载（2026-08-24 修复 F821 未定义名，此前为运行时 NameError）
        import aiohttp  # noqa: PLC0415

        async with aiohttp.ClientSession() as session:
            url = f"{worker_url}/api/sync/upload"
            async with session.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {sync_token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()  # consume body to release connection
                if resp.status == 426:
                    # P2-5-A：版本协商拒绝属结构性问题（客户端过旧/未报版本），
                    # 重试不会好转——standalone 为 fire-and-forget 路径，
                    # 至少升为 warning 留痕，不再 debug 静默。
                    logger.warning("push %s: 426 client version too old: %s", resource, body[:200])
                    return False
                if resp.status != 200:
                    logger.debug("push %s: HTTP %d", resource, resp.status)
                    return False
                return True
    except Exception as exc:
        logger.debug("push %s failed: %s", resource, exc)
        return False


def _derive_sync_group_id(register_secret: str) -> str:
    """P0.1: 从 register_secret 派生稳定的 sync_group_id。

    所有共享同一 register_secret 的设备（即向同一 Worker 部署注册的设备）
    派生出相同的 sync_group_id，从而派生出相同的 AES 密钥，实现跨设备互解。

    只取 sha256 的前 16 个 hex 字符（64 bit），足以做 salt 差异化；
    完整 register_secret 永不落入密文或日志。
    """
    if not register_secret:
        return ""
    return hashlib.sha256(register_secret.encode("utf-8")).hexdigest()[:16]


def _group_salt(sync_group_id: str) -> str:
    """P0.1: v4 同步组 salt。同组设备共享，跨组不同。"""
    return CRYPTO_SALT_V4_PREFIX + sync_group_id


def _encrypt_v4(sync_token: str, plaintext: str, sync_group_id: str) -> str:
    """P0.1: AES-256-GCM 加密（v4 协议：group salt + 信封头带 iterations）。

    信封格式：``[0x04][iter(4 BE)][iv(12)][ct+tag]``

    与 v3 的关键差异：
    - salt = ``siyi-sync-v4:<sync_group_id>``，由接收端用自己的 group_id 派生
      （不入信封 —— 这样跨组设备即使 token 相同也派生出不同密钥）
    - iterations 写入信封头，支持未来调整迭代次数而不换版本字节
    """
    import os as _os
    from base64 import b64encode as _b64e

    salt = _group_salt(sync_group_id)
    key = _derive_aes_key(sync_token, salt, CRYPTO_ITERATIONS_V4)
    iv = _os.urandom(12)
    cipher = AESGCM(key)
    ct = cipher.encrypt(iv, plaintext.encode("utf-8"), None)
    combined = bytearray([VERSION_BYTE_V4])
    combined.extend(CRYPTO_ITERATIONS_V4.to_bytes(4, "big"))
    combined.extend(iv)
    combined.extend(ct)
    return _b64e(bytes(combined)).decode("ascii")


def _encrypt_v3(sync_token: str, plaintext: str) -> str:
    """AES-256-GCM 加密（v3 协议：600K PBKDF2 + per-device salt + version byte）。

    历史写入路径，仅用于无 register_secret 的降级场景（如 info_brief
    在未配置 register_secret 时仍能推送到同设备）。新代码应优先用 v4。
    """
    import os as _os
    from base64 import b64encode as _b64e

    device_id = os.environ.get("SIYI_DEVICE_ID", "desktop-main")
    salt = f"siyi-sync-v2:{device_id}"
    key = _derive_aes_key(sync_token, salt, 600_000)
    iv = _os.urandom(12)
    cipher = AESGCM(key)
    ct = cipher.encrypt(iv, plaintext.encode("utf-8"), None)
    combined = bytearray([0x03])  # v3 version byte
    combined.extend(iv)
    combined.extend(ct)
    return _b64e(bytes(combined)).decode("ascii")


# 密钥缓存（避免每次 push 都跑 600K PBKDF2）
_AES_KEY_CACHE: dict[str, bytes] = {}
_AES_KEY_CACHE_LOCK = threading.Lock()
_AES_KEY_CACHE_MAX = 8


def _derive_aes_key(token: str, salt: str, iterations: int = 600_000) -> bytes:
    cache_key = hashlib.sha256(f"{token}::{salt}::{iterations}".encode()).hexdigest()
    with _AES_KEY_CACHE_LOCK:
        if cache_key in _AES_KEY_CACHE:
            return _AES_KEY_CACHE[cache_key]
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt.encode("utf-8"), iterations=iterations)
    key = kdf.derive(token.encode("utf-8"))
    with _AES_KEY_CACHE_LOCK:
        if len(_AES_KEY_CACHE) >= _AES_KEY_CACHE_MAX:
            _AES_KEY_CACHE.pop(next(iter(_AES_KEY_CACHE)))
        _AES_KEY_CACHE[cache_key] = key
    return key


# ── settings 同步白名单（仅这些字段会被同步）──
# 注意：任何敏感字段（API key、token、密码）都不应出现在这里。
SYNCABLE_SETTING_KEYS: tuple[str, ...] = (
    "cloud_ai_enabled",
    "prefer_desktop_ai",
    "prompt_before_cloud",
    "ui_theme",
    "knowledge_base_enabled",
    "rich_media_markdown_enabled",
)


class CloudSyncError(Exception):
    """云同步相关错误基类。"""


class CloudSyncConfigError(CloudSyncError):
    """配置不完整（缺 worker_url / sync_token / register_secret）。"""


class CloudSyncAuthError(CloudSyncError):
    """认证失败（token 无效或 register_secret 错误）。"""


class CloudSyncVersionError(CloudSyncError):
    """P2-5-A（2026-08-31）：Worker 返回 426 SYNC_VERSION_TOO_OLD——
    本端 client_version 低于 min_client_version 协商下限（或被要求必须报版本
    而未报）。属致命契约错误：继续重试无意义且掩盖真因，同步循环应停止
    并把事件/升级提示透出给用户，而非混入网络抖动。"""


class CloudSyncCryptoError(CloudSyncError):
    """加密/解密失败（通常是 token 不匹配）。"""


def _normalize_ts(value: Any) -> float:
    """将各种时间戳格式归一化为 epoch 秒（float），便于 LWW 比较。

    兼容三种来源：
    - 桌面端 ``updated_at``: ISO 8601 字符串（如 ``2026-06-22T12:00:00+00:00``）
    - 手机端 ``updatedAt``: ``Date.now()`` 毫秒级 epoch（如 ``1719064800000``）
    - Worker ``updated_at``: ``unixepoch()`` 秒级 epoch（如 ``1719064800``）
    - 已是 int/float: 启发式判断 —— > 1e12 视为毫秒，除以 1000

    无法解析时返回 0（视为最旧），确保不会因类型错误中断同步。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        # > 1e12 (year ~33658) 视为毫秒级时间戳
        if v > 1e12:
            return v / 1000.0
        return v
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0.0
        # 纯数字字符串
        try:
            v = float(s)
            if v > 1e12:
                return v / 1000.0
            return v
        except ValueError:
            pass
        # ISO 8601 字符串
        try:
            from datetime import datetime

            # 处理带 'Z' 后缀的 UTC 时间
            clean = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                from datetime import timezone

                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _detect_conflict(local: dict[str, Any] | None, remote_time: float) -> bool:
    """检测真冲突：本地和远端自上次同步后都被修改过。

    判据：本地 ``updated_at`` > 本地 ``last_synced_at``（本地有未同步的修改）
    且 ``remote_time`` > 本地 ``last_synced_at``（远端也有新修改）。

    ``last_synced_at`` 为 None/0 时无法判定冲突，返回 False（回退到纯 LWW）；
    ``local`` 为 None（远端新条目、本地无副本）时同样返回 False。
    """
    if not local:
        return False
    last_synced = _normalize_ts(local.get("last_synced_at"))
    if last_synced <= 0:
        return False  # 从未同步过，无法判定冲突
    local_time = _normalize_ts(local.get("updated_at") or local.get("updatedAt"))
    # 本地有未同步的修改 且 远端也有新修改 → 真冲突
    return local_time > last_synced and remote_time > last_synced


# P1-7: 派生密钥缓存 —— PBKDF2 600K 迭代约 300-500ms，每次 encrypt/decrypt
# 都重新派生会严重拖慢同步（批量解密时累积阻塞）。缓存以 (token_hash, salt,
# iterations) 为 key，避免明文 token 落入缓存。
_KEY_CACHE: dict[tuple[str, str, int], bytes] = {}
_KEY_CACHE_LOCK = threading.Lock()
_KEY_CACHE_MAX = 32


def _derive_key(token: str, salt: str, iterations: int = CRYPTO_ITERATIONS_V1V2) -> bytes:
    """PBKDF2-HMAC-SHA256 派生 256-bit AES 密钥。

    ``iterations`` 默认 100K 仅为 v1/v2 解密兼容；新写入应传
    ``CRYPTO_ITERATIONS_V3`` (600K)。

    P1-7: 派生结果按 (token_sha256, salt, iterations) 缓存，避免批量同步时
    重复执行 600K 迭代 PBKDF2（每次 ~300-500ms）。
    """
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache_key = (token_hash, salt, iterations)
    with _KEY_CACHE_LOCK:
        cached = _KEY_CACHE.get(cache_key)
        if cached is not None:
            return cached

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt.encode("utf-8"),
        iterations=iterations,
    )
    key = kdf.derive(token.encode("utf-8"))

    with _KEY_CACHE_LOCK:
        if len(_KEY_CACHE) >= _KEY_CACHE_MAX:
            # FIFO 淘汰最旧的条目
            _KEY_CACHE.pop(next(iter(_KEY_CACHE)))
        _KEY_CACHE[cache_key] = key
    return key


def _device_salt(device_id: str) -> str:
    """v2 per-device salt。"""
    return CRYPTO_SALT_V2_PREFIX + device_id


def encrypt_data(plaintext: str, token: str, device_id: str, sync_group_id: str = "") -> str:
    """加密为 base64 字符串。

    P0.1: 优先写 v4（group salt），同组不同设备可互解、跨组不可解。
    缺 ``sync_group_id`` 时降级为 v3（per-device salt，仅同设备可解），保留向后兼容。

    - v4 (当前写入): ``[0x04][iter(4 BE)][iv(12)][ct+tag]``, group salt
    - v3 (降级):     ``[0x03][iv(12)][ct+tag]``, 600K 迭代, per-device salt
    """
    if sync_group_id:
        salt = _group_salt(sync_group_id)
        key = _derive_key(token, salt, CRYPTO_ITERATIONS_V4)
        iv = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
        combined_buf = bytearray([VERSION_BYTE_V4])
        combined_buf.extend(CRYPTO_ITERATIONS_V4.to_bytes(4, "big"))
        combined_buf.extend(iv)
        combined_buf.extend(ciphertext)
        return base64.b64encode(bytes(combined_buf)).decode("ascii")
    # 降级：v3 per-device salt
    key = _derive_key(token, _device_salt(device_id), CRYPTO_ITERATIONS_V3)
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), associated_data=None)
    combined = bytes([VERSION_BYTE_V3]) + iv + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_data(ciphertext_b64: str, token: str, device_id: str, sync_group_id: str = "") -> str:
    """解密 base64 密文。

    自动识别 v4/v3/v2（带版本字节）和 v1（legacy，固定 salt）格式。
    与手机端 ``decryptData()`` 兼容。

    P0.1: v4 解密需要接收端的 ``sync_group_id`` 来派生 salt —— 同组设备派生
    出相同 salt → 相同密钥 → 可互解；跨组设备 salt 不同 → 密钥不同 → 解不开。
    iterations 从信封头读取（支持未来调整）。
    """
    try:
        combined = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise CloudSyncCryptoError(f"base64 decode failed: {exc}") from exc

    if not combined:
        raise CloudSyncCryptoError("empty ciphertext")

    version_byte = combined[0]
    if version_byte == VERSION_BYTE_V4:
        # v4: iterations 从信封头读，salt 由接收端 group_id 派生
        # 头部：[ver(1)][iter(4)][iv(12)][ct+tag]
        if len(combined) < 17:
            raise CloudSyncCryptoError("v4 envelope too short for iter+iv")
        if not sync_group_id:
            raise CloudSyncCryptoError("v4 ciphertext requires sync_group_id to derive key (register_secret not configured?)")
        iterations = int.from_bytes(combined[1:5], "big")
        # B-08：iterations 无上限校验是 DoS 面——恶意/被篡改密文可令 PBKDF2
        # 无界长跑（0xFFFFFFFF ≈ 42 亿次迭代）占死 to_thread 解密线程。
        # 正常值 600K；超硬上限（2M）直接拒绝，**必须在 _derive_key 之前**。
        if iterations > MAX_CRYPTO_ITERATIONS:
            raise CloudSyncCryptoError(f"v4 iterations {iterations} exceeds hard cap {MAX_CRYPTO_ITERATIONS} (tampered ciphertext?)")
        iv = combined[5:17]
        data = combined[17:]
        salt = _group_salt(sync_group_id)
        key = _derive_key(token, salt, iterations)
    elif version_byte == VERSION_BYTE_V3:
        # v3: 600K 迭代，per-device salt
        salt = _device_salt(device_id)
        iterations = CRYPTO_ITERATIONS_V3
        iv = combined[1:13]
        data = combined[13:]
        key = _derive_key(token, salt, iterations)
    elif version_byte == VERSION_BYTE_V2:
        # v2: 100K 迭代，per-device salt（历史数据）
        salt = _device_salt(device_id)
        iterations = CRYPTO_ITERATIONS_V1V2
        iv = combined[1:13]
        data = combined[13:]
        key = _derive_key(token, salt, iterations)
    else:
        # Legacy v1: 无版本字节，IV 从 byte 0 开始，固定 salt，100K 迭代
        salt = CRYPTO_SALT_V1
        iterations = CRYPTO_ITERATIONS_V1V2
        iv = combined[0:12]
        data = combined[12:]
        key = _derive_key(token, salt, iterations)

    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(iv, data, associated_data=None)
    except Exception as exc:
        raise CloudSyncCryptoError(f"AES-GCM decrypt failed: {exc}") from exc
    return plaintext.decode("utf-8")


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _now_seconds() -> int:
    """当前 Unix 时间戳（秒）。

    必须与 Worker 的 ``unixepoch()`` 单位一致，否则 download 的
    ``updated_at > since`` 比较会因数量级差异永远为 False。
    """
    return int(time.time())
