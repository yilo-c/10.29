# -*- coding: utf-8 -*-
"""task_queue handlers 传参回归测试（2026-09-16）。

背景（实测缺陷）：``PersistentTaskQueue.enqueue/list/purge_completed`` 的关键
参数是 keyword-only（签名带 ``*``），而 ``backend/handlers/_task_queue.py`` 里
以位置参数调用 ``asyncio.to_thread(q.list, queue_name, status, limit)`` 等，
每次调用必抛 TypeError，被 handler 的 ``except`` 吞成 ``success=False``——
表现为「死信面板恒空」「入队/清理接口恒报错且错误信息是晦涩的 TypeError」。

本文件两层断言：
1. 行为层：注册 handlers 后直调，断言 ``success=True``（修复前必为 False）；
2. 结构层：AST 扫描该文件中所有 ``asyncio.to_thread(q.<method>, ...)`` 调用，
   用 ``inspect.Signature.bind`` 校验实参可绑定，防止未来再引入同类回归。
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from backend.handlers._task_queue import register_task_queue
from services.task_queue import PersistentTaskQueue


class _FakeRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register(self, name: str, fn) -> None:
        self.handlers[name] = fn


class _FakeContext:
    def __init__(self, queue: PersistentTaskQueue) -> None:
        self.task_queue = queue


@pytest.fixture()
def handlers(tmp_path):
    q = PersistentTaskQueue(db_path=str(tmp_path / "task_queue.db"))
    reg = _FakeRegistry()
    register_task_queue(reg, _FakeContext(q))
    yield reg.handlers, q
    q.close()


# ---------------------------------------------------------------- 行为层


@pytest.mark.asyncio
async def test_dead_letter_list_does_not_swallow_typeerror(handlers):
    h, q = handlers
    # 先放一条任务，保证 list 真的执行到 SQL
    q.enqueue(capability_id="test.echo", queue_name="cron")
    out = await h["task_queue.dead_letter.list"](queue_name="cron", limit=10)
    assert out.get("success") is True, f"死信列表被异常吞掉: {out}"
    assert isinstance(out.get("tasks"), list)


@pytest.mark.asyncio
async def test_enqueue_handler_passes_kwargs_correctly(handlers):
    h, _q = handlers
    out = await h["task_queue.enqueue"](
        capability_id="test.echo",
        payload={"x": 1},
        queue_name="cron",
        priority=1,
        max_retries=2,
    )
    assert out.get("success") is True, f"入队 handler 传参错误: {out}"
    assert out.get("task_id")


@pytest.mark.asyncio
async def test_purge_completed_handler_passes_kwargs_correctly(handlers):
    h, _q = handlers
    out = await h["task_queue.purge_completed"](older_than_seconds=0)
    assert out.get("success") is True, f"purge handler 传参错误: {out}"


@pytest.mark.asyncio
async def test_list_handler_works(handlers):
    h, q = handlers
    q.enqueue(capability_id="test.echo")
    out = await h["task_queue.list"](limit=5)
    assert out.get("success") is True, f"list handler 传参错误: {out}"
    assert out.get("total", 0) >= 1


# ---------------------------------------------------------------- 结构层

_HANDLER_SRC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "backend"
    / "handlers"
    / "_task_queue.py"
)


def _to_thread_positional_calls() -> list[tuple[int, str, int]]:
    """返回 (行号, q.<method> 名, 位置实参个数（不含函数本身）) 列表。"""
    tree = ast.parse(_HANDLER_SRC.read_text(encoding="utf-8"))
    found: list[tuple[int, str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Call) is False
        ):
            target = node.args[0]
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "q"
            ):
                positional = len(node.args) - 1
                if positional > 0:
                    found.append((node.lineno, target.attr, positional))
    return found


def test_no_positional_args_to_keyword_only_queue_methods():
    """所有 to_thread(q.method, ...) 的位置实参必须能通过签名绑定。

    PersistentTaskQueue 多数管理方法为 keyword-only；位置传参在运行时才
    抛 TypeError 且被 handler except 吞掉，属于「静默失效」模式，必须在
    静态层拦下。
    """
    bad: list[str] = []
    for lineno, meth, npos in _to_thread_positional_calls():
        fn = getattr(PersistentTaskQueue, meth, None)
        if fn is None:
            continue
        try:
            # 模拟 bind：self + npos 个位置参数
            inspect.signature(fn).bind(None, *([object()] * npos))
        except TypeError as exc:
            bad.append(f"_task_queue.py:{lineno} q.{meth} 位置传参 {npos} 个 → {exc}")
    assert not bad, "发现 keyword-only 方法被位置参数调用:\n" + "\n".join(bad)
