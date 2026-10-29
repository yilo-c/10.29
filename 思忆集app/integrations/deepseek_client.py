# -*- coding: utf-8 -*-
"""DeepSeek 网页账户抓取客户端 — 直接调用 chat.deepseek.com 内部 API。

用途：不依赖浏览器扩展，用账户 token（浏览器 chat.deepseek.com 的 Authorization
JWT）拉取账户下的全部会话与消息，归一化为思忆集对话结构后交由导入链路入库。

端到端调用（会话/历史接口随网页前端可能调整，见模块内 __ENDPOINTS）：
    GET /api/v0/chat_session/fetch_page?count=N
        → data.biz_data.chat_sessions[{id, title, updated_at}]
    GET /api/v0/chat/history_messages?chat_session_id={id}
        → data.biz_data.messages[{content, role}]
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger("siyi.deepseek.client")

_BASE_URL = "https://chat.deepseek.com"
_SESSION_TIMEOUT_SECONDS = 15

# 会话/消息接口在不同客户端（web / app）略有差异，做多套回退
_SESSION_ENDPOINTS = (
    "/api/v0/chat_session/fetch_page?count={count}",
    "/api/v0/chat_session/fetch_page?lte_cursor.pinned=false&count={count}",
    "/api/v0/chat_session/list?count={count}",
)
_HISTORY_ENDPOINTS = (
    "/api/v0/chat/history_messages?chat_session_id={sid}",
    "/api/v0/chat/history?chat_session_id={sid}",
)

# 网页端常见请求头（后端请求不强依赖，但保留以提升兼容性）
_WEB_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9",
    "content-type": "application/json",
    "x-app-version": "20241129.1",
    "x-client-locale": "zh_CN",
    "x-client-platform": "web",
    "x-client-version": "1.0.0-always",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"),
}


def decode_token_claims(token: str) -> dict[str, Any]:
    """解码 JWT 的 payload（不校验签名，仅用于展示过期时间/账户信息）。失败返回空。"""
    try:
        parts = str(token or "").strip().split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        # 补齐 base64 padding
        payload += "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


class DeepSeekAccountClient:
    """DeepSeek 账户聊天抓取客户端。

    Args:
        token: chat.deepseek.com 登录态 token（Authorization: Bearer <token>）。
        base_url: 服务地址，默认官方网页。
    """

    def __init__(self, token: str, base_url: str = _BASE_URL):
        self.token = str(token or "").strip()
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(_WEB_HEADERS)
        self._session.headers["authorization"] = f"Bearer {self.token}"
        self._session.headers["referer"] = f"{self.base_url}/"
        self._ready = bool(self.token)

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def token_expires_at(self) -> str:
        """解析 token 过期时间（无则为空）。"""
        claims = decode_token_claims(self.token)
        exp = claims.get("exp")
        if not exp:
            return ""
        try:
            return datetime.fromtimestamp(int(exp)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:  # noqa: BLE001
            return ""

    def _request_json(self, path: str, *, headers: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        req_headers = dict(self._session.headers)
        if headers:
            req_headers.update(headers)
        resp = self._session.get(url, headers=req_headers, timeout=_SESSION_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek 接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        # 业务码 0 视为成功；非 0 视为业务错误
        code = data.get("code")
        if code not in (0, None):
            raise RuntimeError(f"DeepSeek 接口业务错误 code={code}: {data.get('msg') or data.get('message') or ''}")
        return data

    # 网页 API 单次 count 的安全上限（2026-10-15 实测：count=50 正常返回 JSON；
    # count=200/500 服务端返回非 JSON（风控/错误页）→ JSONDecodeError。
    # 旧默认 500 意味着**每次调用都失败**，同步功能实际坏死。
    _SAFE_SESSION_COUNT = 50

    def list_sessions(self, count: int = 50) -> list[dict[str, Any]]:
        """拉取账户会话列表，返回 [{id, title, updated_at, created_at?}]。多端接口回退。

        2026-10-15 修复：count 夹到安全上限（``_SAFE_SESSION_COUNT``）——旧默认
        500 与服务端限流冲突导致恒失败。需要全量列表应实现游标分页（登记为后续，
        当前 backfill 场景按已知 session_id 直取消息，不依赖全量列表）。
        """
        if not self._ready:
            raise RuntimeError("DeepSeek token 未配置")
        safe_count = max(1, min(int(count), self._SAFE_SESSION_COUNT))
        last_error: Exception | None = None
        for endpoint in _SESSION_ENDPOINTS:
            try:
                path = endpoint.format(count=safe_count)
                data = self._request_json(path)
                biz = data.get("data", {}).get("biz_data", {})
                sessions = biz.get("chat_sessions") or biz.get("chat_session_list") or biz.get("sessions") or []
                if not isinstance(sessions, list):
                    sessions = []
                return [self._normalize_session(s) for s in sessions if isinstance(s, dict)]
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error is not None:
            raise last_error
        return []

    @staticmethod
    def _normalize_session(src: dict[str, Any]) -> dict[str, Any]:
        sid = src.get("id") or src.get("chat_session_id") or src.get("session_id") or src.get("uuid")
        return {
            "id": str(sid or ""),
            "title": str(src.get("title") or src.get("name") or "未命名会话"),
            "updated_at": src.get("updated_at") or src.get("created_at") or "",
            "_raw": src,
        }

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """拉取单会话消息，返回 [{role, content}]。多端接口回退。"""
        if not self._ready:
            raise RuntimeError("DeepSeek token 未配置")
        if not session_id:
            return []
        encoded = urllib.parse.quote(str(session_id), safe="")
        last_error: Exception | None = None
        for endpoint in _HISTORY_ENDPOINTS:
            try:
                path = endpoint.format(sid=encoded)
                data = self._request_json(path, headers={"referer": f"{self.base_url}/a/chat/s/{session_id}"})
                biz = data.get("data", {}).get("biz_data", {})
                messages = biz.get("messages") or biz.get("chat_messages") or biz.get("message_list") or []
                if not isinstance(messages, list):
                    messages = []
                result: list[dict[str, Any]] = []
                for msg in messages:
                    content = self._extract_content(msg)
                    role = self._extract_role(msg)
                    if content:
                        result.append({"role": role, "content": content})
                if result:
                    return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        if last_error is not None:
            raise last_error
        return []

    @staticmethod
    def _extract_role(msg: dict[str, Any]) -> str:
        """消息角色归一为小写 user/assistant/system。

        2026-10-15：网页 API 返回大写 ``USER`` / ``ASSISTANT``，旧实现直接透传
        → 下游按 "user" 判等的分支全不命中（导入的消息 role 全变 "USER"）。
        """
        role = str(msg.get("role") or "").strip().lower()
        if role in ("user", "assistant", "system"):
            return role
        if role:
            return role
        # 网页端 worker_msg 等带 user 前缀，按是否存在 user 相关标志识别
        text = json.dumps(msg, ensure_ascii=False)
        return "user" if ('"user"' in text or "我是用户" in text or msg.get("is_user")) else "assistant"

    @staticmethod
    def _extract_content(msg: dict[str, Any]) -> str:
        """从消息节点取出文本内容。

        2026-10-15（网页 API 结构变更实锤）：消息正文从顶层 ``content`` 改为
        ``fragments[] = [{type: REQUEST|THINK|RESPONSE|…, content: str}]``。
        旧实现只找 content/text → **永远返回空**，导致 fetch_conversations 抓到
        的会话没有任何消息正文（同步功能实际坏死，且图谱 deepseek 出处无法反查）。

        取文优先级：
        1. fragments 中正文类型（REQUEST/RESPONSE/ANSWER/TEXT）拼接；
        2. 若无正文类型，取非 THINK 的 fragment（不把推理过程当正文）；
        3. 全为 THINK 时返回空（宁缺毋滥，思考过程不是正文）；
        4. 旧结构兜底：content / text / content.text / 富文本块列表。
        """
        frags = msg.get("fragments")
        if isinstance(frags, list) and frags:
            primary: list[str] = []
            others: list[str] = []
            for frag in frags:
                if not isinstance(frag, dict):
                    continue
                text = str(frag.get("content") or "").strip()
                if not text:
                    continue
                ftype = str(frag.get("type") or "").strip().upper()
                if ftype in ("REQUEST", "RESPONSE", "ANSWER", "TEXT"):
                    primary.append(text)
                elif ftype != "THINK":
                    others.append(text)
            joined = "\n".join(primary or others).strip()
            if joined:
                return joined

        content = msg.get("content")
        if isinstance(content, dict):
            # 部分接口用 {text: ...} 包一层
            content = content.get("text") or content.get("content") or ""
        if isinstance(content, list):
            # 富文本块：取 text 类型块拼接
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btext = block.get("text") or block.get("content") or ""
                    parts.append(str(btext))
                elif isinstance(block, str):
                    parts.append(block)
            content = "".join(parts)
        text = str(content or msg.get("text") or "").strip()
        return text

    def fetch_conversations(
        self,
        max_sessions: int = 200,
        *,
        since_hours: float | None = None,
        progress: Any = None,
    ) -> list[dict[str, Any]]:
        """抓取账户会话并归一会话结构 [{id,title,timestamp,source,messages:[{role,content}]}]。

        Args:
            max_sessions: 最多抓取的会话数。
            since_hours: 仅抓取最近 N 小时内更新的会话；为空则抓全部。
            progress: 可选回调 progress(done, total) 用于展示进度。
        """
        sessions = self.list_sessions(count=max_sessions)
        # 按更新时间倒序
        sessions.sort(key=lambda s: str(s.get("updated_at") or ""), reverse=True)

        now_epoch = time.time()
        since_epoch = now_epoch - (since_hours * 3600) if since_hours else None

        conversations: list[dict[str, Any]] = []
        total = min(len(sessions), max_sessions)
        for index, session in enumerate(sessions):
            if index >= max_sessions:
                break
            updated = session.get("updated_at")
            if since_epoch is not None and updated:
                try:
                    updated_epoch = float(updated)
                    if updated_epoch < since_epoch:
                        continue
                except (TypeError, ValueError):
                    pass
            messages = self.get_messages(session["id"])
            if not messages:
                continue
            conversations.append(
                {
                    "id": session["id"],
                    "title": session["title"],
                    "timestamp": self._to_iso_timestamp(updated),
                    "source": "deepseek",
                    "messages": messages,
                }
            )
            if progress is not None:
                try:
                    progress(index + 1, total)
                except Exception:  # noqa: BLE001
                    pass
            # 轻微限速，避免触发风控
            time.sleep(0.15)
        return conversations

    @staticmethod
    def _to_iso_timestamp(value: Any) -> str:
        if not value:
            return datetime.now().astimezone().isoformat()
        try:
            return datetime.fromtimestamp(float(value)).astimezone().isoformat()
        except (TypeError, ValueError):
            return str(value)
