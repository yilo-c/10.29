# -*- coding: utf-8 -*-
"""C-1 执行器收敛 步骤 0：把 B 套三样安全策略钉到 A 套（capability_router / _file_ops）。

审计波次②（C-1）先红契约测试。断言 A 套 ``file.read/write/copy`` 必须具备 B 套独有的
三样安全策略，且现有 A 套写边界语义不得回归：

① 敏感路径黑名单（``_SENSITIVE_PATTERNS`` / ``is_sensitive_path``）
② ``SIYI_AGENT_ALLOW_FILE_WRITE`` env 门控
③ agent_outputs 兜底夹取（白名单外、env 未放开时夹回而非直接拒）
⓪ 现有 write_roots 白名单 + write_guard 快照回滚链**不回归**

步骤 2 迁移前预期：**新策略断言红**（1/3/4/5/7），**防回归钉绿**（2/6）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest  # noqa: E402

from assistant.capability_router import AssistantCapabilityRouter  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402

ALLOW_ENV = "SIYI_AGENT_ALLOW_FILE_WRITE"


def _build_router(tmp_path: Path, *, with_guard: bool = False):
    """构造最小 A 套 router（参考 tests/test_assistant_capability_router.py 的构造法）。

    workspace 与 data（runtime）分列，令「白名单内非 workspace 路径」与
    「白名单外路径」可被明确区分。with_guard=True 时注入真 WriteGuard，用于钉快照回滚链。
    """
    runtime_root = tmp_path / "runtime"
    workspace_root = tmp_path / "workspace"
    runtime_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))
    write_guard = None
    trail = None
    if with_guard:
        from core.audit_rollback import AuditTrail, WriteGuard

        trail = AuditTrail(data_dir=str(tmp_path / "audit"))
        write_guard = WriteGuard(trail)
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
        write_guard=write_guard,
    )
    router.shell_execution.workspace_root = workspace_root
    return router, workspace_root, runtime_root, trail


# ── ① 敏感黑名单：拒读（新策略 → 迁移前应红）──────────────────────────────
def test_a_file_read_rejects_sensitive_env(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, workspace_root, _, _ = _build_router(tmp_path)
    secret = workspace_root / ".env"
    secret.write_text("SECRET_TOKEN=leakme", encoding="utf-8")

    res = router.file_domain.file_read({"path": str(secret)})

    assert res.get("success") is False, f"A 套未拒读敏感文件: {res}"
    assert "禁止读取敏感文件" in str(res.get("reason", "")), res
    assert "leakme" not in str(res), "敏感内容不得回显"


async def test_a_execute_direct_read_rejects_sensitive(tmp_path, monkeypatch):
    """经 router.execute_direct 的异步入口同样必须拦截敏感读取（证明策略落在 A 套本体）。"""
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, workspace_root, _, _ = _build_router(tmp_path)
    cred = workspace_root / "credentials.json"
    cred.write_text('{"api_key": "leakme"}', encoding="utf-8")

    res = await router.execute_direct("file.read", {"path": str(cred)})

    assert res.get("success") is False, f"execute_direct 未拒读敏感文件: {res}"
    assert "禁止读取敏感文件" in str(res.get("reason", "")), res


# ── ⓪ 白名单语义不回归：白名单内、env 未放开仍应放行（防回归钉 → 应绿）──────
def test_a_file_write_inside_whitelist_still_allowed_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, _, runtime_root, _ = _build_router(tmp_path)

    res = router.file_domain.file_write({"path": "reports/demo.txt", "content": "ok"})

    assert res.get("success") is True, f"白名单内写被误伤（迁移不得破坏回滚链）: {res}"
    landed = runtime_root / "reports" / "demo.txt"
    assert landed.is_file() and landed.read_text(encoding="utf-8") == "ok"


# ── ②③ env 门控 + agent_outputs 兜底：白名单外、env 未放开夹回而非拒（应红）───
def test_a_file_write_outside_whitelist_clamps_to_agent_outputs(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, _, runtime_root, _ = _build_router(tmp_path)
    outside = tmp_path / "outside" / "leak.txt"

    res = router.file_domain.file_write({"path": str(outside), "content": "x"})

    assert res.get("success") is True, f"env 未放开时白名单外应夹 agent_outputs 而非直接拒: {res}"
    landed = runtime_root / "agent_outputs" / "leak.txt"
    assert landed.is_file() and landed.read_text(encoding="utf-8") == "x", res
    assert not outside.exists(), "不得写到白名单外原路径"


def test_a_file_write_traversal_clamps_to_agent_outputs(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, _, runtime_root, _ = _build_router(tmp_path)

    res = router.file_domain.file_write({"path": "../../etc/pwn.txt", "content": "x"})

    assert res.get("success") is True, res
    landed = runtime_root / "agent_outputs" / "pwn.txt"
    assert landed.is_file(), f"路径穿越未被夹回 agent_outputs: {res}"


# ── ① 敏感黑名单：拒拷（新策略 → 应红）──────────────────────────────────────
def test_a_file_copy_rejects_sensitive_source(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, _, runtime_root, _ = _build_router(tmp_path)
    src = runtime_root / ".env"
    src.write_text("SECRET=leakme", encoding="utf-8")
    dst = runtime_root / "copied.txt"

    res = router.file_domain.file_copy({"source": str(src), "dest": str(dst)})

    assert res.get("success") is False, f"敏感源未被拒拷: {res}"
    assert "禁止复制敏感文件" in str(res.get("reason", "")), res
    assert not dst.exists(), "拒拷不得落盘"


# ── ⓪ 快照回滚链不回归（防回归钉 → 应绿）────────────────────────────────────
def test_a_file_write_snapshot_rollback_still_restores(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, _, runtime_root, trail = _build_router(tmp_path, with_guard=True)
    from core.audit_rollback import RollbackManager

    manager = RollbackManager(SimpleNamespace(save_json=lambda *a: None), trail)
    target = runtime_root / "keep.txt"
    target.write_text("ORIG", encoding="utf-8")

    res = router.file_domain.file_write({"path": str(target), "content": "NEW"})

    assert res.get("success") is True, res
    # 审计走 core.audit_writer 的单写线程（submit 只入队）：写完立刻读会 IndexError。
    trail.writer.drain()  # Queue.join() —— 建立「已落库 → 再读」的 happens-before
    entry = manager.audit_trail.list_entries(limit=1)[0]
    rb = manager.rollback(entry["entry_id"])
    assert rb.get("rolled_back") is True, rb.get("reason")
    assert target.read_text(encoding="utf-8") == "ORIG", "迁移不得破坏快照回滚链"


# ── ① 黑名单大小写/resolve 语义与真源一致（步骤 3 后：B 套已不再自带副本）────
def test_a_read_blacklist_case_insensitive_matches_b_suite(tmp_path, monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    router, workspace_root, _, _ = _build_router(tmp_path)
    # C-1 步骤 3 后语义锤点：黑名单唯一真源 = core.path_safety（B 套内联副本已删），
    # A 套拒读判定必须与它逐字一致（否则“钉一致”就钉了个空对象）。
    from core.path_safety import is_sensitive_path

    import services.react_executor as _b

    assert not hasattr(_b, "_is_sensitive_path") and not hasattr(_b, "_SENSITIVE_PATTERNS"), (
        "B 套不得重新长出第二份敏感黑名单副本（C-1 已收敛）"
    )

    for rel in [".SSH/id_rsa", "APP.SETTINGS.JSON", ".ENV"]:
        p = workspace_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("leak", encoding="utf-8")
        # 钉「语义一致」：真源判敏感，A 套必须同样拒读
        assert is_sensitive_path(p), f"真源应判敏感: {rel}"
        res = router.file_domain.file_read({"path": str(p)})
        assert res.get("success") is False, f"A 套未与真源一致拒读: {rel} -> {res}"
        assert "禁止读取敏感文件" in str(res.get("reason", "")), res


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
