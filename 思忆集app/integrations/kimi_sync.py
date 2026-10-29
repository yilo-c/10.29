"""Kimi 对话自动同步 — 从 Kimi 网页端私有 API 拉取对话并导入思忆集。

本模块封装 Kimi 的 Connect/gRPC-Web 协议调用，复用 integrations.chat_importer
的解析与导入管道，实现原始会话保存 + 记忆提取。

用法：
    client = KimiConnectClient(settings.get("kimi_config"))
    service = KimiSyncService(client, context)
    result = await service.sync_once(max_chats=50)
"""

from __future__ import annotations

import base64
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiohttp

logger = logging.getLogger("siyi.kimi_sync")

# Kimi 公开 OAuth 客户端 ID（Bearer 刷新流程使用）
_KIMI_OAUTH_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
# Kimi web 端 access_token 刷新端点（GET + Bearer refresh_token）
_KIMI_DEFAULT_REFRESH_ENDPOINT = "https://kimi.moonshot.cn/api/auth/token/refresh"
# 距离过期提前刷新的余量（秒）
_KIMI_REFRESH_EARLY_SECONDS = 300


class KimiConnectClient:
    """Kimi Connect API 客户端。

    config 示例：
        {
            "base_url": "https://www.kimi.com",
            "auth_token": "Bearer eyJ...",
            "refresh_token": "...",
            "client_id": "...",
            "refresh_endpoint": "https://auth.kimi.com/api/oauth/token",
            "device_id": "7620892439905050119",
            "session_id": "1730316841807735908",
            "platform": "web",
            "version": "1.0.0",
            "language": "zh-CN",
            "traffic_id": "...",
        }

    当提供了 refresh_token 时，客户端会在 token 接近过期或请求返回 401 时
    自动调用 refresh() 换取新 access_token，并通过 persist 回调把新 token
    写回设置存储。
    """

    PersistCallback = Callable[[str, str | None], Awaitable[None]]

    def __init__(
        self,
        config: dict[str, Any],
        persist: PersistCallback | None = None,
    ):
        self.base_url = (config.get("base_url") or "https://www.kimi.com").rstrip("/")
        self.auth_token = config.get("auth_token", "")
        self.refresh_token = config.get("refresh_token", "")
        self.client_id = config.get("client_id", _KIMI_OAUTH_CLIENT_ID)
        self.refresh_endpoint = config.get("refresh_endpoint", _KIMI_DEFAULT_REFRESH_ENDPOINT)
        self.persist = persist
        self.device_id = config.get("device_id", "")
        self.session_id = config.get("session_id", "")
        self.platform = config.get("platform", "web")
        self.version = config.get("version", "1.0.0")
        self.language = config.get("language", "zh-CN")
        self.traffic_id = config.get("traffic_id", "")

    @staticmethod
    def _decode_jwt_exp(token: str) -> float | None:
        """解析 JWT 的 exp 字段（秒级 Unix 时间戳）；无法解析时返回 None。"""
        payload = token
        if payload.lower().startswith("bearer "):
            payload = payload[7:]
        try:
            # JWT = header.payload.signature，取中间段
            part = payload.split(".")[1] if "." in payload else payload
            pad = "=" * (-len(part) % 4)
            data = json.loads(base64.urlsafe_b64decode(part + pad))
            return float(data["exp"])
        except Exception:
            return None

    def _is_token_expired(self, margin: int = _KIMI_REFRESH_EARLY_SECONDS) -> bool:
        """判断 auth_token 是否已（或将）过期。无法判断过期时保守返回 False。"""
        exp = self._decode_jwt_exp(self.auth_token)
        if exp is None:
            return False
        return exp - margin <= time.time()

    async def refresh(self) -> bool:
        """用 refresh_token 换取新 access_token，并写回设置。成功返回 True。

        Kimi web 端刷新协议：GET 刷新端点，Authorization 头携带
        "Bearer <refresh_token>"，返回 {access_token, refresh_token}。
        """
        if not self.refresh_token:
            logger.warning("Kimi refresh_token 未配置，无法自动刷新")
            return False
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "authorization": f"Bearer {self.refresh_token}",
            "content-type": "application/json",
            "origin": "https://kimi.moonshot.cn",
            "referer": "https://kimi.moonshot.cn/",
            "r-timezone": "Asia/Shanghai",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.refresh_endpoint,
                headers=headers,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Kimi token 刷新失败 {resp.status}: {body[:500]}")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    raise RuntimeError(f"Kimi token 刷新响应非 JSON: {body[:200]}") from None

        new_access = data.get("access_token")
        if not new_access:
            raise RuntimeError("Kimi 刷新响应缺少 access_token")
        self.auth_token = new_access
        new_refresh = data.get("refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh

        if self.persist is not None:
            try:
                await self.persist(new_access, new_refresh)
            except Exception as exc:
                logger.warning("Kimi token 刷新后写回设置失败: %s", exc)
        logger.info("Kimi access_token 已自动刷新")
        return True

    async def _ensure_fresh_token(self) -> bool:
        """请求前确保 token 新鲜；发生过刷新则返回 True。"""
        if not self.refresh_token or not self._is_token_expired():
            return False
        try:
            return await self.refresh()
        except Exception as exc:
            logger.warning("Kimi token 自动刷新失败（继续尝试原 token）: %s", exc)
            return False

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": content_type,
            "origin": self.base_url,
            "referer": f"{self.base_url}/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-language": self.language,
            "x-msh-platform": self.platform,
            "x-msh-version": self.version,
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        }
        if content_type == "application/connect+json":
            headers["connect-protocol-version"] = "1"
        if self.auth_token:
            headers["authorization"] = self.auth_token if self.auth_token.startswith("Bearer ") else f"Bearer {self.auth_token}"
        if self.device_id:
            headers["x-msh-device-id"] = self.device_id
        if self.session_id:
            headers["x-msh-session-id"] = self.session_id
        if self.traffic_id:
            headers["x-traffic-id"] = self.traffic_id
        return headers

    @staticmethod
    def _encode_connect_payload(payload: dict[str, Any]) -> bytes:
        """把 JSON payload 编码为 Connect 请求帧（服务端流式 RPC 使用）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return b"\x00" + len(data).to_bytes(4, "big") + data

    @staticmethod
    def _decode_connect_stream(body: bytes) -> list[dict[str, Any]]:
        """解码 Connect 流式响应，返回事件对象列表。"""
        events = []
        i = 0
        while i < len(body):
            if body[i] not in (0, 1):
                break
            if i + 5 > len(body):
                break
            length = int.from_bytes(body[i + 1 : i + 5], "big")
            if i + 5 + length > len(body):
                break
            payload = body[i + 5 : i + 5 + length]
            try:
                events.append(json.loads(payload))
            except Exception:
                pass
            i += 5 + length

        if events:
            return events

        text = body.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            cleaned = line.lstrip("\x00\x01\x02\x03")
            try:
                events.append(json.loads(cleaned))
            except Exception:
                pass
        return events

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        use_connect: bool = False,
        retry_on_401: bool = True,
    ) -> list[dict[str, Any]]:
        """发送请求并返回解码后的事件列表。

        - 一元列表接口（ListChats）用 application/json + 纯 JSON 体；
        - 服务端流式接口（Chat）用 application/connect+json + 分帧体；
        - 请求前若 token 已临期则自动刷新；
        - 请求返回 401 且持有 refresh_token 时，先刷新一次再重试。
        """
        await self._ensure_fresh_token()
        url = f"{self.base_url}{path}"
        if use_connect:
            headers = self._headers("application/connect+json")
            body = self._encode_connect_payload(payload)
        else:
            headers = self._headers("application/json")
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as resp:
                resp_body = await resp.read()
                if resp.status == 401 and retry_on_401 and self.refresh_token:
                    try:
                        refreshed = await self.refresh()
                    except Exception as exc:
                        logger.warning("Kimi 401 后刷新失败: %s", exc)
                        refreshed = False
                    if refreshed:
                        return await self._post(path, payload, use_connect=use_connect, retry_on_401=False)
                    text = resp_body.decode("utf-8", errors="ignore")[:500]
                    raise RuntimeError(f"Kimi API 鉴权失败 {resp.status}: {text}")
                if resp.status != 200:
                    text = resp_body.decode("utf-8", errors="ignore")[:500]
                    raise RuntimeError(f"Kimi API 错误 {resp.status}: {text}")
                return self._decode_connect_stream(resp_body)

    async def list_chats(
        self,
        endpoint: str = "/apiv2/kimi.gateway.chat.v1.ChatService/ListChats",
        payload: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """拉取对话列表。

        由于 Kimi list 接口结构待确认，endpoint 和 payload 可配置。
        默认假设返回 {chats: [{id, name, createTime}, ...]} 或 {items: [...]}。
        """
        events = await self._post(endpoint, payload or {})

        if not events:
            return []
        data = events[0]
        chats = []
        for key in ("chats", "items", "conversations", "list", "data"):
            val = data.get(key)
            if isinstance(val, list):
                chats = [c for c in val if isinstance(c, dict)]
                break
            if isinstance(val, dict):
                nested = val.get("items") or val.get("chats") or val.get("list")
                if isinstance(nested, list):
                    chats = [c for c in nested if isinstance(c, dict)]
                    break

        if not chats and isinstance(data, dict):
            msgs = data.get("messages")
            if isinstance(msgs, list):
                chats = [data]
        return chats

    async def get_chat_messages(
        self,
        chat_id: str,
        endpoint: str = "/apiv2/kimi.gateway.chat.v1.ChatService/ListMessages",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """拉取单条对话的完整消息，并构建统一对话结构。

        Kimi 拉取历史消息使用一元 POST 接口 ListMessages，返回纯 JSON：
            {messages: [{id, role, blocks: [{text: {content}}]}, ...]}

        注意：不要用 ChatService/Chat（那是生成回复的流式接口，拉历史消息会
        报 invalid_argument）。本方法把返回的 messages 聚合为统一对话 dict。
        """
        req_payload = {"chat_id": chat_id, "page_size": 1000}
        if payload:
            req_payload.update(payload)
        # 一元接口用 application/json + 纯 JSON 体（非 connect 分帧）
        response = await self._post(endpoint, req_payload)

        from integrations.chat_importer import _extract_kimi_message_text

        raw_messages: list[dict[str, Any]] = []
        for ev in response:
            if not isinstance(ev, dict):
                continue
            msgs = ev.get("messages")
            if isinstance(msgs, list):
                raw_messages.extend(m for m in msgs if isinstance(m, dict))
            chat = ev.get("chat")
            if isinstance(chat, dict) and isinstance(chat.get("messages"), list):
                raw_messages.extend(m for m in chat["messages"] if isinstance(m, dict))

        messages: list[dict[str, str]] = []
        for m in raw_messages:
            role = str(m.get("role", "")).lower()
            if role not in ("user", "assistant"):
                continue
            content = _extract_kimi_message_text(m)
            if not content:
                content = str(m.get("content") or "").strip()
            if not content:
                continue
            messages.append({"role": role, "content": content})

        return {"source": "kimi", "title": "Kimi 对话", "timestamp": 0, "messages": messages}


class KimiSyncService:
    """Kimi 同步服务：把拉取到的对话导入思忆集。"""

    def __init__(self, client: KimiConnectClient, context: Any):
        self.client = client
        self.context = context

    async def sync_once(self, max_chats: int = 50) -> dict[str, Any]:
        """执行一次全量同步。"""
        ai = getattr(self.context, "ai_client", None)
        mem_extractor = getattr(self.context, "memory_extractor", None)
        chat_repo = getattr(self.context, "chat_session_repository", None)
        mem_index = getattr(self.context, "memory_index_service", None)
        kg_extractor = getattr(self.context, "knowledge_graph_extractor", None)

        chats = await self.client.list_chats()

        stats = {
            "success": True,
            "total": 0,
            "imported": 0,
            "skipped": 0,
            "sessions_saved": 0,
            "memories_stored": 0,
            "graph_entities_added": 0,
            "graph_relations_added": 0,
            "errors": [],
        }

        for chat in chats[:max_chats]:
            chat_id = str(chat.get("id") or chat.get("chatId") or "")
            if not chat_id:
                stats["skipped"] += 1
                continue
            try:
                conv = await self.client.get_chat_messages(chat_id)
                if not conv.get("messages"):
                    conv["title"] = chat.get("name") or chat.get("title") or conv.get("title") or "Kimi 对话"
                    stats["skipped"] += 1
                    continue
                conv["title"] = chat.get("name") or chat.get("title") or conv.get("title") or "Kimi 对话"
                ts = chat.get("createTime") or chat.get("create_time") or chat.get("timestamp")
                if ts:
                    conv["timestamp"] = ts

                result = self._import_conversation_in_memory(conv, ai, mem_extractor, chat_repo, mem_index, kg_extractor)
                stats["imported"] += result.get("imported", 0)
                stats["sessions_saved"] += result.get("sessions_saved", 0)
                stats["memories_stored"] += result.get("memories_stored", 0)
                stats["graph_entities_added"] += result.get("graph_entities_added", 0)
                stats["graph_relations_added"] += result.get("graph_relations_added", 0)
            except Exception as exc:
                logger.warning("同步 Kimi 对话 %s 失败: %s", chat_id, exc)
                stats["errors"].append({"chat_id": chat_id, "error": str(exc)})
            stats["total"] += 1

        return stats

    @staticmethod
    def _import_conversation_in_memory(
        conv: dict[str, Any],
        ai_client: Any,
        memory_extractor: Any,
        chat_repo: Any,
        memory_index_service: Any,
        knowledge_graph_extractor: Any = None,
    ) -> dict[str, Any]:
        """把一条内存中的对话导入思忆集（与 import_chat_file 的核心逻辑等价）。"""
        stats = {
            "imported": 0,
            "skipped": 0,
            "sessions_saved": 0,
            "memories_stored": 0,
            # 图谱桥接：本次同步新增的图谱实体/关系数
            "graph_entities_added": 0,
            "graph_relations_added": 0,
        }
        if not conv.get("messages"):
            stats["skipped"] = 1
            return stats

        all_text = " ".join(m["content"] for m in conv["messages"] if m.get("content"))[:2000]

        session_id = None
        if chat_repo is not None:
            try:
                title = conv.get("title") or "Kimi 对话"
                session_id = chat_repo.create_session(title)
                repo_messages = [
                    {
                        "speaker": "你" if m.get("role") == "user" else "AI",
                        "message": str(m.get("content", "")),
                        "timestamp": conv.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    }
                    for m in conv["messages"]
                    if m.get("content")
                ]
                chat_repo.sync_messages(session_id, repo_messages)
                stats["sessions_saved"] = 1
            except Exception as exc:
                logger.warning("保存 Kimi 会话失败 '%s': %s", conv.get("title", ""), exc)

        if memory_extractor is not None:
            try:
                conversation_id = str(session_id) if session_id is not None else f"kimi_sync_{conv.get('timestamp', 0)}"
                result = memory_extractor.extract(
                    messages=conv["messages"],
                    conversation_id=conversation_id,
                )
                stored = 0
                if memory_index_service is not None:
                    for candidate in result.auto_write:
                        entry = memory_index_service.index_memory(candidate.to_dict())
                        if entry:
                            stored += 1
                stats["memories_stored"] = stored
                stats["imported"] = 1
            except Exception as exc:
                logger.warning("Kimi 记忆提取失败 '%s': %s", conv.get("title", ""), exc)
                stats["imported"] = 1
        else:
            stats["imported"] = 1

        # 知识图谱桥接：对整段对话做一次显式实体/关系提取，写入 KnowledgeGraphStore。
        if knowledge_graph_extractor is not None and all_text:
            try:
                # P4-KG-ORIGIN：出处一律带 `kimi:` 命名空间。旧写法是裸 session_id
                # 或 `kimi_<ts>`（下划线），与其他域不同口径 → 级联清理与详情
                # 展示都无法判定归属。
                src_id = f"kimi:{session_id}" if session_id is not None else f"kimi:{conv.get('timestamp', 0) or conv.get('title', 'chat')}"
                # KG-CH（2026-09-21）契约：Kimi 对话是聊天/叙述体文本，正则通道会
                # 切出句子碎片噪声——无内核时让抽取器自己返回 via="skipped"，
                # 不再用 `use_llm=(ai_client is not None)` 隐式降级到正则。
                graph_store = getattr(knowledge_graph_extractor, "store", None)

                def _snapshot() -> tuple[set[str], set[tuple[str, str, str]]]:
                    if graph_store is None:
                        return set(), set()
                    ents = {str(e.get("id") or "") for e in graph_store.get_all_entities()}
                    rels = {
                        (str(r.get("source_id") or ""), str(r.get("target_id") or ""), str(r.get("relation_type") or ""))
                        for r in graph_store.get_all_relations()
                    }
                    return ents, rels

                # D-5 同款修正（旧行为把“复用已有实体”计成新增，计数虚高）：
                # 按 store 快照差集计真正新增数。ai_chat_sync 早就改了，这处漏了。
                before_ents, before_rels = _snapshot()
                knowledge_graph_extractor.extract_from_text(
                    all_text,
                    source_id=src_id,
                    use_llm=True,
                    source_kind="kimi",
                )
                after_ents, after_rels = _snapshot()
                stats["graph_entities_added"] = len(after_ents - before_ents)
                stats["graph_relations_added"] = len(after_rels - before_rels)
            except Exception as exc:
                logger.debug("Kimi 图谱提取失败（非阻塞）: %s", exc)

        return stats
