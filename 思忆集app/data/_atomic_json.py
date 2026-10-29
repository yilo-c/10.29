"""原子落盘原语（JSON / 文本）+ 孤儿临时文件清扫。

解决三类现实问题：

1. **崩溃/断电不撕裂**：朴素 ``open(path, "w") + write`` 在写中途崩溃会
   留下半截文件，下次解析失败。本原语先写到同目录的**唯一临时文件**，再
   ``os.replace``（同一文件系统内对读者原子），读者永远不会看到半截内容——
   要么看到旧文件，要么看到完整新文件。

2. **Windows 文件占用重试**：Windows 下 Defender 扫描或另一进程短暂占用会让
   ``os.replace`` 偶发 ``PermissionError``。按项目硬约束做指数退避重试，
   而非直接抛错导致写入丢失。

3. **孤儿 tmp 自愈**：见 :func:`sweep_orphan_tmp`（删除）与 :func:`list_orphan_tmp`
   （只读探测，供诊断入口报告盘面）。

## 命名与清扫必须同处（治本约束）

临时文件命名形状（``{basename}.{pid}.{uuid4hex}.tmp``）与清扫用的正则
（:data:`_ORPHAN_TMP_RE`）**必须一起改**——只改一处会让清扫失配，孤儿永久残留。
故二者刻意放在同一文件，并在 :func:`_atomic_write` 与 :data:`_ORPHAN_TMP_RE`
之间交叉引用。

## 模块名说明

本模块历史上只服务 JSON，故名为 ``_atomic_json``。2026-09-18 起同时承载
:func:`atomic_write_text`（笔记 md / store.json 等文本落盘）与孤儿 tmp 清扫。
**刻意不重命名**：``data/__init__.py`` 记录了
``repositories._chat_session → data._atomic_json → data → data.app_storage_context``
的导入顺序隐患，改路径会牵动导入图，收益不抵风险。

## 兼容的既有命名（清扫必须覆盖）

- ``{name}.{pid}.{uuid4hex}.tmp`` —— 本模块 :func:`_atomic_write` 当前命名
- ``.ai_cache_{rand}.tmp`` —— ``ai/client/_cache.py`` 的 ``tempfile.mkstemp`` 命名
  （2026-09-18 实测：沙箱拒绝写入且拒绝清理时会在 data/ 累积，8/21 一次窗口留下 43 个）
- ``siyi-{rand}.json.tmp`` —— 更早的 ``LocalDataStore`` 实现遗留

所有 JSON / 文本文件写入统一走此处，保证一处修复、全局受益。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from typing import IO, Any

logger = logging.getLogger("siyi.atomic_json")

_DEFAULT_MAX_RETRIES = 5
_DEFAULT_BASE_DELAY = 0.01  # 10ms
_DEFAULT_MAX_DELAY = 0.5  # 500ms

# 孤儿 tmp 清扫的默认最小年龄：比它更新的 tmp 视为"可能仍在途"，不碰。
# 理由：_atomic_write 在 finally 里自清，正常路径不会留；留下的必然是
# 硬杀 / 清理被拒的孤儿，年龄门槛用于避开同目录并发写者的在途文件。
DEFAULT_ORPHAN_MIN_AGE_S = 86400.0

# 只匹配**已知的原子写临时文件命名**，绝不匹配任意 *.tmp。
# 与 _atomic_write 的 tmp_name 构造、ai_cache 的 mkstemp prefix、旧 siyi 命名一一对应。
_ORPHAN_TMP_RE = re.compile(
    r"^(?:"
    r".+\.\d+\.[0-9a-f]{32}\.tmp"  # {basename}.{pid}.{uuid4hex}.tmp（本模块当前命名）
    r"|\.ai_cache_[A-Za-z0-9_]+\.tmp"  # ai/client/_cache.py mkstemp(prefix=".ai_cache_")
    r"|siyi-[A-Za-z0-9_]+\.json\.tmp"  # 更早的 LocalDataStore 遗留
    r")$"
)


def atomic_write_json(
    path: str,
    data: object,
    *,
    indent: int | None = 2,
    encoding: str = "utf-8",
    fsync: bool = True,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
) -> None:
    """将 ``data`` 以 JSON 形式原子地写入 ``path``。

    - 临时文件名含 PID + UUID，避免同目录并发写者互相覆盖彼此的临时文件。
    - 写入完成后 ``fsync`` 再 ``os.replace``，确保替换操作本身刷盘。
    - ``os.replace`` 失败（``PermissionError`` / ``OSError``）时指数退避重试。
    - 任何异常路径都会清理临时文件，绝不遗留 ``.tmp`` 垃圾。
    - 序列化或写入失败时，``path`` 上的既有文件**保持不变**（不撕裂）。
    """
    _atomic_write(
        path,
        lambda fh: json.dump(data, fh, ensure_ascii=False, indent=indent),
        encoding=encoding,
        fsync=fsync,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


def atomic_write_text(
    path: str,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
) -> None:
    """将 ``text`` 原子地写入 ``path``（与 :func:`atomic_write_json` 同一套机制）。

    为什么需要它（2026-09-18 P1-1）：笔记的 ``.md`` 是 YAML frontmatter + body
    **文本**，而 ``store.json`` 用 ``separators=(",", ":")`` 紧凑格式——两者都
    **不能**复用 :func:`atomic_write_json`（后者固定 ``indent=2``，会改字节形态）。
    在它出现之前，全仓只剩这两个文件用裸 ``Path.write_text`` 直写目标，
    崩溃/磁盘满即撕裂（graph / id_map / sidecar 早已原子化，唯独笔记两文件裸奔）。

    保证与 JSON 版**逐条一致**：同目录唯一临时文件、fsync、退避重试、
    异常路径清理、失败时目标文件保持原样。
    """
    _atomic_write(
        path,
        lambda fh: fh.write(text),
        encoding=encoding,
        fsync=fsync,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


def _atomic_write(
    path: str,
    write_body: Callable[[IO[str]], Any],
    *,
    encoding: str,
    fsync: bool,
    max_retries: int,
    base_delay: float,
    max_delay: float,
) -> None:
    """原子写共享机制：tmp → 写入 → flush/fsync → 退避 replace → finally 清理。

    ``write_body`` 只负责往已打开的文件句柄里写内容，序列化差异由调用方承担。
    临时文件命名见模块 docstring「命名与清扫必须同处」——
    若改这里的 ``tmp_name`` 构造，**必须同步改** :data:`_ORPHAN_TMP_RE`。
    """
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_name = f"{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = os.path.join(parent, tmp_name)
    try:
        with open(tmp_path, "w", encoding=encoding) as fh:
            write_body(fh)
            fh.flush()
            if fsync:
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # 某些文件系统/句柄不支持 fsync，忽略不影响正确性。
                    pass
        _replace_with_retry(tmp_path, path, max_retries, base_delay, max_delay)
    finally:
        # 无论成功（已被 replace 移走）还是失败（仍需清理），都清掉残留临时文件。
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def quarantine_corrupt_file(path: str, *, timestamp: int | None = None) -> str | None:
    """把损坏文件原子改名（``os.rename``）为 ``<name>.corrupt-<时间戳>`` 保全现场。

    容错三件套的第①步：解析失败的 JSON 不能被后续写入静默覆盖，先改名保留
    物证。成功返回隔离后的新路径；改名失败返回 ``None``——调用方此时**必须
    拒绝写回该路径并告警**（禁 warning-only 继续），等人工处理现场。

    同一秒内已有隔离文件时追加 ``-<seq>``，避免 Windows 下 ``os.rename``
    遇已存在目标抛 ``FileExistsError``。
    """
    ts = int(time.time()) if timestamp is None else int(timestamp)
    target = f"{path}.corrupt-{ts}"
    seq = 0
    while os.path.exists(target):
        seq += 1
        target = f"{path}.corrupt-{ts}-{seq}"
    try:
        os.rename(path, target)
    except OSError as exc:
        logger.error("损坏文件隔离失败（os.rename %s -> %s）：%s", path, target, exc)
        return None
    return target


def _replace_with_retry(
    tmp_path: str,
    target: str,
    max_retries: int,
    base_delay: float,
    max_delay: float,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            os.replace(tmp_path, target)
            return
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if attempt >= max_retries - 1:
                break
            delay = min(base_delay * (2**attempt), max_delay)
            time.sleep(delay)
    raise last_exc or RuntimeError("atomic replace failed")


def list_orphan_tmp(
    directory: str | os.PathLike[str],
    *,
    min_age_s: float = DEFAULT_ORPHAN_MIN_AGE_S,
    now: float | None = None,
) -> list[str]:
    """**只读**探测：列出 ``directory`` 下符合已知原子写命名且已过期的孤儿临时文件。

    与 :func:`sweep_orphan_tmp` 共用同一套命名判据（:data:`_ORPHAN_TMP_RE`）与
    年龄门槛，区别只在于**不删除**。诊断入口（``system.integrity``）用它报告盘面，
    清扫入口用它决定删哪些——判据单点，不会出现"报告说干净、清扫却删了"的分裂。
    """
    base = os.fspath(directory)
    if not os.path.isdir(base):
        return []
    cutoff = (time.time() if now is None else float(now)) - float(min_age_s)
    found: list[str] = []
    # 用 os.scandir 而非 glob.glob("*.tmp")：**glob 默认不匹配点开头的文件**，
    # 而真实孤儿里 43/45 恰恰是点开头的 ``.ai_cache_*.tmp``（ai/client/_cache.py
    # 的 mkstemp prefix 带点）——用 glob 会静默漏掉绝大多数，清扫形同虚设。
    # 2026-09-18 由 test_local_data_store_sweeps_on_init 实测捕获（先红后绿）。
    try:
        entries = list(os.scandir(base))
    except OSError as exc:
        logger.warning("孤儿 tmp 探测失败（跳过）：%s：%s", base, exc)
        return []
    for entry in entries:
        name = entry.name
        # 正则才是最终判据：只认已知原子写命名，任意 *.tmp 一律不碰。
        if not _ORPHAN_TMP_RE.match(name):
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue  # 太新：可能是在途写
        except OSError:
            continue
        found.append(name)
    return sorted(found)


def sweep_orphan_tmp(
    directory: str | os.PathLike[str],
    *,
    min_age_s: float = DEFAULT_ORPHAN_MIN_AGE_S,
    now: float | None = None,
) -> list[str]:
    """清扫 ``directory`` 下**符合已知原子写命名**且已过期的孤儿临时文件。

    返回被删除的文件名列表（供调用方记录/断言）。

    设计取舍（2026-09-18 P2-3）：

    - **只清已知命名**（:data:`_ORPHAN_TMP_RE`），不碰任意 ``*.tmp``。宁可漏清，
      不可误删——数据目录里可能有别的东西恰好以 ``.tmp`` 结尾。
    - **默认年龄门槛 1 天**：``_atomic_write`` 在 ``finally`` 里自清，正常路径不留；
      真正残留的必然是"硬杀 / 清理被拒"的孤儿。门槛用于避开同目录**并发写者**
      正在使用的在途临时文件。
    - **非递归**：只清 ``directory`` 这一层。写入者清理自己的目录，边界清晰。
    - 单个文件删除失败（被占用 / 权限）**不中断**整体清扫，只记 warning。
    - 删除动作逐个记 log，便于事后核对（不静默删文件）。

    为什么需要它：``ai/client/_cache.py::_write_cache_atomic`` 用
    ``tempfile.mkstemp(prefix=".ai_cache_")``，异常路径虽有 unlink，但进程被硬杀、
    或沙箱**同时拒绝写入与清理**时，tmp 会永久残留——2026-09-18 实测 ``思忆集app/data/``
    下 43 个 ``.ai_cache_*.tmp``（各约 213 KB，集中在 8/21 的 43 分钟窗口）
    + 2 个 5 月的 ``siyi-*.json.tmp``，共 45 个，无人清理。
    """
    base = os.fspath(directory)
    removed: list[str] = []
    for name in list_orphan_tmp(base, min_age_s=min_age_s, now=now):
        candidate = os.path.join(base, name)
        try:
            os.remove(candidate)
        except OSError as exc:
            if os.path.exists(candidate):
                logger.warning("孤儿 tmp 清理失败（跳过）：%s：%s", candidate, exc)
                continue
            # 实测（2026-09-18）：平台级"安全删除 shim"（把删除改道到回收站）
            # 会在**文件已实际移走之后**仍抛 OSError（Windows 下形如
            # ``SHFileOperationW 失败: 0x2``）。此时盘面事实是"已清掉"，
            # 若按异常处理就会谎报失败、并把已消失的文件留在返回列表外。
            # 判据以**盘面**为准（文件是否还在），不以调用是否抛错为准。
            logger.debug("孤儿 tmp 已消失，但删除调用报错（平台安全删除 shim 已知行为）：%s：%s", candidate, exc)
        removed.append(name)
    if removed:
        logger.info("孤儿 tmp 清扫：%s 下删除 %d 个（%s）", base, len(removed), ", ".join(removed[:5]))
    return removed
