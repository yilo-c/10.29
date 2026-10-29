"""波次④包 C 契约测试：双开互斥守卫 + 词表分歧锁 + 岛屿终判守护。

蓝本：deliverables/wave4-sync-ssot-blueprint-2026-09-24.md §3-包C。
- §9-4「orchestrator(A) 与 SyncBridge(B) 可同开、两栈守卫完全互不感知」→
  固化为「可同开但重叠资源必告警」（启动 WARN 日志 + 诊断标记）。
- 词表分歧锁：A 栈 RESOURCE_SPECS 与 Worker ALLOWED_RESOURCES 的差集是
  **有意的两层词表**（§3-C#2），不得静默合并。
- 岛屿终判守护：channels / file_sync_service 已整删，不得复活。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

REPO_ROOT = APP_ROOT.parent
TOOLING_PROVIDER = APP_ROOT / "app_runtime" / "providers" / "_tooling_provider.py"
HEADLESS_MAIN = APP_ROOT / "backend" / "headless_main.py"
WORKER_SYNC_TS = REPO_ROOT / "cloudflare" / "worker" / "src" / "sync.ts"

# B 栈（SyncBridge ↔ Worker wire）周期同步资源集（sync/_cloud_sync_crypto.py RESOURCE_*）
B_STACK_RESOURCES = ("notes", "todos", "settings")


@pytest.fixture(autouse=True)
def _clean_stack_state():
    from api.sync_orchestrator import reset_stack_launch_state

    reset_stack_launch_state()
    yield
    reset_stack_launch_state()


# ────────────────────────────── 双开守卫 ──────────────────────────────


def test_dual_stack_launch_observable(caplog):
    """A+B 装配、重叠资源（notes/tasks↔todos）同开 → 启动 WARN + 诊断标记。

    §9-4 现状「可同开互不感知」的显式化：两栈各自 note_stack_launch 登记后，
    对同一 canonical resource（todos 经映射归一为 tasks）同开必须产出可观测告警。
    """
    from api.sync_orchestrator import (
        RESOURCE_SPECS,
        get_dual_stack_diagnostic,
        note_stack_launch,
    )

    with caplog.at_level(logging.WARNING, logger="siyi.api.sync_orchestrator"):
        diag_a = note_stack_launch("orchestrator", list(RESOURCE_SPECS.keys()), enabled=True)
        assert diag_a["overlap"] == [], "仅 A 栈单开不得告警（默认装机态两栈都不跑）"
        assert get_dual_stack_diagnostic() is None

        diag_b = note_stack_launch("cloud_bridge", B_STACK_RESOURCES, enabled=True)

    # 重叠 = notes（两名相同）+ tasks↔todos（经 alias 归一）
    assert sorted(diag_b["overlap"]) == ["notes", "tasks"]
    marker = get_dual_stack_diagnostic()
    assert marker is not None
    assert marker["stacks"] == ["cloud_bridge", "orchestrator"]
    assert sorted(marker["overlap"]) == ["notes", "tasks"]

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING and "dual-stack" in r.getMessage()]
    assert warnings, "双开重叠必须产出启动 WARN 日志（可观测化 §9-4）"
    msg = warnings[-1].getMessage()
    assert "notes" in msg and "tasks" in msg


def test_dual_stack_no_warn_when_b_stack_disabled():
    """B 栈 cloud_sync_enabled=False → 不算双开，不得告警。"""
    from api.sync_orchestrator import (
        RESOURCE_SPECS,
        get_dual_stack_diagnostic,
        note_stack_launch,
    )

    note_stack_launch("orchestrator", list(RESOURCE_SPECS.keys()), enabled=True)
    diag = note_stack_launch("cloud_bridge", B_STACK_RESOURCES, enabled=False)
    assert diag["overlap"] == []
    assert get_dual_stack_diagnostic() is None


def test_dual_stack_disjoint_resources_no_warn():
    """两栈同开但资源集无交集 → 不告警（守卫按资源判定，不按栈一刀切）。"""
    from api.sync_orchestrator import get_dual_stack_diagnostic, note_stack_launch

    note_stack_launch("orchestrator", ["news", "translations"], enabled=True)
    note_stack_launch("cloud_bridge", ["settings"], enabled=True)
    assert get_dual_stack_diagnostic() is None


def test_dual_stack_guard_wired_at_both_construction_sites():
    """构造点接线契约：A 栈（_tooling_provider）与 B 栈（headless_main）都要登记。"""
    import re

    provider_src = TOOLING_PROVIDER.read_text(encoding="utf-8")
    headless_src = HEADLESS_MAIN.read_text(encoding="utf-8")
    assert re.search(
        r"note_stack_launch\(\s*STACK_ORCHESTRATOR", provider_src
    ), "_tooling_provider.py A 栈构造点必须调用 note_stack_launch 登记资源集"
    assert re.search(
        r"note_stack_launch\(\s*STACK_CLOUD_BRIDGE", headless_src
    ), "headless_main.py B 栈构造点（:821-823）必须调用 note_stack_launch 登记资源集"


# ────────────────────────────── 词表分歧锁 ──────────────────────────────


def _worker_allowed_resources() -> set[str]:
    import re

    ts_src = WORKER_SYNC_TS.read_text(encoding="utf-8")
    m = re.search(r"const ALLOWED_RESOURCES = new Set\(\[(.*?)\]\)", ts_src)
    assert m, "cloudflare/worker/src/sync.ts ALLOWED_RESOURCES 解析失败"
    return {item.strip().strip("'\"") for item in m.group(1).split(",")}


def test_orchestrator_and_worker_resource_vocab_divergence_documented():
    """锁「有意两层词表」：A 栈(RESOURCE_SPECS) ↔ Worker(ALLOWED_RESOURCES) 差集固定。

    - A 独有（automation_hub 域，Worker 不收）：news/translations/wordbooks/words；
    - B/Worker 独有：todos（= A 栈 tasks 的 wire 别名）+ settings/info_brief/info_sections
      （设备配置与资讯频道，不经 hub）；
    - todos↔tasks 映射必须经 WORKER_RESOURCE_ALIAS 显式声明，两栈词表不得静默合并。
    """
    from api.sync_orchestrator import RESOURCE_SPECS, WORKER_RESOURCE_ALIAS

    a_vocab = set(RESOURCE_SPECS.keys())
    w_vocab = _worker_allowed_resources()

    # A 独有 = hub 域四资源 + tasks（tasks 与 W 的 todos 是同一实体的两层名字）
    assert a_vocab - w_vocab == {"news", "translations", "wordbooks", "words", "tasks"}
    assert w_vocab - a_vocab == {"todos", "settings", "info_brief", "info_sections"}
    assert WORKER_RESOURCE_ALIAS.get("todos") == "tasks"

    # 归一后的净差集（wire 名经 alias 归一到 A 栈名）：
    # A 有 B 无 = {news,translations,wordbooks,words}；B 有 A 无 = 配置/资讯频道三键
    normalized_w = {WORKER_RESOURCE_ALIAS.get(r, r) for r in w_vocab}
    assert a_vocab - normalized_w == {"news", "translations", "wordbooks", "words"}
    assert normalized_w - a_vocab == {"settings", "info_brief", "info_sections"}
    assert a_vocab & normalized_w == {"notes", "tasks"}, "归一后重叠集仅限 notes/tasks（双开守卫判定基准）"


# ────────────────────────────── 岛屿终判守护 ──────────────────────────────


@pytest.mark.parametrize("module_name", ["sync.channels", "sync.file_sync_service"])
def test_wave4c_dead_islands_stay_deleted(module_name: str):
    """波次④包 C 岛屿终判：两岛零生产/零测试引用已整删，不得复活。"""
    import importlib

    for stale in [m for m in sys.modules if m == module_name or m.startswith(module_name + ".")]:
        del sys.modules[stale]
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_wave4c_lazy_reexport_no_island_entries():
    """sync/__init__.py 惰性映射不得再登记已删岛屿。"""
    from sync import _MODULE_NAMES

    assert "channels" not in _MODULE_NAMES
    assert "file_sync_service" not in _MODULE_NAMES


def test_wave4c_sync_protocol_kept_as_declaration_layer():
    """sync_protocol importer 归零后仍保留（包 B 声明层 SSOT），不得连带删除。"""
    import core.sync_protocol  # noqa: F401  (可导入即存活)
