from __future__ import annotations



import logging
from datetime import datetime, timezone
from typing import Any



from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO
from core.audit_writer import get_audit_writer



logger = logging.getLogger("siyi.api.integration_hub")






class ApiIntegrationHub:
    """统一 API 接入底座：OAuth、令牌游标、冲突和审计日志。"""



    MAX_AUDIT_LOGS = 5000



    def __init__(self, data_store=None, observability_service=None):
        self.data_store = data_store
        self.observability_service = observability_service
        # T1.1：统一审计写入器（独立 audit_trail.db 单写线程），失败时退回旧路径
        self._writer = None
        try:
            data_dir = getattr(data_store, "base_dir", None)
            self._writer = get_audit_writer(data_dir=data_dir, observability_service=observability_service)
        except Exception:
            logger.warning("audit writer init failed (fallback to legacy path)", exc_info=True)
            self._writer = None



    # ---------- OAuth token ----------
    def get_oauth_token(self, provider: str) -> dict[str, Any] | None:
        provider_key = self._normalize_key(provider)
        tokens = self._load_map("integration_oauth_tokens")
        record = tokens.get(provider_key)
        return dict(record) if isinstance(record, dict) else None



    def upsert_oauth_token(self, provider: str, token_payload: dict[str, Any]) -> dict[str, Any]:
        provider_key = self._normalize_key(provider)
        tokens = self._load_map("integration_oauth_tokens")
        old_record = tokens.get(provider_key) if isinstance(tokens.get(provider_key), dict) else {}



        new_record = {
            "provider": provider_key,
            "access_token": str(token_payload.get("access_token") or "").strip(),
            "refresh_token": str(token_payload.get("refresh_token") or "").strip(),
            "scope": str(token_payload.get("scope") or old_record.get("scope") or "").strip(),
            "token_type": str(token_payload.get("token_type") or old_record.get("token_type") or "Bearer").strip(),
            "expires_at": token_payload.get("expires_at") or old_record.get("expires_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": old_record.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }



        tokens[provider_key] = new_record
        self._save_map("integration_oauth_tokens", tokens)
        self._append_audit(
            provider_key,
            "oauth.upsert",
            "ok",
            {
                "has_access_token": bool(new_record["access_token"]),
                "has_refresh_token": bool(new_record["refresh_token"]),
                "expires_at": new_record.get("expires_at"),
            },
        )
        return dict(new_record)



    def clear_oauth_token(self, provider: str) -> bool:
        provider_key = self._normalize_key(provider)
        tokens = self._load_map("integration_oauth_tokens")
        removed = provider_key in tokens
        if removed:
            tokens.pop(provider_key, None)
            self._save_map("integration_oauth_tokens", tokens)
            self._append_audit(provider_key, "oauth.clear", "ok", {})
        return removed



    # ---------- incremental cursor ----------
    def get_sync_cursor(self, provider: str, resource: str) -> dict[str, Any] | None:
        state = self._load_map("integration_sync_cursors")
        return self._get_nested(state, self._normalize_key(provider), self._normalize_key(resource))



    def upsert_sync_cursor(self, provider: str, resource: str, cursor: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        provider_key = self._normalize_key(provider)
        resource_key = self._normalize_key(resource)
        state = self._load_map("integration_sync_cursors")
        provider_bucket = state.get(provider_key)
        if not isinstance(provider_bucket, dict):
            provider_bucket = {}



        old_record = provider_bucket.get(resource_key) if isinstance(provider_bucket.get(resource_key), dict) else {}
        record = {
            "cursor": str(cursor or "").strip(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": old_record.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "extra": dict(extra or old_record.get("extra") or {}),
        }
        provider_bucket[resource_key] = record
        state[provider_key] = provider_bucket
        self._save_map("integration_sync_cursors", state)



        self._append_audit(
            provider_key,
            "sync.cursor.upsert",
            "ok",
            {
                "resource": resource_key,
                "cursor": record["cursor"],
            },
        )
        return dict(record)



    # ---------- conflict queue ----------
    def list_conflicts(self, provider: str, resource: str | None = None, unresolved_only: bool = True) -> list[dict[str, Any]]:
        provider_key = self._normalize_key(provider)
        conflicts = self._load_list("integration_conflicts")
        result = []
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            if self._normalize_key(item.get("provider")) != provider_key:
                continue
            if resource is not None and self._normalize_key(item.get("resource")) != self._normalize_key(resource):
                continue
            if unresolved_only and str(item.get("status") or "open").strip().lower() != "open":
                continue
            result.append(dict(item))
        return sorted(result, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)



    def enqueue_conflict(
        self,
        provider: str,
        resource: str,
        local_id: str,
        remote_id: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_key = self._normalize_key(provider)
        resource_key = self._normalize_key(resource)
        local_key = str(local_id or "").strip()
        remote_key = str(remote_id or "").strip()
        conflicts = self._load_list("integration_conflicts")



        now = datetime.now(timezone.utc).isoformat()
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            if (
                self._normalize_key(item.get("provider")) == provider_key
                and self._normalize_key(item.get("resource")) == resource_key
                and str(item.get("local_id") or "") == local_key
                and str(item.get("remote_id") or "") == remote_key
                and str(item.get("status") or "open").strip().lower() == "open"
            ):
                item["reason"] = str(reason or item.get("reason") or "conflict")
                item["payload"] = dict(payload or item.get("payload") or {})
                item["updated_at"] = now
                self._save_list("integration_conflicts", conflicts)
                self._append_audit(
                    provider_key,
                    "sync.conflict.refresh",
                    "degraded",
                    {
                        "resource": resource_key,
                        "local_id": local_key,
                        "remote_id": remote_key,
                    },
                )
                return dict(item)



        conflict_id = self._next_conflict_id(conflicts)
        record = {
            "id": conflict_id,
            "provider": provider_key,
            "resource": resource_key,
            "local_id": local_key,
            "remote_id": remote_key,
            "reason": str(reason or "conflict"),
            "payload": dict(payload or {}),
            "status": "open",
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
            "resolution": "",
        }
        conflicts.append(record)
        self._save_list("integration_conflicts", conflicts)
        self._append_audit(
            provider_key,
            "sync.conflict.enqueue",
            "degraded",
            {
                "resource": resource_key,
                "local_id": local_key,
                "remote_id": remote_key,
            },
        )
        return dict(record)



    def resolve_conflict(self, provider: str, conflict_id: int, resolution: str) -> dict[str, Any] | None:
        provider_key = self._normalize_key(provider)
        conflicts = self._load_list("integration_conflicts")
        now = datetime.now(timezone.utc).isoformat()
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            if self._normalize_key(item.get("provider")) != provider_key:
                continue
            if int(item.get("id") or 0) != int(conflict_id):
                continue
            item["status"] = "resolved"
            item["resolution"] = str(resolution or "manual").strip() or "manual"
            item["resolved_at"] = now
            item["updated_at"] = now
            self._save_list("integration_conflicts", conflicts)
            self._append_audit(
                provider_key,
                "sync.conflict.resolve",
                "ok",
                {
                    "conflict_id": int(conflict_id),
                    "resolution": item["resolution"],
                },
            )
            return dict(item)
        return None



    # ---------- sync timestamp ----------
    def get_latest_sync_time(self, provider: str) -> str | None:
        """Get latest sync time for a given provider."""






        provider_key = self._normalize_key(provider)
        state = self._load_map("integration_sync_cursors")
        provider_bucket = state.get(provider_key)
        if not isinstance(provider_bucket, dict):
            return None
        timestamps = []
        for resource_key, record in provider_bucket.items():
            if not isinstance(record, dict):
                continue
            updated_at = str(record.get("updated_at") or "").strip()
            if updated_at:
                timestamps.append(updated_at)
        if not timestamps:
            return None
        timestamps.sort(reverse=True)
        return timestamps[0]



    # ---------- audit ----------
    def list_recent_audit_logs(self, provider: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        provider_key = self._normalize_key(provider) if provider is not None else ""
        if self._writer is None:
            # 兜底：writer 不可用时退回旧读路径
            logs = self._load_list("integration_audit_logs")
            filtered = [dict(item) for item in logs if isinstance(item, dict)]
            if provider_key:
                filtered = [item for item in filtered if self._normalize_key(item.get("provider")) == provider_key]
            filtered.sort(key=lambda row: str(row.get("time") or ""), reverse=True)
            return filtered[: max(1, int(limit))]
        try:
            records = list(self._writer.fetch_integration(provider=provider_key, limit=max(1, int(limit)) * 2))
        except Exception:
            records = []
        # 合并尚未落盘的 pending 条目（刚写入即可见）
        for item in self._writer.pending_integration():
            if provider_key and self._normalize_key(item.get("provider")) != provider_key:
                continue
            records.append(item)
        records.sort(key=lambda row: str(row.get("time") or ""), reverse=True)
        return records[: max(1, int(limit))]



    def append_audit_event(self, provider: str, event: str, status: str, payload: dict[str, Any] | None = None) -> None:
        self._append_audit(provider, event, status, dict(payload or {}))



    # ---------- internal ----------
    def _append_audit(self, provider: str, event: str, status: str, payload: dict[str, Any]) -> None:
        record = {
            "provider": self._normalize_key(provider),
            "event": str(event or "").strip(),
            "status": str(status or "ok").strip(),
            "payload": dict(payload or {}),
            "time": datetime.now(timezone.utc).isoformat(),
        }
        # T1.1：统一走独立 SQLite 审计写入器（单写线程 + WAL），
        # 与 core/audit_rollback.py 共用同一 writer，消除共享 kv_store 键的锁竞争与整列表写放大。
        if self._writer is not None:
            self._writer.submit_integration(record["provider"], record["event"], record["status"], record["payload"])
        else:
            # 兜底：writer 初始化失败时退回旧路径（append_to_list 的 SQL 层原子追加）
            append = getattr(self.data_store, "append_to_list", None)
            if callable(append):
                append("integration_audit_logs", record, cap=self.MAX_AUDIT_LOGS)
            else:
                logs = self._load_list("integration_audit_logs")
                logs.append(record)
                if len(logs) > self.MAX_AUDIT_LOGS:
                    logs = logs[-self.MAX_AUDIT_LOGS :]
                self._save_list("integration_audit_logs", logs)

        severity = SEVERITY_DEGRADED if record["status"] != "ok" else SEVERITY_INFO
        self._track_event("integration.audit", severity, record)



    def _track_event(self, event_name: str, severity: str, payload: dict[str, Any]) -> None:
        service = self.observability_service
        if service is None:
            return
        try:
            service.track_event(event_name, severity=severity, payload=payload)
        except Exception:
            logger.warning("track_event failed", exc_info=True)
            return



    def _next_conflict_id(self, conflicts: list[dict[str, Any]]) -> int:
        max_id = 0
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            max_id = max(max_id, int(item.get("id") or 0))
        return max_id + 1



    def _load_map(self, key: str) -> dict[str, Any]:
        if self.data_store is None:
            return {}
        data = self.data_store.load_json(key, {})
        return data if isinstance(data, dict) else {}



    def _save_map(self, key: str, data: dict[str, Any]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json(key, data)



    def _load_list(self, key: str) -> list[dict[str, Any]]:
        if self.data_store is None:
            return []
        data = self.data_store.load_json(key, [])
        return data if isinstance(data, list) else []



    def _save_list(self, key: str, data: list[dict[str, Any]]) -> None:
        if self.data_store is None:
            return
        self.data_store.save_json(key, data)



    def _normalize_key(self, value: Any) -> str:
        return str(value or "").strip().lower()



    def _get_nested(self, data: dict[str, Any], first: str, second: str) -> dict[str, Any] | None:
        level1 = data.get(first)
        if not isinstance(level1, dict):
            return None
        level2 = level1.get(second)
        return dict(level2) if isinstance(level2, dict) else None

