"""AI 响应缓存 — 从 fallback.py 提取的独立缓存模块。

职责：
- 按消息哈希缓存 AI 响应（JSON 文件存储 + 内存 LRU 缓存层）
- 原子写入、TTL 淘汰、并发安全（asyncio + threading 双锁）
- 内存 LRU 层减少 JSON 文件 I/O，提升热点查询性能
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

# P2-3（2026-09-18）：孤儿原子写 tmp 的清扫入口。
# 依赖方向安全：``data/_atomic_json`` 只 import 标准库；``data/__init__`` 是
# PEP 562 惰性导出（不 eager import repositories），故不会触发
# repositories ↔ data 的 import 环（见 data/__init__.py 顶部说明）。
from data._atomic_json import sweep_orphan_tmp

logger = logging.getLogger("siyi.ai.cache")


class _LruMemoryCache:
    """线程安全的内存 LRU 缓存，减少 JSON 文件 I/O。"""

    def __init__(self, capacity: int = 128) -> None:
        self._capacity = capacity
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._hits += 1
                return self._data[key]
            self._misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            if len(self._data) > self._capacity:
                self._data.popitem(last=False)  # 淘汰最老的

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "capacity": self._capacity,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / max(1, self._hits + self._misses), 4),
            }


class AiResponseCache:
    """AI 响应缓存，按消息哈希存储/检索。

    - 支持同步和异步两种访问模式
    - 使用原子写入防止文件损坏
    - 自动 TTL 淘汰和大小裁剪
    """

    def __init__(
        self,
        cache_path: str | Path,
        ttl_seconds: int = 86400,
        max_entries: int = 500,
    ):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        # P2-3（2026-09-18）：本目录的孤儿 tmp 由**写入者自己**清扫（边界清晰，
        # 见 data/_atomic_json 模块 docstring）。本类用 tempfile.mkstemp(
        # prefix=".ai_cache_") 落 tmp，异常路径虽有 unlink，但进程被硬杀或沙箱
        # 同时拒绝写入与清理时会永久残留——2026-09-18 实测生产 data/ 下 43 个
        # .ai_cache_*.tmp（各约 213 KB，集中在 8/21 的 43 分钟窗口）。
        # 年龄门槛 1 天：绝不碰并发写者的在途文件；失败不影响缓存可用性。
        try:
            sweep_orphan_tmp(self.cache_path.parent)
        except Exception:  # noqa: BLE001 —— 清扫是卫生动作，不能让缓存构造失败
            logger.warning("AI 缓存目录孤儿 tmp 清扫失败（忽略）", exc_info=True)
        self.cache_ttl_seconds = ttl_seconds
        self.cache_max_entries = max_entries
        self._cache: dict[str, Any] | None = None
        self._cache_lock = asyncio.Lock()
        self._cache_init_lock = threading.Lock()
        # P3-4: 保留 fire-and-forget 任务的强引用，避免被 GC 提前回收
        self._pending_set_tasks: set[asyncio.Task] = set()
        # S12: 内存 LRU 缓存层，减少热点查询的 JSON 文件 I/O
        self._lru_cache = _LruMemoryCache(capacity=128)

    # ── 公共 API ──

    def make_key(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        model: str | None = None,
        stream: bool | None = None,
    ) -> str:
        """生成消息列表的缓存键（SHA256 哈希）。

        model/stream 为可选参数：传入时参与哈希，避免不同模型/是否流式
        复用同一缓存键而命中错误结果。两者均不传时与旧版本键一致（向后兼容）。
        """
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        if model is not None:
            payload["model"] = model
        if stream is not None:
            payload["stream"] = stream
        joined = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> Any:
        """异步获取缓存条目（带 TTL 检查）。

        S12: 先查内存 LRU 层，命中则直接返回，避免文件 I/O；
        未命中再查 JSON 文件，命中后回填 LRU。
        """
        # S12: 先查 LRU 内存层（无锁快速路径）
        lru_entry = self._lru_cache.get(key)
        if lru_entry is not None:
            if isinstance(lru_entry, dict) and lru_entry.get("ts", 0) + self.cache_ttl_seconds > time.time():
                return lru_entry
            # 已过期，从 LRU 中清除
            self._lru_cache.invalidate(key)

        async with self._cache_lock:
            cache = await self._load_cache_locked()
            entry = cache.get(key)
            if entry is None:
                return None
            if isinstance(entry, dict) and entry.get("ts", 0) + self.cache_ttl_seconds <= time.time():
                del cache[key]
                await self._save_cache_locked()
                # 同步从 LRU 清除
                self._lru_cache.invalidate(key)
                return None
            # S12: 回填 LRU 内存层
            self._lru_cache.put(key, entry)
            return entry

    async def get_many(self, keys: list[str]) -> dict[str, Any]:
        """批量获取缓存条目。"""
        result: dict[str, Any] = {}
        async with self._cache_lock:
            cache = await self._load_cache_locked()
            now = time.time()
            for key in keys:
                entry = cache.get(key)
                if entry is None:
                    continue
                if isinstance(entry, dict) and entry.get("ts", 0) + self.cache_ttl_seconds <= now:
                    del cache[key]
                    continue
                result[key] = entry
            if result:
                await self._save_cache_locked()
        return result

    async def set(self, key: str, text: str) -> None:
        """异步存储缓存条目。

        S12: 同时写入 JSON 文件和 LRU 内存层，保证下次读取命中内存。
        """
        entry = {"text": text, "ts": time.time()}
        async with self._cache_lock:
            cache = await self._load_cache_locked()
            cache[key] = entry
            await self._save_cache_locked()
        # S12: 同步写入 LRU 内存层（在锁外执行，LRU 内部自带线程锁）
        self._lru_cache.put(key, entry)

    def get_sync(self, key: str) -> Any:
        """同步获取缓存（用于同步上下文如 chat_completion）。

        S12: 先查内存 LRU 层，命中则直接返回。
        """
        # S12: 先查 LRU 内存层
        lru_entry = self._lru_cache.get(key)
        if lru_entry is not None:
            if isinstance(lru_entry, dict) and lru_entry.get("ts", 0) + self.cache_ttl_seconds > time.time():
                return lru_entry
            self._lru_cache.invalidate(key)

        cache = self._load_cache_sync()
        entry = cache.get(key)
        if entry is None:
            return None
        if isinstance(entry, dict) and entry.get("ts", 0) + self.cache_ttl_seconds <= time.time():
            del cache[key]
            self._save_cache_sync()
            self._lru_cache.invalidate(key)
            return None
        # S12: 回填 LRU 内存层
        self._lru_cache.put(key, entry)
        return entry

    def set_sync(self, key: str, text: str) -> None:
        """同步存储缓存。

        如果在事件循环中，调度异步 set() 共享同一个 _cache_lock（asyncio.Lock），
        避免 asyncio.Lock 与 threading.Lock 互不排斥导致的并发竞态；
        否则（无事件循环）使用 threading.Lock 保护同步路径。
        """
        try:
            loop = asyncio.get_running_loop()
            # P3-4: ensure_future 返回的 Task 必须保留强引用，否则可能被 GC
            # 回收导致写入丢失；同时挂接 done_callback 记录异常并自动清理。
            task = loop.create_task(self.set(key, text))
            self._pending_set_tasks.add(task)

            def _on_done(t: asyncio.Task) -> None:
                self._pending_set_tasks.discard(t)
                if t.cancelled():
                    return
                exc = t.exception()
                if exc is not None:
                    logger.warning("AiResponseCache.set_sync background task failed: %s", exc)

            task.add_done_callback(_on_done)
        except RuntimeError:
            with self._cache_init_lock:
                self._do_set_sync(key, text)

    # ── 缓存管理 ──

    async def prune(self) -> None:
        """清理过期条目并按大小上限裁剪缓存。"""
        async with self._cache_lock:
            if self._cache is None:
                _ = await self._load_cache_locked()
                if self._cache is None:
                    return
            now = time.time()
            ttl = self.cache_ttl_seconds
            before = len(self._cache)

            self._cache = {k: v for k, v in self._cache.items() if isinstance(v, dict) and v.get("ts", 0) + ttl > now}

            max_entries = self.cache_max_entries
            if len(self._cache) > max_entries:
                sorted_keys = sorted(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].get("ts", 0),
                )
                for k in sorted_keys[: len(self._cache) - max_entries]:
                    del self._cache[k]

            after = len(self._cache)
            if before != after:
                logger.info("AI 缓存清理: 从 %d 条清理到 %d 条", before, after)
                await self._save_cache_locked()
                # S12: JSON 文件已裁剪，清空 LRU 避免残留已删除条目；
                # 下次访问时会从 JSON 重新回填 LRU
                self._lru_cache.clear()

    async def save(self) -> None:
        """显式保存缓存到磁盘（修复 B4: fallback.py 的 _save_cache 误调 prune）。

        如果缓存尚未加载，此方法为空操作。
        """
        async with self._cache_lock:
            if self._cache is None:
                # 尝试加载，如果文件不存在则跳过
                _ = await self._load_cache_locked()
                if self._cache is None:
                    return
            await self._save_cache_locked()

    def clear(self) -> None:
        """清空缓存。

        S12: 同时清空 LRU 内存层。
        """
        with self._cache_init_lock:
            self._cache = {}
        self._write_cache_atomic()
        # S12: 清空 LRU 内存层
        self._lru_cache.clear()

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        if self._cache is None:
            return 0
        return len(self._cache)

    @property
    def lru_stats(self) -> dict[str, int]:
        """S12: 返回 LRU 内存缓存的统计信息（命中数/未命中数/命中率）。

        供 metrics 端点采集，监控 LRU 层的命中率与实际效果。
        """
        return self._lru_cache.stats

    # ── 内部：加载 ──

    async def _load_cache(self) -> dict:
        """异步加载缓存（外部调用，自行获取锁）。"""
        async with self._cache_lock:
            return await self._load_cache_locked()

    async def _load_cache_locked(self) -> dict:
        """异步加载缓存（调用方已持有 _cache_lock）。"""
        if self._cache is not None:
            return self._cache
        loop = asyncio.get_running_loop()
        try:
            if self.cache_path.exists():
                content = await loop.run_in_executor(None, self._read_file)
                self._cache = json.loads(content) if content else {}
            else:
                self._cache = {}
        except Exception as exc:
            logger.warning("AI 缓存文件加载失败: %s", exc)
            self._cache = {}
        return self._cache

    def _load_cache_sync(self) -> dict:
        """同步加载缓存（用于初始化，使用 threading.Lock）。"""
        if self._cache is not None:
            return self._cache
        with self._cache_init_lock:
            if self._cache is not None:
                return self._cache
            try:
                if self.cache_path.exists():
                    with open(self.cache_path, encoding="utf-8") as f:
                        self._cache = json.load(f)
                else:
                    self._cache = {}
            except Exception as exc:
                logger.warning("AI 缓存文件加载失败，回退空缓存: %s", exc)
                self._cache = {}
            return self._cache

    def _read_file(self) -> str | None:
        """在线程池中读取缓存文件。"""
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    # ── 内部：保存（原子写入） ──

    async def _save_cache_locked(self) -> None:
        """保存缓存到文件（已持有锁）。"""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_cache_atomic)
        except Exception as exc:
            logger.warning("AI 缓存文件写入失败: %s", exc)

    def _save_cache_sync(self) -> None:
        """同步保存缓存。"""
        self._write_cache_atomic()

    def _write_cache_atomic(self) -> None:
        """原子写入缓存文件。"""
        data = json.dumps(
            self._cache if self._cache is not None else {},
            ensure_ascii=False,
            indent=2,
        )
        dir_path = self.cache_path.parent
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=".ai_cache_",
            dir=dir_path,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            if os.name == "nt":
                try:
                    import ctypes

                    MOVEFILE_REPLACE_EXISTING = 0x1
                    MOVEFILE_WRITE_THROUGH = 0x8
                    result = ctypes.windll.kernel32.MoveFileExW(
                        str(tmp_path),
                        str(self.cache_path),
                        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
                    )
                    if not result:
                        os.replace(tmp_path, self.cache_path)
                except (OSError, AttributeError):
                    os.replace(tmp_path, self.cache_path)
            else:
                os.replace(tmp_path, self.cache_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

    # ── 内部：同步存储 ──

    def _do_set_sync(self, key: str, text: str) -> None:
        """执行实际的同步缓存存储（需在 _cache_init_lock 保护下调用）。

        S12: 同时写入 LRU 内存层。
        """
        if self._cache is None:
            try:
                if self.cache_path.exists():
                    with open(self.cache_path, encoding="utf-8") as f:
                        self._cache = json.load(f)
                else:
                    self._cache = {}
            except Exception as exc:
                logger.warning("AI 缓存加载失败: %s", exc)
                self._cache = {}
        entry = {"text": text, "ts": time.time()}
        self._cache[key] = entry
        self._write_cache_atomic()
        # S12: 同步写入 LRU 内存层
        self._lru_cache.put(key, entry)
