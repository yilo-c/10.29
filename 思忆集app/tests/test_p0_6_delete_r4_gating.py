"""P0-6 回归：删除类 handler 必须过 R4 sandbox 审批闸门（attack-chain）。

机制澄清（2026-08-26 复核）：
本仓删除类方法经生产入口 register_all 构建真实 MethodRegistry 时，
由 register_all 内的集中清单 _R4_METHODS 统一 set_risk(RISK_R4) 施加 R4 闸门。
各 register_* 调用处**不**内联 risk=RISK_R4（早期逐处内联改法冗余且会破坏
测试 mock registry，已回退）。因此本测试的 durable fix 是：
  (a) 验证集中清单完整覆盖 18 个删除类方法；
  (b) 验证经真实 register_all 构建后每个方法 is_r4()==True（运行时真源）；
  (c) 攻击链：未审批 R4 dispatch 返回 requires_approval+pending 且不执行；
  (d) 运行时真源扫描：基于真实 register_all 注册表，任何已注册的删除类方法
      必须落在 _R4_METHODS，防止集中清单未来漏项（实现审计方案要求的
      test_no_unclassified_methods）。

隔离：纯内存 MagicMock context，不依赖真实后端进程 / 文件系统。
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.handlers import register_all
from backend.handlers import _R4_METHODS  # 集中清单（register_all 内定义）
from backend.method_registry import RISK_R4, MethodRegistry

# P0-6 收口清单：删除类方法（register 原记 6 处；代码实测含 3 处原集中清单漏项，
# 共 21 处：18 处原删除类 + cron.remove / task_queue.purge_completed / usage.purge）
DELETE_METHODS = [
    "notes.delete",
    "notes.batch_delete",
    "notes.permanent_delete",
    "tasks.delete",
    "wordbook.delete",
    "word.delete",
    "transfer.delete",
    "reading.delete",
    "reading.delete_article",
    "reminder.delete",
    "chat.delete_conversation",
    "chat.feedback.delete",
    "info.sections.delete",
    "info.sources.remove",
    "info.sources.reset",
    "notification.delete",
    "api_keys.delete",
    "provider.remove",
    "cron.remove",
    "task_queue.purge_completed",
    "usage.purge",
    # 2026-09-21：知识图谱实体级不可逆操作（批4 新增 delete，本轮新增 merge）。
    # merge 名字不命中传统删除正则，靠下方 \.merge$ 收紧后才能被运行时真源拦住。
    "knowledge.entity.delete",
    "knowledge.entity.merge",
]


class _CollectingContext:
    """最小 AppContext mock：register_all 在注册期只用到 local_data_store.base_dir
    与 settings_repository；其余按需返回 MagicMock，不触发真实 IO。"""

    def __init__(self) -> None:
        self.local_data_store = SimpleNamespace(base_dir="/tmp/siyi-p0-6-test")
        self.settings_repository = MagicMock()


@pytest.fixture
def registry_with_all() -> MethodRegistry:
    reg = MethodRegistry()
    ctx = _CollectingContext()
    asyncio.run(register_all(reg, ctx, ws_port=18765))  # 生产真实入口
    return reg


def test_all_delete_methods_registered_and_r4(registry_with_all: MethodRegistry) -> None:
    """集中清单 + 运行时真源：所有删除类方法全部过 R4 闸门。"""
    missing = [m for m in DELETE_METHODS if not registry_with_all.has_method(m)]
    assert not missing, f"以下删除方法未注册（集中清单可能引用了不存在的方法）: {missing}"
    not_r4 = [m for m in DELETE_METHODS if not registry_with_all.is_r4(m)]
    assert not not_r4, f"以下删除方法未过 R4 审批闸门（绕 sandbox！）: {not_r4}"


_DELETE_CLASS_RE = re.compile(r"\.delete|\.remove$|\.reset$|\.purge|\.clear$|\.wipe$|\.destroy$|\.terminate$|\.rollback$|\.merge$")


def _runtime_delete_methods(reg: MethodRegistry) -> set[str]:
    """从真实 register_all 构建的注册表中，筛出所有删除/破坏性方法名。"""
    return {m for m in reg.list_methods() if _DELETE_CLASS_RE.search(m)}


def test_no_delete_method_unclassified(registry_with_all: MethodRegistry) -> None:
    """运行时真源扫描：任何已注册的删除类方法必须落在集中 _R4_METHODS 清单。

    防止未来新增删除 handler 时漏加进 _R4_METHODS（审计方案要求的防回退断言）。
    基于真实 register_all 注册表，比源码静态扫描更权威。
    """
    found = _runtime_delete_methods(registry_with_all)
    unclassified = sorted(m for m in found if m not in _R4_METHODS)
    assert not unclassified, f"以下删除类方法未纳入 _R4_METHODS 集中清单（将绕 sandbox 审批！）: {unclassified}"
    # 反向：已知删除方法必须确实已注册（清单非空且对准真实代码）
    missing_in_runtime = [m for m in DELETE_METHODS if not registry_with_all.has_method(m)]
    assert not missing_in_runtime, f"以下已知删除方法未在注册表中找到（清单与代码漂移）: {missing_in_runtime}"


class _FakeSandbox:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict]] = []

    def add(self, action: str, args: dict, preview: dict, kind: str | None = None) -> str:
        self.added.append((action, args))
        return f"pid-{len(self.added)}"


@pytest.mark.asyncio
async def test_unapproved_delete_dispatch_pending_and_not_executed() -> None:
    """攻击链核心：未审批调用返回 pending 且不执行 handler（不可逆删除被拦）。"""
    reg = MethodRegistry()
    executed: list[str] = []

    async def spy_delete(note_id: str = "", **kwargs: object) -> dict:
        executed.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy_delete, risk=RISK_R4)
    fake = _FakeSandbox()
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        result = await reg.dispatch("notes.delete", {"note_id": "X"})

    assert result.get("requires_approval") is True, "未审批删除未返回 requires_approval"
    assert result.get("pending") is True, "未审批删除未进入 pending"
    assert executed == [], "未审批删除竟执行了 handler（不可逆删除泄露！）"
    assert len(fake.added) == 1, "未审批删除未进入 sandbox 审批队列"


@pytest.mark.asyncio
async def test_skip_approval_replays_execution() -> None:
    """sandbox.approve 以 skip_approval=True 重放才真正执行（确认后放行）。"""
    reg = MethodRegistry()
    executed: list[str] = []

    async def spy_delete(note_id: str = "", **kwargs: object) -> dict:
        executed.append(note_id)
        return {"deleted": note_id}

    reg.register("notes.delete", spy_delete, risk=RISK_R4)
    fake = _FakeSandbox()
    with patch("services.sandbox_manager.get_sandbox_manager", return_value=fake):
        result = await reg.dispatch("notes.delete", {"note_id": "X"}, skip_approval=True)

    assert executed == ["X"], "skip_approval 重放应执行 handler"
    assert result == {"deleted": "X"}
