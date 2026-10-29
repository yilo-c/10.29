"""L2 旅程 E2E（T4 全模块测试战役）：笔记 / 任务 / 收件箱 三条整车用户旅程。

走真实仓储（LocalDataStore + repositories/*，tmp_path 数据目录）+ 真实
MethodRegistry.dispatch（含 JSON Schema 校验与 R4 审批闸门），AI 调用一律
mock（收件箱分类返回固定 JSON），不触网、不依赖后端进程、不碰真实数据。

- J1 笔记旅程: notes.create → notes.list 可见 → FTS 内容检索命中（非 AI
  路径，repo.search_notes）→ notes.update → R4 删除闸门 → 软删除 →
  notes.list_trashed → notes.restore → 全程状态一致
- J2 任务旅程: tasks.create(due_date) → 到期查询（客户端过滤语义）→
  tasks.schedule / tasks.unschedule（FakeTaskManager 桥）→ tasks.toggle
  完成状态机
- J3 收件箱旅程: inbox.add → inbox.classify（mock AI 固定分类写回）→
  转为任务/笔记 → 源条目归档状态一致
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.handlers._inbox_crud import register_inbox_crud
from backend.handlers._notes import register_notes
from backend.handlers._tasks import register_tasks
from backend.method_registry import MethodRegistry
from data.local_storage import LocalDataStore
from repositories._inbox import LocalPersonalInboxRepository
from repositories._note import LocalPersonalNoteRepository
from repositories._task import LocalPersonalTaskRepository


# ── 测试替身 ──────────────────────────────────────────────────────────────


class _MockAIClient:
    """固定返回预置文本的 AI 客户端桩（inbox.classify 走 complete_text）。"""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete_text(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append({"messages": messages, **kwargs})
        return self.response


class _FakeTaskManager:
    """记录 schedule/unschedule 调用的 TaskManager 桩（tasks.schedule 桥接）。"""

    def __init__(self) -> None:
        self.scheduled: list[dict[str, Any]] = []
        self.unscheduled: list[str] = []

    def schedule_task_reminder(
        self,
        task_id: str,
        schedule: str = "",
        run_at: str = "",
        repeat: str = "none",
        reminder_text: str = "",
        max_retries: int = 0,
        retry_backoff: int = 60,
    ) -> dict[str, Any]:
        self.scheduled.append(
            {
                "task_id": task_id,
                "schedule": schedule,
                "run_at": run_at,
                "repeat": repeat,
                "reminder_text": reminder_text,
            }
        )
        return {"success": True, "task_id": task_id, "run_at": run_at}

    def unschedule_task(self, task_id: str) -> dict[str, Any]:
        self.unscheduled.append(task_id)
        return {"success": True, "task_id": task_id}


class _JourneyContext:
    """最小 DI context：只注入旅程需要的仓储，其余槽位显式置空。

    settings_repository=None 会关闭 notes 自动打标与 inbox.add 自动分类的
    后台 AI 路径（生产语义：开关默认关），保证旅程内 AI 调用只发生在
    显式 mock 的 inbox.classify 上。
    """

    def __init__(
        self,
        note_repo: Any,
        task_repo: Any,
        inbox_repo: Any,
        ai_client: Any,
        task_manager: Any,
    ) -> None:
        self.personal_note_repository = note_repo
        self.personal_task_repository = task_repo
        self.personal_inbox_repository = inbox_repo
        self.ai_client = ai_client
        self.task_manager = task_manager
        self.settings_repository = None
        self.write_guard = None


class _Env:
    """一趟旅程的完整环境：registry + context + 三个真实仓储 + 桩。"""

    def __init__(
        self,
        registry: MethodRegistry,
        context: Any,
        note_repo: LocalPersonalNoteRepository,
        task_repo: LocalPersonalTaskRepository,
        inbox_repo: LocalPersonalInboxRepository,
        ai_client: _MockAIClient,
        task_manager: _FakeTaskManager | None,
    ) -> None:
        self.registry = registry
        self.context = context
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.inbox_repo = inbox_repo
        self.ai_client = ai_client
        self.task_manager = task_manager


def _build_env(tmp_path: Any, *, with_task_manager: bool = True) -> _Env:
    store = LocalDataStore(base_dir=str(tmp_path))
    note_repo = LocalPersonalNoteRepository(store)
    task_repo = LocalPersonalTaskRepository(store)
    inbox_repo = LocalPersonalInboxRepository(store)
    ai_client = _MockAIClient()
    task_manager = _FakeTaskManager() if with_task_manager else None
    context = _JourneyContext(
        note_repo=note_repo,
        task_repo=task_repo,
        inbox_repo=inbox_repo,
        ai_client=ai_client,
        task_manager=task_manager,
    )
    registry = MethodRegistry()
    register_notes(registry, context)
    register_tasks(registry, context)
    register_inbox_crud(registry, context)
    return _Env(
        registry=registry,
        context=context,
        note_repo=note_repo,
        task_repo=task_repo,
        inbox_repo=inbox_repo,
        ai_client=ai_client,
        task_manager=task_manager,
    )


# ── J1 笔记旅程 ──────────────────────────────────────────────────────────


async def test_journey_notes_create_search_update_trash_restore(tmp_path) -> None:
    env = _build_env(tmp_path)
    reg = env.registry

    # 1. 创建
    created = await reg.dispatch(
        "notes.create",
        {
            "title": "Q3 灵犀云项目复盘",
            "content": "灵犀云白皮书初稿完成，下周评审灰度方案。",
            "folder": "工作",
            "tags": ["meeting", "q3"],
        },
    )
    nid = created["id"]
    assert nid
    assert created["title"] == "Q3 灵犀云项目复盘"

    # 2. 列表可见（folder/tags 落库一致）
    notes = await reg.dispatch("notes.list", {})
    assert [n["id"] for n in notes] == [nid]
    assert notes[0]["title"] == "Q3 灵犀云项目复盘"
    assert notes[0]["folder_path"] == "工作"
    assert notes[0]["tags"] == ["meeting", "q3"]

    # 3. 单条读取
    got = await reg.dispatch("notes.get", {"note_id": nid})
    assert got is not None
    assert got["content"] == "灵犀云白皮书初稿完成，下周评审灰度方案。"

    # 4. 内容检索命中（FTS 非 AI 路径：repo.search_notes）
    assert any(h["id"] == nid for h in env.note_repo.search_notes("灵犀云"))
    assert any(h["id"] == nid for h in env.note_repo.search_notes("复盘"))
    assert env.note_repo.search_notes("zzqqxy不存在的词") == []

    # 5. 更新（内容 + 标签；旧内容进版本历史）
    ok = await reg.dispatch(
        "notes.update",
        {
            "note_id": nid,
            "content": "灵犀云白皮书已定稿，灰度方案通过。",
            "tags": ["meeting", "q3", "done"],
        },
    )
    assert ok is True
    got2 = await reg.dispatch("notes.get", {"note_id": nid})
    assert got2["content"] == "灵犀云白皮书已定稿，灰度方案通过。"
    assert got2["tags"] == ["meeting", "q3", "done"]
    assert int(got2.get("version") or 0) >= 2
    assert got2["versions"][0]["content"] == "灵犀云白皮书初稿完成，下周评审灰度方案。"
    # 检索反映更新后内容
    assert any(h["id"] == nid for h in env.note_repo.search_notes("定稿"))

    # 6. R4 闸门：未审批的删除请求挂起且不改变状态
    gate = await reg.dispatch("notes.delete", {"note_id": nid})
    assert gate.get("pending") is True
    assert gate.get("requires_approval") is True
    assert len(await reg.dispatch("notes.list", {})) == 1

    # 7. 审批通过（skip_approval 重放路径）→ 软删除
    ok = await reg.dispatch("notes.delete", {"note_id": nid}, skip_approval=True)
    assert ok is True
    assert await reg.dispatch("notes.list", {}) == []
    assert await reg.dispatch("notes.get", {"note_id": nid}) is None

    # 8. 回收站可见，内容未丢
    trashed = await reg.dispatch("notes.list_trashed", {})
    assert len(trashed) == 1
    assert trashed[0]["id"] == nid
    assert trashed[0]["deleted_at"]
    assert trashed[0]["content"] == "灵犀云白皮书已定稿，灰度方案通过。"

    # 9. 恢复 → 回到列表，内容/标签一致，回收站清空
    ok = await reg.dispatch("notes.restore", {"note_id": nid})
    assert ok is True
    back = await reg.dispatch("notes.get", {"note_id": nid})
    assert back is not None
    assert back["deleted_at"] is None
    assert back["content"] == "灵犀云白皮书已定稿，灰度方案通过。"
    assert back["tags"] == ["meeting", "q3", "done"]
    assert await reg.dispatch("notes.list_trashed", {}) == []
    assert [n["id"] for n in await reg.dispatch("notes.list", {})] == [nid]


# ── J2 任务旅程 ──────────────────────────────────────────────────────────


async def test_journey_tasks_create_due_query_schedule_toggle(tmp_path) -> None:
    env = _build_env(tmp_path)
    reg = env.registry
    today = datetime.now().date()
    future = (today + timedelta(days=3)).isoformat()
    past = (today - timedelta(days=1)).isoformat()

    # 1. 创建（含到期日）
    ta = await reg.dispatch("tasks.create", {"title": "提交季度报告", "priority": "high", "due_date": future})
    await reg.dispatch("tasks.create", {"title": "缴水电费", "priority": "normal", "due_date": past})
    await reg.dispatch("tasks.create", {"title": "今天复诊", "priority": "high", "due_date": today.isoformat()})
    await reg.dispatch("tasks.create", {"title": "无边界的想法"})
    ida = ta["id"]

    got = await reg.dispatch("tasks.get", {"task_id": ida})
    assert got["title"] == "提交季度报告"
    assert got["priority"] == "high"
    assert got["due_date"] == future
    assert got["is_completed"] is False
    assert got["completed"] is False

    # 2. 到期查询（非 AI：客户端按 due_date 过滤，今天为边界含当日）
    tasks_all = await reg.dispatch("tasks.list", {})
    assert len(tasks_all) == 4
    due = sorted(t["title"] for t in tasks_all if t.get("due_date") and str(t["due_date"])[:10] <= today.isoformat())
    assert due == ["今天复诊", "缴水电费"]

    # 3. 定时提醒桥接（tasks.schedule → TaskManager.schedule_task_reminder）
    run_at = future + "T09:00:00"
    sched = await reg.dispatch(
        "tasks.schedule",
        {"task_id": str(ida), "run_at": run_at, "reminder_text": "该交季报了"},
    )
    assert sched.get("success") is True
    assert env.task_manager.scheduled[0]["task_id"] == str(ida)
    assert env.task_manager.scheduled[0]["run_at"] == run_at
    assert env.task_manager.scheduled[0]["reminder_text"] == "该交季报了"
    uns = await reg.dispatch("tasks.unschedule", {"task_id": str(ida)})
    assert uns.get("success") is True
    assert env.task_manager.unscheduled == [str(ida)]

    # 4. toggle 完成状态机：False → True → False → True，双字段同步、版本单调
    assert await reg.dispatch("tasks.toggle", {"task_id": ida, "completed": True}) is True
    done = await reg.dispatch("tasks.get", {"task_id": ida})
    assert done["is_completed"] is True
    assert done["completed"] is True
    v1 = int(done["version"])

    assert await reg.dispatch("tasks.toggle", {"task_id": ida, "completed": False}) is True
    undone = await reg.dispatch("tasks.get", {"task_id": ida})
    assert undone["is_completed"] is False
    assert undone["completed"] is False
    assert int(undone["version"]) == v1 + 1

    assert await reg.dispatch("tasks.toggle", {"task_id": ida, "completed": True}) is True
    final = await reg.dispatch("tasks.get", {"task_id": ida})
    assert final["is_completed"] is True
    # toggle 不改变任务数量
    assert len(await reg.dispatch("tasks.list", {})) == 4


# ── J3 收件箱旅程 ────────────────────────────────────────────────────────


async def test_journey_inbox_classify_writeback_convert(tmp_path) -> None:
    env = _build_env(tmp_path)
    reg = env.registry

    # 1. 捕获到收件箱（默认 new / manual）
    added = await reg.dispatch(
        "inbox.add",
        {"content": "周五 15:00 看牙医复诊，记得带医保卡", "title": "牙医复诊"},
    )
    eid = added["id"]
    assert isinstance(eid, int)
    listed = await reg.dispatch("inbox.list", {})
    assert len(listed) == 1
    assert listed[0]["status"] == "new"
    assert listed[0]["source"] == "manual"

    # 2. AI 分类（mock 固定返回 task 分类）→ 写回条目
    env.ai_client.response = json.dumps(
        {
            "kind": "task",
            "confidence": 0.92,
            "reason": "包含明确时间与行动项",
            "suggested_action": "convert_to_task",
        },
        ensure_ascii=False,
    )
    cls = await reg.dispatch("inbox.classify", {"entry_id": eid})
    assert cls.get("success") is True
    assert cls.get("kind") == "task"
    assert cls.get("confidence") == 0.92
    assert cls.get("suggested_action") == "convert_to_task"
    entry = env.inbox_repo.get_entry(eid)
    assert entry["ai_kind"] == "task"
    assert entry["ai_confidence"] == 0.92
    assert entry["ai_reason"] == "包含明确时间与行动项"
    assert entry["ai_suggested_action"] == "convert_to_task"
    assert len(env.ai_client.calls) == 1

    # 3. 按分类建议转为任务，源条目归档并携带转换痕迹
    due = (datetime.now().date() + timedelta(days=2)).isoformat()
    created = await reg.dispatch("tasks.create", {"title": "看牙医复诊", "priority": "normal", "due_date": due})
    tid = created["id"]
    upd = await reg.dispatch(
        "inbox.update",
        {
            "entry_id": eid,
            "status": "archived",
            "metadata": {"converted_task_id": str(tid), "converted": True},
        },
    )
    assert upd.get("success") is True
    active = await reg.dispatch("inbox.list", {})
    assert all(e["id"] != eid for e in active)
    archived = await reg.dispatch("inbox.list", {"include_archived": True})
    src = [e for e in archived if e["id"] == eid]
    assert len(src) == 1
    assert src[0]["status"] == "archived"
    assert src[0]["metadata"]["converted_task_id"] == str(tid)
    assert src[0]["ai_kind"] == "task"
    task = await reg.dispatch("tasks.get", {"task_id": tid})
    assert task["title"] == "看牙医复诊"
    assert task["due_date"] == due

    # 4. 第二条：分类为 note → 转为笔记，源条目归档
    added2 = await reg.dispatch(
        "inbox.add",
        {"content": "把灵犀云灰度方案整理成一页纪要", "title": "灰度纪要"},
    )
    eid2 = added2["id"]
    env.ai_client.response = json.dumps(
        {
            "kind": "note",
            "confidence": 0.88,
            "reason": "知识沉淀类内容",
            "suggested_action": "convert_to_note",
        },
        ensure_ascii=False,
    )
    cls2 = await reg.dispatch("inbox.classify", {"entry_id": eid2})
    assert cls2.get("success") is True
    assert cls2.get("kind") == "note"
    note_created = await reg.dispatch(
        "notes.create",
        {"title": "灰度纪要", "content": "把灵犀云灰度方案整理成一页纪要", "folder": "工作"},
    )
    nid = note_created["id"]
    await reg.dispatch(
        "inbox.update",
        {
            "entry_id": eid2,
            "status": "archived",
            "metadata": {"converted_note_id": str(nid), "converted": True},
        },
    )
    note = await reg.dispatch("notes.get", {"note_id": nid})
    assert note["content"] == "把灵犀云灰度方案整理成一页纪要"
    arch2 = await reg.dispatch("inbox.list", {"include_archived": True})
    src2 = [e for e in arch2 if e["id"] == eid2]
    assert len(src2) == 1
    assert src2[0]["status"] == "archived"
    assert src2[0]["metadata"]["converted_note_id"] == str(nid)


# ── 契约补充：分类与调度的错误路径 ────────────────────────────────────────


async def test_inbox_classify_contract_errors(tmp_path) -> None:
    env = _build_env(tmp_path)
    reg = env.registry

    # 条目不存在
    miss = await reg.dispatch("inbox.classify", {"entry_id": 999})
    assert miss == {"success": False, "error": "entry not found"}
    # entry_id 缺省
    zero = await reg.dispatch("inbox.classify", {})
    assert zero.get("success") is False
    # ai_client 不可用
    added = await reg.dispatch("inbox.add", {"content": "x"})
    env.context.ai_client = None
    no_ai = await reg.dispatch("inbox.classify", {"entry_id": added["id"]})
    assert no_ai == {"success": False, "error": "ai_client unavailable"}


async def test_tasks_schedule_requires_task_manager(tmp_path) -> None:
    env = _build_env(tmp_path, with_task_manager=False)
    reg = env.registry
    created = await reg.dispatch("tasks.create", {"title": "无桥任务"})
    result = await reg.dispatch(
        "tasks.schedule",
        {"task_id": str(created["id"]), "run_at": "2026-09-01T09:00:00"},
    )
    assert result == {"success": False, "error": "task manager not available"}
