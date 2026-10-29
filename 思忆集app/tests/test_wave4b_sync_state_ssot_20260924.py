"""波次④包B 契约锚：sync_state 枚举单源化 + file_* 矩阵有意性 + 内部资源词汇映射。

蓝本：deliverables/wave4-sync-ssot-blueprint-2026-09-24.md §3-包B「需新增断言」#1/#3，
以及 #2 的保守案调整版（不补 file_* 键，改为"矩阵无 file_* 键是显式声明的有意设计"注释锚）。

守则第 7 条：本包结论固化为机器断言——
1. ``SyncState`` 枚举（core/sync_protocol.py）是 sync_state 四值唯一真源，
   ``sync/note_sync.py`` 的 4 个 ``SYNC_STATE_*`` 常量必须是其 re-export（逐字等值）；
2. 散字面量棘轮：AST 扫描生产目录中恰为四枚举值的字符串字面量，按文件锁基线、只减不增；
3. ``CONFLICT_STRATEGY_MATRIX`` 无 ``file_*`` 键是包B 裁定的有意设计（源码头注释锚 + 行为锚：
   ``resolve_conflict_strategy("file_*")`` 恒回落 REMOTE_WINS），file_sync_service 假查找
   的清理归波次④包C；
4. 内部词汇映射 ``sync.resource_vocab.translate_resource("tasks") == "todos"`` 单向锚定
   （A 栈内部名 → B/Worker/手机 wire 名），仅固化差异、不接线、不碰 wire。
"""
from __future__ import annotations

import ast
import sys
from enum import Enum
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.sync_protocol import (  # noqa: E402
    CONFLICT_STRATEGY_MATRIX,
    ConflictStrategy,
    SyncState,
    resolve_conflict_strategy,
)
from sync.note_sync import (  # noqa: E402
    SYNC_STATE_CONFLICT,
    SYNC_STATE_DIRTY,
    SYNC_STATE_LOCAL_ONLY,
    SYNC_STATE_SYNCED,
)
from sync.resource_vocab import RESOURCE_ALIASES, translate_resource  # noqa: E402

# ── 棘轮基线（2026-09-24 包B 改动前实测，共 84 处 / 14 文件）─────────────
# 口径：扫描下列生产目录内 .py 的 AST 字符串字面量，值恰为
# {"local_only","dirty","synced","conflict"} 者计数；SSOT 定义文件
# core/sync_protocol.py 豁免。允许存量随治理下降，禁止任何文件新增。
_SCAN_DIRS = [
    "sync", "repositories", "data", "api", "core", "backend",
    "services", "cross_device", "app_runtime", "desktop",
]
_STATE_VALUES = {"local_only", "dirty", "synced", "conflict"}
_EXCLUDED_FILES = {APP_ROOT / "core" / "sync_protocol.py"}

RATCHET_BASELINE: dict[str, int] = {
    "sync/_cloud_sync_entity_handlers.py": 4,
    "sync/note_sync.py": 4,  # 包B re-export 后应降为 0（下方单独断言）
    "sync/ui_support.py": 1,
    "repositories/_note_sync_mixin.py": 10,
    "repositories/_task.py": 12,
    "repositories/_translation.py": 9,
    "repositories/_vocab_sync_mixin.py": 18,
    "data/desknotes_sync_meta.py": 3,
    "data/repositories/desknotes_note_adapter.py": 9,
    "data/repositories/desknotes_task_adapter.py": 1,
    "api/integration_hub.py": 2,
    "backend/handlers/_sync.py": 8,
    "services/capability_types.py": 1,
    "services/lifecycle_recovery_service.py": 2,
}


def _scan_state_literals() -> dict[str, int]:
    counts: dict[str, int] = {}
    for subdir in _SCAN_DIRS:
        base = APP_ROOT / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts or path in _EXCLUDED_FILES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            n = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _STATE_VALUES
            )
            if n:
                rel = str(path.relative_to(APP_ROOT)).replace("\\", "/")
                counts[rel] = n
    return counts


# ── 断言 #1：SyncState 枚举是 SSOT，note_sync 常量为其 re-export ─────────


def test_sync_state_enum_is_ssot():
    # 枚举存在且四值逐字与 note_sync.py:6-11 原常量一致
    assert issubclass(SyncState, Enum)
    assert {member.value for member in SyncState} == _STATE_VALUES
    assert SyncState.LOCAL_ONLY.value == "local_only"
    assert SyncState.DIRTY.value == "dirty"
    assert SyncState.SYNCED.value == "synced"
    assert SyncState.CONFLICT.value == "conflict"

    # note_sync 4 常量 = 枚举值 re-export（既有消费者不破的等值前提）
    assert SYNC_STATE_LOCAL_ONLY == SyncState.LOCAL_ONLY.value
    assert SYNC_STATE_DIRTY == SyncState.DIRTY.value
    assert SYNC_STATE_SYNCED == SyncState.SYNCED.value
    assert SYNC_STATE_CONFLICT == SyncState.CONFLICT.value

    # note_sync.py 自身不得再残留散字面量（真·re-export，非"两边都写"）
    note_sync_src = APP_ROOT / "sync" / "note_sync.py"
    tree = ast.parse(note_sync_src.read_text(encoding="utf-8-sig"))
    leftovers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _STATE_VALUES
    ]
    assert not leftovers, f"sync/note_sync.py 仍残留 sync_state 散字面量: {leftovers}"


def test_state_string_literals_ratchet():
    """棘轮：散字面量按文件锁 2026-09-24 基线，允许存量下降、禁止新增。"""
    current = _scan_state_literals()
    regressions = {
        rel: n for rel, n in current.items() if n > RATCHET_BASELINE.get(rel, 0)
    }
    assert not regressions, (
        "sync_state 四值散字面量较基线新增（请改用 core.sync_protocol.SyncState.*.value）: "
        f"{regressions}"
    )


# ── 断言 #2（保守案调整版）：矩阵无 file_* 键 = 显式声明的有意设计 ────────


def test_matrix_without_file_keys_is_declared_intentional():
    # 行为锚：file_* 无键、恒回落 REMOTE_WINS（包C 处置假查找前的现状锁）
    assert not [key for key in CONFLICT_STRATEGY_MATRIX if key.startswith("file_")]
    for subdir in ("photos", "diaries", "notes", "tasks", "memories", "settings"):
        assert resolve_conflict_strategy(f"file_{subdir}") is ConflictStrategy.REMOTE_WINS

    # 注释锚：sync_protocol.py 矩阵处必须留"有意设计 + 包C 处置"声明，
    # 防止后人把恒回落误当 bug 顺手"修复"（补键/改语义须走包C 裁定）。
    src = (APP_ROOT / "core" / "sync_protocol.py").read_text(encoding="utf-8")
    assert "file_*" in src and "有意" in src and "包C" in src, (
        "CONFLICT_STRATEGY_MATRIX 处缺少『file_* 键缺失为有意设计、清理见波次④包C』注释锚"
    )


# ── 断言 #3：内部资源词汇映射（tasks↔todos 单向锚定，禁碰 wire）──────────


def test_resource_name_translation_tasks_todos():
    # A 栈内部名 → B/Worker/手机 wire 名 的单向锚定
    assert translate_resource("tasks") == "todos"
    assert RESOURCE_ALIASES["tasks"] == "todos"
    # 未登记词汇原样透传（本包只固化差异，未接线任何调用点）
    assert translate_resource("notes") == "notes"
    assert translate_resource("todos") == "todos"
    assert translate_resource("unknown_resource") == "unknown_resource"
