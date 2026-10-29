"""automation-hub HTTP 客户端：泛型化实体同步 + 健康/身份探测 + Agent 代理接口。"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import requests

# T4.1(2026-08-23) 修复：常量真实定义在 routes.personal_factory，
# 原 import automation_hub.api.auth.deps 是错误路径（无此常量）→ 导入即崩，
# 使 8 个 app_context_builder 测试 setup error、并污染 coverage_gate 门禁。
try:
    from automation_hub.api.routes.personal_factory import (
        PERSONAL_MIRROR_SYNC_HEADER,
        PERSONAL_MIRROR_SYNC_VALUE,
    )
except Exception:  # pragma: no cover - hub 不在环境时降级（常量仅用于写入头）
    PERSONAL_MIRROR_SYNC_HEADER = "X-Siyi-Sync-Source"
    PERSONAL_MIRROR_SYNC_VALUE = "desktop"
from core.settings_keys import KEY_AUTOMATION_HUB_API_BASE, KEY_AUTOMATION_HUB_API_TOKEN

from .automation_hub_types import (
    AutomationHubAgentAskResponse,
    AutomationHubAgentPlanResponse,
    AutomationHubClientError,
    AutomationHubConnectionConfig,
    AutomationHubRequestSpec,
    SyncResponse,
    UpsertResponse,
)
from core.error_grading import (
    ANCHOR_SYNC_PULL_FAILED,
    ANCHOR_SYNC_PUSH_FAILED,
    sync_anchor,
)
from core.observability import SEVERITY_DEGRADED

logger = logging.getLogger("siyi.automation_hub")


# ── 实体端点注册表（单一定义源） ──────────────────────────────
# 仅登记 automation-hub `/personal/*` 实际暴露的实体。
# 说明：inbox / memories / settings 等实体在 hub 侧没有 `/personal/*` 端点，
# 它们走云同步链路（见 docs/automation-hub-disposition.md），不属于本客户端。
_ENTITY_ENDPOINTS: dict[str, tuple[str, str, str, str]] = {
    "note": ("notes", "notes", "note", "notes"),
    "task": ("tasks", "tasks", "task", "tasks"),
    "news": ("news", "items", "item", "news"),
    "translation": ("translations", "records", "record", "translation"),
    "wordbook": ("wordbooks", "wordbooks", "wordbook", "wordbook"),
    "word": ("words", "words", "word", "word"),
}


def _service_error_message(action: str, detail: str) -> str:
    return f"{action}失败: {detail}"


def _decrypt_if_secure(value: Any) -> Any:
    from core.secure_key_storage import SecureKeyStorage

    if not isinstance(value, str):
        return value
    secure = SecureKeyStorage(namespace="com.siyi.apikey")
    if secure.is_secure_token(value):
        return secure.decrypt(value)
    return value


def resolve_automation_hub_connection_config(settings_repository=None, environ=None):
    resolved_environ = environ or os.environ
    base_url = (
        resolved_environ.get("SIYI_AUTOMATION_HUB_API_BASE")
        or resolved_environ.get("AUTOMATION_HUB_API_BASE")
    )
    api_token = (
        resolved_environ.get("SIYI_AUTOMATION_HUB_API_TOKEN")
        or resolved_environ.get("AUTOMATION_HUB_API_TOKEN")
    )
    if settings_repository is not None:
        base_url = base_url or settings_repository.get_setting(KEY_AUTOMATION_HUB_API_BASE, "")
        api_token = api_token or settings_repository.get_setting(KEY_AUTOMATION_HUB_API_TOKEN, "")

    api_token = _decrypt_if_secure(api_token)

    normalized_base = base_url.strip().rstrip("/") if isinstance(base_url, str) and base_url.strip() else None
    normalized_token = api_token.strip() if isinstance(api_token, str) and api_token.strip() else None
    return AutomationHubConnectionConfig(base_url=normalized_base, api_token=normalized_token)


def create_automation_hub_client(settings_repository=None, base_url=None, api_token=None, session=None):
    config = resolve_automation_hub_connection_config(settings_repository=settings_repository)
    return AutomationHubClient(
        base_url=base_url or config.base_url,
        api_token=api_token or config.api_token,
        session=session,
    )


class AutomationHubClient:
    """Communicating with Automation Hub.

    对外提供两类能力：
    - 泛型实体同步：pull / pull_all / upsert / delete（对应 hub `/personal/*`）
    - 探测与代理：check_health / probe_connection / ask_agent / plan_agent
    """

    SUPPORTED_ENTITIES = frozenset(_ENTITY_ENDPOINTS)

    def __init__(self, base_url=None, api_token=None, session=None):
        self.base_url = base_url.strip().rstrip("/") if isinstance(base_url, str) and base_url.strip() else None
        self.api_token = api_token.strip() if isinstance(api_token, str) and api_token.strip() else None
        self.session = session or requests.Session()

    # ── 底层 HTTP ──

    def _require_base_url(self):
        if self.base_url:
            return
        raise AutomationHubClientError(
            _service_error_message(
                "连接 automation-hub",
                "请先设置 SIYI_AUTOMATION_HUB_API_BASE 或在 settings.json 中配置 automation_hub_api_base。",
            )
        )

    def _build_headers(self, require_token=False, extra_headers=None):
        headers = {}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        elif require_token:
            raise AutomationHubClientError(
                _service_error_message(
                    "验证 automation-hub 身份",
                    "当前未配置 API Token，请设置 SIYI_AUTOMATION_HUB_API_TOKEN 或在 settings.json 中配置 automation_hub_api_token。",
                )
            )
        if isinstance(extra_headers, dict):
            headers.update(extra_headers)
        return headers

    def _request_json(self, spec: AutomationHubRequestSpec, timeout=10):
        self._require_base_url()
        url = spec.resolve_url(self.base_url)
        headers = self._build_headers(require_token=spec.require_token, extra_headers=spec.extra_headers)
        try:
            response = self.session.request(
                spec.method, url, headers=headers, json=spec.json_body, timeout=timeout,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            logger.warning("automation-hub 请求超时: method=%s url=%s", spec.method, url)
            anchor = ANCHOR_SYNC_PUSH_FAILED if spec.method != "GET" else ANCHOR_SYNC_PULL_FAILED
            sync_anchor(SEVERITY_DEGRADED, anchor, "request timeout", method=spec.method, url=url)
            raise AutomationHubClientError(_service_error_message("连接 automation-hub", "请求超时")) from exc
        except requests.exceptions.RequestException as exc:
            logger.warning("automation-hub 请求失败: method=%s url=%s error=%s", spec.method, url, exc)
            anchor = ANCHOR_SYNC_PUSH_FAILED if spec.method != "GET" else ANCHOR_SYNC_PULL_FAILED
            sync_anchor(SEVERITY_DEGRADED, anchor, "request failed", method=spec.method, url=url)
            raise AutomationHubClientError(_service_error_message("连接 automation-hub", str(exc))) from exc
        try:
            return response.json()
        except ValueError as exc:
            logger.warning("automation-hub 返回无效 JSON: method=%s url=%s", spec.method, url)
            raise AutomationHubClientError(_service_error_message("连接 automation-hub", "服务返回了无效响应")) from exc

    # ── 注册表 / 请求构建 ──

    @staticmethod
    def _resolve_endpoint(entity: str) -> tuple[str, str, str, str]:
        resolved = _ENTITY_ENDPOINTS.get(entity)
        if resolved is None:
            supported = ", ".join(sorted(_ENTITY_ENDPOINTS))
            raise AutomationHubClientError(
                _service_error_message(
                    "同步",
                    f"不支持的实体 {entity!r}，支持: {supported}",
                )
            )
        return resolved

    def _prepare_pull(self, entity: str, since_updated_at=None, limit=100, cursor=None):
        endpoint, _, _, _ = self._resolve_endpoint(entity)
        params = {"limit": max(int(limit), 1)}
        if since_updated_at:
            params["since_updated_at"] = since_updated_at
        if cursor:
            params["cursor"] = cursor
        return AutomationHubRequestSpec(
            method="GET", path=f"/personal/{endpoint}?{urlencode(params)}", require_token=True,
        )

    def _prepare_upsert(self, entity: str, payload: dict):
        endpoint, _, _, label = self._resolve_endpoint(entity)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise AutomationHubClientError(_service_error_message(f"构建 {label} 同步请求", "缺少有效的本地 payload"))
        remote_id = payload.get("remote_id")
        # T1.6：标识为桌面同步来源，满足 hub personal 镜像锁的来源守卫。
        sync_headers = {PERSONAL_MIRROR_SYNC_HEADER: PERSONAL_MIRROR_SYNC_VALUE}
        if remote_id:
            return AutomationHubRequestSpec(
                method="PUT", path=f"/personal/{endpoint}/{remote_id}", require_token=True,
                json_body=dict(payload), extra_headers=sync_headers,
            )
        return AutomationHubRequestSpec(
            method="POST", path=f"/personal/{endpoint}", require_token=True,
            json_body=dict(payload), extra_headers=sync_headers,
        )

    def _prepare_delete(self, entity: str, payload: dict):
        endpoint, _, _, label = self._resolve_endpoint(entity)
        remote_id = payload.get("remote_id") if isinstance(payload, dict) else None
        if not remote_id:
            raise AutomationHubClientError(_service_error_message(f"构建 {label} 删除请求", "当前记录还没有 remote_id"))
        return AutomationHubRequestSpec(
            method="DELETE", path=f"/personal/{endpoint}/{remote_id}", require_token=True,
            extra_headers={PERSONAL_MIRROR_SYNC_HEADER: PERSONAL_MIRROR_SYNC_VALUE},
        )

    # ── 泛型解析 ──

    @staticmethod
    def _parse_sync_response(payload: dict, list_field: str) -> SyncResponse[dict]:
        if not isinstance(payload, dict):
            raise AutomationHubClientError(_service_error_message("解析同步响应", "响应不是有效对象"))
        raw = payload.get(list_field)
        if raw is None or not isinstance(raw, list):
            raise AutomationHubClientError(_service_error_message("解析同步响应", f"缺少 {list_field} 字段"))
        return SyncResponse(items=[dict(item) for item in raw if isinstance(item, dict)], next_cursor=payload.get("next_cursor"))

    @staticmethod
    def _parse_upsert_response(payload: dict, item_field: str, action: str = "写入") -> UpsertResponse[dict]:
        if not isinstance(payload, dict):
            raise AutomationHubClientError(_service_error_message(f"解析 {action}响应", "响应不是有效对象"))
        raw = payload.get(item_field)
        if not isinstance(raw, dict):
            if action == "删除":
                return UpsertResponse(item={"remote_id": payload.get("remote_id", "")})
            raise AutomationHubClientError(_service_error_message(f"解析 {action}响应", f"缺少 {item_field} 字段"))
        return UpsertResponse(item=dict(raw))

    # ── 健康检查与探测 ──

    def check_health(self):
        return self._request_json(AutomationHubRequestSpec(method="GET", path="/health", require_token=False))

    def get_current_identity(self):
        return self._request_json(AutomationHubRequestSpec(method="GET", path="/auth/me", require_token=True))

    def probe_connection(self):
        health = self.check_health()
        identity = None
        token_status = "未配置 token，仅验证 health"
        if self.api_token:
            identity = self.get_current_identity()
            token_status = "已验证 /auth/me"
        return {"base_url": self.base_url, "health": health, "identity": identity, "token_status": token_status}

    # ── 泛型实体操作（公开接口） ──

    def pull(self, entity: str, since_updated_at=None, limit=100, cursor=None):
        """拉取单页实体。entity ∈ SUPPORTED_ENTITIES。"""
        _, list_field, _, _ = self._resolve_endpoint(entity)
        spec = self._prepare_pull(entity, since_updated_at=since_updated_at, limit=limit, cursor=cursor)
        return self._parse_sync_response(self._request_json(spec), list_field)

    def pull_all(self, entity: str, since_updated_at=None, limit=100):
        """分页拉取全部实体。"""
        _, list_field, _, _ = self._resolve_endpoint(entity)
        collected = []
        cursor = None
        while True:
            resp = self.pull(entity, since_updated_at=since_updated_at, limit=limit, cursor=cursor)
            collected.extend(resp.items)
            if not resp.next_cursor:
                break
            cursor = resp.next_cursor
        return SyncResponse(items=collected, next_cursor=None)

    def upsert(self, entity: str, payload: dict):
        _, _, item_field, _ = self._resolve_endpoint(entity)
        spec = self._prepare_upsert(entity, payload)
        return self._parse_upsert_response(self._request_json(spec), item_field)

    def delete(self, entity: str, payload: dict):
        _, _, item_field, _ = self._resolve_endpoint(entity)
        spec = self._prepare_delete(entity, payload)
        return self._parse_upsert_response(self._request_json(spec), item_field, "删除")

    # ── 兼容别名（泛型方法公开化前的私有名，避免破坏既有调用） ──

    def _pull(self, entity, *args, **kwargs):
        return self.pull(entity, *args, **kwargs)

    def _pull_all(self, entity, *args, **kwargs):
        return self.pull_all(entity, *args, **kwargs)

    def _upsert(self, entity, *args, **kwargs):
        return self.upsert(entity, *args, **kwargs)

    def _delete(self, entity, *args, **kwargs):
        return self.delete(entity, *args, **kwargs)

    def read(self, method: str, path: str, json_body=None, timeout=10, require_token=True):
        """底层原始请求封装，供 bridge 等模块复用同一 HTTP/session/token 逻辑。"""
        return self._request_json(
            AutomationHubRequestSpec(method=method, path=path, require_token=require_token, json_body=json_body),
            timeout=timeout,
        )

    # ── Agent（桌面端 → hub 侧执行）代理接口 ──

    def prepare_agent_ask_request(self, query: str, session_id: str | None = None, context: dict | None = None, repo_context: dict | None = None):
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise AutomationHubClientError(_service_error_message("构建 agent ask 请求", "query 不能为空"))
        body: dict[str, Any] = {"query": normalized_query}
        if isinstance(session_id, str) and session_id.strip():
            body["session_id"] = session_id.strip()
        if isinstance(context, dict) and context:
            body["context"] = dict(context)
        if isinstance(repo_context, dict) and repo_context:
            body["repo_context"] = dict(repo_context)
        return AutomationHubRequestSpec(method="POST", path="/agent/ask", require_token=True, json_body=body)

    def prepare_agent_plan_request(self, query: str, session_id: str | None = None, context: dict | None = None, repo_context: dict | None = None):
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise AutomationHubClientError(_service_error_message("构建 agent plan 请求", "query 不能为空"))
        body: dict[str, Any] = {"query": normalized_query}
        if isinstance(session_id, str) and session_id.strip():
            body["session_id"] = session_id.strip()
        if isinstance(context, dict) and context:
            body["context"] = dict(context)
        if isinstance(repo_context, dict) and repo_context:
            body["repo_context"] = dict(repo_context)
        return AutomationHubRequestSpec(method="POST", path="/agent/plan", require_token=True, json_body=body)

    def parse_agent_ask_response(self, payload: dict):
        if not isinstance(payload, dict):
            raise AutomationHubClientError(_service_error_message("解析 agent ask 响应", "响应不是有效对象"))
        summary = str(payload.get("summary") or "").strip()
        plan_id = str(payload.get("plan_id") or "").strip()
        status = str(payload.get("status") or "").strip() or "unknown"
        session_id = str(payload.get("session_id") or "").strip()
        if not summary:
            raise AutomationHubClientError(_service_error_message("解析 agent ask 响应", "缺少 summary 字段"))
        if not plan_id:
            raise AutomationHubClientError(_service_error_message("解析 agent ask 响应", "缺少 plan_id 字段"))
        pending_approvals = payload.get("pending_approvals")
        if not isinstance(pending_approvals, list):
            pending_approvals = []
        return AutomationHubAgentAskResponse(
            plan_id=plan_id, summary=summary, status=status, steps_completed=int(payload.get("steps_completed") or 0),
            total_steps=int(payload.get("total_steps") or 0), session_id=session_id,
            execution_time=float(payload.get("execution_time") or 0),
            pending_approvals=[str(item) for item in pending_approvals if isinstance(item, (str, int, float))],
        )

    def parse_agent_plan_response(self, payload: dict):
        if not isinstance(payload, dict):
            raise AutomationHubClientError(_service_error_message("解析 agent plan 响应", "响应不是有效对象"))
        plan_id = str(payload.get("plan_id") or "").strip()
        task_type = str(payload.get("task_type") or "").strip() or "unknown"
        if not plan_id:
            raise AutomationHubClientError(_service_error_message("解析 agent plan 响应", "缺少 plan_id 字段"))
        steps = payload.get("steps")
        if not isinstance(steps, list):
            raise AutomationHubClientError(_service_error_message("解析 agent plan 响应", "steps 字段格式无效"))
        return AutomationHubAgentPlanResponse(
            plan_id=plan_id, task_type=task_type, steps=[dict(s) for s in steps if isinstance(s, dict)],
            estimated_duration=int(payload.get("estimated_duration") or 0),
        )

    def ask_agent(self, query: str, session_id: str | None = None, context: dict | None = None, repo_context: dict | None = None):
        spec = self.prepare_agent_ask_request(query=query, session_id=session_id, context=context, repo_context=repo_context)
        return self.parse_agent_ask_response(self._request_json(spec, timeout=45))

    def plan_agent(self, query: str, session_id: str | None = None, context: dict | None = None, repo_context: dict | None = None):
        spec = self.prepare_agent_plan_request(query=query, session_id=session_id, context=context, repo_context=repo_context)
        return self.parse_agent_plan_response(self._request_json(spec, timeout=30))