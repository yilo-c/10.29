"""AI 聊天记录导入器 — 从 ChatGPT/Claude/DeepSeek 导出文件导入对话
并送入记忆提取管道。

支持的格式：
- ChatGPT JSON 导出（conversations.json）
- Claude JSON 导出（project export）
- 通用 Markdown 对话格式
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.chat_importer")

# ── 解析器 ──


def _parse_chatgpt_json(path: str) -> list[dict[str, Any]]:
    """解析 ChatGPT 导出的 conversations.json。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    conversations = data if isinstance(data, list) else data.get("items", data.get("conversations", []))
    result = []
    for conv in conversations:
        title = conv.get("title", "") or ""
        msgs = conv.get("messages", conv.get("conversation", [])) or []
        result.append(
            {
                "source": "chatgpt",
                "title": title,
                "timestamp": conv.get("create_time", conv.get("created_at", 0)),
                "messages": [
                    {
                        "role": m.get("role", ""),
                        "content": m.get("content", "") or m.get("parts", [""])[0] if isinstance(m.get("parts"), list) else str(m.get("content", "")),
                    }
                    for m in msgs
                    if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                ],
            }
        )
    return result


def _parse_claude_json(path: str) -> list[dict[str, Any]]:
    """解析 Claude 项目导出的 JSON。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    conversations = data if isinstance(data, list) else data.get("conversations", data.get("projects", []))
    result = []
    for conv in conversations:
        title = conv.get("name", conv.get("title", "")) or ""
        msgs = conv.get("chat_messages", conv.get("messages", conv.get("conversation", []))) or []
        result.append(
            {
                "source": "claude",
                "title": title,
                "timestamp": conv.get("created_at", 0),
                "messages": [
                    {
                        "role": "user" if m.get("role") in ("user", "human") else "assistant",
                        "content": m.get("content", "") or m.get("text", "") or str(m.get("message", "")),
                    }
                    for m in msgs
                    if isinstance(m, dict) and m.get("role") in ("user", "human", "assistant", "ai")
                ],
            }
        )
    return result


def _parse_markdown(path: str) -> list[dict[str, Any]]:
    """解析通用 Markdown 对话格式。

    格式：
    # 对话标题
    **User**: 消息
    **AI**: 回复
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _parse_markdown_text(text, source="markdown")


def _parse_markdown_text(text: str, source: str = "markdown", title: str = "") -> list[dict[str, Any]]:
    """从 Markdown 文本解析对话，供文件解析和 Kimi 复制粘贴复用。"""
    conv: dict[str, Any] = {"source": source, "title": title or "", "timestamp": 0, "messages": []}
    if not title:
        title_match = re.search(r"^#\s+(.+)", text, re.M)
        if title_match:
            conv["title"] = title_match.group(1).strip()

    # 多行消息累计：同一角色的连续行合并为一条消息
    lines = text.split("\n")
    current_role: str | None = None
    current_content: list[str] = []

    def flush():
        if current_role and current_content:
            content = "\n".join(current_content).strip()
            if content:
                conv["messages"].append({"role": current_role, "content": content})

    for raw_line in lines:
        line = raw_line.rstrip()
        user_match = re.match(r"\*\*(?:User|用户|我)\*\*[:：]\s*(.*)", line, re.I)
        ai_match = re.match(r"\*\*(?:AI|Assistant|Kimi|助手|Bot|机器人)\*\*[:：]\s*(.*)", line, re.I)
        if user_match:
            flush()
            current_role = "user"
            current_content = [user_match.group(1).strip()] if user_match.group(1).strip() else []
        elif ai_match:
            flush()
            current_role = "assistant"
            current_content = [ai_match.group(1).strip()] if ai_match.group(1).strip() else []
        elif current_role is not None:
            # 跳过纯空行开头的连续空行，保留内容中的空行
            current_content.append(line)
    flush()
    return [conv] if conv["messages"] else []


def _parse_kimi_json(path: str) -> list[dict[str, Any]]:
    """解析 Kimi 网页/API 导出的 JSON。

    兼容结构：
    - {title, messages: [{role, content, create_time}]}
    - {data: {items: [{...}]}}
    - [{title, messages: [...]}, ...]
    - Connect/gRPC-Web 流式响应（二进制或文本）
    """
    # 先尝试标准 JSON（messages 数组格式）
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        conversations: list[dict[str, Any]] = []

        # 先尝试取出顶层对象里的 messages
        if isinstance(data, dict):
            if "messages" in data and isinstance(data.get("messages"), list):
                conversations.append(_normalize_kimi_conversation(data))
            else:
                for key in ("data", "items", "conversations", "list", "result"):
                    val = data.get(key)
                    if isinstance(val, list):
                        conversations.extend(_normalize_kimi_conversation(c) for c in val if isinstance(c, dict))
                        break
                    elif isinstance(val, dict) and "items" in val:
                        items = val.get("items")
                        if isinstance(items, list):
                            conversations.extend(_normalize_kimi_conversation(c) for c in items if isinstance(c, dict))
                            break
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    conversations.append(_normalize_kimi_conversation(item))

        if conversations:
            return [c for c in conversations if c.get("messages")]
    except Exception:
        pass

    # 标准 JSON 解析失败或没有 messages，回退到 Connect 流解析
    return _parse_kimi_connect_stream(path)


def _normalize_kimi_conversation(conv: dict[str, Any]) -> dict[str, Any]:
    """把 Kimi 单条对话对象规范化为统一结构。"""
    title = conv.get("title") or conv.get("name") or conv.get("session_title") or "Kimi 对话"
    raw_msgs = conv.get("messages") or conv.get("chat_messages") or conv.get("msgs") or []
    timestamp = conv.get("create_time") or conv.get("created_at") or conv.get("timestamp") or 0

    messages: list[dict[str, str]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or m.get("sender") or "").lower()
        content = m.get("content") or m.get("text") or m.get("message") or ""
        if isinstance(content, list):
            # OpenAI 风格的 content parts
            content = "\n".join(str(part.get("text", part) if isinstance(part, dict) else part) for part in content)
        if not role or not str(content).strip():
            continue
        if role in ("user", "human", "我"):
            messages.append({"role": "user", "content": str(content).strip()})
        elif role in ("assistant", "ai", "kimi", "bot"):
            messages.append({"role": "assistant", "content": str(content).strip()})

    return {"source": "kimi", "title": str(title).strip(), "timestamp": timestamp, "messages": messages}


def _parse_kimi_connect_stream(path: str) -> list[dict[str, Any]]:
    """解析 Kimi 的 Connect/gRPC-Web 流式响应。

    支持两种形式：
    1. 原始二进制 Connect 帧（5 字节头 + JSON payload）
    2. 浏览器里复制出来的文本形式（多条 JSON 拼接）
    """
    with open(path, "rb") as f:
        raw = f.read()

    events = _parse_connect_binary_frames(raw)
    if not events:
        # 回退到文本解析
        text = raw.decode("utf-8", errors="ignore")
        events = _parse_connect_text_events(text)

    if not events:
        return []

    return [_build_kimi_conversation_from_events(events)]


def _parse_connect_binary_frames(data: bytes) -> list[dict[str, Any]]:
    """按 Connect 协议二进制帧解析。"""
    events: list[dict[str, Any]] = []
    i = 0
    while i < len(data):
        # Connect 帧头：1 字节标志（0/1 表示是否压缩）+ 4 字节大端长度
        if data[i] not in (0x00, 0x01):
            break
        if i + 5 > len(data):
            break
        length = int.from_bytes(data[i + 1 : i + 5], "big")
        if i + 5 + length > len(data):
            break
        payload = data[i + 5 : i + 5 + length]
        try:
            events.append(json.loads(payload))
        except Exception:
            pass
        i += 5 + length
    # 至少成功解析一条才算有效
    return events if events and i >= 5 else []


def _parse_connect_text_events(text: str) -> list[dict[str, Any]]:
    """从浏览器复制出来的文本中解析 Connect 事件对象。"""
    events: list[dict[str, Any]] = []

    # 策略 1：按行解析，去掉行首控制字符
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r"^[\x00-\x1F]+", "", line)
        if not cleaned:
            continue
        try:
            events.append(json.loads(cleaned))
        except Exception:
            pass

    # 策略 2：如果策略 1 没解析出，尝试用正则从混乱文本中提取 JSON 对象
    if not events:
        pattern = re.compile(r"(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})")
        for match in pattern.findall(text):
            try:
                events.append(json.loads(match))
            except Exception:
                pass

    return events


def _build_kimi_conversation_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """从 Connect 事件列表构建统一对话结构。"""
    title = "Kimi 对话"
    for ev in events:
        if ev.get("op") == "set" and ev.get("mask") == "chat.name":
            chat = ev.get("chat")
            if isinstance(chat, dict) and chat.get("name"):
                title = str(chat["name"]).strip()
                break

    # 收集后续 block.text 事件，按 messageId 聚合完整回复文本
    block_texts_by_message: dict[str, list[str]] = {}
    for ev in events:
        if ev.get("op") != "set":
            continue
        mask = ev.get("mask", "")
        if mask not in ("block", "block.text"):
            continue
        block = ev.get("block")
        if not isinstance(block, dict):
            continue
        message_id = block.get("messageId")
        if not message_id:
            continue
        text_content = ""
        text_obj = block.get("text")
        if isinstance(text_obj, dict) and text_obj.get("content"):
            text_content = str(text_obj["content"]).strip()
        elif block.get("content"):
            text_content = str(block["content"]).strip()
        if text_content:
            block_texts_by_message.setdefault(message_id, []).append(text_content)

    raw_messages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for ev in events:
        if ev.get("op") != "set":
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        msg_id = msg.get("id")
        if not msg_id:
            continue
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        role = str(msg.get("role", "")).lower()
        if role not in ("user", "assistant"):
            continue
        content = _extract_kimi_message_text(msg)
        # 如果消息自身没有 blocks 文本，尝试从 block.text 事件补全
        if not content and msg_id in block_texts_by_message:
            content = "\n\n".join(block_texts_by_message[msg_id])
        if not content:
            continue
        raw_messages.append(
            {
                "role": role,
                "content": content,
                "create_time": msg.get("createTime", ""),
            }
        )

    # 按 createTime 排序
    raw_messages.sort(key=lambda m: str(m["create_time"]))
    messages = [{"role": m["role"], "content": m["content"]} for m in raw_messages]

    return {"source": "kimi", "title": title, "timestamp": 0, "messages": messages}


def _extract_kimi_message_text(msg: dict[str, Any]) -> str:
    """从 Kimi 消息对象的 blocks 里提取文本内容。"""
    blocks = msg.get("blocks") or []
    if not isinstance(blocks, list):
        return ""

    texts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text_obj = block.get("text")
        if isinstance(text_obj, dict) and text_obj.get("content"):
            texts.append(str(text_obj["content"]).strip())
    return "\n\n".join(texts)


def _parse_kimi_markdown(path: str) -> list[dict[str, Any]]:
    """解析从 Kimi 网页端复制粘贴保存的 Markdown 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _parse_markdown_text(text, source="kimi", title="Kimi 对话")


# ── 微信解析器（WeChatMsg / 留痕 导出 txt/csv） ──

# 微信系统消息（撤回/拍一拍/加好友/通话等），按行内容判定后跳过
_WECHAT_SYSTEM_MARKERS = (
    "撤回了一条消息",
    "撤回消息",
    "拍了拍",
    "你已添加了",
    "你添加了",
    "添加了你",
    "邀请你加入群聊",
    "加入了群聊",
    "开启了好友验证",
    "以上是打招呼的内容",
    "语音通话",
    "视频通话",
    "通话时长",
    "位置共享",
    "群聊的聊天记录",
    "开启了朋友验证",
    "当前聊天",
    "消息已发出，但被对方拒收了",
    "你收到一条转账消息",
    "收款方备注",
    "群成员",
    "以下为新成员",
    "你在群聊中的昵称",
    "你已成为该群",
    "你退出群聊",
    "解散该群聊",
)

# 微信时间戳正则：2024-01-01 10:00:00 / 2024/1/1 10:00（兼容秒省略）
_WECHAT_TS_RE = re.compile(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$")


def _parse_wechat_txt_file(path: str) -> list[dict[str, Any]]:
    """解析微信导出的 txt 文件（WeChatMsg/留痕）。"""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    title = os.path.splitext(os.path.basename(path))[0] or "微信聊天记录"
    return _parse_wechat_text(text, title=title)


def _is_wechat_system_line(content: str) -> bool:
    """判断一行微信消息是否为系统消息（撤回/拍一拍/加好友/通话等）。"""
    return any(marker in content for marker in _WECHAT_SYSTEM_MARKERS)


def _parse_wechat_text(text: str, title: str) -> list[dict[str, Any]]:
    """解析微信导出文本（txt）为对话。

    每行格式: `2024-01-01 10:00:00 发送人: 内容`
    - "我" 的消息 → role=user；其他发送人 → role=assistant（语义角色）
    - 每条消息保留 name（发送人）与 timestamp（ISO）
    - 系统消息（撤回/拍一拍/加好友/通话）过滤
    """
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _WECHAT_TS_RE.match(stripped)
        if not m:
            continue
        ts, rest = m.group(1), m.group(2).strip()
        # 发送人: 内容（发送人可能含冒号，用最后一个分隔）
        sep_idx = rest.rfind(":")
        sep2 = rest.rfind("：")
        idx = max(sep_idx, sep2)
        if idx <= 0:
            continue
        sender = rest[:idx].strip()
        content = rest[idx + 1 :].strip()
        if not sender or not content:
            continue
        if _is_wechat_system_line(content):
            continue
        rows.append({"ts": ts, "sender": sender, "content": content})
    return _wechat_rows_to_conversations(rows, title)


def _parse_wechat_csv(path: str) -> list[dict[str, Any]]:
    """解析微信导出 CSV（WeChatMsg 留痕 csv：time,sender,content 列，兼容中文列名）。"""
    import csv as _csv

    with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
        reader = _csv.DictReader(f)
        if not reader.fieldnames:
            return []
        cols = {str(c).strip().lower(): c for c in reader.fieldnames}
        # 列名探测：time/发送人/内容（兼容 ts/时间戳/时间、昵称/发送者、消息/文本）
        ts_col = cols.get("time") or cols.get("ts") or cols.get("timestamp") or cols.get("时间") or cols.get("时间戳") or cols.get("datetime")
        sender_col = cols.get("sender") or cols.get("发送人") or cols.get("发送者") or cols.get("昵称") or cols.get("from") or cols.get("talker")
        content_col = cols.get("content") or cols.get("message") or cols.get("text") or cols.get("内容") or cols.get("消息") or cols.get("msg")
        if not (ts_col and sender_col and content_col):
            return []
        rows: list[dict[str, Any]] = []
        for row in reader:
            ts = str(row.get(ts_col, "")).strip()
            sender = str(row.get(sender_col, "")).strip()
            content = str(row.get(content_col, "")).strip()
            if not ts or not sender or not content:
                continue
            if _is_wechat_system_line(content):
                continue
            rows.append({"ts": ts, "sender": sender, "content": content})
        title = os.path.splitext(os.path.basename(path))[0] or "微信聊天记录"
        return _wechat_rows_to_conversations(rows, title)


def _wechat_rows_to_conversations(rows: list[dict[str, Any]], title: str) -> list[dict[str, Any]]:
    """把微信消息行聚合成会话（整文件一个会话；按时间排序）。"""
    if not rows:
        return []
    rows.sort(key=lambda r: str(r["ts"]))
    messages: list[dict[str, Any]] = []
    for row in rows:
        sender = row["sender"]
        is_self = sender in ("我", "自己", "我")
        messages.append(
            {
                "role": "user" if is_self else "assistant",
                "content": row["content"],
                "name": sender,
                "timestamp": _wechat_ts_to_iso(str(row["ts"])),
            }
        )
    return [
        {
            "source": "wechat",
            "kind": "chatlog",
            "title": title or "微信聊天记录",
            "timestamp": messages[0].get("timestamp", ""),
            "messages": messages,
        }
    ]


def _wechat_ts_to_iso(ts: str) -> str:
    """把微信时间戳转 ISO：`2024-01-01 10:00:00` → `2024-01-01T10:00:00`。"""
    return ts.replace(" ", "T", 1) if "T" not in ts else ts


def _classify_messages(ai_client: Any, text: str) -> str:
    """用本地 Ollama 分类对话内容（工作/个人/学习）。"""
    from assistant.content_classifier import ContentClassifier

    return ContentClassifier().classify(text, ai_client)


def _store_raw_items(conv: dict[str, Any], raw_item_repo: Any) -> int:
    """把一条对话的每条消息写入 raw items（个人知识体系底座 L2）。

    敏感度由 SensitivityClassifier 保守判定：
    - source=wechat/clipboard/daily → 默认 sensitive
    - 内容命中敏感关键词（证件/财务/医疗）→ sensitive
    """
    if raw_item_repo is None:
        return 0
    from services.sensitivity_classifier import get_classifier

    clf = get_classifier()
    conv_source = str(conv.get("source") or "unknown")
    items: list[dict[str, Any]] = []
    for m in conv.get("messages") or []:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        items.append(
            {
                "content": content,
                "source": conv_source,
                "kind": "text",
                "sensitivity": clf.classify(content, conv_source),
                "meta": {
                    "sender": str(m.get("name") or ""),
                    "role": str(m.get("role") or ""),
                    "conversation": str(conv.get("title") or ""),
                    "timestamp": str(m.get("timestamp") or "") or str(conv.get("timestamp") or ""),
                },
            }
        )
    return raw_item_repo.add_many(items)


# ── 主导入函数 ──


def import_chat_file(
    path: str,
    ai_client: Any = None,
    memory_extractor: Any = None,
    chat_repo: Any = None,
    memory_index_service: Any = None,
    raw_item_repo: Any = None,
    format_hint: str = "",
    max_conversations: int = 50,
) -> dict[str, Any]:
    """导入一个聊天记录文件。

    当传入 chat_repo 和 memory_index_service 时，会同时：
    1. 把原始对话保存到 chat_session_repository（可在 chat.history 回看）
    2. 提取记忆并写入 memory_index（与 knowledge.ingest_conversation 等效）

    传入 raw_item_repo 时，每条消息写入个人知识体系 raw items（带敏感度标签）。

    返回统计信息。
    """
    if not os.path.exists(path):
        return {"success": False, "error": f"文件不存在: {path}"}

    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    fmt = (format_hint or "").lower()

    # 1. 探测格式并解析
    conversations: list[dict[str, Any]] = []
    if ext == ".json":
        if fmt == "kimi" or "kimi" in name:
            conversations = _parse_kimi_json(path)
        elif fmt == "chatgpt" or "chatgpt" in name or "conversations" in name:
            conversations = _parse_chatgpt_json(path)
        elif fmt == "claude" or "claude" in name:
            conversations = _parse_claude_json(path)
        else:
            # 依次尝试 Kimi / ChatGPT / Claude 解析器
            parsers = [
                ("kimi", _parse_kimi_json),
                ("chatgpt", _parse_chatgpt_json),
                ("claude", _parse_claude_json),
            ]
            last_exc: Exception | None = None
            for _label, parser in parsers:
                try:
                    conversations = parser(path)
                    if conversations:
                        break
                except Exception as exc:
                    last_exc = exc
            if not conversations and last_exc is not None:
                return {"success": False, "error": f"无法解析 JSON: {last_exc}"}
    elif ext == ".md":
        if fmt == "kimi" or "kimi" in name:
            conversations = _parse_kimi_markdown(path)
        else:
            conversations = _parse_markdown(path)
    elif ext == ".txt":
        # 微信导出 txt（WeChatMsg/留痕）优先；无微信时间戳格式则回退通用 Markdown
        if fmt == "wechat" or "wechat" in name:
            conversations = _parse_wechat_txt_file(path)
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            wechat_conv = _parse_wechat_text(text, title=os.path.splitext(name)[0])
            if wechat_conv:
                conversations = wechat_conv
            else:
                conversations = _parse_markdown_text(text, title=os.path.splitext(name)[0])
    elif ext == ".csv":
        # 微信导出 csv（WeChatMsg 留痕）
        conversations = _parse_wechat_csv(path)
    else:
        return {"success": False, "error": f"不支持的文件格式: {ext}"}

    if not conversations:
        return {"success": False, "error": "未找到对话"}

    conversations = conversations[:max_conversations]
    stats: dict[str, Any] = {
        "total": len(conversations),
        "imported": 0,
        "skipped": 0,
        "categories": {},
        "sessions_saved": 0,
        "memories_stored": 0,
        "raw_items_stored": 0,
    }

    # 2. 逐条处理
    for conv in conversations:
        if not conv.get("messages"):
            stats["skipped"] += 1
            continue
        # 合并消息文本用于分类
        all_text = " ".join(m["content"] for m in conv["messages"] if m.get("content"))[:2000]

        # 分类
        category = "personal"
        if ai_client is not None:
            try:
                category = _classify_messages(ai_client, all_text)
            except Exception as exc:
                logger.debug("classify failed: %s", exc)
        stats["categories"][category] = stats["categories"].get(category, 0) + 1

        # 保存原始会话到 chat_session_repository（如果可用）
        session_id: int | None = None
        if chat_repo is not None:
            try:
                title = conv.get("title") or "导入的对话"
                session_id = chat_repo.create_session(title)
                # 2026-10-15（id 口径专项审计·治本）：把本地会话 id **回填到 conv**，
                # 供 ai_chat_sync._extract_graph 写图谱出处。原实现出处写平台
                # `deepseek:{conv.id}`（uuid），与本地 int id 无映射 → 孤儿指针
                # （实测 558 实体详情补不上、面板不可跳）。回填后新导入的出处
                # 一律指向本地会话（deepseek:{本地int}），读取侧可反查。
                conv["local_session_id"] = session_id
                repo_messages = [
                    {
                        "speaker": m.get("name") or ("你" if m.get("role") == "user" else "AI"),
                        "message": str(m.get("content", "")),
                        "timestamp": conv.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    }
                    for m in conv["messages"]
                    if m.get("content")
                ]
                chat_repo.sync_messages(session_id, repo_messages)
                stats["sessions_saved"] += 1
            except Exception as exc:
                logger.warning("保存会话失败 '%s': %s", conv.get("title", ""), exc)

        # 写入 raw items（个人知识体系底座，带敏感度标签）
        if raw_item_repo is not None:
            try:
                stats["raw_items_stored"] += _store_raw_items(conv, raw_item_repo)
            except Exception as exc:
                logger.warning("raw items 存储失败 '%s': %s", conv.get("title", ""), exc)

        # 提取记忆并写入索引（与 knowledge.ingest_conversation 等效）
        if memory_extractor is not None and conv.get("messages"):
            try:
                conversation_id = str(session_id) if session_id is not None else f"import_{conv.get('source', 'unknown')}_{conv.get('timestamp', 0)}"
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
                stats["memories_stored"] += stored
                stats["imported"] += 1
            except Exception as exc:
                logger.warning("memory extraction failed for conv '%s': %s", conv.get("title", ""), exc)
                stats["imported"] += 1
        else:
            stats["imported"] += 1

    return {"success": True, **stats}


def import_conversations(
    conversations: list[dict[str, Any]],
    *,
    ai_client: Any = None,
    memory_extractor: Any = None,
    chat_repo: Any = None,
    memory_index_service: Any = None,
    raw_item_repo: Any = None,
) -> dict[str, Any]:
    """直接导入内存中的对话列表（供浏览器 bridge 等场景使用）。

    逻辑与 import_chat_file 相同，但跳过文件读取和格式探测。
    """
    if not conversations:
        return {"success": False, "error": "未提供对话"}

    stats: dict[str, Any] = {
        "total": len(conversations),
        "imported": 0,
        "skipped": 0,
        "categories": {},
        "sessions_saved": 0,
        "memories_stored": 0,
        "raw_items_stored": 0,
    }

    for conv in conversations:
        if not conv.get("messages"):
            stats["skipped"] += 1
            continue

        all_text = " ".join(m["content"] for m in conv["messages"] if m.get("content"))[:2000]
        category = "personal"
        if ai_client is not None:
            try:
                category = _classify_messages(ai_client, all_text)
            except Exception as exc:
                logger.debug("classify failed: %s", exc)
        stats["categories"][category] = stats["categories"].get(category, 0) + 1

        session_id: int | None = None
        if chat_repo is not None:
            try:
                title = conv.get("title") or "导入的对话"
                session_id = chat_repo.create_session(title)
                # 2026-10-15（id 口径专项审计·治本）：把本地会话 id **回填到 conv**，
                # 供 ai_chat_sync._extract_graph 写图谱出处。原实现出处写平台
                # `deepseek:{conv.id}`（uuid），与本地 int id 无映射 → 孤儿指针
                # （实测 558 实体详情补不上、面板不可跳）。回填后新导入的出处
                # 一律指向本地会话（deepseek:{本地int}），读取侧可反查。
                conv["local_session_id"] = session_id
                repo_messages = [
                    {
                        "speaker": m.get("name") or ("你" if m.get("role") == "user" else "AI"),
                        "message": str(m.get("content", "")),
                        "timestamp": conv.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    }
                    for m in conv["messages"]
                    if m.get("content")
                ]
                chat_repo.sync_messages(session_id, repo_messages)
                stats["sessions_saved"] += 1
            except Exception as exc:
                logger.warning("保存会话失败 '%s': %s", conv.get("title", ""), exc)

        if raw_item_repo is not None:
            try:
                stats["raw_items_stored"] += _store_raw_items(conv, raw_item_repo)
            except Exception as exc:
                logger.warning("raw items 存储失败 '%s': %s", conv.get("title", ""), exc)

        if memory_extractor is not None and conv.get("messages"):
            try:
                conversation_id = str(session_id) if session_id is not None else f"import_bridge_{conv.get('timestamp', 0)}"
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
                stats["memories_stored"] += stored
                stats["imported"] += 1
            except Exception as exc:
                logger.warning("memory extraction failed for conv '%s': %s", conv.get("title", ""), exc)
                stats["imported"] += 1
        else:
            stats["imported"] += 1

    return {"success": True, **stats}
