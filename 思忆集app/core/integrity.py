"""进程级完整性台账——"失败必须可观测"的单一出口（2026-09-18 治本基元）。

## 为什么需要它（过往经验）

env-drill 三波演练（T1 击杀 / T2 时钟 / T3 磁盘）**各自独立**暴露出同一个形状的
缺陷：**写入失败被吞掉，上层不可观测**。四处病灶、同一个形状：

======================================  ==========================================
站点                                     吞掉后的用户可见后果
======================================  ==========================================
``_notes.py:505/512/662``                级联清理失败（图谱删了没落盘）→ RPC 恒成功，
                                        重启后孤立实体复活
``_tasks.py:232/237/278``                同款（cron / reminder / KG 侧）
``desknotes_note_adapter._save_store_state``  文件夹改动没写盘，调用方以为成功
``desknotes_note_adapter.list_notes``    解析失败笔记被 ``logger.debug`` 跳过 →
                                        "有这条" 变成 "没有这条"
======================================  ==========================================

**元判据（MEMORY.md）**：同一 bug 形态 ≥2 次 → 必须建基元 + 挂门禁，不再逐处打补丁。
本模块即该基元的落地：把"记录一次完整性事件"收敛为单一入口，任何站点调用
:func:`record` 即同时获得 **① 有界台账**（供 RPC 查询）与 **② WARNING 日志**
（供运维直接看见）——两者不会再因为作者忘记写日志而分裂。

## 个人使用场景

单人单机自用 → 没有 SRE 看仪表盘、没有告警管道。用户唯一的观测手段是**日志**与
**问一句"我的数据还好吗"**。故本台账刻意做成"进程内、有界、可一键快照"：
``system.integrity`` RPC 直接把它交出来，无需落盘、无需轮转。

## 设计取舍

* **有界（``MAX_EVENTS``）**：台账服务"此刻是否健康"，不是历史审计；历史审计另有
  ``audit_trail``。无界会随长期运行吃内存。
* **不落盘**：落盘会引入新的"写失败"面（本模块存在的理由正是治这类问题），
  且进程重启后盘面本身会被重新探测（``notes.integrity`` 的 md 扫描）。
* **计数不衰减**：``counts`` 累计自进程启动，便于判断"是否反复发生"。
* **线程安全**：适配器方法可能被 ``asyncio.to_thread`` 调用，``deque.append`` 虽在
  CPython 下原子，``Counter`` 的自增不是——统一加锁。
* **只记不抛**：台账是观测通道，任何调用点都不得因记账而失败（内部吞掉自身异常）。

## 用法

```python
from core.integrity import record, snapshot

record("note_store", "store.json 写盘失败（文件夹改动未持久化）", target=path)
print(snapshot())  # {"counts": {...}, "events": [...], "dropped": 0}
```
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from typing import Any

from core.clock import wall_ts

logger = logging.getLogger("siyi.integrity")

# 台账上限：够覆盖"一次故障潮"的现场，又不至于长期运行吃内存。
MAX_EVENTS = 200

# 事件种类（kind）常量——避免各处写裸字符串漂移，也便于 RPC 侧按类分组。
KIND_NOTE_STORE = "note_store"
KIND_NOTE_READ = "note_read"
KIND_CASCADE = "cascade"
KIND_TMP_SWEEP = "tmp_sweep"

_lock = threading.Lock()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_counts: Counter[str] = Counter()
_dropped = 0


def record(kind: str, detail: str, *, target: str = "", level: int = logging.WARNING) -> None:
    """记录一次完整性事件：入台账 + 打日志（两者不可分割）。

    ``kind`` 用上面的常量之一（或新域的短名）；``target`` 是被影响的对象
    （文件路径 / 笔记 id / 任务 id），便于事后定位。

    本函数**永不抛出**——记账失败绝不能把一次"可降级的写入失败"升级成崩溃。
    """
    global _dropped
    try:
        event = {"kind": kind, "target": target, "detail": detail, "at": wall_ts()}
        with _lock:
            if len(_events) == MAX_EVENTS:
                _dropped += 1
            _events.append(event)
            _counts[kind] += 1
        if target:
            logger.log(level, "完整性事件[%s] %s：%s", kind, target, detail)
        else:
            logger.log(level, "完整性事件[%s]：%s", kind, detail)
    except Exception:  # noqa: BLE001 —— 观测通道自身不得成为故障源
        pass


def snapshot() -> dict[str, Any]:
    """返回台账快照（JSON 可序列化），供 RPC / 诊断脚本消费。"""
    with _lock:
        return {
            "counts": dict(_counts),
            "events": list(_events),
            "dropped": _dropped,
            "max_events": MAX_EVENTS,
        }


def reset() -> None:
    """清空台账（**仅供测试**；生产代码不得调用）。"""
    global _dropped
    with _lock:
        _events.clear()
        _counts.clear()
        _dropped = 0
