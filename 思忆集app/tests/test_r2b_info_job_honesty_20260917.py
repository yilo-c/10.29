# -*- coding: utf-8 -*-
"""R2-B 信息/求职诚实性缺陷回归（2026-09-17）。

逐条对应实测缺陷，每条都是"修前应红 / 修后应绿"：

- D-PG-5：``info.items.list(offset=...)`` 返回第一页 —— 仓储 list_items 无
  offset 参数、SQL 只有 LIMIT、kv 路径 `filtered[:limit]`，handler 也没把
  offset 传下去。此处分别锁死表路径 / kv 路径的切片语义与 handler 透传。
- D-PG-2：``job_hunter.scrape`` 全平台失败仍返回 success=True → 任务被标
  completed。此处锁死"全失败=success False + all_platforms_failed"，以及
  部分失败时 success=True 但必须带 errors / failed_platforms。
- D-PG-4：``job_hunter.analyze_stream`` handler 是协程而非 async generator，
  dispatch_stream 直接 AttributeError，一条 chunk 都发不出去。此处经真实
  ``MethodRegistry.dispatch_stream`` 端到端锁死"职位不存在"文本 chunk 送达。
- D5：``rss.list`` 原返回 ``subscriptions`` 键，唯一消费方读 ``feeds`` →
  订阅列表恒空。此处锁死键名契约。
- D4：``notification.test`` 返回 ok:true 但不落库 → list/mark_read/delete
  全指向空。此处锁死写后读 + mark_read/delete 可用 + test 标记保留
  （单条回读走 list：``notification.get`` 已于审计波次①-c 作为僵尸 RPC 下线）。
- D1：``reminder.create`` 调不存在的 ``repo.create_task`` + 错误传参形态，
  创建必失败。此处锁死写后读（create → list 可见）与失败如实报错。
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.method_registry import MethodRegistry
from data._hot_tables import ensure_hot_tables
from data.local_storage import LocalDataStore
from repositories import LocalInfoItemRepository, LocalPersonalTaskRepository
from services.job_hunter.scrapers.base import JobListing


# ══════════════════════════════════════════════════════════════════
# 公共假件
# ══════════════════════════════════════════════════════════════════


class _FakeSettings:
    """最小 settings 仓储（get_setting/set_setting）。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(data or {})

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self._data[key] = value


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> bool:
        self.events.append(event)
        return True

    def on(self, pattern: str, handler: Any) -> None:
        pass

    def off(self, pattern: str, handler: Any) -> None:
        pass


# ══════════════════════════════════════════════════════════════════
# D-PG-5：info.items.list offset 翻页
# ══════════════════════════════════════════════════════════════════

_SEED_COUNT = 25


def _seed_info_items(store: LocalDataStore) -> LocalInfoItemRepository:
    """灌 25 条构造数据（collected_at 递增，用于确定性排序）。"""
    repo = LocalInfoItemRepository(store)
    records = [
        {
            "url": f"https://example.com/item/{i}",
            "title": f"item-{i:02d}",
            "collected_at": f"2026-09-01T00:{i:02d}:00",
        }
        for i in range(_SEED_COUNT)
    ]
    changed = repo.upsert_items(records)
    assert changed == _SEED_COUNT
    return repo


def _assert_paging(repo: LocalInfoItemRepository) -> None:
    page1, total1 = repo.list_items(limit=10, offset=0)
    page2, total2 = repo.list_items(limit=10, offset=10)
    all_items, total3 = repo.list_items(limit=100, offset=0)

    assert total1 == total2 == total3 == _SEED_COUNT
    assert len(page1) == 10 and len(page2) == 10
    assert len(all_items) == _SEED_COUNT
    # 核心断言：第二页必须是全量的第 11-20 条，而不是第一页的重复
    assert [i["id"] for i in page2] == [i["id"] for i in all_items[10:20]]
    assert not ({i["id"] for i in page1} & {i["id"] for i in page2})
    # 越界 offset 返回空（不回落第一页）
    tail, _ = repo.list_items(limit=10, offset=_SEED_COUNT + 5)
    assert tail == []


def test_dpg5_offset_kv_path(tmp_path: Path) -> None:
    """kv 回退路径（热点表未建，即迁移前生产路径）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = _seed_info_items(store)
    assert repo._table_conn() is None, "本用例应走 kv 路径（热点表未建）"
    _assert_paging(repo)


def test_dpg5_offset_table_path(tmp_path: Path) -> None:
    """表路径（ensure_hot_tables 建表后）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    conn = store._sqlite._connect()
    ensure_hot_tables(conn)
    repo = _seed_info_items(store)
    assert repo._table_conn() is not None, "本用例应走表路径（热点表已建）"
    _assert_paging(repo)


def test_dpg5_handler_forwards_offset() -> None:
    """handler 必须把 offset 透传到仓储层（spy 记录收到的关键字参数）。"""
    from backend.handlers import _info_items

    calls: list[dict[str, Any]] = []

    class _SpyRepo:
        def list_items(self, **kwargs: Any):
            calls.append(kwargs)
            return [], 0

    reg = MethodRegistry()
    asyncio.run(_info_items.register(reg, SimpleNamespace(info_item_repository=_SpyRepo())))
    result = asyncio.run(reg.dispatch("info.items.list", {"limit": 10, "offset": 10}))

    assert result == {"items": [], "total": 0}
    assert len(calls) == 1
    assert calls[0]["offset"] == 10, "offset 未透传到仓储层（翻页恒第一页）"
    assert calls[0]["limit"] == 10


# ══════════════════════════════════════════════════════════════════
# D-PG-2：scrape 全平台失败不得假成功
# ══════════════════════════════════════════════════════════════════


class _FailScraper:
    async def search(self, keywords: list[str], city: str = "", page: int = 1):
        raise RuntimeError("anti-bot 403")

    async def close(self) -> None:
        pass


class _OkScraper:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, keywords: list[str], city: str = "", page: int = 1):
        self.calls += 1
        return [
            JobListing(
                id="job-1",
                title="Python 后端工程师",
                company="ACME",
                url="https://example.com/job/1",
                platform="lagou",
            )
        ]

    async def close(self) -> None:
        pass


def _make_job_service(tmp_path: Path, scrapers: dict[str, Any]):
    from services.job_hunter import JobHunterService
    from services.job_hunter.storage import JobStorage

    return JobHunterService(
        ai_client=None,
        settings_repository=_FakeSettings(),
        storage=JobStorage(data_dir=str(tmp_path / "jh")),
        scrapers=scrapers,
    )


def test_dpg2_all_platforms_failed_is_honest_failure(tmp_path: Path, monkeypatch) -> None:
    from services import job_hunter as jh

    monkeypatch.setattr(jh, "_PLATFORM_RETRY_DELAY", 0.0)
    svc = _make_job_service(tmp_path, {"boss": _FailScraper(), "lagou": _FailScraper()})

    result = asyncio.run(svc.search(platforms=["boss", "lagou"], keywords=["python"]))

    assert result["success"] is False, "全平台失败仍报成功（任务会被标 completed）"
    assert result["error"] == "all_platforms_failed"
    assert result["reason"] == "all_platforms_failed"
    assert result["added"] == 0
    assert result["failed_platforms"] == 2
    assert len(result["errors"]) == 2


def test_dpg2_partial_failure_keeps_success_but_reports(tmp_path: Path, monkeypatch) -> None:
    from services import job_hunter as jh

    monkeypatch.setattr(jh, "_PLATFORM_RETRY_DELAY", 0.0)
    svc = _make_job_service(tmp_path, {"boss": _FailScraper(), "lagou": _OkScraper()})

    result = asyncio.run(svc.search(platforms=["boss", "lagou"], keywords=["python"]))

    assert result["success"] is True
    assert result["added"] == 1
    assert result["failed_platforms"] == 1
    assert [e["platform"] for e in result["errors"]] == ["boss"]


def test_dpg2_cron_action_propagates_failure(tmp_path: Path, monkeypatch) -> None:
    """worker/轮询链路：失败必须透传为 status=failed（不硬编码 completed）。"""
    from services import cron_actions, job_hunter as jh

    monkeypatch.setattr(jh, "_PLATFORM_RETRY_DELAY", 0.0)
    svc = _make_job_service(tmp_path, {"boss": _FailScraper()})
    out = cron_actions.execute_cron_action(
        SimpleNamespace(job_hunter_service=svc),
        "job_hunter.scrape",
        {"platforms": ["boss"], "keywords": ["python"], "city": "", "page": 1},
    )

    assert out["success"] is False
    assert out["status"] == "failed"
    assert out["error"] == "all_platforms_failed"


# ══════════════════════════════════════════════════════════════════
# D-PG-4：analyze_stream 无 job 时必须下发文本 chunk
# ══════════════════════════════════════════════════════════════════


def _make_job_registry(tmp_path: Path) -> MethodRegistry:
    from backend.handlers import _job_hunter

    svc = _make_job_service(tmp_path, {})
    reg = MethodRegistry()
    _job_hunter.register_job_hunter(reg, SimpleNamespace(job_hunter_service=svc))
    return reg


async def _drain(reg: MethodRegistry, params: dict[str, Any]) -> tuple[list[str], list[tuple[str, str]]]:
    sent: list[tuple[str, str]] = []

    async def send_chunk(chunk: str, chunk_type: str = "chunk") -> None:
        sent.append((chunk_type, chunk))

    yielded: list[str] = []
    async for chunk in reg.dispatch_stream("job_hunter.analyze_stream", dict(params), send_chunk=send_chunk):
        yielded.append(chunk)
    return yielded, sent


def test_dpg4_stream_sends_not_found_chunk(tmp_path: Path) -> None:
    """经真实 dispatch_stream：无 job 必须有一条文本 chunk 到达聚合器。

    修前：handler 是协程（非 async generator），dispatch_stream 迭代时
    AttributeError → 0 chunk，前端聚合结果为 ''。
    """
    reg = _make_job_registry(tmp_path)
    yielded, sent = asyncio.run(_drain(reg, {"job_id": "no-such-job"}))

    assert yielded, "stream 未产出任何 chunk（handler 非 async generator）"
    assert sent, "send_chunk 从未被调用：chunk 未到达 RPC 聚合通道"
    text = "".join(data for _, data in sent)
    assert "不存在" in text
    assert any(ctype == "chunk" for ctype, _ in sent)


def test_dpg4_non_stream_call_returns_chunks(tmp_path: Path) -> None:
    """非流式调用（无 send_chunk）也必须有可读文本，不再返回空。

    registry 对 async generator handler 的非流式降级是"收集 chunk 列表"
    （method_registry.py:428-447），故此处兼容 str / list 两种聚合形状。
    """
    reg = _make_job_registry(tmp_path)
    result = asyncio.run(reg.dispatch("job_hunter.analyze_stream", {"job_id": "no-such-job"}))
    text = result if isinstance(result, str) else "".join(result or [])
    assert "不存在" in text


class _StubJobSvc:
    """最小 JobHunterService 替身：无 job 时 yield「职位不存在」。"""

    async def analyze_job_stream(self, job_id: str):
        if job_id == "no-such":
            yield "职位不存在"
            return
        yield "正文"


def test_dpg4_handler_is_async_generator() -> None:
    """契约级：dispatch_stream 直接 __anext__ handler 返回值 → handler 必须是
    async generator 函数（修前是普通协程，必 AttributeError）。"""
    from backend.handlers import _job_hunter

    captured: dict[str, Any] = {}

    class _Reg:
        def register(self, name: str, handler: Any, **kw: Any) -> None:
            captured[name] = handler

        def register_with_schema(self, name: str, handler: Any, schema: Any, **kw: Any) -> None:
            captured[name] = handler

    _job_hunter.register_job_hunter(_Reg(), SimpleNamespace(job_hunter_service=_StubJobSvc()))
    handler = captured["job_hunter.analyze_stream"]
    assert inspect.isasyncgenfunction(handler), "handler 不是 async generator，dispatch_stream 必崩"

    async def _probe() -> tuple[list[str], list[str]]:
        sent: list[str] = []

        async def _send(data: str, chunk_type: str = "chunk") -> None:
            sent.append(data)

        stream = handler(job_id="no-such", send_chunk=_send)
        yielded = [chunk async for chunk in stream]
        return yielded, sent

    yielded, sent = asyncio.run(_probe())
    assert yielded == ["职位不存在"]
    assert sent == ["职位不存在"]


# ══════════════════════════════════════════════════════════════════
# D5：rss.list 键名契约 = feeds
# ══════════════════════════════════════════════════════════════════


def _rss_registry(subscriber: Any = None) -> MethodRegistry:
    from backend.handlers import _rss

    reg = MethodRegistry()
    ctx = SimpleNamespace(settings_repository=_FakeSettings(), rss_subscriber=subscriber)
    asyncio.run(_rss.register(reg, ctx))
    return reg


def test_d5_rss_list_returns_feeds_key() -> None:
    """键名契约：唯一消费方（InfoModule.tsx:126）读 feeds。"""
    result = asyncio.run(_rss_registry().dispatch("rss.list", {}))
    assert "feeds" in result, "rss.list 缺 feeds 键 → 桌面端订阅列表恒空"
    assert "subscriptions" not in result, "已裁决单键直改，不做双键过渡"
    assert isinstance(result["feeds"], list)
    assert result["total"] == len(result["feeds"])


def test_d5_rss_list_feeds_carries_subscriptions() -> None:
    """已有订阅时必须出现在 feeds 里（数据路径未被改坏）。"""
    from services.rss_subscriber import RSSSubscriber

    settings = _FakeSettings({"rss_subscriptions": {"subscriptions": [{"id": "feed-1", "url": "https://example.com/rss.xml", "name": "示例源"}]}})
    subscriber = RSSSubscriber(settings_repository=settings)
    result = asyncio.run(_rss_registry(subscriber).dispatch("rss.list", {}))

    assert [f["id"] for f in result["feeds"]] == ["feed-1"]
    assert result["total"] == 1


# ══════════════════════════════════════════════════════════════════
# D4：notification.test 必须真实落库
# ══════════════════════════════════════════════════════════════════


class _FakeDataStore:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load_json(self, key: str, default: Any = None) -> Any:
        import copy

        return copy.deepcopy(self._data.get(key, default))

    def save_json(self, key: str, data: Any) -> None:
        import copy

        self._data[key] = copy.deepcopy(data)


def _notification_registry():
    from backend.handlers._notifications import register_notifications
    from services.notification_dispatcher import NotificationDispatcher

    store = _FakeDataStore()
    settings = _FakeSettings({"notifications_enabled": True})
    bus = _FakeEventBus()
    dispatcher = NotificationDispatcher(data_store=store, settings_repository=settings, event_bus=bus)
    dispatcher.start()
    reg = MethodRegistry()
    register_notifications(reg, SimpleNamespace(notification_dispatcher=dispatcher))
    return reg, store


def test_d4_notification_test_is_persisted_and_operable() -> None:
    reg, _store = _notification_registry()

    res = asyncio.run(reg.dispatch("notification.test", {"channel": "websocket"}))
    assert res.get("ok") is True, f"测试通知未发出: {res}"
    nid = res["notification"]["id"]
    assert res["notification"].get("test") is True

    # 写后读：list 可见且带 test 标记
    listed = asyncio.run(reg.dispatch("notification.list", {}))
    hit = [n for n in listed["items"] if n.get("id") == nid]
    assert hit, "notification.test 后 list 查不到该条（未落库）"
    assert hit[0].get("test") is True

    # mark_read / delete 均可用（单条回读经 list：notification.get 已于审计波次①-c 下线）
    def _read_one(target_id: str) -> dict[str, Any]:
        items = asyncio.run(reg.dispatch("notification.list", {"limit": 200}))["items"]
        found = [n for n in items if n.get("id") == target_id]
        assert found, f"list 读不回 id={target_id}"
        return found[0]

    assert _read_one(nid)["id"] == nid
    marked = asyncio.run(reg.dispatch("notification.mark_read", {"notification_id": nid, "read": True}))
    assert marked["updated"] is True
    reread = _read_one(nid)
    assert reread["read"] is True
    deleted = asyncio.run(reg.dispatch("notification.delete", {"notification_id": nid}))
    assert deleted["deleted"] is True


# ══════════════════════════════════════════════════════════════════
# D1：reminder.create 生产死缺陷
# ══════════════════════════════════════════════════════════════════


def _reminder_registry(repo: Any) -> MethodRegistry:
    from backend.handlers._reminder import register_reminder

    reg = MethodRegistry()
    register_reminder(reg, SimpleNamespace(personal_task_repository=repo))
    return reg


def test_d1_reminder_create_then_list_visible(tmp_path: Path) -> None:
    """真实仓储写后读：create 成功 → list 命中（修前 create 必抛 AttributeError）。"""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalTaskRepository(store)
    reg = _reminder_registry(repo)

    past = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)
    res = asyncio.run(reg.dispatch("reminder.create", {"title": "__r2b_reminder__", "reminder_time": past}))

    assert res.get("success") is True, f"reminder.create 未成功: {res}"
    assert res.get("id"), "reminder.create 未返回 id"
    # 仓储层确实落盘
    stored = [t for t in repo.list_tasks() if t.get("id") == res["id"]]
    assert stored and stored[0].get("reminder_time"), "提醒未写入任务仓储"

    listed = asyncio.run(reg.dispatch("reminder.list", {}))
    assert any(item["id"] == res["id"] for item in listed), "写后读 MISS：reminder.list 查不到刚建的提醒"


def test_d1_reminder_create_passes_kwargs_to_repo() -> None:
    """契约级：handler 必须以 keyword 调用仓储（项目铁律），且不再调 create_task。"""
    calls: list[dict[str, Any]] = []

    class _SpyRepo:
        def list_tasks(self, *a: Any, **kw: Any):
            return []

        def add_task(self, task_info: dict[str, Any]) -> int:
            calls.append(task_info)
            return 7

        def create_task(self, *a: Any, **kw: Any):  # pragma: no cover - 修后不应被调用
            raise AssertionError("handler 仍调用不存在的 repo.create_task")

    res = asyncio.run(_reminder_registry(_SpyRepo()).dispatch("reminder.create", {"title": "x", "reminder_time": 1758000000000}))

    assert res == {"success": True, "id": 7}
    assert len(calls) == 1
    assert calls[0]["title"] == "x"
    assert calls[0]["reminder_time"] == 1758000000000


def test_d1_reminder_create_reports_failure_honestly() -> None:
    """仓储抛错时必须如实报错（不带 success=True 的假成功）。"""

    class _BrokenRepo:
        def list_tasks(self, *a: Any, **kw: Any):
            return []

        def add_task(self, *a: Any, **kw: Any):
            raise RuntimeError("disk full")

    res = asyncio.run(_reminder_registry(_BrokenRepo()).dispatch("reminder.create", {"title": "x"}))

    assert res["success"] is False
    assert "disk full" in res["error"]
