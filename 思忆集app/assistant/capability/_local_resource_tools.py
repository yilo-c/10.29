"""本地资源工具 — notes / tasks / inbox / vocab / info.brief / knowledge.query /
transfer.send 的**原生执行体**。

审计波次② C-1 步骤 3：此前本模块只做「B 套 ``services/react_executor.make_react_executor``
的薄转发接线」（22 个 handler 全量回调 B 套），与「B 套收缩后统一走
``router.dispatch``」形成 A↔B 循环委托（无限递归）。因此把这 22 条实现从 B 套
**逐字搬迁**为原生 handler，本模块不再 import ``services.react_executor``。

搬迁纪律：

- 返回信封保持 B 套形态 ``{"error": ...}`` / ``{"result": ...}``（ReAct / butler
  侧与既有回归测试全部按此形状消费，不在本波顺手改形状）；
- LLM 参数容错基元与破坏性审批预队门上移到 ``services.tool_exec``（两侧共用，
  避免 assistant → services 反向依赖）；
- 依赖一律懒读取（property）：注册发生在 router ``__init__``，而
  vocabulary_repository 等由 _context_builder 构建后注入，固化快照会拿到 None。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from random import choice as _random_choice
from typing import Any, Callable

# 唯一事实来源在 core/action_guard.py（准入白名单必须用同一份清单，
# 否则「注册了但被白名单拦」的断线会再次发生）。此处保持同名导出。
from core.action_guard import LOCAL_RESOURCE_ACTIONS
from services.tool_exec import coerce_bool, coerce_int, enforce_destructive_approval

logger = logging.getLogger("siyi.assistant.autonomy.router.local_tools")

__all__ = ["LOCAL_RESOURCE_ACTIONS", "register_local_resource_handlers"]


class _RouterLocalToolContext:
    """把 AssistantCapabilityRouter 的仓储字段适配成本模块 handler 期望的属性名。"""

    def __init__(self, router: Any) -> None:
        self._router = router

    @property
    def personal_note_repository(self) -> Any:
        return getattr(self._router, "note_repository", None)

    @property
    def personal_task_repository(self) -> Any:
        return getattr(self._router, "task_repository", None)

    @property
    def personal_inbox_repository(self) -> Any:
        return getattr(self._router, "inbox_repository", None)

    @property
    def vocabulary_repository(self) -> Any:
        return getattr(self._router, "vocabulary_repository", None)

    @property
    def memory_index_service(self) -> Any:
        return getattr(self._router, "memory_index_service", None)

    @property
    def knowledge_graph_store(self) -> Any:
        return getattr(self._router, "knowledge_graph_store", None)

    @property
    def info_item_repository(self) -> Any:
        return getattr(self._router, "info_item_repository", None)

    @property
    def local_data_store(self) -> Any:
        return getattr(self._router, "data_store", None)


# ── 词库 ────────────────────────────────────────────────────────────────────


def _vocab_quiz_in_chat(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "vocabulary_repository", None)
    if not repo:
        return {"error": "词库不可用"}
    prefer_due = coerce_bool(args.get("prefer_due"), True)
    wordbook_id = args.get("wordbook_id")
    try:
        if wordbook_id:
            words = repo.get_words_by_wordbook(int(wordbook_id))
        else:
            words = repo.list_words()
    except Exception as exc:
        logger.warning("vocab.quiz_in_chat 读取词库失败: %s", exc)
        return {"error": f"读取词库失败: {exc}"}
    if not words:
        return {"result": {"empty": True, "message": "词库还是空的，先加几个词再来叫我考你吧。"}}

    now = datetime.now(timezone.utc).isoformat()
    pool: list[dict] = []
    if prefer_due:
        pool = [w for w in words if w.get("next_review") and w["next_review"] <= now]
    if not pool:
        pool = words
    quiz_word = _random_choice(pool)
    return {
        "result": {
            "word_id": quiz_word.get("id"),
            "word": quiz_word.get("word"),
            "meaning": quiz_word.get("meaning"),
            "example": quiz_word.get("example") or "",
            "phonetic": quiz_word.get("phonetic") or "",
            "due": bool(quiz_word.get("next_review") and quiz_word["next_review"] <= now),
            "hint": "把英文猜出来再揭晓；如果你说错了我会记一笔。",
        }
    }


def _vocab_review_status(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "vocabulary_repository", None)
    if not repo:
        return {"error": "词库不可用"}
    try:
        words = repo.list_words()
    except Exception as exc:
        logger.warning("vocab.review_status 读取词库失败: %s", exc)
        return {"error": f"读取词库失败: {exc}"}
    now = datetime.now(timezone.utc).isoformat()
    today_local = datetime.now().strftime("%Y-%m-%d")
    total = len(words)
    due = sum(1 for w in words if w.get("next_review") and w["next_review"] <= now)
    reviewed_today = sum(1 for w in words if str(w.get("last_review") or "").startswith(today_local))
    return {
        "result": {
            "total": total,
            "due": due,
            "reviewed_today": reviewed_today,
            "today": today_local,
        }
    }


# ── 笔记 ────────────────────────────────────────────────────────────────────


def _notes_list(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    limit = coerce_int(args.get("limit"), 20, 1, 200)
    notes = repo.list_notes()[:limit]
    simplified = [
        {
            "id": n.get("id"),
            "title": n.get("title"),
            "tags": n.get("tags"),
            "created_at": n.get("created_at"),
            "updated_at": n.get("updated_at"),
            "content_length": len(str(n.get("content") or "")),
            "is_empty": not str(n.get("content") or "").strip(),
        }
        for n in notes
    ]
    return {"result": simplified, "count": len(simplified)}


def _notes_search(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "缺少 query 参数"}
    max_results = coerce_int(args.get("max_results"), 20, 1, 100)
    if not hasattr(repo, "search_notes"):
        return {"error": "笔记仓储不支持搜索"}
    notes = repo.search_notes(query, max_results=max_results)
    simplified = [
        {
            "id": n.get("id"),
            "title": n.get("title"),
            "tags": n.get("tags"),
            "updated_at": n.get("updated_at"),
            "content_length": len(str(n.get("content") or "")),
            "is_empty": not str(n.get("content") or "").strip(),
        }
        for n in notes
    ]
    return {"result": simplified, "count": len(simplified)}


def _notes_create(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    title = str(args.get("title", "")).strip()
    if not title:
        return {"error": "缺少 title 参数"}
    content = str(args.get("content", ""))
    tags_raw = str(args.get("tags", "")).strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    note_data = {"title": title, "content": content, "tags": tags}
    note_id = repo.add_note(note_data)
    return {"result": {"id": note_id, "title": title}, "note_id": note_id}


def _notes_get(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    note_id = args.get("note_id")
    if note_id is None:
        return {"error": "缺少 note_id 参数"}
    notes = repo.list_notes()
    # get 本质需要定位笔记：规范化比较（str|int 等价）；update/delete 已
    # 下沉为适配器布尔契约（U0 治本），不再在此自建解析。
    for n in notes:
        if str(n.get("id")) == str(note_id):
            return {"result": n}
    return {"error": f"笔记不存在: {note_id}"}


def _notes_update(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    note_id = args.get("note_id")
    if note_id is None:
        return {"error": "缺少 note_id 参数"}
    new_info: dict[str, Any] = {}
    for k in ("title", "content", "category", "favorite", "folder_path"):
        if k in args:
            new_info[k] = args[k]
    if "tags" in args:
        tags_raw = str(args.get("tags", "")).strip()
        new_info["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    if "folder" in args and "folder_path" not in new_info:
        new_info["folder_path"] = args["folder"]
    if not new_info:
        return {"error": "未提供要更新的字段"}
    # U0 治本（2026-09-19）：适配器 S0-1 已返回诚实布尔（str|int 原生解析，
    # 未知 id → False）——直接透传并消费返回值，不再自建 id 解析（第三套）。
    ok = repo.update_note(note_id, new_info)
    return {"result": {"note_id": note_id, "updated": bool(ok)}}


def _notes_tag(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    note_ids_raw = args.get("note_ids")
    if not note_ids_raw:
        return {"error": "缺少 note_ids 参数"}
    if isinstance(note_ids_raw, list):
        note_ids = [str(x) for x in note_ids_raw]
    else:
        note_ids = [x.strip() for x in str(note_ids_raw).split(",") if x.strip()]
    tags_raw = str(args.get("tags", "")).strip()
    if not tags_raw:
        return {"error": "缺少 tags 参数"}
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    remove = coerce_bool(args.get("remove"), False)
    updated: list[str] = []
    for nid in note_ids:
        try:
            notes = repo.list_notes()
            note_target = next((n for n in notes if str(n.get("id")) == str(nid)), None)
            if note_target is None:
                continue
            current_tags = list(note_target.get("tags") or [])
            if remove:
                new_tags = [t for t in current_tags if t not in tags]
            else:
                new_tags = list(dict.fromkeys(current_tags + tags))
            repo.update_note(nid, {"tags": new_tags})
            updated.append(str(nid))
        except Exception:
            logger.debug("notes.tag 单条打标签失败: %s", nid, exc_info=True)
    return {"result": {"updated_count": len(updated), "note_ids": updated, "tags": tags, "remove": remove}}


def _notes_delete(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    note_id = args.get("note_id")
    if note_id is None:
        return {"error": "缺少 note_id 参数"}
    # 同 update：透传并消费适配器的诚实布尔（U0 治本）
    deleted = repo.delete_note(note_id)
    return {"result": {"note_id": note_id, "deleted": bool(deleted)}}


def _notes_batch_delete(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_note_repository", None)
    if not repo:
        return {"error": "笔记仓储不可用"}
    note_ids_raw = args.get("note_ids")
    empty_only = coerce_bool(args.get("empty_only"), False)
    ids_to_delete: list[str] = []
    if note_ids_raw:
        if isinstance(note_ids_raw, list):
            ids_to_delete = [str(x) for x in note_ids_raw]
        elif isinstance(note_ids_raw, str):
            ids_to_delete = [x.strip() for x in note_ids_raw.split(",") if x.strip()]
    elif empty_only:
        for n in repo.list_notes():
            content = str(n.get("content") or "").strip()
            if not content:
                ids_to_delete.append(str(n.get("id")))
    else:
        return {"error": "需提供 note_ids 或设置 empty_only=true"}
    deleted = []
    for nid in ids_to_delete:
        try:
            repo.delete_note(nid)
            deleted.append(nid)
        except Exception:
            logger.debug("notes.batch_delete 单条删除失败: %s", nid, exc_info=True)
    return {"result": {"deleted_count": len(deleted), "note_ids": deleted}}


# ── 任务 ────────────────────────────────────────────────────────────────────


def _tasks_list(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    limit = coerce_int(args.get("limit"), 30, 1, 200)
    tasks = repo.list_tasks()[:limit]
    simplified = [
        {
            "id": t.get("id"),
            "name": t.get("name") or t.get("title"),
            "is_completed": bool(t.get("is_completed")),
            "due_date": t.get("due_date"),
        }
        for t in tasks
    ]
    return {"result": simplified, "count": len(simplified)}


def _tasks_search(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "缺少 query 参数"}
    max_results = coerce_int(args.get("max_results"), 20, 1, 100)
    if not hasattr(repo, "search_tasks"):
        return {"error": "任务仓储不支持搜索"}
    tasks = repo.search_tasks(query, max_results=max_results)
    simplified = [
        {
            "id": t.get("id"),
            "name": t.get("name") or t.get("title"),
            "is_completed": bool(t.get("is_completed")),
            "due_date": t.get("due_date"),
            "updated_at": t.get("updated_at"),
        }
        for t in tasks
    ]
    return {"result": simplified, "count": len(simplified)}


def _tasks_create(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "缺少 name 参数"}
    data = {"name": name, "description": str(args.get("description", "")), "is_completed": False}
    if args.get("due_date"):
        data["due_date"] = str(args["due_date"])
    task_id = repo.add_task(data)
    return {"result": {"id": task_id, "name": name}, "task_id": task_id}


def _tasks_get(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    task_id = args.get("task_id")
    if task_id is None:
        return {"error": "缺少 task_id 参数"}
    tasks = repo.list_tasks()
    for t in tasks:
        if t.get("id") == task_id:
            return {"result": t}
    return {"error": f"任务不存在: {task_id}"}


def _tasks_update(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    task_id = args.get("task_id")
    if task_id is None:
        return {"error": "缺少 task_id 参数"}
    task_info: dict[str, Any] = {}
    for k in ("name", "description", "due_date", "priority", "is_completed"):
        if k in args:
            task_info[k] = args[k]
    if not task_info:
        return {"error": "未提供要更新的字段"}
    hit = repo.update_task(task_id, task_info)
    # S1-2：消费 bool 命中信号（仅认显式 False，旧式 None 实现不误伤）
    if hit is False:
        return {"error": f"任务不存在或不可编辑: {task_id}"}
    return {"result": {"task_id": task_id, "updated": True}}


def _tasks_delete(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    task_id = args.get("task_id")
    if task_id is None:
        return {"error": "缺少 task_id 参数"}
    repo.delete_task(task_id)
    return {"result": {"task_id": task_id, "deleted": True}}


def _tasks_toggle(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_task_repository", None)
    if not repo:
        return {"error": "任务仓储不可用"}
    task_id = args.get("task_id")
    if task_id is None:
        return {"error": "缺少 task_id 参数"}
    completed = coerce_bool(args.get("completed"), True)
    repo.toggle_task_completion(task_id, completed)
    return {"result": {"task_id": task_id, "completed": completed}}


# ── 收件箱 ──────────────────────────────────────────────────────────────────


def _inbox_add(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_inbox_repository", None)
    if not repo:
        return {"error": "收件箱仓储不可用"}
    content = str(args.get("content", "")).strip()
    if not content:
        return {"error": "缺少 content 参数"}
    title = str(args.get("title", content[:30]))
    source = str(args.get("source", "agent"))
    entry = {"title": title, "content": content, "source": source}
    entry_id = repo.add_entry(entry)
    return {"result": {"id": entry_id, "title": title}, "entry_id": entry_id}


def _inbox_list(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    repo = getattr(ctx, "personal_inbox_repository", None)
    if not repo:
        return {"error": "收件箱仓储不可用"}
    include_archived = coerce_bool(args.get("include_archived"), False)
    # 无界加载收口（2026-09-16）：本 action 直连 repo，走的是
    # list_entries(limit=None) 全量路径 —— 689 条真实数据下这条链路
    # 会产出 2MB+ 的中介结果，最终随 agent 链结果回注 WS 帧。守卫虽已把
    # 它从"打死连接"降级为"可见错误(-32007)"，但根治要在取数侧分页。
    # 口径与 RPC 面 inbox.list 一致：默认 50、上限 200。
    limit = coerce_int(args.get("limit"), 50, 1, 200)
    offset = coerce_int(args.get("offset"), 0, 0, None)
    entries = repo.list_entries(include_archived=include_archived, limit=limit, offset=offset)
    return {"result": entries, "count": len(entries), "limit": limit, "offset": offset}


# ── 资讯 / 知识 / 传送 ──────────────────────────────────────────────────────


def _info_brief(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from services.info_collector_service import InfoCollectorService

    cached = InfoCollectorService.get_cached()
    items = cached.get("items", []) or []
    limit = coerce_int(args.get("limit"), 20, 1, 100)
    return {
        "result": {
            "brief": cached.get("brief", ""),
            "items": items[:limit],
            "cached_at": cached.get("cached_at", 0.0),
            "total": len(items),
        }
    }


def _knowledge_query(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from knowledge.unified_service import UnifiedKnowledgeService

    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "缺少 query 参数"}
    limit = coerce_int(args.get("limit"), 15, 1, 50)
    service = UnifiedKnowledgeService(
        memory_search=getattr(ctx, "memory_index_service", None),
        knowledge_graph=getattr(ctx, "knowledge_graph_store", None),
        rag_pipeline=None,
        note_service=getattr(ctx, "personal_note_repository", None),
        wordbook_repo=getattr(ctx, "vocabulary_repository", None),
        info_item_repo=getattr(ctx, "info_item_repository", None),
    )
    return {"result": service.query(query, limit=limit)}


def _transfer_send(ctx: _RouterLocalToolContext, args: dict[str, Any]) -> dict[str, Any]:
    ds = getattr(ctx, "local_data_store", None)
    base_dir = getattr(ds, "base_dir", None) if ds else None
    if not base_dir:
        return {"error": "data_store 不可用，无法传送"}
    text = str(args.get("text", "")).strip()
    if not text:
        return {"error": "缺少 text 参数"}
    send_target = str(args.get("target", "mobile")).strip() or "mobile"
    transfer_entry: dict[str, Any] = {
        "text": text[:5000],
        "target": send_target,
        "from_device": "agent",
        "timestamp": time.time(),
    }
    try:
        transfer_path = os.path.join(base_dir, "transfer_history.json")
        os.makedirs(base_dir, exist_ok=True)
        history: list[dict] = []
        if os.path.exists(transfer_path):
            with open(transfer_path, "r", encoding="utf-8") as f:
                try:
                    history = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    history = []
        history.insert(0, transfer_entry)
        if len(history) > 50:
            history = history[:50]
        tmp = f"{transfer_path}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    logger.debug("transfer.send fsync 失败（非致命）", exc_info=True)
            os.replace(tmp, transfer_path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    logger.debug("transfer.send 临时文件清理失败", exc_info=True)
    except Exception as exc:
        logger.warning("transfer.send persist failed: %s", exc)
        return {"error": f"传送记录写入失败: {exc}"}
    try:
        from core.events import AppEvent, event_bus

        event_bus.emit(AppEvent("transfer.received", payload=transfer_entry))
    except Exception:
        logger.debug("transfer.received 事件发射失败（非致命）", exc_info=True)
    return {"result": {"success": True, "target": send_target, "text_preview": text[:80]}}


# action_id → 原生实现（与 core/action_guard.LOCAL_RESOURCE_ACTIONS 一一对应）
_LOCAL_RESOURCE_HANDLERS: dict[str, Callable[[_RouterLocalToolContext, dict[str, Any]], dict[str, Any]]] = {
    "vocab.quiz_in_chat": _vocab_quiz_in_chat,
    "vocab.review_status": _vocab_review_status,
    "notes.list": _notes_list,
    "notes.search": _notes_search,
    "notes.create": _notes_create,
    "notes.get": _notes_get,
    "notes.update": _notes_update,
    "notes.tag": _notes_tag,
    "notes.delete": _notes_delete,
    "notes.batch_delete": _notes_batch_delete,
    "tasks.list": _tasks_list,
    "tasks.search": _tasks_search,
    "tasks.create": _tasks_create,
    "tasks.get": _tasks_get,
    "tasks.update": _tasks_update,
    "tasks.delete": _tasks_delete,
    "tasks.toggle": _tasks_toggle,
    "inbox.add": _inbox_add,
    "inbox.list": _inbox_list,
    "info.brief": _info_brief,
    "knowledge.query": _knowledge_query,
    "transfer.send": _transfer_send,
}


def _make_action_handler(
    ctx: _RouterLocalToolContext,
    action_id: str,
    impl: Callable[[_RouterLocalToolContext, dict[str, Any]], dict[str, Any]],
):
    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        args = dict(payload or {})
        # 破坏性操作审批预队门（与 B 套同一实现、同一 ContextVar 判据）：
        # 保住 router.execute_direct("notes.delete") 今日已有的「先入队等批准」语义。
        held = enforce_destructive_approval(action_id, args, ctx)
        if held is not None:
            return held
        return impl(ctx, args)

    return _handler


def register_local_resource_handlers(router: Any) -> int:
    """把本地资源工具的原生实现注册进 ``router._handlers``，返回新注册数量。

    已存在的 action_id 一律跳过（不覆盖既有接线，例如某工具迁移为原生
    domain 实现后自动以原生版为准）。
    """
    ctx = _RouterLocalToolContext(router)
    registered = 0
    for action_id in LOCAL_RESOURCE_ACTIONS:
        impl = _LOCAL_RESOURCE_HANDLERS.get(action_id)
        if impl is None:
            logger.warning("本地资源工具 %s 无原生实现，接线跳过（断线风险）", action_id)
            continue
        if action_id in router._handlers:
            continue
        router._handlers[action_id] = _make_action_handler(ctx, action_id, impl)
        registered += 1
    if registered:
        logger.info("Registered %d local resource tool handlers", registered)
    return registered
