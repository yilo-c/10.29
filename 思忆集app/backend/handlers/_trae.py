"""Trae IDE RPC 方法 — 通过 JSON-RPC 暴露 Trae CLI 能力。

注册方法：
    trae.status             — 查询 Trae 安装状态
    trae.open               — 打开项目/文件夹
    trae.open_file          — 打开文件（可选跳转行/列）
    trae.diff               — 比较两个文件
    trae.list_extensions    — 列出已安装扩展
    trae.install_extension  — 安装扩展
    trae.uninstall_extension — 卸载扩展

@deprecated (S11, 2026-08-07): 本模块全部 8 个方法均为僵尸接口——
后端已实现但前端（desk-notes / 手机端app）从未调用，前端无 TraeModule 组件。
列入 UNUSED_METHODS_BASELINE (tests/test_jsonrpc_contract.py)。
产品决策前保留实现，但新增调用方前不应依赖这些方法签名稳定。
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Any

logger = logging.getLogger("siyi.backend.handlers.trae")

# 延迟导入 TraeBridge，避免模块加载时探测 CLI
_TraeBridge = None
# P3.4 修复：原实现用 id(context) 作为 dict 键，存在两个问题：
#   1. 旧 context 被 GC 后新对象可能复用相同 id，返回携带过期 settings 的 bridge
#   2. 永久缓存无容量上限，长期运行导致内存泄漏
# 改用 WeakKeyDictionary：context 被 GC 后自动清理对应条目，无需手动失效。
# AppContext 无 __slots__，支持 weakref。
_bridge_instances: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_bridge_lock = threading.Lock()


def _get_bridge(context):
    """获取或创建 TraeBridge 实例（per context，按 context 引用缓存）。"""
    global _TraeBridge
    if _TraeBridge is None:
        from integrations.trae_bridge import TraeBridge

        _TraeBridge = TraeBridge
    # 先无锁查询（读多写少，命中是常态）；未命中再加锁创建
    bridge = _bridge_instances.get(context)
    if bridge is not None:
        return bridge
    settings = getattr(context, "settings_repository", None)
    new_bridge = _TraeBridge(settings_repository=settings)
    with _bridge_lock:
        # double-check：防止两个并发请求各创建一份
        existing = _bridge_instances.get(context)
        if existing is not None:
            return existing
        _bridge_instances[context] = new_bridge
    return new_bridge


def register_trae(registry, context) -> None:
    """注册 Trae IDE 相关的 JSON-RPC 方法。"""

    # ── trae.status ──
    async def trae_status(**kwargs: Any) -> dict[str, Any]:
        """查询 Trae IDE 安装状态。

        Returns:
            {"available": bool, "cli_path": str, "version": str}
        """
        bridge = _get_bridge(context)
        return bridge.get_status()

    registry.register("trae.status", trae_status)

    # ── trae.open ──
    async def trae_open(
        path: str = "",
        new_window: bool = False,
        reuse_window: bool = False,
        wait: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """打开项目/文件夹。

        Args:
            path: 项目或文件夹路径。
            new_window: 强制在新窗口打开。
            reuse_window: 强制在已有窗口打开。
            wait: 等待窗口关闭后返回。
        """
        if not path:
            return {"success": False, "error": "path required"}
        bridge = _get_bridge(context)
        result = await bridge.open_project(path, new_window=new_window, reuse_window=reuse_window, wait=wait)
        return {
            "success": result.success,
            "action": result.action,
            "error": result.error,
            "cli_path": result.cli_path,
        }

    registry.register("trae.open", trae_open)

    # ── trae.open_file ──
    async def trae_open_file(
        file_path: str = "",
        line: int | None = None,
        column: int | None = None,
        new_window: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """打开文件并可选跳转到指定行/列。

        Args:
            file_path: 文件路径。
            line: 行号（从 1 开始）。
            column: 列号（从 1 开始）。
            new_window: 在新窗口打开。
        """
        if not file_path:
            return {"success": False, "error": "file_path required"}
        bridge = _get_bridge(context)
        result = await bridge.open_file(file_path, line=line, column=column, new_window=new_window)
        return {
            "success": result.success,
            "action": result.action,
            "error": result.error,
            "cli_path": result.cli_path,
        }

    registry.register("trae.open_file", trae_open_file)

    # ── trae.diff ──
    async def trae_diff(file1: str = "", file2: str = "", **kwargs: Any) -> dict[str, Any]:
        """比较两个文件。"""
        if not file1 or not file2:
            return {"success": False, "error": "file1 and file2 required"}
        bridge = _get_bridge(context)
        result = await bridge.diff(file1, file2)
        return {
            "success": result.success,
            "action": result.action,
            "error": result.error,
        }

    registry.register("trae.diff", trae_diff)

    # ── trae.list_extensions ──
    async def trae_list_extensions(**kwargs: Any) -> dict[str, Any]:
        """列出已安装的 Trae 扩展。"""
        bridge = _get_bridge(context)
        result = await bridge.list_extensions()
        extensions = []
        if result.success and result.output:
            # 输出格式: publisher.extension@version（每行一个）
            for line in result.output.split("\n"):
                line = line.strip()
                if line:
                    parts = line.rsplit("@", 1)
                    ext_id = parts[0]
                    version = parts[1] if len(parts) > 1 else ""
                    extensions.append({"id": ext_id, "version": version})
        return {
            "success": result.success,
            "extensions": extensions,
            "raw_output": result.output,
            "error": result.error,
        }

    registry.register("trae.list_extensions", trae_list_extensions)

    # ── trae.install_extension ──
    async def trae_install_extension(ext_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """安装扩展。

        Args:
            ext_id: 扩展 ID（如 "ms-python.python"）或 VSIX 文件路径。
        """
        if not ext_id:
            return {"success": False, "error": "ext_id required"}
        bridge = _get_bridge(context)
        result = await bridge.install_extension(ext_id)
        return {
            "success": result.success,
            "action": result.action,
            "output": result.output,
            "error": result.error,
        }

    registry.register("trae.install_extension", trae_install_extension)

    # ── trae.uninstall_extension ──
    async def trae_uninstall_extension(ext_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """卸载扩展。"""
        if not ext_id:
            return {"success": False, "error": "ext_id required"}
        bridge = _get_bridge(context)
        result = await bridge.uninstall_extension(ext_id)
        return {
            "success": result.success,
            "action": result.action,
            "output": result.output,
            "error": result.error,
        }

    registry.register("trae.uninstall_extension", trae_uninstall_extension)

    logger.info("Trae IDE RPC methods registered")
