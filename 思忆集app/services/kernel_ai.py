# -*- coding: utf-8 -*-
"""kernel_ai — cron 任务 AI 部分的内核通道（M-cron 收敛）。

架构定位（大脑/手/闹钟三层）：
- 大脑 = 成熟 agent 内核（dsh / claude_code / codex，经 agent_gateway）
- 本模块只做一件事：把"生成一段文本"的 AI 活优先交给内核，
  内核不可用/失败时回退调用方提供的原路径（ai_client 等）。

用法::

    from services.kernel_ai import kernel_generate, call_ai_text

    text = kernel_generate(
        gateway, prompt, system=system_prompt, timeout_s=180,
        fallback=lambda: call_ai_text(ai_client, prompt, system=system_prompt),
    )
"""

from __future__ import annotations

import logging

from core.isolation import is_offline_mode

logger = logging.getLogger("siyi.kernel_ai")


def call_ai_text(ai_client, prompt: str, *, system: str | None = None) -> str:
    """标准文本回退：经 chat_completion 取一次非流式文本。

    背景（2026-09-19 U0 实测）：cron_actions / knowledge_synthesis 的 fallback
    闭包曾调 `ai_client.chat(prompt)`，而 JudgeAwareClient 的真方法名是
    `chat_completion` → 内核失败时回退必然 AttributeError，"内核+回退双失败"。
    统一收敛到本 helper；返回 "" 表示回退未产出（与 kernel_generate 契约一致）。
    """
    if ai_client is None:
        return ""
    try:
        if hasattr(ai_client, "chat_completion"):
            messages = [{"role": "user", "content": prompt}]
            if system:
                messages = [{"role": "system", "content": system}] + messages
            resp = ai_client.chat_completion(messages=messages)
            if isinstance(resp, dict):
                choices = resp.get("choices") or []
                if choices:
                    return str(choices[0].get("message", {}).get("content", "") or "").strip()
            return ""
        if hasattr(ai_client, "complete_text"):
            return str(ai_client.complete_text(prompt) or "").strip()
    except Exception as exc:
        logger.warning("kernel_ai.call_ai_text failed: %s", exc)
    return ""


def kernel_generate(
    gateway,
    prompt: str,
    *,
    system: str | None = None,
    timeout_s: int = 180,
    fallback=None,
):
    """经 agent_gateway（成熟内核）生成文本；失败回退 fallback。

    返回生成的文本（str）；两条路都失败时返回 ""。

    离线模式（PD-ISO-b，2026-09-18）：显式声明的离线必须**同时**短路内核与
    fallback。内核会拉起成熟 agent 进程、自身也会出网；只拦 ai_client 等于
    留了第二条烧配额的腿。这里不尝试、不降级、直接空手而归并留痕——离线环境
    下"沉默地没生成"是正确结果，"偷偷联网生成"才是缺陷。
    """
    if is_offline_mode():
        logger.info("kernel_ai: 离线模式（显式声明）→ 跳过内核与 fallback")
        return ""

    text = ""
    if gateway is not None:
        try:
            full = f"{system}\n\n{prompt}" if system else prompt
            run = gateway.submit(full, timeout_s=timeout_s)
            if getattr(run, "ok", False) and getattr(run, "result", None):
                text = str(run.result).strip()
                logger.info("kernel_ai: generated via kernel=%s len=%d", getattr(gateway, "name", "unknown"), len(text))
                return text
            logger.warning(
                "kernel_ai: kernel run not ok (status=%s error=%s), falling back",
                getattr(run, "status", "?"),
                getattr(run, "error", ""),
            )
        except Exception as exc:
            logger.warning("kernel_ai: kernel submit failed: %s, falling back", exc)
    if fallback is not None:
        try:
            return fallback()
        except Exception as exc:
            logger.warning("kernel_ai: fallback failed: %s", exc)
    return ""
