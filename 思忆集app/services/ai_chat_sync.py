# -*- coding: utf-8 -*-
"""AI 平台账户聊天同步 — 直接抓取账户对话并导入思忆集知识体系。

职责：
  1. 从 LocalDataStore 读取/写入各 AI 平台账户 token（ai_account_tokens.json）。
  2. 用 DeepSeekAccountClient 直接拉取账户会话+消息。
  3. 复用 chat_importer.import_conversations 将对话入库（raw→记忆→知识图谱）。

RPC（ai_chat.*）与 cron action（ai_chat.collect）都收敛于此，避免逻辑重复。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("siyi.ai_chat_sync")

_TOKEN_STORE_KEY = "ai_account_tokens"


# ── token 存取 ──────────────────────────────────────────────────────────


def get_account_token(context: Any, platform: str = "deepseek") -> str:
    store = getattr(context, "local_data_store", None)
    if store is None:
        return ""
    try:
        data = store.load_json(_TOKEN_STORE_KEY, {}) or {}
        return str(data.get(platform) or "") or str(data.get(f"{platform}_token") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 %s 账户 token 失败: %s", platform, exc)
        return ""


def set_account_token(context: Any, platform: str, token: str) -> bool:
    store = getattr(context, "local_data_store", None)
    if store is None:
        return False
    try:
        data = store.load_json(_TOKEN_STORE_KEY, {}) or {}
        data[platform] = str(token or "").strip()
        store.save_json(_TOKEN_STORE_KEY, data)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入 %s 账户 token 失败: %s", platform, exc)
        return False


def list_configured_accounts(context: Any) -> list[dict[str, Any]]:
    store = getattr(context, "local_data_store", None)
    data: dict[str, Any] = {}

    if store is not None:
        try:
            data = store.load_json(_TOKEN_STORE_KEY, {}) or {}
        except Exception:  # noqa: BLE001
            data = {}
    result: list[dict[str, Any]] = []
    if data.get("deepseek"):
        result.append({"platform": "deepseek", "configured": True})
    return result


# ── 同步主体 ────────────────────────────────────────────────────────────


def _build_import_deps(context: Any) -> dict[str, Any]:
    """从 AppContext 取导入所需组件。"""
    return {
        "ai_client": getattr(context, "ai_service", None),
        "memory_extractor": getattr(context, "memory_extractor", None),
        "chat_repo": getattr(context, "chat_session_repository", None),
        "memory_index_service": getattr(context, "memory_index_service", None),
        # 个人知识体系底座：原始条目（带敏感度标签）入库
        "raw_item_repo": getattr(context, "raw_item_repo", None),
    }


def _extract_graph(context: Any, conversations: list[dict[str, Any]]) -> int:
    """主动把抓取的对话写入知识图谱。

    使用 LLM 语义抽取（KnowledgeGraphExtractor._extract_with_llm）获得高质量的
    人物/项目/概念实体与 related_to 关系；单条失败仅记警告，不阻塞整体导入。

    D-5 修复（2026-09-16）：返回值改为**真正新增**的实体数。此前计
    len(res["entities"])，而抽取结果的实体含"复用已有实体"（_add_entity_if_new
    命中同名即返回旧 ID）——图谱 918 实体几乎全被复用时该计数仍虚高，
    上层 out["graph_entities_added"] 严重失真。改为按 store 快照差集计数。
    """
    kg = getattr(context, "knowledge_graph_extractor", None)
    if kg is None:
        return 0
    store = getattr(kg, "store", None)
    if store is None:
        return 0
    new_count = 0
    for conv in conversations:
        text = " ".join(str(m.get("content") or "") for m in (conv.get("messages") or []) if m.get("content")).strip()
        if not text:
            continue
        try:
            before = {str(e.get("id") or "") for e in store.get_all_entities()}
            # 2026-10-15（id 口径专项审计·治本）：出处优先用**本地会话 id**
            # （chat_importer 回填的 local_session_id）——原写平台 uuid 与本地
            # int id 无映射 → 孤儿指针（实测 558 实体详情补不上）。旧数据仍为
            # 平台 uuid，读取侧 resolver 按"纯数字=本地 / uuid=平台 API"分流。
            local_sid = conv.get("local_session_id")
            source_id = f"deepseek:{local_sid if local_sid else (conv.get('id') or conv.get('title') or '')}"
            kg.extract_from_text(
                text[:4000],
                source_id=source_id,
                use_llm=True,
            )
            after = {str(e.get("id") or "") for e in store.get_all_entities()}
            new_count += len(after - before)
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识图谱 LLM 提取失败（非阻塞）: %s", exc)
    return new_count


def refresh_deepseek(
    context: Any,
    *,
    token: str = "",
    max_sessions: int = 30,
    since_hours: float = 0,
    persist_token: bool = False,
) -> dict[str, Any]:
    """抓取 DeepSeek 账户聊天并导入思忆集。

    Args:
        context: AppContext。
        token: 可选，若不传则从本地存储读取已有 token。
        max_sessions: 最多抓取会话数（默认最近 30 个，控制耗时）。
        since_hours: 仅抓最近 N 小时更新的会话；0 = 不限制。
        persist_token: 为 True 时把本次传入的 token 存入本地。

    Returns:
        统计信息 dict，含 imported/sessions_saved/memories_stored/raw_items_stored。
    """
    from integrations.deepseek_client import DeepSeekAccountClient

    resolved_token = (token or "").strip() or get_account_token(context, "deepseek")
    if not resolved_token:
        return {
            "success": False,
            "error": "DeepSeek 账户 token 未配置",
            "hint": "请先通过 ai_chat.set_token 配置 chat.deepseek.com 的登录 token",
        }

    client = DeepSeekAccountClient(resolved_token)
    if persist_token:
        set_account_token(context, "deepseek", resolved_token)

    try:
        progress = {"done": 0, "total": 0}
        conversations = client.fetch_conversations(
            max_sessions=max_sessions,
            since_hours=since_hours or 0,
            progress=lambda done, total: progress.update({"done": done, "total": total}) or True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DeepSeek 抓取失败: %s", exc)
        return {"success": False, "error": f"抓取 DeepSeek 账户失败: {exc}"}

    if not conversations:
        stats = {
            "total": 0,
            "imported": 0,
            "sessions_saved": 0,
            "memories_stored": 0,
            "raw_items_stored": 0,
        }
        return {
            "success": True,
            "stats": stats,
            "conversations_fetched": 0,
            "token_expires_at": client.token_expires_at,
            "message": "未抓取到会话（可能最近无新对话或 token 无效）",
        }

    from integrations.chat_importer import import_conversations

    result = import_conversations(
        conversations,
        **_build_import_deps(context),
    )
    status_success = bool(result.get("success", False))
    out = dict(result)
    out["success"] = True
    out["conversations_fetched"] = len(conversations)
    out["token_expires_at"] = client.token_expires_at
    out["source"] = "deepseek"
    if not status_success:
        out["warning"] = result.get("error", "")
    # 主动写知识图谱（LLM 语义抽取；见 _extract_graph docstring）
    # D-2 同族修复：失败不再只留 warning 日志——写 degraded 标记，
    # 让 cron 历史/通知可见"这一步没做成"，而非整个任务假绿。
    if conversations:
        try:
            out["graph_entities_added"] = _extract_graph(context, conversations)
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识图谱写入失败（非阻塞）: %s", exc)
            out["degraded"] = [*(out.get("degraded") or []), f"graph_extraction: {exc}"]

    # 知识归纳：把对话进一步归纳成原子知识卡片（kernel dsh 优先）
    if conversations:
        try:
            from services.knowledge_synthesis import atomic_cards_from_conversation

            out["knowledge_cards_added"] = sum(len(atomic_cards_from_conversation(context, conv)) for conv in conversations)
        except Exception as exc:  # noqa: BLE001
            logger.warning("原子知识卡片生成失败（非阻塞）: %s", exc)
            out["degraded"] = [*(out.get("degraded") or []), f"card_generation: {exc}"]
    return out
