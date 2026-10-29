"""数据目录隔离的 fail-closed 基元（P0-2 治本，2026-09-18）。

## 为什么需要它（过往经验）

本项目**已发生三次隔离泄漏**，且 ``思忆集app/data/**`` 在 gitignore 内 →
用 ``git status`` 看不见，只能靠正向探针验：

1. **PD-ISO**：隔离实例图谱路径跟后端 CWD，AUTOSTART 写测试直污生产图谱
   （``headless_main.py`` 构造 AppContext 不传 ``data_base_dir``）；
2. **ctx-fix**：首版测试写穿生产 ``data/info_cache.json``，回归套件另重写
   ``knowledge_graph.json`` 与 ``memory_fts.db``；
3. **env-drill T1 首冒烟**：``SIYI_DESKNOTES_DATA_DIR`` 目标目录未预建 ``notes/``
   → 候选链静默回退 ``%LOCALAPPDATA%\\desk-notes\\siyi-notes`` **生产目录**，
   泄漏 1 条 ``SMOKE_OK`` 笔记。

三次根因**同源**：调用方显式声明了隔离意图，但解析失败时**静默回退默认（生产）
路径**。逐处打补丁 = 治标；本模块把"**显式 override 即权威**"固化为可复用基元，
后续任何 ``*_DATA_DIR`` 类 override 都走同一语义。

## 个人使用场景

单人单机自用 → "生产目录"就是**用户本人的真实笔记与图谱**。一次静默回退不是
"测试污染"，而是**用户数据被脚本改写**，且无告警、无版本可回溯（gitignore 内）。

故此处选择 **fail-closed（响亮失败）** 而非"优雅回退"：隔离配置错误必须立刻
可见，宁可启动失败也不写坏个人数据。

## 用法

```python
from core.isolation import IsolationError, resolve_isolated_dir

override = resolve_isolated_dir("SIYI_DESKNOTES_DATA_DIR", required_subdir="notes")
if override is None:
    ...  # 未声明隔离 → 调用方走默认解析链
else:
    ...  # 显式隔离，权威
```
"""

# ── 离线基元（PD-ISO-b，2026-09-18）──────────────────────────────
#
# 隔离不只有"数据目录"一维：AUTOSTART 拉起的临时后端虽然数据目录是隔离的，
# **但它的 LLM 通道仍会真实出网**（实测 knowledge.brief 20.3s 成功 =
# 每轮集成测试替用户烧 DeepSeek 配额）。根因同源：离线开关虽被声明，却只被
# headless_main 的 RSS/autonomy 两处消费，ai_client 与 kernel_ai 完全无视
# ——"声明了但没人读"与"声明了但静默回退"是同一类腐化。
#
# 故这里把离线声明也做成**与 resolve_isolated_dir 同级的基元**：所有出网通道
# （AI HTTP 客户端、内核 agent 网关）统一读同一个判定，禁止各自 if os.environ
# 分叉（分叉 = 下次新增通道必然再漏一处）。

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("siyi.isolation")

#: 离线声明开关。注意：`scripts/gen_inventory.py` 是**文本级**扫描（连注释里的
#: 字面量都入账），故该名字只允许在本模块出现一次，其余地方一律引用本常量。
OFFLINE_ENV_KEY = "SIYI_TEST_OFFLINE"

#: 显式"关闭离线"的取值（大小写不敏感）。**其余任何非空值一律判为离线**
#: ——fail-closed：离线是安全侧，拼错值宁可多拦一次也不该静默放开网络。
_OFFLINE_FALSE_VALUES = frozenset({"0", "off", "false", "no"})


class IsolationError(RuntimeError):
    """显式隔离 override 无法满足时抛出（fail-closed，禁止回退生产）。"""


def is_offline_mode() -> bool:
    """是否处于**显式声明的离线模式**（出网通道必须一律短路）。

    语义与 ``resolve_isolated_dir`` 对齐："显式 override 即权威"。

    * 未设置 / 空串 → ``False``（未声明，走正常联网路径）；
    * ``0`` / ``off`` / ``false`` / ``no``（大小写不敏感）→ ``False``（显式关闭）；
    * **其他任何非空值 → ``True``**（fail-closed：写错值不会静默放开出网）。

    调用方契约：判为 ``True`` 时**不得**发起任何真实网络请求，也不得调用会
    拉起外部 agent 进程的内核网关；应返回空结果并留痕，让"离线"可见。
    """
    raw = os.environ.get(OFFLINE_ENV_KEY)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _OFFLINE_FALSE_VALUES or not value:
        return False
    return True


def resolve_isolated_dir(
    env_key: str,
    *,
    required_subdir: Optional[str] = None,
    hint_disable_key: Optional[str] = None,
) -> Optional[Path]:
    """解析显式隔离 override；**设置即权威**，不满足则 raise。

    返回值语义严格区分：

    * ``None`` —— **未声明隔离**（环境变量未设），调用方走默认解析链；
    * ``Path`` —— 显式隔离成立且已校验；
    * **绝不返回** "声明了但回退到默认路径"。

    Args:
        env_key: 环境变量名（如 ``SIYI_DESKNOTES_DATA_DIR``）。
        required_subdir: 目标目录必须存在的子目录（如 ``notes``）；缺则 raise。
        hint_disable_key: 报错文案里提示的"显式关闭"开关名（如
            ``SIYI_DISABLE_DESKNOTES_BRIDGE``），便于运维看到逃生口。

    Raises:
        IsolationError: ``env_key`` 已设置，但目录不存在或缺 ``required_subdir``。
    """
    raw = os.environ.get(env_key)
    if not raw:
        return None

    path = Path(raw)
    escape = f"，或设 {hint_disable_key}=1 显式跳过" if hint_disable_key else ""

    if not path.is_dir():
        msg = f"{env_key} 指向 {path}，但目录不存在。拒绝回退默认（生产）目录以防隔离泄漏；请先创建该目录{escape}。"
        logger.error(msg)
        raise IsolationError(msg)

    if required_subdir and not (path / required_subdir).is_dir():
        msg = f"{env_key} 指向 {path}，但缺少 {required_subdir}/ 子目录。拒绝回退默认（生产）目录以防隔离泄漏；请先创建 {required_subdir}/{escape}。"
        logger.error(msg)
        raise IsolationError(msg)

    logger.info("%s 解析为 %s（显式隔离，权威）", env_key, path)
    return path


def assert_not_under(path: "str | Path", forbidden_root: "str | Path") -> None:
    """正向探针：断言 ``path`` 不落在 ``forbidden_root`` 之下。

    给测试夹具 / 演练 harness 用——``data/**`` 在 gitignore 内，泄漏用
    ``git status`` 看不见，只能靠这类**正向断言**自证隔离成立。

    Raises:
        IsolationError: ``path`` 位于 ``forbidden_root`` 之下。
    """
    p = Path(path).resolve()
    root = Path(forbidden_root).resolve()
    if p == root or root in p.parents:
        msg = f"隔离自检失败：{p} 落在受保护目录 {root} 之下（可能写穿生产数据）"
        logger.error(msg)
        raise IsolationError(msg)
