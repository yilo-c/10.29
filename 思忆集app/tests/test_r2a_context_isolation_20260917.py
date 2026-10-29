# -*- coding: utf-8 -*-
"""R2-A1 + R2-C1 修复回归（2026-09-17）。

两个缺陷同属一根因族（AppContext 装配缺失 / 数据目录不隔离）：

R2-A1 — info_source_registry 全仓零装配：
    修复前 backend/handlers/_info_sources.py 与 InfoCollectorService 各自
    新建 InfoSourceRegistry（且 collector 兜底实例不带 settings），
    用户经 info.sources.add/toggle/remove/reset 写入的配置不被采集器消费。
    本文件锁死：AppContext 装配共享实例 → RPC 写入 → 采集器（含未注入
    共享实例的兜底路径）都能读到同一份配置。

R2-C1 — SIYI_DATA_DIR 数据目录隔离：
    修复前图谱 / 记忆 / info 缓存三处绕过 SIYI_DATA_DIR 直写生产
    思忆集app/data/。本文件锁死：隔离实例只写隔离目录，生产同名文件
    md5 前后不变；且 env 未设时默认路径与旧行为逐字一致。

注意：思忆集app/data/** 在 .gitignore 内，git status 不可见，因此生产
数据是否被写穿一律用 md5 前后对比判定，不用 git。
"""

from __future__ import annotations

import hashlib
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.info_source_registry import InfoSourceRegistry  # noqa: E402

PROD_DATA_DIR = APP_ROOT / "data"

# 生产侧需守护的文件（隔离实例写穿会改变其 md5）
_GUARDED_PROD_FILES = (
    PROD_DATA_DIR / "knowledge_graph.json",
    PROD_DATA_DIR / "info_cache.json",
    PROD_DATA_DIR / "memory_fts.db",
)


# ── 工具 ─────────────────────────────────────────────────────────────


def _md5(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


def _snapshot_guarded() -> dict[str, str | None]:
    return {str(p): _md5(p) for p in _GUARDED_PROD_FILES}


def _assert_guarded_unchanged(before: dict[str, str | None]) -> None:
    after = _snapshot_guarded()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not changed, f"生产数据被隔离实例写穿（md5 变化）: {changed}"


class _StubRpcRegistry:
    """最小 JSON-RPC registry：只收集 handler，供直接调用。"""

    def __init__(self) -> None:
        self.methods: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.methods[name] = fn


# ── 共享 AppContext（隔离数据目录）─────────────────────────────────


@pytest.fixture(scope="module")
def isolated_ctx(tmp_path_factory):
    """构造一个以临时 SIYI_DATA_DIR 为数据目录的 headless AppContext。"""
    data_dir = tmp_path_factory.mktemp("siyi-r2a-data")
    saved = os.environ.get("SIYI_DATA_DIR")
    os.environ["SIYI_DATA_DIR"] = str(data_dir)
    try:
        from app_runtime.app_context import AppContext

        ctx = AppContext(alarms=[], headless=True, data_base_dir=str(data_dir))
    finally:
        # 构造完成后恢复进程环境，避免污染同进程其它测试
        if saved is None:
            os.environ.pop("SIYI_DATA_DIR", None)
        else:
            os.environ["SIYI_DATA_DIR"] = saved
    return ctx, data_dir


# ── R2-A1：共享注册表装配 + RPC↔采集器联动 ───────────────────────────


def test_appcontext_assembles_info_source_registry(isolated_ctx):
    """AppContext 必须装配 info_source_registry，且 router 侧拿到同一实例。"""
    ctx, _ = isolated_ctx
    reg = ctx.info_source_registry
    assert reg is not None, "AppContext 未装配 info_source_registry（R2-A1 未修复）"
    assert isinstance(reg, InfoSourceRegistry)
    # 同一实例必须下达到 router（workflow.publish_article / router 侧采集路径）
    assert ctx.autonomous_assistant_manager.router.info_source_registry is reg


@pytest.mark.asyncio
async def test_rpc_write_reaches_collector_shared_and_fallback(isolated_ctx):
    """info.sources.add 写入 → 采集器（注入/兜底两条路径）都能读到。"""
    from backend.handlers import _info_sources
    from services.info_collector_service import InfoCollectorService

    ctx, _ = isolated_ctx
    reg = ctx.info_source_registry

    # ① 经真实 RPC handler 写入自定义源
    stub = _StubRpcRegistry()
    await _info_sources.register(stub, ctx)
    resp = await stub.methods["info.sources.add"](
        name="r2a_probe_feed",
        url="https://example.invalid/r2a.xml",
        parser_type="rss",
        language="en",
        category_hint="dev",
        display_name="R2A探针源",
    )
    assert resp.get("success") is True, resp
    listed = await stub.methods["info.sources.list"]()
    assert "r2a_probe_feed" in {s["name"] for s in listed["sources"]}

    # ② RPC 实例 === context 共享实例（内存态联动）
    assert reg.get_source("r2a_probe_feed") is not None

    # ③ 注入共享实例的采集器：同一对象、能看到新源
    svc = InfoCollectorService(
        ai_client=None,
        source_registry=ctx.info_source_registry,
        settings_repository=ctx.settings_repository,
    )
    assert svc._source_registry is reg
    assert svc._source_registry.get_source("r2a_probe_feed") is not None

    # ④ 兜底路径（cron/scheduler 等未显式注入的构造点）：修复前裸建
    #    InfoSourceRegistry() 丢 settings → 读不到用户配置；修复后按 settings
    #    自建，仍能看到 RPC 已落盘的源。
    fallback = InfoCollectorService(settings_repository=ctx.settings_repository)
    assert fallback._source_registry is not reg
    assert fallback._source_registry.get_source("r2a_probe_feed") is not None, "采集器兜底实例未按 settings 自建 —— info.sources.* 配置对采集器不可见"

    # ⑤ 修前行为反证：旧的裸构造（不带 settings）确实读不到已落盘配置。
    #    这正是 R2-A1 的根因——兜底实例丢 settings 后回落到纯默认源。
    assert InfoSourceRegistry().get_source("r2a_probe_feed") is None


def test_consumption_sites_reference_shared_registry():
    """锁死消费点接线：cron / headless 定时器 / router 两处均取 context 共享实例。"""
    for rel in (
        "services/cron_actions.py",
        "backend/headless_main.py",
        "assistant/_router_content.py",
        "assistant/capability_router.py",
    ):
        text = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert 'source_registry=getattr(context, "info_source_registry", None)' in text or (
            'source_registry=getattr(self, "info_source_registry", None)' in text
        ), f"{rel} 未接线共享 info_source_registry"


# ── R2-C1：隔离探针（图谱 / 记忆 / info 缓存）───────────────────────


def test_isolation_probe_writes_only_into_isolated_dir(isolated_ctx, monkeypatch):
    """隔离实例的三处写路径只落临时目录，生产同名文件 md5 不变。"""
    ctx, data_dir = isolated_ctx
    data_dir = Path(data_dir)
    # 真实运行隔离实例时 SIYI_DATA_DIR 全程在场；此处显式复现，
    # 保证调用期解析（_resolve_persist_path）也落在隔离目录。
    monkeypatch.setenv("SIYI_DATA_DIR", str(data_dir))
    before = _snapshot_guarded()

    # ① 图谱（记忆域的 KnowledgeGraphStore）：路径须解析到隔离目录
    kg = ctx.knowledge_graph_store
    assert Path(kg.path).resolve() == (data_dir / "knowledge_graph.json").resolve()
    kg.add_entity("r2a-entity-1", "R2A隔离探针实体")
    kg.save()
    assert (data_dir / "knowledge_graph.json").is_file()

    # ② info 缓存持久化
    from services import _info_collector_utils as utils

    # 写前护栏：解析出的路径必须已在隔离目录内，否则立即失败而非写穿生产
    assert Path(utils._resolve_persist_path()).resolve() == (data_dir / "info_cache.json").resolve()
    # 修前行为反证：旧实现把源码树 data/info_cache.json 固化为模块常量，
    # 与隔离目录不同源 —— 即"env 在场仍写生产"的根因。
    legacy_path = Path(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(utils.__file__))),
            "data",
            "info_cache.json",
        )
    ).resolve()
    assert legacy_path == (PROD_DATA_DIR / "info_cache.json").resolve()
    assert Path(utils._resolve_persist_path()).resolve() != legacy_path
    saved_cache = dict(utils._cache)
    try:
        utils._cache.clear()
        utils._cache.update({"items": [{"id": "r2a"}], "brief": "r2a", "cached_at": 1.0})
        utils._save_persisted_cache()
    finally:
        utils._cache.clear()
        utils._cache.update(saved_cache)
    assert (data_dir / "info_cache.json").is_file()

    # ③ 记忆索引（HybridMemorySearch 的 FTS 库 + chroma 目录）
    assert (data_dir / "memory_fts.db").is_file(), "记忆 FTS 库未落在隔离目录"
    assert (data_dir / "chroma" / "memories").is_dir(), "chroma 目录未落在隔离目录"

    _assert_guarded_unchanged(before)


def test_memory_context_honors_env_when_no_explicit_dir(tmp_path, monkeypatch):
    """MemoryContext 未显式传 data_base_dir 时必须 honor SIYI_DATA_DIR。"""
    from memory.context import MemoryContext

    isolated = tmp_path / "mem-env"
    isolated.mkdir()
    monkeypatch.setenv("SIYI_DATA_DIR", str(isolated))
    before = _snapshot_guarded()

    mc = MemoryContext(
        data_store=MagicMock(name="data_store"),
        data_base_dir=None,
        ai_client=MagicMock(name="ai_client"),
        settings_repository=MagicMock(name="settings_repository"),
        user_profile_service=MagicMock(name="user_profile_service"),
        note_repository=MagicMock(name="note_repository"),
        task_repository=MagicMock(name="task_repository"),
        inbox_repository=MagicMock(name="inbox_repository"),
        hybrid_search_service=None,
    )
    assert Path(mc.knowledge_graph_store.path).resolve() == (isolated / "knowledge_graph.json").resolve()
    assert (isolated / "chroma" / "memories").is_dir()

    mc.knowledge_graph_store.add_entity("r2a-mem-1", "R2A记忆探针")
    mc.knowledge_graph_store.save()
    assert (isolated / "knowledge_graph.json").is_file()

    _assert_guarded_unchanged(before)


def test_graph_store_default_path_honors_env(tmp_path, monkeypatch):
    """KnowledgeGraphStore 默认路径须 honor SIYI_DATA_DIR。"""
    from knowledge.graph_store import KnowledgeGraphStore

    isolated = tmp_path / "kg-env"
    isolated.mkdir()
    monkeypatch.setenv("SIYI_DATA_DIR", str(isolated))
    before = _snapshot_guarded()

    store = KnowledgeGraphStore()
    assert Path(store.path).resolve() == (isolated / "knowledge_graph.json").resolve()
    store.add_entity("r2a-kg-1", "R2A图谱探针")
    store.save()
    assert (isolated / "knowledge_graph.json").is_file()

    _assert_guarded_unchanged(before)


# ── 兼容性护栏：env 未设时默认路径与旧行为逐字一致 ───────────────────


def test_default_paths_unchanged_without_env(monkeypatch):
    monkeypatch.delenv("SIYI_DATA_DIR", raising=False)

    from knowledge.graph_store import KnowledgeGraphStore
    from services import _info_collector_utils as utils

    assert KnowledgeGraphStore().path == "data/knowledge_graph.json"
    assert Path(utils._resolve_persist_path()).resolve() == (PROD_DATA_DIR / "info_cache.json").resolve()


def test_production_construct_sites_pass_data_base_dir():
    """两条生产构造路径（桌面端 / headless）必须传 data_base_dir。"""
    needle = 'data_base_dir=os.environ.get("SIYI_DATA_DIR") or None'
    for rel in ("app_entry/main.py", "backend/headless_main.py"):
        text = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert needle in text, f"{rel} 未把 SIYI_DATA_DIR 传入 AppContext(data_base_dir=...)"


def test_appcontext_signature_still_accepts_data_base_dir():
    """AppContext 签名不得回退（data_base_dir 仍是受支持的入参）。"""
    from app_runtime.app_context import AppContext

    params = inspect.signature(AppContext.__init__).parameters
    assert "data_base_dir" in params
    assert params["data_base_dir"].default is None
