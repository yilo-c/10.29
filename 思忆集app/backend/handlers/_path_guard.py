"""路径白名单守卫（B-02 公共 util）。

背景：``url_learning.learn`` 的本地文件读取白名单（原 ``_url_learning.py:18-48``）
有效防任意路径读取；``data.import_chat_folder`` 却无此边界（仅 isdir + 扩展名
过滤）→ 远程已认证客户端可导入桌面任意目录文件（内容进 chat/memory/raw_items
后可经搜索接口回读片段）。本模块把白名单逻辑抽为公共 util，
``data.import_chat`` / ``data.import_chat_folder`` 双入口接入。

白名单根目录：
- 应用数据目录（``local_data_store.base_dir``，含用户自建笔记/知识 md）
- 用户常用文档目录 Documents / Downloads / Desktop

判定为「规范化后精确前缀」：``realpath`` + 分隔符，防符号链接 / ``..`` 绕过，
杜绝 ``/data`` 与 ``/data_bak`` 串扰（与 _url_learning 原语义逐位一致）。
"""

from __future__ import annotations

import os
from typing import Any


def allowed_import_bases(context: Any) -> tuple[str, ...]:
    """惰性计算允许导入的根目录集合（每次都重新解析，容忍运行时路径变化）。"""
    bases: list[str] = []
    data_dir = getattr(getattr(context, "local_data_store", None), "base_dir", None)
    if data_dir:
        bases.append(os.path.abspath(str(data_dir)))
    for sub in ("Documents", "Downloads", "Desktop"):
        try:
            p = os.path.abspath(os.path.expanduser(f"~/{sub}"))
            if os.path.isdir(p):
                bases.append(p)
        except Exception:
            continue
    return tuple(bases)


def is_within_import_whitelist(path: str, context: Any) -> bool:
    """规范化后判断路径是否落在导入白名单内（防符号链接 / ``..`` 绕过）。

    语义与旧 ``_url_learning._is_within_learn_whitelist`` 逐位一致（同一套
    根目录 + 精确前缀），仅模块归属公共化。
    """
    try:
        real = os.path.realpath(os.path.abspath(os.path.normpath(path)))
    except Exception:
        return False
    for base in allowed_import_bases(context):
        try:
            base_real = os.path.realpath(os.path.abspath(base))
        except Exception:
            continue
        # 精确前缀判断（含分隔符），杜绝 /data 与 /data_bak 串扰
        if real == base_real or real.startswith(base_real + os.sep):
            return True
    return False
