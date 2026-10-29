"""Cloud Sync Client — Cloudflare Worker 协议的桌面端实现。

与手机端 `思忆集手机端app/src/lib/cloudSync.ts` 协议完全兼容：
- AES-256-GCM 加密
- PBKDF2 迭代次数按版本：v1/v2 = 100K，v3/v4 = 600K（OWASP 2023 ≥600K）
- v4 (当前写入): sync_group_id 派生 salt，**同组不同设备可互解、跨组不可解**
  - sync_group_id = sha256(register_secret)[:16]，同 Worker 部署的所有设备共享
  - salt = ``siyi-sync-v4:<sync_group_id>``，由接收端用自己的 group_id 派生
    （不入信封 —— 这样跨组设备即使 token 相同也派生出不同密钥）
  - 信封头只带 iterations（4 字节），支持未来调整迭代次数而不换版本字节
- v3 (历史): per-device salt = ``'siyi-sync-v2:' + device_id``，**不同 device_id 不可互解**
  - 仅用作同设备解密兼容；新写入不再使用
- 密文格式：base64 编码
  - v4 (当前写入): ``[0x04][iter(4 BE)][iv(12)][ct+tag]``
  - v3 (历史):     ``[0x03][iv(12)][ct+tag]``, 600K 迭代, per-device salt
  - v2 (历史):     ``[0x02][iv(12)][ct+tag]``, 100K 迭代, per-device salt
  - v1 (legacy):   ``[iv(12)][ct+tag]``       首字节非 0x02/0x03/0x04, 100K 固定 salt

资源域（与手机端对齐）：
- ``notes``    → ``PersonalNoteRepository``
- ``todos``    → ``PersonalTaskRepository``（桌面端叫 tasks，手机端叫 todos）
- ``settings`` → ``SettingsRepository``（仅同步非敏感字段）

合并策略：Last-Write-Wins (LWW) + 冲突检测，基于归一化后的 ``updatedAt`` 时间戳。
P1-8: 时间戳归一化（ISO/毫秒/秒 → epoch 秒）+ 双方同时修改时保留本地版本为冲突副本。

端点（Cloudflare Worker）：
- POST ``/api/sync/register``  — 注册设备（需要 ``register_secret``）
- POST ``/api/sync/upload``    — 上传加密数据
- GET ``/api/sync/download``  — 拉取其他设备的更新
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import aiohttp
from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.sync.cloud")


# ─────────────────────────────────────────────────────────────
# 以下符号由抽离模块 sync/_cloud_sync_crypto.py 透明 re-export，
# 外部 import（core/envelope_crypto.py、tests、headless_main 等）无需改动。
# ─────────────────────────────────────────────────────────────
from sync._cloud_sync_crypto import (
    FETCH_TIMEOUT_SECONDS,
    RESOURCE_NOTES,
    RESOURCE_TODOS,
    RESOURCE_SETTINGS,
    RESOURCE_INFO_BRIEF,
    RESOURCE_INFO_SECTIONS,
    SYNC_CLIENT_VERSION,
    _derive_sync_group_id,
    CloudSyncConfigError,
    CloudSyncAuthError,
    CloudSyncVersionError,
    CloudSyncCryptoError,
    _normalize_ts,
    encrypt_data,
    decrypt_data,
    _sha256_hex,
    VERSION_BYTE_V4,
    _get_worker_config,
    _group_salt,
    _encrypt_v3,
    _encrypt_v4,
    push_info_brief_standalone,
    push_info_sections_standalone,
    _push_standalone,
    # re-export：测试经 csc.CRYPTO_SALT_V4_PREFIX 访问（test_cloud_sync_client.py:1091）
    CRYPTO_SALT_V4_PREFIX,  # noqa: F401
)


# 实体采集/合并（notes/todos/settings）已拆至 sync/_cloud_sync_entity_handlers.py
from sync._cloud_sync_entity_handlers import SyncEntityHandlersMixin

# V2.1 批次3（V3-05 治本）：同步客户端版本号，随 upload/download 上报，
# 供 Worker 的 min_client_version 协商（低于阈值 426 拒绝）。
# T7 缺口 2（2026-08-30）：常量定义下沉到 _cloud_sync_crypto（standalone
# 推送路径同需上报），上方 import re-export，门面引用名保持不变。


class CloudSyncClient(SyncEntityHandlersMixin):
    """桌面端云同步客户端。

    使用方式::

        client = CloudSyncClient(
            worker_url="https://siyi-sync.example.workers.dev",
            sync_token="user-chosen-password",
            device_id="desktop-abc123",
            register_secret="...",
            note_repo=context.personal_note_repository,
            task_repo=context.personal_task_repository,
            settings_repo=context.settings_repository,
        )
        await client.register_device("My Desktop")
        await client.push_local_data()
        await client.pull_remote_data()
        client.start_sync_loop(interval_seconds=300)  # 5 分钟一次
    """

    def __init__(
        self,
        worker_url: str,
        sync_token: str,
        device_id: str,
        register_secret: str,
        note_repo: Any | None = None,
        task_repo: Any | None = None,
        settings_repo: Any | None = None,
        initial_cursor: int = 0,
        cursor_persister: Any | None = None,
        sync_group_id: str = "",
        memory_index: Any | None = None,
        reindex_after_pull: bool = True,
    ) -> None:
        self.worker_url = worker_url.rstrip("/")
        self.sync_token = sync_token
        self.device_id = device_id or f"desktop-{uuid.uuid4().hex[:8]}"
        self.register_secret = register_secret
        # P0.1: sync_group_id 用于派生跨设备共享的 AES 密钥。
        # 优先用调用方显式传入的；否则从 register_secret 派生。
        self.sync_group_id = sync_group_id or _derive_sync_group_id(register_secret)
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.settings_repo = settings_repo
        # T5.4: 记忆索引引用 + 拉取合并后是否重建索引。
        # pull 合并远端笔记后本地索引不刷新曾是索引漂移源；
        # 注入 memory_index 并在合并有变化时触发 rebuild_index 对账。
        self.memory_index = memory_index
        self.reindex_after_pull = reindex_after_pull

        # P1-4: 同步游标 = Worker 返回的最大 ``updated_at``（秒级 epoch，与
        # Worker ``unixepoch()`` 单位一致）。仅由 ``pull_remote_data`` 推进；
        # push 不再用 ``_now_seconds()`` 覆盖（旧逻辑会因本地/Worker 时钟偏移
        # 跳过或重放条目，且 Worker SQL ``WHERE device_id != ?`` 已经过滤了
        # 自己 push 的条目，没必要本地推进）。
        # ``initial_cursor`` 由 ``SyncBridge`` 从 settings 读出，确保重启不重拉全量。
        self._cursor: int = int(initial_cursor) if initial_cursor else 0
        self._cursor_persister = cursor_persister  # callable(int) -> None
        self._sync_task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._last_sync_at: float = 0.0
        self._last_sync_ok: bool = False
        self._last_error: str | None = None
        self._push_count: int = 0
        self._pull_count: int = 0
        # V2.1 批次3（P1-1）：pull 失败累计（非 2xx / 解密 / 合并失败），供死信告警
        self._pull_failures: int = 0

    def _persist_cursor(self) -> None:
        """P1-4: 把当前游标写回持久层，重启后能恢复。失败仅记 debug。"""
        persister = self._cursor_persister
        if persister is None:
            return
        try:
            persister(self._cursor)
        except Exception:
            logger.debug("persist cursor failed", exc_info=True)

    def reset_cursor(self) -> int:
        """B-18 逃生舱：把游标重置为 0（内存 + 持久层双清）。

        水位错乱（漏拉/重复拉）时调用：运行中的同步循环下次 pull 以 since=0
        全量重拉对齐。**必须同时清内存 ``_cursor`` 与持久层**——只写持久层
        （旧 WatermarkStore 实现）对运行中 client 无效（``_cursor`` 仅构造期
        读入），逃生舱形同虚设直到重启。

        持久层经 ``_cursor_persister``（SyncBridge._save_cursor）写回 settings
        ``cloud_sync_cursor``；无 persister 时仅清内存（构造期未注入的场景）。
        """
        self._cursor = 0
        self._persist_cursor()
        logger.warning("cloud sync cursor reset to 0 (escape hatch)")
        return 0

    # ── 配置检查 ──

    def is_configured(self) -> bool:
        """是否已配置必要参数。"""
        return bool(self.worker_url and self.sync_token)

    def _require_config(self) -> None:
        if not self.is_configured():
            raise CloudSyncConfigError("cloud sync not configured: worker_url and sync_token required")

    # ── HTTP session 管理 ──

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """释放 HTTP session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── 设备注册 ──

    async def register_device(self, name: str = "Desktop") -> bool:
        """向 Worker 注册当前设备。

        Worker 端要求 ``register_secret``（H1 安全修复），手机端当前未传该字段
        — 桌面端必须传，否则注册会被拒绝。
        """
        self._require_config()
        if not self.register_secret:
            raise CloudSyncConfigError("register_secret required (set sync_register_secret in settings)")

        session = await self._get_session()
        url = f"{self.worker_url}/api/sync/register"
        payload = {
            "device_id": self.device_id,
            "name": name,
            "token": self.sync_token,
            "register_secret": self.register_secret,
        }

        try:
            async with session.post(url, json=payload) as resp:
                if resp.status in (401, 403):
                    raise CloudSyncAuthError("register rejected: invalid register_secret")
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("register failed: %d %s", resp.status, body[:200])
                    return False
                logger.info("cloud sync device registered: %s (%s)", self.device_id, name)
                return True
        except aiohttp.ClientError as exc:
            logger.warning("register network error: %s", exc)
            return False

    # ── 推送本地数据 ──

    async def _upload_resource(self, resource: str, data: Any) -> bool:
        """上传单个资源到 Worker。"""
        self._require_config()
        session = await self._get_session()
        url = f"{self.worker_url}/api/sync/upload"

        plaintext = json.dumps(data, ensure_ascii=False, default=str)
        # P0.1: 用 sync_group_id 派生跨设备共享密钥；缺失时降级 v3
        encrypted = encrypt_data(plaintext, self.sync_token, self.device_id, self.sync_group_id)
        checksum = _sha256_hex(plaintext)
        payload = {
            "resource": resource,
            "data_encrypted": encrypted,
            "checksum": checksum,
            # V2.1 批次3（V3-05）：补发 client_version，避免被 Worker min 版本协商拒绝
            "client_version": SYNC_CLIENT_VERSION,
        }

        try:
            async with session.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.sync_token}"},
            ) as resp:
                if resp.status == 401:
                    raise CloudSyncAuthError("upload rejected: invalid token")
                if resp.status == 426:
                    # P2-5-A：Worker 版本协商拒绝——升级前重试无意义，
                    # 抛结构化错误让 sync_once fatal 化，不再混入网络抖动。
                    body = await resp.text()
                    raise CloudSyncVersionError(f"upload rejected: client version too old ({body[:200]})")
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "upload %s failed: %d %s",
                        resource,
                        resp.status,
                        body[:200],
                    )
                    return False
                return True
        except aiohttp.ClientError as exc:
            logger.warning("upload %s network error: %s", resource, exc)
            return False

    async def push_local_data(self) -> bool:
        """推送所有资源到 Worker。任一资源失败则返回 False。"""
        self._require_config()
        # B-01：_collect_* 是同步 SQLite I/O（list_notes/list_tasks/get_setting），
        # 直接调用会阻塞事件循环（AGENTS.md to_thread 铁律）。逐资源 to_thread。
        resources = {
            RESOURCE_NOTES: await asyncio.to_thread(self._collect_notes),
            RESOURCE_TODOS: await asyncio.to_thread(self._collect_todos),
            RESOURCE_SETTINGS: await asyncio.to_thread(self._collect_settings),
        }

        all_ok = True
        for resource, data in resources.items():
            try:
                ok = await self._upload_resource(resource, data)
                if not ok:
                    all_ok = False
            except (CloudSyncAuthError, CloudSyncVersionError):
                raise
            except Exception as exc:
                logger.warning("push %s error: %s", resource, exc)
                all_ok = False

        if all_ok:
            self._push_count += 1
        return all_ok

    async def push_info_brief(
        self,
        overview: str,
        section_briefs: dict,
        full_brief: str,
        source_count: int,
    ) -> bool:
        """推送最新简报到 Worker（加密存储）。"""
        import time

        data = {
            "overview": overview,
            "section_briefs": section_briefs,
            "full_brief": full_brief,
            "generated_at": int(time.time()),
            "source_count": source_count,
        }
        try:
            return await self._upload_resource(RESOURCE_INFO_BRIEF, data)
        except Exception as exc:
            logger.warning("push info_brief failed: %s", exc)
            return False

    async def push_info_sections(self, user_sections: list[dict]) -> bool:
        """推送板块配置到 Worker（加密存储）。"""
        import time

        data = {
            "user_sections": user_sections,
            "updated_at": int(time.time()),
        }
        try:
            return await self._upload_resource(RESOURCE_INFO_SECTIONS, data)
        except Exception as exc:
            logger.warning("push info_sections failed: %s", exc)
            return False

    # ── 拉取远端数据 ──

    async def _download(self, since: int) -> list[dict[str, Any]] | None:
        """GET /api/sync/download → 返回 items 列表。

        Returns:
            items 列表；非 2xx / 网络错误返回 None（调用方计失败，不视为"无数据"）。
        """
        self._require_config()
        session = await self._get_session()
        url = (
            f"{self.worker_url}/api/sync/download"
            f"?since={since}&device_id={self.device_id}"
            # V2.1 批次3（V3-05）：补发 client_version，避免被 Worker min 版本协商拒绝
            f"&client_version={SYNC_CLIENT_VERSION}"
        )

        try:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {self.sync_token}"},
            ) as resp:
                if resp.status == 401:
                    raise CloudSyncAuthError("download rejected: invalid token")
                if resp.status == 426:
                    # P2-5-A：版本协商拒绝 → fatal（同 upload 路径）。
                    body = await resp.text()
                    raise CloudSyncVersionError(f"download rejected: client version too old ({body[:200]})")
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("download failed: %d %s", resp.status, body[:200])
                    return None
                data = await resp.json()
                return data.get("items", [])
        except aiohttp.ClientError as exc:
            logger.warning("download network error: %s", exc)
            return None

    async def pull_remote_data(self) -> bool:
        """拉取远端更新并合并到本地。

        V2.1 批次3（P1-1）：游标仅按「成功解密 + 成功合并」推进——失败条目
        （decrypt/merge 异常）挡住游标，下次 pull 自动重试；连续失败计入
        ``_pull_failures`` 并发 ``sync.pull_failed`` 事件（死信告警）。
        """
        self._require_config()
        since = self._cursor
        items = await self._download(since)
        if items is None:
            # 非 2xx / 网络错误 → 计失败（不再当空成功推进）
            self._last_sync_ok = False
            self._pull_failures += 1
            return False
        if not items:
            self._last_sync_ok = True
            self._pull_count += 1
            return True

        total_merged = 0
        success_updated = since
        blocking_updated: int | None = None  # 失败条目的最小 updated_at，游标不得越过
        failed: list[str] = []
        # B-09（2026-08-27）：worker download 改 `>= since` 后，同秒条目在游标
        # 推进后仍会被重拉（cursor == 该秒）；按 (resource, device_id) 去重，
        # 防止同秒重复行重复合并/重复计失败。不同 device 同秒条目各自可达
        # （正是原 `>` 严格大于丢失的窗口）。LWW 幂等（remote_time <= local_time
        # 跳过）兜底跨批重复。
        seen: set[tuple[str, str]] = set()
        for item in items:
            resource = item.get("resource")
            data_encrypted = item.get("data_encrypted")
            # B-09: worker 返回 device_id 供去重（主键 = device_id + resource）
            item_device = item.get("device_id") or ""
            dedup_key = (resource, item_device)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            # P1-4: 防御性归一化 —— Worker 返回的 ``updated_at`` 应为 ``unixepoch()``
            # 秒级；万一上游变成毫秒（年份 > 33658 视为 ms），通过 ``_normalize_ts``
            # 折算回秒，保证 ``since=cursor`` 下次请求时单位一致。
            raw_ts = item.get("updated_at", 0)
            updated_at = int(_normalize_ts(raw_ts)) if raw_ts else 0

            if not data_encrypted:
                continue  # 无密文条目不参与水位推进（无数据变更）
            try:
                # P1-4: decrypt_data 执行 PBKDF2 600K 迭代（~300-500ms），
                # 同步调用会阻塞事件循环。用 asyncio.to_thread 移到工作线程。
                # P0.1: 传 sync_group_id 让 v4 密文能用同组密钥解开
                plaintext = await asyncio.to_thread(
                    decrypt_data,
                    data_encrypted,
                    self.sync_token,
                    self.device_id,
                    self.sync_group_id,
                )
                data = json.loads(plaintext)
            except CloudSyncCryptoError as exc:
                logger.warning("decrypt failed for %s: %s (token mismatch?)", resource, exc)
                failed.append(str(resource))
                if blocking_updated is None or updated_at < blocking_updated:
                    blocking_updated = updated_at
                continue
            except json.JSONDecodeError as exc:
                logger.warning("json decode failed for %s: %s", resource, exc)
                failed.append(str(resource))
                if blocking_updated is None or updated_at < blocking_updated:
                    blocking_updated = updated_at
                continue

            try:
                # B-01：_merge_* 是同步 SQLite RMW（list/update/delete/add），
                # 直接调用会阻塞事件循环（AGENTS.md to_thread 铁律）。
                # merge 内 _emit_sync_event → event_bus.emit 线程安全（lock），
                # EventBridge handler 经 call_soon_threadsafe 跨线程（B5 修复）。
                if resource == RESOURCE_NOTES:
                    total_merged += await asyncio.to_thread(self._merge_notes, data)
                elif resource == RESOURCE_TODOS:
                    total_merged += await asyncio.to_thread(self._merge_todos, data)
                elif resource == RESOURCE_SETTINGS:
                    total_merged += await asyncio.to_thread(self._merge_settings, data)
            except Exception as exc:
                logger.warning("merge %s failed: %s", resource, exc)
                failed.append(str(resource))
                if blocking_updated is None or updated_at < blocking_updated:
                    blocking_updated = updated_at
                continue

            if updated_at > success_updated:
                success_updated = updated_at

        # 游标仅按「成功解密+成功合并」推进；失败条目挡住游标（游标停在失败条目
        # 之前），下次 pull 以 since=游标 重拉该失败条目 → 自动重试。
        if blocking_updated is not None and blocking_updated <= success_updated:
            new_cursor = max(since, blocking_updated - 1)
        else:
            new_cursor = success_updated
        if new_cursor > self._cursor:
            self._cursor = new_cursor
            # 仅在游标推进时落盘（避免无新数据时反复写 settings）。
            self._persist_cursor()

        if failed:
            # 死信告警：失败累计 + sync.pull_failed 事件供前端提示
            self._pull_failures += 1
            logger.warning(
                "cloud sync: %d item(s) failed, cursor paused (will retry): %s",
                len(failed),
                failed[:5],
            )
            try:
                self._emit_sync_event(
                    "sync.pull_failed",
                    {
                        "count": len(failed),
                        "resources": failed[:10],
                        "cursor_paused_at": self._cursor,
                    },
                )
            except Exception:
                logger.debug("emit sync.pull_failed failed", exc_info=True)

        self._pull_count += 1
        self._last_sync_ok = not bool(failed)
        if total_merged:
            logger.info("cloud sync pulled %d items", total_merged)
            # T5.4: 合并远端变更后刷新记忆索引，消除「pull 后 recall 命中不了新笔记」的漂移。
            await self._maybe_reindex_after_pull()
        return self._last_sync_ok

    async def _maybe_reindex_after_pull(self) -> None:
        """合并远端数据后重建记忆索引（best-effort，不阻塞同步主流程）。

        仅在注入了 memory_index 且开关开启时执行；rebuild_index 是同步
        I/O + Chroma 写入，放到工作线程避免阻塞事件循环。
        """
        if not self.reindex_after_pull:
            return
        mi = getattr(self, "memory_index", None)
        if mi is None or not hasattr(mi, "rebuild_index"):
            return
        try:
            state = await asyncio.to_thread(mi.rebuild_index)
            entry_count = len(state.get("entries") or []) if isinstance(state, dict) else 0
            logger.info("cloud sync reindexed memory index after pull (%d entries)", entry_count)
        except Exception as exc:
            logger.warning("cloud sync reindex after pull failed: %s", exc, exc_info=True)

    # ── 同步循环 ──

    def _emit_sync_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """发射云同步事件；事件失败不应影响同步主流程。"""
        try:
            event_bus.emit(AppEvent(event_type, payload=payload, source="cloud_sync_client"))
        except Exception:
            logger.debug("emit sync event skipped: %s", event_type, exc_info=True)

    def _sync_status_payload(self, *, push_ok: bool, pull_ok: bool) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "push_ok": push_ok,
            "pull_ok": pull_ok,
            "push_count": self._push_count,
            "pull_count": self._pull_count,
            "cursor": self._cursor,
        }

    async def sync_once(self) -> bool:
        """执行一次完整的 push + pull。"""
        if not self.is_configured():
            return False
        push_ok = False
        pull_ok = False
        try:
            push_ok = await self.push_local_data()
            pull_ok = await self.pull_remote_data()
            ok = push_ok and pull_ok
            self._last_sync_at = time.time()
            self._last_sync_ok = ok
            self._last_error = None if ok else "push or pull failed"
            payload = self._sync_status_payload(push_ok=push_ok, pull_ok=pull_ok)
            self._emit_sync_event("sync.completed" if ok else "sync.failed", payload)
            return ok
        except CloudSyncVersionError as exc:
            # P2-5-A：版本协商拒绝（426）→ fatal + 停循环，复用认证失败同型
            # 链路。升级前重试只会掩盖问题（同步静默停摆→数据孤岛）。
            self._last_sync_ok = False
            self._last_error = f"version: {exc}"
            logger.error("cloud sync rejected by version gate: %s", exc)
            self._emit_sync_event(
                "sync.failed",
                {
                    "device_id": self.device_id,
                    "push_ok": push_ok,
                    "pull_ok": pull_ok,
                    "push_count": self._push_count,
                    "pull_count": self._pull_count,
                    "cursor": self._cursor,
                    "error": str(exc),
                    "reason": "version_too_old",
                    "client_version": SYNC_CLIENT_VERSION,
                    "fatal": True,
                },
            )
            self.stop_sync_loop()
            return False
        except CloudSyncAuthError as exc:
            self._last_sync_ok = False
            self._last_error = f"auth: {exc}"
            logger.error("cloud sync auth failed: %s", exc)
            self._emit_sync_event(
                "sync.failed",
                {
                    "device_id": self.device_id,
                    "push_ok": push_ok,
                    "pull_ok": pull_ok,
                    "push_count": self._push_count,
                    "pull_count": self._pull_count,
                    "cursor": self._cursor,
                    "error": str(exc),
                    "fatal": True,
                },
            )
            # 认证失败时停止循环，避免被 Worker 封禁
            self.stop_sync_loop()
            return False
        except Exception as exc:
            self._last_sync_ok = False
            self._last_error = str(exc)
            logger.warning("cloud sync once failed: %s", exc)
            self._emit_sync_event(
                "sync.failed",
                {
                    "device_id": self.device_id,
                    "push_ok": push_ok,
                    "pull_ok": pull_ok,
                    "push_count": self._push_count,
                    "pull_count": self._pull_count,
                    "cursor": self._cursor,
                    "error": str(exc),
                },
            )
            return False

    async def _sync_loop(self, interval_seconds: int) -> None:
        """定期同步循环。

        P1-14: 连续失败时使用指数退避（interval * 2^n，上限 30 分钟），
        避免 Worker 故障时高频重试加剧负载。
        """
        logger.info(
            "cloud sync loop started: interval=%ds device=%s",
            interval_seconds,
            self.device_id,
        )
        consecutive_failures = 0
        max_backoff = 1800  # 30 分钟上限
        while True:
            try:
                ok = await self.sync_once()
                if ok:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    logger.warning("sync loop iteration failed (#%d)", consecutive_failures)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                consecutive_failures += 1
                logger.warning("sync loop iteration error (#%d): %s", consecutive_failures, exc)
            # 计算下次延迟：成功后用基础间隔；失败后指数退避
            if consecutive_failures > 0:
                backoff_exp = min(consecutive_failures - 1, 5)  # 最多 2^5=32 倍
                delay = min(interval_seconds * (2**backoff_exp), max_backoff)
            else:
                delay = interval_seconds
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
        logger.info("cloud sync loop stopped")

    def start_sync_loop(self, interval_seconds: int = 300) -> None:
        """启动后台同步循环（非阻塞）。

        :param interval_seconds: 同步间隔，默认 5 分钟。
        """
        if self._sync_task and not self._sync_task.done():
            logger.warning("sync loop already running")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("start_sync_loop must be called from a running event loop")
            return
        self._sync_task = loop.create_task(self._sync_loop(interval_seconds))

    def stop_sync_loop(self) -> None:
        """停止同步循环。"""
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        self._sync_task = None

    def is_running(self) -> bool:
        """P1-17: 暴露同步循环运行状态，避免外部直接访问 ``_sync_task`` 私有属性。"""
        return bool(self._sync_task and not self._sync_task.done())

    # ── 状态查询 ──

    def get_status(self) -> dict[str, Any]:
        """返回当前同步状态，供 ``sync.status`` JSON-RPC 方法使用。"""
        return {
            "configured": self.is_configured(),
            "device_id": self.device_id,
            "worker_url": self.worker_url,
            "last_sync_at": self._last_sync_at,
            "last_sync_ok": self._last_sync_ok,
            "last_error": self._last_error,
            "cursor": self._cursor,
            "push_count": self._push_count,
            "pull_count": self._pull_count,
            "loop_running": bool(self._sync_task and not self._sync_task.done()),
        }


# B3 将下列原 CloudSyncClient 方法抽离为 sync/_cloud_sync_crypto 的模块级函数；
# 为保持「外部 import / 既有调用点 / 测试」零改动，这里把它们作为 staticmethod
# 重新绑定到 CloudSyncClient，恢复稳定门面（facade）。
CloudSyncClient._get_worker_config = staticmethod(_get_worker_config)
CloudSyncClient._group_salt = staticmethod(_group_salt)
CloudSyncClient._encrypt_v3 = staticmethod(_encrypt_v3)
CloudSyncClient._encrypt_v4 = staticmethod(_encrypt_v4)
CloudSyncClient.push_info_brief_standalone = staticmethod(push_info_brief_standalone)
CloudSyncClient.push_info_sections_standalone = staticmethod(push_info_sections_standalone)
CloudSyncClient._push_standalone = staticmethod(_push_standalone)
CloudSyncClient.VERSION_BYTE_V4 = VERSION_BYTE_V4


class SyncBridge:
    """桥接 AppContext 与 CloudSyncClient，负责从 settings 读取配置并管理生命周期。"""

    def __init__(self, context: Any) -> None:
        self.context = context
        self.client: CloudSyncClient | None = None

    def _get_setting(self, key: str, default: Any = None) -> Any:
        repo = getattr(self.context, "settings_repository", None)
        if not repo:
            return default
        try:
            return repo.get_setting(key, default)
        except Exception:
            return default

    def _ensure_device_id(self) -> str:
        """从 settings 读取或生成稳定的 device_id。"""
        device_id = self._get_setting("cloud_sync_device_id", "")
        if device_id:
            return device_id
        device_id = f"desktop-{uuid.uuid4().hex[:12]}"
        repo = getattr(self.context, "settings_repository", None)
        if repo:
            try:
                repo.set_setting("cloud_sync_device_id", device_id)
            except Exception as exc:
                logger.warning("persist device_id failed: %s", exc)
        return device_id

    def is_enabled(self) -> bool:
        return bool(self._get_setting("cloud_sync_enabled", False))

    def is_configured(self) -> bool:
        return bool(self._get_setting("cloud_sync_worker_url", "") and self._get_setting("cloud_sync_token", ""))

    def build_client(self) -> CloudSyncClient | None:
        """根据当前 settings 构造 CloudSyncClient。配置不完整时返回 None。"""
        worker_url = self._get_setting("cloud_sync_worker_url", "")
        token = self._get_setting("cloud_sync_token", "")
        register_secret = self._get_setting("cloud_sync_register_secret", "")
        if not worker_url or not token:
            return None
        device_id = self._ensure_device_id()
        # P1-4: 从 settings 恢复游标，避免重启后从 0 全量重拉。
        # 容错：旧版本可能写入了毫秒级或字符串，归一化为秒级 int。
        raw_cursor = self._get_setting("cloud_sync_cursor", 0)
        try:
            initial_cursor = int(_normalize_ts(raw_cursor)) if raw_cursor else 0
        except Exception:
            initial_cursor = 0
        return CloudSyncClient(
            worker_url=worker_url,
            sync_token=token,
            device_id=device_id,
            register_secret=register_secret,
            note_repo=getattr(self.context, "personal_note_repository", None),
            task_repo=getattr(self.context, "personal_task_repository", None),
            settings_repo=getattr(self.context, "settings_repository", None),
            initial_cursor=initial_cursor,
            cursor_persister=self._save_cursor,
            # T5.4: 注入记忆索引，pull 合并后重建索引消除漂移
            memory_index=(getattr(self.context, "memory_index", None) or getattr(self.context, "memory_index_service", None)),
        )

    def _save_cursor(self, value: int) -> None:
        """P1-4: 把游标写回 settings；失败仅记 debug，不影响同步主流程。"""
        repo = getattr(self.context, "settings_repository", None)
        if not repo:
            return
        try:
            repo.set_setting("cloud_sync_cursor", int(value))
        except Exception:
            logger.debug("save cloud_sync_cursor failed", exc_info=True)

    async def start(self) -> bool:
        """启动云同步（若已启用且配置完整）。"""
        if not self.is_enabled():
            logger.info("cloud sync disabled, skip start")
            return False
        if not self.is_configured():
            logger.warning("cloud sync enabled but not configured, skip start")
            return False
        if self.client and self.client.is_running():
            logger.warning("cloud sync already running")
            return True

        self.client = self.build_client()
        if not self.client:
            return False

        # 启动循环（首次同步在循环开始时立即执行）
        interval = int(self._get_setting("cloud_sync_interval_seconds", 300))
        self.client.start_sync_loop(interval_seconds=interval)
        logger.info("cloud sync bridge started")
        return True

    async def stop(self) -> None:
        """停止云同步并释放资源。"""
        if self.client:
            self.client.stop_sync_loop()
            await self.client.close()
            self.client = None
            logger.info("cloud sync bridge stopped")

    def get_status(self) -> dict[str, Any]:
        """返回同步状态。"""
        if not self.client:
            return {
                "enabled": self.is_enabled(),
                "configured": self.is_configured(),
                "running": False,
                "message": "cloud sync not started",
            }
        status = self.client.get_status()
        status["enabled"] = self.is_enabled()
        status["configured"] = self.is_configured()
        status["running"] = self.client.is_running()
        return status
