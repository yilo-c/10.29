"""B-02 任意路径读取旁路封堵回归测试。

审计（audit-report-2026-08-27 B-02，P1）：data.import_chat_folder 无路径白名单
（仅 isdir + 扩展名过滤），远程已认证客户端可枚举并导入桌面任意目录文件
（内容进 chat/memory/raw_items 后可经搜索接口回读片段）。

修复：白名单逻辑从 _url_learning.py 抽为公共 util backend/handlers/_path_guard.py
（应用数据目录 + Documents/Downloads/Desktop + realpath 精确前缀），
data.import_chat / data.import_chat_folder 双入口接入。

本文件覆盖：
1. util 行为：白名单内放行 / 白名单外拒绝 / `..` 遍历拒绝 / 前缀串扰拒绝。
   （纯字符串路径判断，realpath 不需要目录真实存在 → 沙箱 tmp_path 限制不适用。）
2. 入口静态门禁：断言两个入口源码保留 is_within_import_whitelist 守卫调用
   （防回归删守卫——data.* 为 register_all 闭包，handler 级注入测试过重）。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from backend.handlers._path_guard import allowed_import_bases, is_within_import_whitelist

# 测试用假 base_dir（不存在亦可，realpath 纯字符串规范化）
FAKE_DATA = str(Path("D:/fake-siyi-data").resolve())


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(local_data_store=SimpleNamespace(base_dir=FAKE_DATA))


def test_within_whitelist_allowed():
    inside = os.path.join(FAKE_DATA, "notes", "chat_export.json")
    assert is_within_import_whitelist(inside, _ctx()) is True


def test_data_dir_exact_match_allowed():
    assert is_within_import_whitelist(FAKE_DATA, _ctx()) is True


def test_outside_path_rejected():
    # Windows 上 realpath 会把 /etc/passwd 规范到当前盘根，仍应在白名单外
    assert is_within_import_whitelist("/etc/passwd", _ctx()) is False
    assert is_within_import_whitelist(os.environ.get("SystemRoot", "C:/Windows"), _ctx()) is False


def test_traversal_rejected():
    trap = os.path.join(FAKE_DATA, "..", "secret", "x.md")
    assert is_within_import_whitelist(trap, _ctx()) is False


def test_prefix_sibling_not_confused():
    """杜绝 /data 与 /data_bak 串扰：同前缀兄弟目录不得放行。"""
    sibling = FAKE_DATA + "-backup/notes/a.md"
    assert is_within_import_whitelist(sibling, _ctx()) is False


def test_allowed_import_bases_includes_data_dir_and_user_dirs():
    bases = allowed_import_bases(_ctx())
    assert FAKE_DATA in bases
    # 用户 Documents/Downloads/Desktop 若存在则入列（存在性由环境决定，不强断言）


def test_url_learning_wrapper_still_works():
    """B-02: _url_learning 薄包装保持既有 import 路径（test_p1_8_hemostasis 依赖）。"""
    from backend.handlers._url_learning import _is_within_learn_whitelist

    inside = os.path.join(FAKE_DATA, "notes", "a.md")
    assert _is_within_learn_whitelist(inside, _ctx()) is True
    assert _is_within_learn_whitelist("/etc/passwd", _ctx()) is False


# ── 入口静态门禁（data.* 为 register_all 闭包，源码断言防守卫被删）──

_HANDLERS_INIT = Path(__file__).resolve().parents[1] / "backend" / "handlers" / "__init__.py"


def _read_handlers_init() -> str:
    assert _HANDLERS_INIT.is_file(), f"handlers/__init__.py 不存在: {_HANDLERS_INIT}"
    return _HANDLERS_INIT.read_text(encoding="utf-8")


def test_import_chat_folder_keeps_whitelist_guard():
    src = _read_handlers_init()
    # 取 data_import_chat_folder 函数体（def 到下一个顶层 def/注册行）
    start = src.index("async def data_import_chat_folder")
    end = src.index("registry.register(\"data.import_chat_folder\"")
    body = src[start:end]
    assert "is_within_import_whitelist(folder_path, context)" in body, (
        "data_import_chat_folder 必须保留白名单守卫（B-02）"
    )


def test_import_chat_keeps_whitelist_guard():
    src = _read_handlers_init()
    start = src.index("async def data_import_chat(")
    end = src.index("registry.register(\"data.import_chat\"")
    body = src[start:end]
    assert "is_within_import_whitelist(resolved_path, context)" in body, (
        "data_import_chat 绝对路径分支必须保留白名单守卫（B-02）"
    )
