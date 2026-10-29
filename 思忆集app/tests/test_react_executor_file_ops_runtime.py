# -*- coding: utf-8 -*-
"""A-P2：file.* actuation 工具**运行时**普查（真跑执行器、临时目录隔离、不碰真实盘）。

补 A-P1 只读普查之外的"写类 actuation 真返回"证据：file.write→read→find 往返 + 路径穿越防护。

C-1 执行器收敛 步骤 3 重指向说明：本文件的**断言语义保持不变**（写成功、落在受控根内、
穿越被夹回、敏感文件拒读），但提供这些行为的实现已从 B 套内联分支换成 A 套
``assistant/capability/_file_ops.py``（经 ``make_react_executor`` → ``router.dispatch_sync``）。
因此三处形状差异是收敛的**预期结果**，不是回归：

- 返回信封：B 套 ``{"result": "写入成功: ..."}`` → A 套 ``{"success": True, "path": ...}``
  （失败态由适配层补 ``error`` 键，故 ``"error" in r`` 判据依旧成立）；
- 白名单内相对路径落点：B 套一律 basename 夹进 ``agent_outputs`` → A 套按
  ``_write_roots``（data 根）解析，**越界时**才夹 ``agent_outputs``（契约见
  tests/test_capability_file_ops_safety_contract.py）；
- file.find 结果：B 套返回路径字符串列表 → A 套返回 ``matches`` 字典列表（含 path/name/size）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from assistant.capability_router import AssistantCapabilityRouter  # noqa: E402
from data.local_storage import LocalDataStore  # noqa: E402
from services.react_executor import make_react_executor  # noqa: E402


def _ctx(base_dir: str):
    """构造带真 A 套路由的 context（构造法同 tests/test_assistant_capability_router.py）。

    data（runtime）与 workspace 分列：写根取 data 根，读根两者皆可，令「白名单内」
    与「越界夹取」两条路径可被分别钉住。
    """
    root = Path(base_dir)
    runtime_root = root / "runtime"
    workspace_root = root / "workspace"
    runtime_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    store = LocalDataStore(base_dir=str(runtime_root))
    router = AssistantCapabilityRouter(
        data_store=store,
        settings_repository=None,
        observability_service=None,
        note_repository=None,
        task_repository=None,
        news_repository=None,
        inbox_repository=None,
    )
    router.shell_execution.workspace_root = workspace_root
    return types.SimpleNamespace(local_data_store=store, capability_router=router)


def test_file_write_read_find_roundtrip(tmp_path):
    ex = make_react_executor(_ctx(str(tmp_path)))

    w = ex("file.write", {"path": "note.txt", "content": "壳内AI文件普查-A-P2"})
    assert w.get("success") is True, w
    written = tmp_path / "runtime" / "note.txt"  # 白名单内 → 落 data 根
    assert written.is_file() and written.read_text(encoding="utf-8") == "壳内AI文件普查-A-P2"

    r = ex("file.read", {"path": str(written)})
    assert r.get("success") is True, r
    assert "壳内AI文件普查-A-P2" in str(r.get("content", "")), r

    f = ex("file.find", {"pattern": "*.txt", "root": str(tmp_path / "runtime")})
    assert f.get("success") is True, f
    assert any("note.txt" in str(m.get("path")) for m in f.get("matches", [])), f


def test_file_write_confinement_no_path_escape(tmp_path):
    """写文件只允许落在受控根，含 ../ 或绝对路径也必须被夹回安全根。"""
    ex = make_react_executor(_ctx(str(tmp_path)))
    w = ex("file.write", {"path": "../../../etc/pwn.txt", "content": "x"})
    assert w.get("success") is True, w
    # 实际落点必须在 data/agent_outputs 内（取 basename 夹回）
    landed = tmp_path / "runtime" / "agent_outputs" / "pwn.txt"
    assert landed.is_file(), w
    assert not (tmp_path.parent.parent / "etc" / "pwn.txt").exists()


def test_file_read_rejects_sensitive(tmp_path):
    """安全：file.read 拒读敏感路径黑名单命中（如 .env），且失败仍以 ``error`` 键暴露。"""
    ex = make_react_executor(_ctx(str(tmp_path)))
    secret = tmp_path / "workspace" / ".env"  # workspace 属可读根，命中点在黑名单而非白名单
    secret.write_text("SECRET_TOKEN=leakme", encoding="utf-8")
    r = ex("file.read", {"path": str(secret)})
    assert "error" in r, f"敏感文件未被拒读: {r}"
    assert "禁止读取敏感文件" in str(r["error"]), r


def test_executor_without_router_fails_closed(tmp_path):
    """步骤 3 收缩的 fail-closed 契约：未挂载 capability_router 的 context 不得静默降级。

    旧形态下 ``make_react_executor(None)`` 会用手写的第二份 file.* 实现继续跑（B 套
    影子执行体，正是 C-1 要收敛掉的病灶）；现在只能拿到一条明确错误。
    """
    ex = make_react_executor(None)
    r = ex("file.write", {"path": str(tmp_path / "x.txt"), "content": "x"})
    assert "capability_router" in str(r.get("error", "")), r
    assert not (tmp_path / "x.txt").exists(), "fail-closed 不得有任何副作用"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
