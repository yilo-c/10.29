"""siyi_* MCP 工具注册（M3 首批，11 个数据工具；runner 注入时另加
siyi_tool_run / siyi_tool_list，共 13 个）。

工具契约属于"数据边界"：换执行内核（DSH / Claude Code / Codex）时
这批工具名与 schema 保持不变 —— 内核侧都是标准 MCP client。

命名约定：``siyi_<域>_<动作>``；返回统一为 JSON 文本。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, cast

from services.tooling import tool_policy
from . import desktop as _desktop  # noqa: F401  审批门控（_apply_policy_gate / _gated 共用）
from .tool_runner import _McpAudit  # 七维-安C3(c)：复用既有 audit_mcp.db 审计写路径
from .protocol import ToolSpec
from .store import SiyiStore

logger = logging.getLogger("siyi.mcp_server.tools")


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _load_confirmation_store():
    """按文件路径直接加载 services.confirmation_store，避免触发 services/__init__
    （其内部 eager import 多个重服务且可能间接引入 app_runtime/assistant，违反 MCP
    Server 隔离约束 §3.1）。confirmation_store.py 仅依赖标准库，可安全独立加载。
    """
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "services" / "confirmation_store.py"
    spec = importlib.util.spec_from_file_location("_siyi_confirmation_store", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _confirm_request(cs, args: dict) -> dict:
    cid = cs.create(
        args.get("action_id", "unknown"),
        dict(args.get("args") or {}),
        source="mcp:kernel",
        title=args.get("title") or args.get("action_id") or "",
        detail=args.get("detail") or "",
        ttl_s=float(args.get("ttl_s") or 300),
    )
    return {
        "confirmation_id": cid,
        "status": "pending",
        "message": "已发起确认请求，等待用户在前端（桌面端/手机端）批准后，内核可重新下发该动作。",
    }


def _confirm_status(cs, confirmation_id) -> dict:
    e = cs.get(confirmation_id)
    if e is None:
        return {"confirmation_id": confirmation_id, "status": "expired_or_not_found"}
    return {
        "confirmation_id": e["confirmation_id"],
        "action_id": e["action_id"],
        "status": e["status"],
        "source": e.get("source"),
        "reason": e.get("reason"),
        "created_at": e.get("created_at"),
        "expires_at": e.get("expires_at"),
    }


def _with_declared_level(spec: ToolSpec) -> ToolSpec:
    """为工具 spec 声明工具级别（M5 工具策略层）。

    启发式：get/list/search/status → RO；create/update/set → RW；
    delete/backup_snapshot/app_launch/shell/exec → DANGER；health/notify → RO
    （services/tooling/tool_policy.classify_tool_level，对齐 core/action_guard 语义但独立实现）。

    缺声明（工具不在显式声明表且启发式结果为 RW）→ 视为 RW + 告警日志。
    显式声明非法 → 告警并按启发式兜底。
    """
    level = tool_policy.classify_tool_level(spec.name)
    if spec.level not in tool_policy.TOOL_LEVELS:
        logger.warning("MCP tool %s 级别声明 %r 非法，视为 %s", spec.name, spec.level, level)
    elif not tool_policy.is_explicitly_declared(spec.name) and level == tool_policy.DEFAULT_TOOL_LEVEL:
        logger.warning("MCP tool %s 缺级别声明，视为 RW", spec.name)
    if spec.level == level:
        return spec
    return dataclasses.replace(spec, level=level)


# ── 七维-安C3(c)：policy 缺省 fail-close ─────────────────────────────
# 审批闭环自身（发起/查询确认）不可被门控，否则内核永远无法发起审批（死锁）。
_APPROVAL_LOOP_TOOLS = frozenset({"siyi_confirm_request", "siyi_confirm_status"})


def _resolve_gate_decision(spec: ToolSpec, policy: Any, kernel: str) -> str:
    """解析工具调用决策：allow / deny / require_approval。

    七维-安C3(c)：policy 为 None（settings 未配置，缺省）时 fail-close ——
    RW/DANGER 默认 require_approval；RO 与审批闭环工具（siyi_confirm_request /
    siyi_confirm_status）放行。policy 非 None 时保持既有 resolve_policy
    语义（allow_all / restrict 白名单）。
    """
    if policy is not None:
        return tool_policy.resolve_policy(spec.level, policy, spec.name, kernel)
    if spec.name in _APPROVAL_LOOP_TOOLS:
        return tool_policy.DECISION_ALLOW
    if spec.level == tool_policy.TOOL_LEVEL_RO:
        return tool_policy.DECISION_ALLOW
    return tool_policy.DECISION_REQUIRE_APPROVAL


def _audit_gate_block(audit: Any, spec: ToolSpec, reason: str, kernel: str) -> None:
    """被拦调用写入既有 audit_mcp.db 审计路径（复用 tool_runner._McpAudit.record）。"""
    if audit is None:
        return
    audit.record(spec.name, "policy_gate", False, 0.0, {"reason": reason, "level": spec.level, "kernel": kernel or ""})


def _apply_policy_gate(
    spec: ToolSpec,
    policy: Any,
    kernel: str,
    cs: Any = None,
    audit: Any = None,
) -> ToolSpec:
    """策略门控包装 handler（M5 调用入口判定，参照 mcp_server/desktop.py 审批门控）。

    - allow            → 直放（调用原 handler）
    - deny             → 返回明确错误（无副作用，不执行原 handler），并落审计
    - require_approval → 走 ConfirmationStore（_require_approved：未批准返回
      requires_approval 并落审计，已批准放行）

    cs：与确认闭环工具共用的 store 侧 ConfirmationStore 实例（与
    SiyiStore.data_dir 一致，跨进程经 confirmations.json 对齐；不再使用
    get_confirmation_store() 单例，避免误写其缺省真实数据目录）。
    """
    orig = spec.handler

    def _wrapped(args: dict) -> str:
        decision = _resolve_gate_decision(spec, policy, kernel)
        if decision == tool_policy.DECISION_DENY:
            _audit_gate_block(audit, spec, "denied", kernel)
            return _json(
                {
                    "status": "denied",
                    "tool": spec.name,
                    "level": spec.level,
                    "error": "tool denied by agent_kernel.mcp_tool_policy（无副作用拦截）",
                }
            )
        if decision == tool_policy.DECISION_REQUIRE_APPROVAL:
            gate = _desktop._require_approved(cs, spec.name, args)
            if gate is not None:
                _audit_gate_block(audit, spec, str(gate.get("status") or "blocked"), kernel)
                return _json(gate)
            # 已批准 → 直放执行（原 handler）
        return orig(args)

    return dataclasses.replace(spec, handler=_wrapped)


def build_tool_specs(
    store: SiyiStore,
    runner: Any = None,
    policy: Any = None,
    kernel: str = "",
) -> list[ToolSpec]:
    """构建全部 siyi_* MCP 工具 spec。

    M5 工具策略层：
    - 每个 spec 声明 ``level``（RO/RW/DANGER，缺声明视为 RW + 告警日志）。
    - ``policy``（agent_kernel.mcp_tool_policy 原始值）不为 None 时应用策略门控：
      allow 直放 / deny 明确错误（无副作用）/ require_approval 走 ConfirmationStore。
    - ``kernel``：本 MCP 实例服务的内核名（per-kernel 白名单用；None/''=仅全局策略）。
    - 七维-安C3(c)：默认（policy=None，settings 未配置）→ fail-close：RW/DANGER
      默认 require_approval，RO 与审批闭环工具（siyi_confirm_*）放行；被拦调用
      复用 tool_runner._McpAudit 落 audit_mcp.db。
    """
    specs = [
        ToolSpec(
            name="siyi_health",
            description="Siyi personal data server health: data dir + counts of notes/tasks/inbox/news. Call first to verify connectivity.",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args: _json(store.health()),
        ),
        ToolSpec(
            name="siyi_notes_search",
            description="Search the user's personal notes by keyword. Returns id/title/content_preview/tags.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "keyword to search"},
                    "limit": {"type": "integer", "description": "max results, default 10"},
                },
                "required": ["query"],
            },
            handler=lambda args: _json(store.notes_search(args.get("query", ""), args.get("limit", 10))),
        ),
        ToolSpec(
            name="siyi_notes_create",
            description="Create a new personal note. Returns the new note id.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "folder_path": {"type": "string", "description": "default '/'"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "content"],
            },
            handler=lambda args: _json(
                store.notes_create(
                    args.get("title", ""),
                    args.get("content", ""),
                    args.get("folder_path", "/"),
                    args.get("tags"),
                )
            ),
        ),
        ToolSpec(
            name="siyi_tasks_list",
            description="List the user's tasks (uncompleted by default).",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "default 20"},
                    "include_completed": {"type": "boolean"},
                },
            },
            handler=lambda args: _json(store.tasks_list(args.get("limit", 20), bool(args.get("include_completed", False)))),
        ),
        ToolSpec(
            name="siyi_tasks_create",
            description="Create a task/todo for the user.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string", "description": "low/medium/high, default medium"},
                    "due_date": {"type": "string", "description": "optional ISO date"},
                },
                "required": ["title"],
            },
            handler=lambda args: _json(
                store.tasks_create(
                    args.get("title", ""),
                    args.get("priority", "medium"),
                    args.get("due_date"),
                )
            ),
        ),
        ToolSpec(
            name="siyi_inbox_list",
            description="List recent inbox entries (captured ideas/items awaiting triage).",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "default 20"}},
            },
            handler=lambda args: _json(store.inbox_list(args.get("limit", 20))),
        ),
        ToolSpec(
            name="siyi_inbox_add",
            description="Add an item to the user's inbox for later triage.",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "title": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["content"],
            },
            handler=lambda args: _json(
                store.inbox_add(
                    args.get("content", ""),
                    args.get("title", ""),
                    args.get("tags"),
                )
            ),
        ),
        # ── M-mcp P0：任务 / 笔记 / 收件箱（全部走 SiyiStore 门面，禁 import assistant/backend） ──
        ToolSpec(
            name="siyi_tasks_toggle",
            description="切换任务完成状态（默认标记完成）。",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": ["string", "integer"], "description": "任务 ID"},
                    "is_completed": {"type": "boolean", "default": True, "description": "是否标记为完成"},
                },
                "required": ["task_id"],
            },
            handler=lambda args: _json(store.tasks_toggle(cast("str | int", args.get("task_id")), args.get("is_completed", True))),
        ),
        ToolSpec(
            name="siyi_tasks_update",
            description="更新任务字段（标题 / 优先级 / 截止日期）。",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": ["string", "integer"], "description": "任务 ID"},
                    "title": {"type": "string", "description": "新标题（可选）"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "新优先级（可选）"},
                    "due_date": {"type": "string", "description": "新截止日期 YYYY-MM-DD（可选）"},
                },
                "required": ["task_id"],
            },
            handler=lambda args: _json(
                store.tasks_update(cast("str | int", args.get("task_id")), args.get("title"), args.get("priority"), args.get("due_date"))
            ),
        ),
        ToolSpec(
            name="siyi_tasks_search",
            description="按关键词搜索任务（留空返回全部）。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词（可选）"},
                    "limit": {"type": "integer", "default": 20, "description": "返回条数上限"},
                },
            },
            handler=lambda args: _json(store.tasks_list(args.get("query", ""), include_completed=True)),
        ),
        ToolSpec(
            name="siyi_notes_get",
            description="按 ID 获取单条笔记详情。",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": ["string", "integer"], "description": "笔记 ID"},
                },
                "required": ["note_id"],
            },
            handler=lambda args: _json(store.notes_get(cast("str | int", args.get("note_id")))),
        ),
        ToolSpec(
            name="siyi_notes_update",
            description="更新笔记字段（标题 / 正文 / 目录 / 标签）。",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": ["string", "integer"], "description": "笔记 ID"},
                    "title": {"type": "string", "description": "新标题（可选）"},
                    "content": {"type": "string", "description": "新正文（可选）"},
                    "folder_path": {"type": "string", "description": "新目录（可选）"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签（可选，覆盖式）"},
                },
                "required": ["note_id"],
            },
            handler=lambda args: _json(
                store.notes_update(
                    cast("str | int", args.get("note_id")), args.get("title"), args.get("content"), args.get("folder_path"), args.get("tags")
                )
            ),
        ),
        ToolSpec(
            name="siyi_notes_tag",
            description="覆盖式设置笔记标签。",
            input_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": ["string", "integer"], "description": "笔记 ID"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                },
                "required": ["note_id", "tags"],
            },
            handler=lambda args: _json(store.notes_tag(cast("str | int", args.get("note_id")), cast("list[str]", args.get("tags")))),
        ),
        ToolSpec(
            name="siyi_inbox_update",
            description="更新收件箱条目（标题 / 正文 / 状态 / 标签）。",
            input_schema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": ["string", "integer"], "description": "收件箱条目 ID"},
                    "title": {"type": "string", "description": "新标题（可选）"},
                    "content": {"type": "string", "description": "新正文（可选）"},
                    "status": {"type": "string", "description": "新状态（可选）"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "新标签（可选，覆盖式）"},
                },
                "required": ["entry_id"],
            },
            handler=lambda args: _json(
                store.inbox_update(
                    cast("str | int", args.get("entry_id")), args.get("title"), args.get("content"), args.get("status"), args.get("tags")
                )
            ),
        ),
        ToolSpec(
            name="siyi_vocab_review_status",
            description="查询词汇本中到期待复习的词汇概览（只读）。",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数上限，默认 20"},
                },
            },
            handler=lambda args: _json(store.vocab_review_status(args.get("limit", 20))),
        ),
        ToolSpec(
            name="siyi_vocab_quiz",
            description="记录一次词汇复习打分（FSRS，quality 0-5），返回复习后状态。",
            input_schema={
                "type": "object",
                "properties": {
                    "word_id": {"type": ["string", "integer"], "description": "词汇 ID"},
                    "quality": {"type": "integer", "description": "FSRS 评级 0-5（0=完全遗忘，5=完美回忆）"},
                },
                "required": ["word_id", "quality"],
            },
            handler=lambda args: _json(store.vocab_quiz(args.get("word_id"), args.get("quality"))),
        ),
        ToolSpec(
            name="siyi_info_brief",
            description="返回信息收集缓存的简报（按来源聚合、未读数，只读）。",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数上限，默认 15"},
                },
            },
            handler=lambda args: _json(store.info_brief(args.get("limit", 15))),
        ),
        ToolSpec(
            name="siyi_knowledge_query",
            description="跨源知识检索（笔记/任务/收件箱/资讯/词汇），RRF 融合 + 意图识别（只读）。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词"},
                    "limit": {"type": "integer", "description": "融合后返回条数上限，默认 15"},
                },
                "required": ["query"],
            },
            handler=lambda args: _json(store.knowledge_query(args.get("query", ""), args.get("limit", 15))),
        ),
        ToolSpec(
            name="siyi_memory_recall",
            description="记忆召回（跨源融合 + 拼接 recall_context 供智能体引用，只读）。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "召回关键词"},
                    "limit": {"type": "integer", "description": "融合后返回条数上限，默认 10"},
                },
                "required": ["query"],
            },
            handler=lambda args: _json(store.memory_recall(args.get("query", ""), args.get("limit", 10))),
        ),
        ToolSpec(
            name="siyi_inbox_classify",
            description="对收件箱条目做启发式分类（无 AI，仅分析建议，不写入数据）。",
            input_schema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": ["string", "integer"], "description": "收件箱条目 ID"},
                },
                "required": ["entry_id"],
            },
            handler=lambda args: _json(store.inbox_classify(args.get("entry_id"))),
        ),
        ToolSpec(
            name="siyi_news_list",
            description="List collected news/info items (newest first).",
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "default 15"},
                    "unread_only": {"type": "boolean"},
                },
            },
            handler=lambda args: _json(store.news_list(args.get("limit", 15), bool(args.get("unread_only", False)))),
        ),
        ToolSpec(
            name="siyi_news_save",
            description="Save/upsert one news item into the user's news library (keyed by url).",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "source": {"type": "string", "description": "e.g. 'mcp', 'hackernews'"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "title_zh": {"type": "string"},
                    "summary_zh": {"type": "string"},
                },
                "required": ["url", "title"],
            },
            handler=lambda args: _json(
                store.news_save(
                    args.get("url", ""),
                    args.get("title", ""),
                    args.get("summary", ""),
                    args.get("source", "mcp"),
                    args.get("tags"),
                    args.get("title_zh", ""),
                    args.get("summary_zh", ""),
                )
            ),
        ),
        ToolSpec(
            name="siyi_notify",
            description="Push a notification to the user's desktop notification center.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "priority": {"type": "string", "description": "normal/high/urgent, default normal"},
                },
                "required": ["title", "content"],
            },
            handler=lambda args: _json(
                store.notify(
                    args.get("title", ""),
                    args.get("content", ""),
                    args.get("priority", "normal"),
                )
            ),
        ),
        ToolSpec(
            name="siyi_memory_store",
            description="Write one structured memory entry (RW): persist a durable fact / work result "
            "into the memory library so it can be recalled later via siyi_memory_recall. "
            "memory_type examples: '@知识' '@偏好' '@任务' '@经验'.",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "the fact/result to persist"},
                    "memory_type": {"type": "string", "description": "supertag, e.g. '@知识', default '@知识'"},
                    "entity_type": {"type": "string", "description": "optional entity type, e.g. person/project"},
                    "source": {"type": "string", "description": "origin tag, default 'kernel'"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["content"],
            },
            handler=lambda args: _json(
                store.memory_store(
                    args.get("content", ""),
                    args.get("memory_type", "@知识"),
                    args.get("entity_type", ""),
                    args.get("source", "kernel"),
                    args.get("tags"),
                )
            ),
        ),
    ]
    # 审批闭环（契约 §4.3）：内核经 MCP 发起确认，前端批准后由内核重新下发动作。
    cs = _load_confirmation_store().ConfirmationStore(data_dir=store.data_dir)
    specs.append(
        ToolSpec(
            name="siyi_confirm_request",
            description="Request user approval for a sensitive action before executing it "
            "(e.g. notes.delete). Returns a confirmation_id; poll siyi_confirm_status "
            "until approved/rejected. Requires desktop-side approval (审批权在壳).",
            input_schema={
                "type": "object",
                "properties": {
                    "action_id": {"type": "string", "description": "target action/tool id, e.g. 'notes.delete'"},
                    "args": {"type": "object", "description": "action arguments"},
                    "title": {"type": "string", "description": "human-readable title"},
                    "detail": {"type": "string", "description": "human-readable detail"},
                    "ttl_s": {"type": "integer", "description": "expiry seconds, default 300"},
                },
                "required": ["action_id"],
            },
            handler=lambda args: _json(_confirm_request(cs, args)),
        )
    )
    specs.append(
        ToolSpec(
            name="siyi_confirm_status",
            description="Query the status of a confirmation request by confirmation_id. status ∈ pending / approved / rejected / expired.",
            input_schema={
                "type": "object",
                "properties": {
                    "confirmation_id": {"type": "string"},
                },
                "required": ["confirmation_id"],
            },
            handler=lambda args: _json(_confirm_status(cs, args.get("confirmation_id"))),
        )
    )

    # ── OQ2：桌面能力 P1 工具（安全边界 = 审批门控，见 mcp_server/desktop.py）──
    # 全部 risk=high：先经 siyi_confirm_request 发起审批，批准后重调本工具执行；
    # 未批准一律返回 requires_approval，内核不可自我批准。browser.* 暂缓。

    def _gated(action_id: str, fn):
        def _handler(args: dict) -> str:
            cs = _load_confirmation_store().get_confirmation_store()
            gate = _desktop._require_approved(cs, action_id, args)
            if gate is not None:
                return _json(gate)  # requires_approval / unavailable / error
            result = fn(args)
            return _json(result)

        return _handler

    _desktop_specs = [
        ToolSpec(
            name="siyi_backup_snapshot",
            description="(risk=high, 需批准) 触发全量数据快照备份（笔记/任务/设置/通知/记忆），保存到 backups/snapshots/ 并轮转保留最近 10 份。",
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "备份原因，如 'daily-review'"},
                },
            },
            handler=_gated("siyi_backup_snapshot", lambda args: store.backup_snapshot(args.get("reason", "mcp.kernel"))),
        ),
        ToolSpec(
            name="siyi_desktop_screenshot",
            description="(risk=high, 需批准) 截取当前屏幕保存为 PNG。",
            input_schema={"type": "object", "properties": {}},
            handler=_gated("siyi_desktop_screenshot", lambda args: _desktop.desktop_screenshot(args.get("target_dir", "."))),
        ),
        ToolSpec(
            name="siyi_clipboard_read",
            description="(risk=high, 需批准) 读取剪贴板文本。",
            input_schema={"type": "object", "properties": {}},
            handler=_gated("siyi_clipboard_read", lambda args: _desktop.clipboard_read()),
        ),
        ToolSpec(
            name="siyi_clipboard_write",
            description="(risk=high, 需批准) 写入剪贴板文本。",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            handler=_gated("siyi_clipboard_write", lambda args: _desktop.clipboard_write(args.get("text", ""))),
        ),
        ToolSpec(
            name="siyi_app_launch",
            description=f"(risk=high, 需批准, 白名单: {', '.join(sorted(_desktop.APP_LAUNCH_ALLOWLIST))}) 启动桌面应用。",
            input_schema={"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]},
            handler=_gated("siyi_app_launch", lambda args: _desktop.app_launch(args.get("app_name", ""))),
        ),
    ]
    specs.extend(_desktop_specs)

    if runner is not None:
        specs.append(
            ToolSpec(
                name="siyi_tool_run",
                description="Run a registered external CLI tool from the tool registry. "
                "risk=high tools are refused here (requires desktop-side approval).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "tool_id": {"type": "string", "description": "registered tool id"},
                        "command": {"type": "string", "description": "command name of the tool"},
                        "params": {"type": "object", "description": "command params"},
                    },
                    "required": ["tool_id", "command"],
                },
                handler=lambda args: _json(runner.run(args.get("tool_id", ""), args.get("command", ""), args.get("params") or {})),
            )
        )
        specs.append(
            ToolSpec(
                name="siyi_tool_list",
                description="List registered external tools (tool registry).",
                input_schema={"type": "object", "properties": {}},
                handler=lambda args: _json(runner.list_tools()),
            )
        )
    # M5：声明每个工具级别（缺声明视为 RW + 告警日志）
    specs = [_with_declared_level(s) for s in specs]
    # M5+安C3(c)：策略门控 —— policy 非 None 按 allow_all/restrict；policy=None
    # （缺省）fail-close：RW/DANGER require_approval、RO 放行。门控与确认闭环
    # 共用 store 侧 ConfirmationStore；被拦调用写入既有 audit_mcp.db 审计路径。
    audit = _McpAudit(store.data_dir)
    specs = [_apply_policy_gate(s, policy, kernel, cs=cs, audit=audit) for s in specs]
    return specs
