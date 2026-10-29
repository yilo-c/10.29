from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.error_grading import (
    ANCHOR_SYNC_BATCH_BEGIN,
    ANCHOR_SYNC_BATCH_OK,
    ANCHOR_SYNC_BATCH_DEGRADED,
    ANCHOR_SYNC_RESOURCE_BEGIN,
    ANCHOR_SYNC_RESOURCE_OK,
    ANCHOR_SYNC_RESOURCE_CONFLICT,
    sync_anchor,
)
from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO

logger = logging.getLogger("siyi.api.sync_orchestrator")


@dataclass(frozen=True)
class ResourceSyncSpec:
    name: str
    pull_method: str
    merge_remote_method: str
    list_pending_method: str
    build_payload_method: str
    upsert_method: str
    delete_method: str
    mark_synced_method: str
    resolve_local_deleted_method: str


@dataclass(frozen=True)
class ResourceSyncResult:
    resource: str
    pulled: int
    pushed: int
    deleted: int
    skipped_local_deleted: int
    conflicts: int
    next_cursor: str | None


RESOURCE_SPECS: dict[str, ResourceSyncSpec] = {
    "notes": ResourceSyncSpec(
        name="notes",
        pull_method="pull_notes",
        merge_remote_method="merge_remote_notes",
        list_pending_method="list_pending_sync_notes",
        build_payload_method="build_note_sync_payload",
        upsert_method="upsert_note",
        delete_method="delete_note",
        mark_synced_method="mark_note_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_note",
    ),
    "tasks": ResourceSyncSpec(
        name="tasks",
        pull_method="pull_tasks",
        merge_remote_method="merge_remote_tasks",
        list_pending_method="list_pending_sync_tasks",
        build_payload_method="build_task_sync_payload",
        upsert_method="upsert_task",
        delete_method="delete_task",
        mark_synced_method="mark_task_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_task",
    ),
    "news": ResourceSyncSpec(
        name="news",
        pull_method="pull_news",
        merge_remote_method="merge_remote_news",
        list_pending_method="list_pending_sync_news",
        build_payload_method="build_news_sync_payload",
        upsert_method="upsert_news",
        delete_method="delete_news",
        mark_synced_method="mark_news_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_news",
    ),
    "translations": ResourceSyncSpec(
        name="translations",
        pull_method="pull_translations",
        merge_remote_method="merge_remote_translations",
        list_pending_method="list_pending_sync_translations",
        build_payload_method="build_translation_sync_payload",
        upsert_method="upsert_translation",
        delete_method="delete_translation",
        mark_synced_method="mark_translation_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_translation",
    ),
    "wordbooks": ResourceSyncSpec(
        name="wordbooks",
        pull_method="pull_wordbooks",
        merge_remote_method="merge_remote_wordbooks",
        list_pending_method="list_pending_sync_wordbooks",
        build_payload_method="build_wordbook_sync_payload",
        upsert_method="upsert_wordbook",
        delete_method="delete_wordbook",
        mark_synced_method="mark_wordbook_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_wordbook",
    ),
    "words": ResourceSyncSpec(
        name="words",
        pull_method="pull_words",
        merge_remote_method="merge_remote_words",
        list_pending_method="list_pending_sync_words",
        build_payload_method="build_word_sync_payload",
        upsert_method="upsert_word",
        delete_method="delete_word",
        mark_synced_method="mark_word_as_synced",
        resolve_local_deleted_method="resolve_local_only_deleted_word",
    ),
}


# ── 双开互斥守卫（波次④包 C，蓝本 §3-包C / §9-4）────────────────────────────
#
# §9-4 实证：orchestrator（A 栈，automation_hub）与 SyncBridge（B 栈，Cloudflare
# Worker E2E）**可同开、两栈守卫完全互不感知**；重叠资源 = notes / tasks（wire 名
# todos）。本守卫不改「可同开」的产品决策，只把「互不感知」显式化为「可同开但重叠
# 必告警」：两栈构造点各自登记一次 note_stack_launch，后登记方若发现两栈同开且
# canonical 资源集有交集，则产出启动 WARN 日志 + LAST_DUAL_STACK_DIAGNOSTIC 标记。
# 判定按资源而非按栈一刀切：只开不重叠资源（如 A=news / B=settings）不告警。

STACK_ORCHESTRATOR = "orchestrator"  # A 栈：ApiSyncOrchestrator（_tooling_provider.py 构造）
STACK_CLOUD_BRIDGE = "cloud_bridge"  # B 栈：SyncBridge（headless_main.py 构造）

# Worker/B 栈 wire 名 → A 栈（编排层）规范名的显式映射。两层词表是有意分歧
# （蓝本 §3-C#2 / 波次④包 B 内部映射表），不得静默合并；本表仅用于双开守卫的
# canonical 归一与词表分歧锁，**不改任何 wire 字段**（§4 协议变更另单）。
WORKER_RESOURCE_ALIAS: dict[str, str] = {"todos": "tasks"}

# B 栈周期同步资源集（sync/_cloud_sync_crypto.py RESOURCE_NOTES/TODOS/SETTINGS；
# info_brief/info_sections 为 standalone 推送频道，不参与双开重叠判定）
CLOUD_BRIDGE_SYNC_RESOURCES: tuple[str, ...] = ("notes", "todos", "settings")

_stack_launch_state: dict[str, dict[str, Any]] = {}
_dual_stack_diagnostic: dict[str, Any] | None = None


def canonical_resource(name: str) -> str:
    """把 wire/Worker 资源名归一为 A 栈规范名（todos→tasks，其余原样）。"""
    return WORKER_RESOURCE_ALIAS.get(str(name), str(name))


def reset_stack_launch_state() -> None:
    """清空两栈启动登记态（测试隔离用；生产构造点不依赖调用顺序）。"""
    global _dual_stack_diagnostic
    _stack_launch_state.clear()
    _dual_stack_diagnostic = None


def get_dual_stack_diagnostic() -> dict[str, Any] | None:
    """返回最近一次双开重叠诊断标记；无双开重叠时为 None。"""
    return _dual_stack_diagnostic


def note_stack_launch(
    stack: str,
    resources: Any,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """登记一个同步栈的启动态；若与另一已登记且同开的栈重叠 → 启动 WARN。

    Args:
        stack: STACK_ORCHESTRATOR（A 栈）或 STACK_CLOUD_BRIDGE（B 栈）。
        resources: 该栈覆盖的资源名集合（wire 名允许，内部经 canonical 归一）。
        enabled: 该栈是否实际启用（A 栈=hub client 就绪；B 栈=cloud_sync_enabled）。

    Returns:
        {"stack", "enabled", "resources", "overlap"} 诊断 dict。
    """
    global _dual_stack_diagnostic
    canonical = sorted({canonical_resource(r) for r in (resources or ())})
    _stack_launch_state[stack] = {"enabled": bool(enabled), "resources": canonical}

    overlap: list[str] = []
    enabled_stacks = sorted(s for s, st in _stack_launch_state.items() if st["enabled"])
    if len(enabled_stacks) == 2:
        sets = [_stack_launch_state[s]["resources"] for s in enabled_stacks]
        overlap = sorted(set(sets[0]) & set(sets[1]))
    if overlap:
        _dual_stack_diagnostic = {
            "stacks": enabled_stacks,
            "overlap": overlap,
        }
        # 可同开（产品决策不变），但重叠双开必须可观测（§9-4 显式化）
        logger.warning(
            "dual-stack sync overlap: stacks=%s resources=%s (orchestrator↔automation_hub "
            "and cloud_bridge↔worker may both touch these entities; guards are independent)",
            ",".join(enabled_stacks),
            ",".join(overlap),
        )
    return {
        "stack": stack,
        "enabled": bool(enabled),
        "resources": canonical,
        "overlap": overlap if bool(enabled) else [],
    }


class ApiSyncOrchestrator:
    """统一同步编排器：串联 pull/merge/conflict/push/cursor/audit。"""

    def __init__(self, provider: str, integration_hub, automation_hub_client, repository_map: dict[str, Any]):
        self.provider = str(provider or "automation_hub").strip().lower()
        self.integration_hub = integration_hub
        self.client = automation_hub_client
        self.repository_map = dict(repository_map or {})

    def sync_all(self, resources: list[str] | None = None, limit: int = 100) -> dict[str, Any]:
        selected_resources = resources or list(RESOURCE_SPECS.keys())
        sync_anchor(SEVERITY_INFO, ANCHOR_SYNC_BATCH_BEGIN, "sync batch started", resources=",".join(selected_resources))
        summary = {
            "provider": self.provider,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "resources": [],
            "total_conflicts": 0,
            "total_pushed": 0,
            "total_deleted": 0,
        }

        for resource_name in selected_resources:
            result = self.sync_resource(resource_name, limit=limit)
            summary["resources"].append(result)
            summary["total_conflicts"] += result["conflicts"]
            summary["total_pushed"] += result["pushed"]
            summary["total_deleted"] += result["deleted"]

        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        sync_anchor(
            SEVERITY_DEGRADED if summary["total_conflicts"] else SEVERITY_INFO,
            ANCHOR_SYNC_BATCH_DEGRADED if summary["total_conflicts"] else ANCHOR_SYNC_BATCH_OK,
            "sync batch completed",
            conflicts=summary["total_conflicts"],
            pushed=summary["total_pushed"],
        )
        self.integration_hub.append_audit_event(
            self.provider,
            "sync.batch.complete",
            "degraded" if summary["total_conflicts"] else "ok",
            {
                "resources": [item["resource"] for item in summary["resources"]],
                "total_conflicts": summary["total_conflicts"],
                "total_pushed": summary["total_pushed"],
                "total_deleted": summary["total_deleted"],
            },
        )
        return summary

    def sync_resource(self, resource_name: str, limit: int = 100) -> dict[str, Any]:
        sync_anchor(SEVERITY_INFO, ANCHOR_SYNC_RESOURCE_BEGIN, "sync resource begin", resource=resource_name)
        spec = RESOURCE_SPECS.get(resource_name)
        if spec is None:
            raise ValueError(f"unsupported resource: {resource_name}")

        repository = self.repository_map.get(resource_name)
        if repository is None:
            raise ValueError(f"missing repository binding for resource: {resource_name}")

        cursor_record = self.integration_hub.get_sync_cursor(self.provider, resource_name) or {}
        cursor = cursor_record.get("cursor")

        pull_method = getattr(self.client, spec.pull_method)
        merge_remote_method = getattr(repository, spec.merge_remote_method)

        all_remote_records: list[dict] = []
        next_cursor = cursor
        latest_cursor = cursor
        while True:
            pull_response = pull_method(cursor=next_cursor, limit=limit)
            page_records = self._extract_records(resource_name, pull_response)
            all_remote_records.extend(page_records)
            next_cursor = pull_response.next_cursor
            if next_cursor:
                latest_cursor = next_cursor
            if not next_cursor:
                break

        merge_result = merge_remote_method(all_remote_records, prefer_remote=False)
        conflicts = merge_result.get("conflicts") or []
        for conflict in conflicts:
            self.integration_hub.enqueue_conflict(
                self.provider,
                resource_name,
                local_id=str(conflict.get("local_id") or ""),
                remote_id=str(conflict.get("remote_id") or ""),
                reason="merge_conflict",
                payload=dict(conflict),
            )

        list_pending_method = getattr(repository, spec.list_pending_method)
        build_payload_method = getattr(repository, spec.build_payload_method)
        mark_synced_method = getattr(repository, spec.mark_synced_method)
        resolve_local_deleted_method = getattr(repository, spec.resolve_local_deleted_method)
        upsert_method = getattr(self.client, spec.upsert_method)
        delete_method = getattr(self.client, spec.delete_method)

        pushed = 0
        deleted = 0
        skipped_local_deleted = 0
        pending_items = list_pending_method()
        for item in pending_items:
            local_id = item.get("id")
            if local_id is None:
                continue
            payload = build_payload_method(local_id)
            if not isinstance(payload, dict):
                continue

            deleted_at = payload.get("deleted_at")
            remote_id = payload.get("remote_id")
            if deleted_at and not remote_id:
                resolve_local_deleted_method(local_id)
                skipped_local_deleted += 1
                continue

            if deleted_at:
                response_obj = delete_method(payload)
                remote_record = self._extract_write_result(resource_name, response_obj)
                mark_synced_method(local_id, remote_record)
                deleted += 1
                continue

            response_obj = upsert_method(payload)
            remote_record = self._extract_write_result(resource_name, response_obj)
            mark_synced_method(local_id, remote_record)
            pushed += 1

        # ── 游标语义（波次④包 C 文档化，蓝本 §1a「cursor 四类型不互通」/ §4 PP-2）──
        # 这里写的是 A 栈专属游标载体 kv `integration_sync_cursors`（③ 号载体）：
        #   * next_cursor：automation_hub 服务端返回的**字符串分页游标**（ISO 时间戳形态），
        #     由本函数上方 pull 循环逐页推进（sync_resource :175-192 读回）；
        #   * timestamp_fallback：服务端单页未返回 next_cursor 时，以「本次同步完成的
        #     UTC ISO 时刻」作为下次增量起点，extra.cursor_type 标注该语义。
        # 与 B 栈（SyncBridge）的 **float 秒级游标**（⑤ 号载体，settings 键 cloud_sync_*，
        # Worker `since` 强校验整数秒）**互不互通**：两套游标各自只对自己的对端服务，
        # 任何跨栈读写都是错的。打通需 Worker SQL + 手机端 cursor 三端同改，属 §4 PP-2
        # 跨端协议变更——**本波不动**（仅文档化多类型并存）。
        if latest_cursor:
            self.integration_hub.upsert_sync_cursor(
                self.provider,
                resource_name,
                cursor=latest_cursor,
                extra={"limit": int(limit)},
            )
        else:
            # 服务端本次未返回分页 cursor（单页数据），保存储同步时间戳作为下次增量起点
            fallback_cursor = datetime.now(timezone.utc).isoformat() + "Z"
            self.integration_hub.upsert_sync_cursor(
                self.provider,
                resource_name,
                cursor=fallback_cursor,
                extra={"limit": int(limit), "cursor_type": "timestamp_fallback"},
            )

        status = "degraded" if conflicts else "ok"
        result = ResourceSyncResult(
            resource=resource_name,
            pulled=len(all_remote_records),
            pushed=pushed,
            deleted=deleted,
            skipped_local_deleted=skipped_local_deleted,
            conflicts=len(conflicts),
            next_cursor=latest_cursor,
        )
        self.integration_hub.append_audit_event(
            self.provider,
            f"sync.resource.{resource_name}",
            status,
            {
                "pulled": result.pulled,
                "pushed": result.pushed,
                "deleted": result.deleted,
                "skipped_local_deleted": result.skipped_local_deleted,
                "conflicts": result.conflicts,
                "next_cursor": result.next_cursor,
            },
        )
        sync_anchor(
            SEVERITY_DEGRADED if result.conflicts else SEVERITY_INFO,
            ANCHOR_SYNC_RESOURCE_CONFLICT if result.conflicts else ANCHOR_SYNC_RESOURCE_OK,
            "sync resource completed",
            resource=resource_name,
            pulled=result.pulled,
            pushed=result.pushed,
            conflicts=result.conflicts,
        )
        return result.__dict__.copy()

    def get_sync_summary(self, resources: list[str] | None = None) -> dict[str, Any]:
        """Get sync summary for specified resources.



        Args:
            resources: 要查询的资源类型列表，默认使用 RESOURCE_SPECS 的所有键。

        Returns:
            {
                "provider": ...,
                "last_sync_time": "ISO 8601" or None,
                "resources": [{"resource": ..., "pending": N, "conflicts": N}, ...],
                "total_pending": N,
                "total_conflicts": N,
            }
        """
        selected_resources = resources or list(RESOURCE_SPECS.keys())
        provider = self.provider
        integration_hub = self.integration_hub

        resource_list = []
        total_pending = 0
        total_conflicts = 0

        # 读取所有未解决冲突
        all_conflicts = (
            integration_hub.list_conflicts(provider, unresolved_only=True)
            if integration_hub is not None
            else []
        )
        conflict_map: dict[str, int] = {}
        for item in all_conflicts:
            key = str(item.get("resource") or "").strip().lower()
            if key:
                conflict_map[key] = conflict_map.get(key, 0) + 1

        for resource_name in selected_resources:
            spec = RESOURCE_SPECS.get(resource_name)
            if spec is None:
                continue

            repository = self.repository_map.get(resource_name)
            pending_count = 0
            if repository is not None:
                list_pending_method = getattr(repository, spec.list_pending_method, None)
                if callable(list_pending_method):
                    try:
                        pending_items = list_pending_method()
                        pending_count = len(pending_items) if pending_items else 0
                    except Exception:
                        pending_count = 0

            conflict_count = conflict_map.get(resource_name, 0)
            total_pending += pending_count
            total_conflicts += conflict_count

            resource_list.append({
                "resource": resource_name,
                "pending": pending_count,
                "conflicts": conflict_count,
            })

        # 计算最近同步时间
        last_sync_time: str | None = None
        if integration_hub is not None and hasattr(integration_hub, "get_latest_sync_time"):
            last_sync_time = integration_hub.get_latest_sync_time(provider)

        return {
            "provider": provider,
            "last_sync_time": last_sync_time,
            "resources": resource_list,
            "total_pending": total_pending,
            "total_conflicts": total_conflicts,
        }

    def _extract_records(self, resource_name: str, response_obj: Any) -> list[dict]:
        attr_map = {
            "notes": "notes",
            "tasks": "tasks",
            "news": "items",
            "translations": "records",
            "wordbooks": "wordbooks",
            "words": "words",
        }
        attr = attr_map.get(resource_name)
        data = getattr(response_obj, attr, []) if attr else []
        return [dict(item) for item in data if isinstance(item, dict)]

    def _extract_write_result(self, resource_name: str, response_obj: Any) -> dict[str, Any]:
        attr_map = {
            "notes": "note",
            "tasks": "task",
            "news": "item",
            "translations": "record",
            "wordbooks": "wordbook",
            "words": "word",
        }
        attr = attr_map.get(resource_name)
        value = getattr(response_obj, attr, None) if attr else None
        return dict(value) if isinstance(value, dict) else {}
