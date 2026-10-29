"""M2.1 SEC-1 + QUA-6：审批条目语义（args_sha256 绑定 + TTL 校验 + 一次性消费）。

出口断言（任务书 M2.1-1）：
①过期 pending 条目 resolve 返回 None 且 status 翻 expired；
②args_sha256 不匹配返回 None；
③二次 resolve 同 id 返回 None（一次性，防重放）；
④匹配指纹+未过期成功翻转；
⑤create 后 entry 含 args_sha256（sha256 可复算）。

隔离：ConfirmationStore 直连 tmp_path 临时数据目录，绝不触碰 data/data 生产队列。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pytest

from services.confirmation_store import ConfirmationStore, compute_args_sha256


@pytest.fixture
def store(tmp_path) -> ConfirmationStore:
    s = ConfirmationStore(data_dir=str(tmp_path))
    s.clear()
    return s


def _raw(store: ConfirmationStore, cid: str) -> dict:
    data = json.loads(Path(store.path).read_text(encoding="utf-8"))
    return data[cid]


def test_expired_pending_resolve_returns_none_and_flips_expired(store: ConfirmationStore) -> None:
    """①QUA-6：resolve 须校验 expires_at（与 get() 同款判定），过期拒批并翻 expired。"""
    cid = store.create("notes.delete", {"id": 1}, source="butler", ttl_s=-1.0)
    assert store.resolve(cid, approved=True) is None
    assert _raw(store, cid)["status"] == "expired"


def test_args_sha256_mismatch_rejected(store: ConfirmationStore) -> None:
    """②SEC-1：批准绑定参数指纹，指纹不符拒批且条目保持 pending 可复查。"""
    cid = store.create("notes.delete", {"id": 1}, source="butler")
    wrong = hashlib.sha256(b'{"id": 2}').hexdigest()
    assert store.resolve(cid, approved=True, args_sha256=wrong) is None
    assert _raw(store, cid)["status"] == "pending"


def test_resolve_is_one_shot_replay_denied(store: ConfirmationStore) -> None:
    """③一次性消费：同 id 二次 resolve（含重放）返回 None。"""
    cid = store.create("tasks.delete", {"id": 7}, source="butler")
    first = store.resolve(cid, approved=True)
    assert first is not None and first["status"] == "approved"
    assert store.resolve(cid, approved=True) is None
    assert store.resolve(cid, approved=False) is None


def test_matching_fingerprint_unexpired_flips(store: ConfirmationStore) -> None:
    """④匹配指纹+未过期 → 成功翻转 approved。"""
    args = {"id": 3, "note": "思忆"}
    cid = store.create("notes.delete", args, source="butler")
    resolved = store.resolve(cid, approved=True, args_sha256=compute_args_sha256(args))
    assert resolved is not None and resolved["status"] == "approved"


def test_create_records_recomputable_args_sha256(store: ConfirmationStore) -> None:
    """⑤create 后 entry 含 args_sha256 且可按任务书口径复算。"""
    args = {"b": 2, "a": "中文"}
    cid = store.create("x.y", args, source="butler")
    entry = _raw(store, cid)
    expected = hashlib.sha256(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert entry["args_sha256"] == expected


def test_rpc_replay_second_approve_denied_zero_rows(tmp_path, monkeypatch) -> None:
    """SEC-1 重放路径：同 rpc pending 二次批准 → 拒（UPDATE 命中 0 rows 等价），仅一次真执行。"""
    from backend.handlers import _sandbox
    from backend.method_registry import RISK_R4, MethodRegistry
    from services.sandbox_manager import SandboxManager

    executions: list[str] = []
    reg = MethodRegistry()

    async def spy(note_id: str = "", **kwargs) -> dict:
        executions.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy, risk=RISK_R4)
    mgr = SandboxManager()
    monkeypatch.setattr(_sandbox, "get_sandbox_manager", lambda: mgr)
    pid = mgr.add("notes.delete", {"note_id": "R1"}, {"action": "notes.delete"}, kind="rpc")
    first = asyncio.run(_sandbox._handle_sandbox_approve(pid, registry=reg))
    assert first["success"] is True
    second = asyncio.run(_sandbox._handle_sandbox_approve(pid, registry=reg))
    assert "error" in second
    assert second.get("success") is not True
    assert executions == ["R1"]


def test_sandbox_add_preview_carries_fingerprint(tmp_path, monkeypatch) -> None:
    """附加：sandbox_manager.add() preview 增记同款 args_sha256（SEC-1 指纹贯通）。"""
    monkeypatch.setenv("SIYI_DATA_DIR", str(tmp_path))
    from services.sandbox_manager import SandboxManager

    args = {"command": "notepad.exe"}
    sm = SandboxManager()
    pid = sm.add("app_launch", args, {"action": "app_launch"}, kind="rpc")
    entry = sm.get(pid)
    assert entry["preview"]["args_sha256"] == compute_args_sha256(args)
