"""P0 治本契约测试（2026-09-18，批1 安全最小改动）。

覆盖 env-drill 第三波演练暴露的两个 P0，以及为"治本"新建的两个共享基元：

* **P0-1** confirmation TTL 用墙钟 → 系统时钟回拨后 pending 审批永不超时。
  治本：``core.clock.deadline_expired()`` 统一判据 + 墙钟倒退检测；
  ``services/confirmation_store.py`` 迁移为 0 处直读墙钟。
* **P0-2** ``resolve_desknotes_dir()`` 在 ``SIYI_DESKNOTES_DATA_DIR`` 缺 ``notes/``
  时静默回退 ``%LOCALAPPDATA%`` **生产**目录 → 隔离调用方写穿真实用户笔记。
  治本：``core.isolation.resolve_isolated_dir()`` 把"显式 override 即权威"固化；
  ``app_runtime/desknotes_path.py`` 迁移为调用基元。

隔离：ConfirmationStore 直连 tmp_path；desknotes_path 用 monkeypatch 改 env，
绝不触碰真实 data/data 与真实 %LOCALAPPDATA%\\desk-notes。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

import core.clock as clock
from core.isolation import IsolationError, assert_not_under, resolve_isolated_dir
from services.confirmation_store import ConfirmationStore


@pytest.fixture
def freeze(monkeypatch):
    """冻结 ``core.clock.wall_ts`` 到指定时间戳（不动真实系统钟）。"""

    def _freeze(ts: float) -> None:
        monkeypatch.setattr(clock, "wall_ts", lambda: ts)

    return _freeze


@pytest.fixture
def store(tmp_path) -> ConfirmationStore:
    s = ConfirmationStore(data_dir=str(tmp_path))
    s.clear()
    return s


def _raw(store: ConfirmationStore, cid: str) -> dict:
    return json.loads(Path(store.path).read_text(encoding="utf-8"))[cid]


# ── P0-1：confirmation TTL 墙钟倒退 ──────────────────────────────────


def test_rollback_48h_expires_pending_on_resolve(store, freeze) -> None:
    """P0-1 主判据：回拨 48h 后，2 天前的 pending R4 审批必须被拒（翻 expired）。"""
    cid = store.create("notes.delete", {"id": 1}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created - 48 * 3600)

    assert store.resolve(cid, approved=True) is None
    assert _raw(store, cid)["status"] == "expired"


def test_rollback_48h_expires_pending_on_get(store, freeze) -> None:
    """P0-1：``get()`` 同款判据（读路径不得成为绕过口）。"""
    cid = store.create("file.write", {"path": "x"}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created - 48 * 3600)

    assert store.get(cid)["status"] == "expired"


def test_rollback_48h_expires_pending_on_list_and_purge(store, freeze) -> None:
    """P0-1：``list()`` / ``purge_expired()`` 同款判据。"""
    cid = store.create("notes.delete", {"id": 2}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created - 48 * 3600)

    assert [e for e in store.list() if e["confirmation_id"] == cid][0]["status"] == "expired"

    cid2 = store.create("notes.delete", {"id": 3}, source="butler")
    freeze(_raw(store, cid2)["created_at"] - 48 * 3600)
    assert store.purge_expired() >= 1


def test_small_ntp_adjustment_within_tolerance_not_expired(store, freeze) -> None:
    """容忍窗：小幅校时（<60s 倒退）不得误杀合法待审批。"""
    cid = store.create("notes.delete", {"id": 4}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created - 30.0)

    entry = store.resolve(cid, approved=True)
    assert entry is not None
    assert entry["status"] == "approved"


def test_forward_jump_still_expires(store, freeze) -> None:
    """前进跳变仍按过期处理（保守方向，防"未来时间绕过 TTL"）。"""
    cid = store.create("notes.delete", {"id": 5}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created + 10000)

    assert store.resolve(cid, approved=True) is None
    assert _raw(store, cid)["status"] == "expired"


def test_normal_within_ttl_still_approvable(store, freeze) -> None:
    """回归：正常时钟下 TTL 内审批不受本次修改影响。"""
    cid = store.create("notes.delete", {"id": 6}, source="butler")
    created = _raw(store, cid)["created_at"]

    freeze(created + 100)

    entry = store.resolve(cid, approved=True)
    assert entry is not None
    assert entry["status"] == "approved"


def test_confirmation_store_has_no_direct_wall_clock_read() -> None:
    """结构级防回归：本模块不得再直读墙钟（必须经 core.clock）。"""
    src = (APP_ROOT / "services" / "confirmation_store.py").read_text(encoding="utf-8")
    assert "time.time()" not in src
    assert "datetime.now()" not in src
    assert "import time" not in src


# ── core.clock 基元契约 ─────────────────────────────────────────────


def test_deadline_expired_missing_deadline_is_false() -> None:
    """缺 ``expires_at`` 的旧/伪造条目 → False（语义交由调用方）。"""
    assert clock.deadline_expired(2000.0, None, reference=1000.0) is False


def test_detect_rollback_tolerance_boundary() -> None:
    """倒退检测边界：容忍窗内不判倒退，超出即判。"""
    assert clock.detect_rollback(1000.0 - 59.0, 1000.0) is False
    assert clock.detect_rollback(1000.0 - 61.0, 1000.0) is True
    assert clock.detect_rollback(1000.0, None) is False


def test_safe_timestamp_write_refuses_poisoned_overwrite() -> None:
    """P1-5 基元：墙钟倒退时拒绝覆写 last_run（防"恢复后双触发"）。"""
    last_run = 1_700_000_000.0
    rolled_back = last_run - 48 * 3600

    assert clock.safe_timestamp_write(rolled_back, last_run) == last_run
    assert clock.safe_timestamp_write(last_run + 3600, last_run) == last_run + 3600
    assert clock.safe_timestamp_write(1234.0, None) == 1234.0


def test_mono_ts_is_non_decreasing() -> None:
    """单调钟读数非递减（进程内时长判据的正确来源）。"""
    a = clock.mono_ts()
    b = clock.mono_ts()
    assert b >= a


# ── P0-2：desknotes_path override 权威化（经 core.isolation）─────────


@pytest.fixture
def desknotes_env(monkeypatch):
    """清空所有相关 env，返回 monkeypatch 供用例设置。"""
    for k in ("SIYI_DESKNOTES_DATA_DIR", "ELECTRON_USER_DATA_DIR", "SIYI_DISABLE_DESKNOTES_BRIDGE"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _make_notes_dir(root: Path, name: str) -> Path:
    d = root / name
    (d / "notes").mkdir(parents=True, exist_ok=True)
    return d


def test_override_with_notes_resolves(desknotes_env, tmp_path) -> None:
    """override 目录含 notes/ → 直接采用（正常路径）。"""
    from app_runtime.desknotes_path import resolve_desknotes_dir

    target = _make_notes_dir(tmp_path, "iso-ok")
    desknotes_env.setenv("SIYI_DESKNOTES_DATA_DIR", str(target))

    assert resolve_desknotes_dir() == str(target)


def test_override_without_notes_raises_and_never_falls_back_to_production(desknotes_env, tmp_path) -> None:
    """P0-2 主判据：override 缺 notes/ 必须 raise，**绝不**回退生产目录。

    构造：override 指向无 notes/ 的目录；同时把 LOCALAPPDATA 指到一个"假生产"
    （含 desk-notes/siyi-notes/notes）。若实现回退，就会返回假生产路径 → 断言失败。
    """
    from app_runtime.desknotes_path import resolve_desknotes_dir

    bad_override = tmp_path / "iso-bad"
    bad_override.mkdir()

    fake_production = _make_notes_dir(tmp_path, "desk-notes/siyi-notes")
    desknotes_env.setenv("LOCALAPPDATA", str(tmp_path))
    desknotes_env.setenv("SIYI_DESKNOTES_DATA_DIR", str(bad_override))

    with pytest.raises(IsolationError) as ei:
        resolve_desknotes_dir()

    assert "notes/" in str(ei.value)
    assert str(fake_production) not in str(ei.value)


def test_override_pointing_at_nonexistent_dir_raises(desknotes_env, tmp_path) -> None:
    """override 指向不存在目录 → 同样 raise（不得静默回退）。"""
    from app_runtime.desknotes_path import resolve_desknotes_dir

    desknotes_env.setenv("SIYI_DESKNOTES_DATA_DIR", str(tmp_path / "nope"))

    with pytest.raises(IsolationError):
        resolve_desknotes_dir()


def test_no_override_fallback_chain_still_works(desknotes_env, tmp_path) -> None:
    """回归：未设 override 时 fallback 链保持原行为（LOCALAPPDATA 候选仍可用）。"""
    from app_runtime.desknotes_path import resolve_desknotes_dir

    production = _make_notes_dir(tmp_path, "desk-notes/siyi-notes")
    desknotes_env.setenv("LOCALAPPDATA", str(tmp_path))

    assert resolve_desknotes_dir() == str(production)


def test_disable_bridge_short_circuits_before_override(desknotes_env, tmp_path) -> None:
    """显式关闭桥接优先于 override（运维逃生口）。"""
    from app_runtime.desknotes_path import resolve_desknotes_dir

    desknotes_env.setenv("SIYI_DESKNOTES_DATA_DIR", str(tmp_path / "nope"))
    desknotes_env.setenv("SIYI_DISABLE_DESKNOTES_BRIDGE", "1")

    assert resolve_desknotes_dir() is None


def test_candidate_dirs_excludes_override(desknotes_env, tmp_path) -> None:
    """``_candidate_dirs()`` 不得再包含 override（结构级防回归）。"""
    from app_runtime import desknotes_path as dp

    override = tmp_path / "iso-x"
    override.mkdir()
    desknotes_env.setenv("SIYI_DESKNOTES_DATA_DIR", str(override))

    assert override not in dp._candidate_dirs()


# ── core.isolation 基元契约 ─────────────────────────────────────────
#
# 注意：本节的 key 是**合成**的（不存在的配置项），故意**不带本项目的环境变量前缀**。
# 原因：scripts/gen_inventory.py 用 `SIYI_[A-Z0-9_]+` 全仓扫描生成"权威环境变量
# 清单"（docs/inventory.md）——该正则**连注释和 docstring 里的字面量也算**，
# 故带前缀的合成 key 会被当成真实配置项入账，清单里就多出一个根本不存在的开关，
# 后人会误以为可以设。
# 2026-09-18 实测踩过两次：① 合成 key 带前缀 → 漂移；② 修完写注释说明该坑，
# 注释里又写出了那个字面量 → 再次漂移。所以本段注释也刻意不写完整变量名。


def test_resolve_isolated_dir_returns_none_when_unset(monkeypatch) -> None:
    """未声明隔离 → None（调用方走默认链），与"声明了但回退"严格区分。"""
    monkeypatch.delenv("FAKE_ISO_DIR", raising=False)
    assert resolve_isolated_dir("FAKE_ISO_DIR") is None


def test_resolve_isolated_dir_requires_subdir(monkeypatch, tmp_path) -> None:
    """``required_subdir`` 缺失 → raise（含逃生口提示）。"""
    monkeypatch.setenv("FAKE_ISO_DIR", str(tmp_path))
    with pytest.raises(IsolationError) as ei:
        resolve_isolated_dir("FAKE_ISO_DIR", required_subdir="notes", hint_disable_key="FAKE_ISO_DISABLE")
    assert "FAKE_ISO_DISABLE" in str(ei.value)


def test_assert_not_under_catches_production_leak(tmp_path) -> None:
    """正向探针：落在受保护目录之下必须抛错（data/** gitignore 看不见泄漏）。"""
    production = tmp_path / "production"
    (production / "notes").mkdir(parents=True)

    assert_not_under(tmp_path / "iso", production)  # 隔离目录 → 放行

    with pytest.raises(IsolationError):
        assert_not_under(production / "notes" / "a.md", production)
