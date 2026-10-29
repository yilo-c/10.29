# -*- coding: utf-8 -*-
"""对话附件（Task #9，2026-09-18「一二三档都补」第一档）。

chat.send params 新增可选 ``attachments``：
    [{"name": str, "mime": str, "data_base64": str}]

设计判据（最小化）：
- **不动 PydanticAI 多模态链**——``SiyiChatModel._convert_messages_to_openai``
  现对多模态 part 只取文本（assistant/agents/butler_agent.py:428），图片会
  被静默丢弃；打通真·视觉消息要同时动适配器+provider 能力判定，属深水区。
- 采用 **enrich 咽喉点**：附件在进入任何执行路径（gateway / aggregation /
  multi_agent / butler / simple_stream）之前统一并入 message 文本——
  文本类直读内联，图片类经 ``ai_client.chat_completion_vision``（fallback
  客户端已有 purpose=vision 专用 provider 路由 + primary 回退）转文字描述。
- 附件是**每次发送的瞬时上下文**，不进历史存储（前端 context_messages 仍只带
  文本），避免会话历史里堆 base64。

守卫（对齐"20k 超长无守卫"观察项，这次带上限）：
- 单文件 base64 ≤ ``MAX_FILE_B64_BYTES``（WS 帧上限 20MB，留余量）；
- 文本内联截断：单文件 ``MAX_TEXT_CHARS`` / 整轮 ``TOTAL_TEXT_CHARS``；
- 图片张数 ``MAX_IMAGES``；vision 调用失败 → 诚实降级为"无法解析"标注，不炸整轮对话。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any

logger = logging.getLogger("siyi.chat.attachments")

MAX_FILE_B64_BYTES = 6 * 1024 * 1024  # 单文件 base64 上限（≈4.5MB 原始）
MAX_TEXT_CHARS = 24_000  # 单文本文件内联上限
TOTAL_TEXT_CHARS = 64_000  # 整轮附件文本总预算
MAX_IMAGES = 4  # 图片张数上限（每张一次 vision 调用）

_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/x-sh",
    "application/sql",
    "application/toml",
    "application/x-ndjson",
    "application/csv",
}
_TEXT_SUFFIXES = (
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".log",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
    ".html",
    ".css",
    ".xml",
    ".rst",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".rb",
    ".php",
)

_VISION_PROMPT = (
    "这是用户在对话中附带的一张图片。请用中文客观描述图片中的关键信息"
    "（文字内容请原样转写，界面/图表/照片请说明主体与要素），"
    "不要推测图片之外的问题，直接输出描述。"
)


def _is_text_attachment(name: str, mime: str) -> bool:
    m = (mime or "").lower()
    if m.startswith(_TEXT_MIME_PREFIXES):
        return True
    if m in _TEXT_MIME_EXACT:
        return True
    return (name or "").lower().endswith(_TEXT_SUFFIXES)


def _decode_text(raw_b64: str) -> str:
    data = base64.b64decode(raw_b64, validate=False)
    return data.decode("utf-8", errors="replace")


async def _describe_image(context: Any, name: str, raw_b64: str, mime: str) -> str:
    """图片 → 文字描述（经既有 vision provider 链）。失败返回诚实标注。"""
    client = getattr(context, "ai_client", None)
    if client is None or not hasattr(client, "chat_completion_vision"):
        return "（未配置可用的视觉模型，图片未解析）"
    fmt = (mime or "").split("/")[-1].lower() or "png"
    if fmt == "jpg":
        fmt = "jpeg"
    try:
        resp = await asyncio.to_thread(
            client.chat_completion_vision,
            text=_VISION_PROMPT,
            image_base64=raw_b64,
            image_format=fmt,
            max_tokens=900,
        )
        desc = str((resp or {}).get("content") or (resp or {}).get("text") or "").strip()
        if desc:
            return desc
        return "（视觉模型返回空描述）"
    except Exception as exc:  # AIServiceError / 网络 / 预算等——降级不阻断对话
        logger.warning("attachment vision failed name=%r: %s", name, exc, exc_info=True)
        return f"（图片解析失败：{exc}）"


async def enrich_message_with_attachments(context: Any, message: str, attachments: Any) -> str:
    """把附件块并入用户消息文本；无有效附件时原样返回 message。"""
    if not isinstance(attachments, list) or not attachments:
        return message

    blocks: list[str] = []
    total_chars = 0
    image_seq = 0
    for item in attachments[: MAX_IMAGES + 8]:  # 数量硬上限在下面的分类计数里控
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "未命名文件")
        mime = str(item.get("mime") or "")
        raw_b64 = str(item.get("data_base64") or item.get("data") or "")
        # 前端 readAsDataURL 结果可能带 data:...;base64, 前缀——剥掉
        if "," in raw_b64[:100] and raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        if not raw_b64:
            blocks.append(f"<附件 name={name!r} status=空文件/>")
            continue
        if len(raw_b64) > MAX_FILE_B64_BYTES:
            blocks.append(f"<附件 name={name!r} status=过大已拒收 size_kb={len(raw_b64) * 3 // 4 // 1024}/>")
            continue
        if _is_text_attachment(name, mime):
            try:
                text = _decode_text(raw_b64)
            except (binascii.Error, ValueError):
                blocks.append(f"<附件 name={name!r} status=base64解码失败/>")
                continue
            budget = min(MAX_TEXT_CHARS, TOTAL_TEXT_CHARS - total_chars)
            if budget <= 0:
                blocks.append(f"<附件 name={name!r} status=超出整轮附件预算未内联/>")
                continue
            if len(text) > budget:
                text = text[:budget] + "\n…（截断）"
            total_chars += len(text)
            blocks.append(f'<attachment name={name!r} type="text">\n{text}\n</attachment>')
        elif (mime or "").startswith("image/"):
            if image_seq >= MAX_IMAGES:
                blocks.append(f"<附件 name={name!r} status=图片数量超上限未解析/>")
                continue
            image_seq += 1
            desc = await _describe_image(context, name, raw_b64, mime)
            blocks.append(f'<attachment name={name!r} type="image">\n{desc}\n</attachment>')
        else:
            blocks.append(f"<attachment name={name!r} type=other status=暂不支持此类型未解析/>)")

    if not blocks:
        return message
    header = "[用户本轮附带文件——以下为系统对附件的解析注入，非用户原文]"
    return f"{message}\n\n{header}\n" + "\n".join(blocks)
