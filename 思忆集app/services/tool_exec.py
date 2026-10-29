"""工具执行共享件（审计波次② C-1 步骤 3 抽离）。

B 套 ``services/react_executor.py`` 收缩为薄适配层后，两件东西被两侧同时需要，
放在任何一侧都会造成反向依赖（A 套 ``assistant/capability/_local_resource_tools``
不得 import B 套，B 套也不得 import assistant）：

1. LLM 参数容错基元 ``coerce_bool`` / ``coerce_int``；
2. 破坏性操作的**沙箱审批预队门** ``enforce_destructive_approval``
   （含预览构建 ``build_destructive_preview``）——它是 sandbox 确认闭环的
   转发职责，既要在 B 套入口生效，也要在 A 套原生 handler 生效，否则
   ``router.execute_direct("notes.delete")`` 会丢掉今日已有的审批闸。

语义与迁移前（react_executor :29-148）**逐字一致**，只做了两处诚实化修正：

- ``targets`` 先置空列表（原实现在仓储不可用时会 NameError，被外层
  except 吞成「工具执行异常」终态错误，用户拿不到审批卡）；
- 原先 ``except Exception: pass`` 的静默兜底补 debug 日志（无行为变化，
  只是留痕，遵守本仓 broad-except 棘轮）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.services.tool_exec")


def coerce_bool(value: Any, default: bool = False) -> bool:
    """把 LLM 可能返回的字符串/布尔值统一转成 bool。

    避免 ``bool("false")`` 这种把非空字符串误判为 True 的问题。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(value) if value is not None else default


def coerce_int(value: Any, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    """把 LLM 可能返回的字符串/数字统一转成 int，并做边界裁剪。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if min_val is not None:
        result = max(result, min_val)
    if max_val is not None:
        result = min(result, max_val)
    return result


# 需要用户确认后方可执行的破坏性操作
DESTRUCTIVE_ACTIONS = frozenset(
    {
        "notes.delete",
        "notes.batch_delete",
        "tasks.delete",
    }
)


def _note_repo_for(context: Any) -> Any:
    """取笔记仓储：既接受 AppContext（personal_note_repository），
    也接受 A 套 router 适配上下文（capability_router.note_repository）。"""
    repo = getattr(context, "personal_note_repository", None) if context else None
    if repo is not None:
        return repo
    router = getattr(context, "capability_router", None) if context else None
    return getattr(router, "note_repository", None)


def _task_repo_for(context: Any) -> Any:
    repo = getattr(context, "personal_task_repository", None) if context else None
    if repo is not None:
        return repo
    router = getattr(context, "capability_router", None) if context else None
    return getattr(router, "task_repository", None)


def build_destructive_preview(action_id: str, args: dict[str, Any], context: Any) -> dict[str, Any]:
    """为破坏性操作构建沙箱预览信息。"""
    preview: dict[str, Any] = {"action": action_id, "args": dict(args)}
    if action_id == "notes.delete":
        note_id = args.get("note_id")
        repo = _note_repo_for(context)
        title = ""
        if repo and note_id is not None:
            try:
                for n in repo.list_notes():
                    if str(n.get("id")) == str(note_id):
                        title = str(n.get("title") or "")
                        break
            except Exception:
                logger.debug("notes.delete 预览取标题失败", exc_info=True)
        preview["title"] = title
        preview["summary"] = f"删除笔记 id={note_id}"
    elif action_id == "notes.batch_delete":
        note_ids = str(args.get("note_ids", "")).strip()
        empty_only = coerce_bool(args.get("empty_only"), False)
        repo = _note_repo_for(context)
        titles: list[str] = []
        targets: list[Any] = []
        if repo:
            try:
                all_notes = repo.list_notes()
                if empty_only:
                    targets = [n for n in all_notes if not str(n.get("content") or "").strip()]
                elif note_ids:
                    target_ids = {raw.strip() for raw in note_ids.split(",") if raw.strip()}
                    targets = [n for n in all_notes if str(n.get("id")) in target_ids]
                else:
                    targets = []
                titles = [str(n.get("title") or "(无标题)") for n in targets[:10]]
            except Exception:
                logger.debug("notes.batch_delete 预览取目标失败", exc_info=True)
        preview["empty_only"] = empty_only
        preview["titles"] = titles
        preview["count"] = len(targets)
        preview["summary"] = f"批量删除 {len(titles)} 条笔记" + ("（空笔记）" if empty_only else "")
    elif action_id == "tasks.delete":
        task_id = args.get("task_id")
        repo = _task_repo_for(context)
        name = ""
        if repo and task_id is not None:
            try:
                for t in repo.list_tasks():
                    if str(t.get("id")) == str(task_id):
                        name = str(t.get("name") or t.get("title") or "")
                        break
            except Exception:
                logger.debug("tasks.delete 预览取名称失败", exc_info=True)
        preview["name"] = name
        preview["summary"] = f"删除任务 id={task_id}"
    return preview


def is_in_approval_context() -> bool:
    """当前是否处于 sandbox 审批放行后的执行流（``_in_approval_var`` 置位）。"""
    from services.sandbox_manager import _in_approval_var

    return bool(_in_approval_var.get())


def enforce_destructive_approval(action_id: str, args: dict[str, Any], context: Any) -> dict[str, Any] | None:
    """破坏性操作的审批预队门。

    命中 :data:`DESTRUCTIVE_ACTIONS` 且**不在**审批执行流中时，把动作挂进
    sandbox 待审批队列并返回 ``requires_approval`` 信封（调用方据此直接返回）；
    其余情形返回 ``None`` 表示放行继续执行。
    """
    if action_id not in DESTRUCTIVE_ACTIONS:
        return None
    from services.sandbox_manager import get_sandbox_manager

    if is_in_approval_context():
        return None

    sandbox = get_sandbox_manager()
    preview = build_destructive_preview(action_id, args, context)
    pid = sandbox.add(action_id, dict(args), preview)
    # P1-C 残留补刀：即时返回信封同须脱敏（见 method_registry 同注）
    from services.preview_redact import redact_preview_args

    return {
        "requires_approval": True,
        "pending_id": pid,
        "action": action_id,
        "preview": redact_preview_args(preview),
        "message": f"[等待用户批准] 已提交 {action_id} 审批 pending_id={pid}",
    }


__all__ = [
    "DESTRUCTIVE_ACTIONS",
    "build_destructive_preview",
    "coerce_bool",
    "coerce_int",
    "enforce_destructive_approval",
    "is_in_approval_context",
]
