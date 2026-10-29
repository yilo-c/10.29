"""启动自检（批次5 基建9，2026-08-26）。

反射扫描「接缝」一致性，把「方法名错配 / 事件契约漂移 / handler 引用未接线服务」
这类在静态审计中反复出现的回归，在启动即红（fail-fast），而非运行时静默失败。

设计原则：
- **无条件 fail-fast** 的扫描必须「零误报」：只拦那些 100% 确定是 bug 的模式
  （旧式双参 emit、handler 模块导入失败）。
- **仅告警不阻断** 的扫描允许误报（context 接线），因为 AppContext 部分属性由
  headless_main 运行时 setattr 注入（audit_trail / write_guard / rollback_manager /
  trace_recorder），静态无法穷举。
- 提供 SIYI_SKIP_SELFCHECK=1 紧急逃生开关，但 CI / 生产不应设置。
"""

from __future__ import annotations

import ast
import importlib
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("siyi.startup.selfcheck")

_BACKEND_ROOT = Path(__file__).resolve().parent.parent  # 思忆集app/
_SKIP = os.environ.get("SIYI_SKIP_SELFCHECK", "") == "1"

# 这些目录不参与扫描（测试 / 迁移 / 缓存）
_EXCLUDED_DIRS = ("tests", "migrations", "__pycache__", ".venv", "archive")

# 无 .py 源码的大目录：必须在遍历前剪枝，绝不能 rglob 全树——
# 本仓 BACKEND_ROOT 实测 ~4 万文件（data/ 871MB + web_dist + assets），
# pathlib.rglob 逐文件 stat 在 Windows 上单次调用即分钟级，启动自检会被拖死。
_PRUNE_DIRS = frozenset(_EXCLUDED_DIRS) | {
    ".git", ".pytest-tmp", "node_modules", "data", "web_dist", "assets",
}


def _iter_source_py(root: Path) -> list[Path]:
    """os.walk 自顶向下剪枝收集 .py（语义 = rglob 后过滤 _EXCLUDED_DIRS，但快百倍）。"""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        out.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return out


class SelfCheckError(RuntimeError):
    """启动自检失败（应阻断启动）。"""


def _expr_dotted_name(node: ast.AST) -> str:
    """把 expr 还原成 a.b.c 形式（Name / Attribute 链）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_expr_dotted_name(node.value)}.{node.attr}"
    return ""


def check_event_bus_emit_signatures() -> list[str]:
    """静态扫描旧式双参 ``emit("type", payload)`` 调用。

    应统一为 ``emit(AppEvent("type", payload=...))``。旧式双参会被
    EventBus.emit 的 try/except 吞掉，事件实际从不发出（P1-8/P1-9 同类回归）。

    只拦「接收者明显是事件总线」且「首参为字符串常量、存在第二参」的调用，
    Qt 信号（data_changed.emit）与单参 AppEvent 均不误报。
    """
    violations: list[str] = []
    for py in _iter_source_py(_BACKEND_ROOT):
        rel = py.relative_to(_BACKEND_ROOT)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "emit":
                continue
            recv = _expr_dotted_name(node.func.value)
            if not (recv.endswith("event_bus") or recv == "bus" or recv.endswith("_bus")):
                continue
            args = node.args
            if len(args) == 2 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
                violations.append(f"{rel}:{node.lineno}: emit({args[0].value!r}, <payload>) 旧式双参——应改为 emit(AppEvent(...))")
    return violations


def _iter_handler_modules() -> list[Path]:
    handlers_dir = _BACKEND_ROOT / "backend" / "handlers"
    mods: list[Path] = []
    for py in sorted(handlers_dir.glob("_*.py")):
        if py.name == "__init__.py":
            continue
        mods.append(py)
    return mods


def check_handlers_importable() -> tuple[list[str], int]:
    """导入全部 handler 模块，确保无导入期错误（捕获本次编辑引入的语法/依赖断裂）。

    返回 (导入失败模块列表, 成功导入模块数)。
    """
    failed: list[str] = []
    ok = 0
    for py in _iter_handler_modules():
        module_name = f"backend.handlers.{py.stem}"
        try:
            importlib.import_module(module_name)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{module_name}: {type(exc).__name__}: {exc}")
    return failed, ok


def _appcontext_provided_attrs() -> set[str]:
    """从 app_runtime 下所有 ContextMixin / AppContext 解析 self.<attr> = 赋值，
    得到 AppContext 静态提供的属性集合。"""
    provided: set[str] = set()
    app_runtime = _BACKEND_ROOT / "app_runtime"
    files = list(app_runtime.rglob("*.py")) if app_runtime.exists() else []
    for py in files:
        if "context" not in py.name and "mixin" not in py.name:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            tgt = node.targets[0]
            if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self":
                provided.add(tgt.attr)
    return provided


def check_context_wiring() -> list[str]:
    """扫描 handler 对 ``context.<attr>`` 的直接访问，列出不在 AppContext 静态
    提供集合中的属性（可能未接线）。仅告警，不阻断——因部分属性运行时 setattr 注入。"""
    provided = _appcontext_provided_attrs()
    # 运行时由 headless_main setattr 注入的已知属性（误报白名单）
    runtime_injected = {
        "audit_trail",
        "write_guard",
        "rollback_manager",
        "trace_recorder",
        "multi_agent_orchestrator",
        "feedback_service",
    }
    provided |= runtime_injected

    warnings: list[str] = []
    for py in _iter_handler_modules():
        rel = py.relative_to(_BACKEND_ROOT)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "context"):
                continue
            # 跳过 getattr(context, "x", default) 形式（带默认，安全）
            if isinstance(node.ctx, ast.Load) and node.attr not in provided:
                warnings.append(f"{rel}:{node.lineno}: context.{node.attr} 未在 AppContext 静态提供（确认是否已接线）")
    return warnings


def run_startup_selfcheck() -> dict[str, Any]:
    """执行启动自检。

    Returns:
        结果字典，含各扫描项的状态；fail-fast 项失败时抛 SelfCheckError。
    """
    if _SKIP:
        logger.warning("启动自检被 SIYI_SKIP_SELFCHECK=1 跳过（仅紧急逃生，CI/生产不应设置）")
        return {"skipped": True}

    result: dict[str, Any] = {}

    # 1) 事件总线契约（fail-fast）
    emit_violations = check_event_bus_emit_signatures()
    result["emit_violations"] = emit_violations

    # 2) handler 可导入性（fail-fast）
    handler_failed, handler_ok = check_handlers_importable()
    result["handler_import_failed"] = handler_failed
    result["handler_import_ok"] = handler_ok

    # 3) context 接线（仅告警）
    ctx_warnings = check_context_wiring()
    result["context_wiring_warnings"] = ctx_warnings

    # ── 判定 ──
    fatal: list[str] = []
    fatal.extend(emit_violations)
    fatal.extend([f"handler 导入失败: {f}" for f in handler_failed])

    if fatal:
        for line in fatal:
            logger.error("[启动自检 FAIL] %s", line)
        raise SelfCheckError("启动自检发现 %d 项致命接缝漂移，阻断启动：\n  - " % len(fatal) + "\n  - ".join(fatal))

    for w in ctx_warnings:
        logger.warning("[启动自检 WARN] %s", w)
    logger.info(
        "启动自检通过：emit 契约 %d 违例 / handler 导入 %d 成功 / context 接线 %d 告警",
        len(emit_violations),
        handler_ok,
        len(ctx_warnings),
    )
    return result


if __name__ == "__main__":
    import json

    print(json.dumps(run_startup_selfcheck(), ensure_ascii=False, indent=2, default=str))
