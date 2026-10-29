"""个人画像提取器 — 个人知识体系 M2。

从导入的原始信息条目（raw items，微信/AI 聊天/浏览器/剪贴板…）提取：
- 联系人（人物关系）：规则收集消息发送人（meta.sender）
- 兴趣/关注主题：AI 提取（JSON）→ 规则兜底（"我喜欢X" 模式）
- 最近焦点：活跃话题词频
- 重要事件：AI 提取（可选）

写回 UserProfileService（focus_topics / key_people / recent_events /
current_focus），并把处理过的 raw items 标记 processed。

隐私原则：提取全部在本地进行；AI 提取走现有 client 降级链
（DeepSeek 欠费自动回退本地 Ollama），未来本地 AI 部署后可完全离线。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger("siyi.services.personal_profile_extractor")

# 联系人黑名单（系统账号/非真人）
_CONTACT_BLACKLIST = {
    "我", "自己", "系统", "群聊", "文件传输助手", "微信团队", "微信支付",
    "服务通知", "订阅号", "腾讯新闻", "公众号",
}

# 停用词（兴趣词过滤）
_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "这", "那", "和", "与",
    "也", "都", "就", "在", "有", "没", "不", "很", "太", "真", "还", "要",
    "会", "能", "可以", "一个", "什么", "怎么", "为什么", "然后", "但是",
}

# 兴趣句式：喜欢/热爱/关注/感兴趣/常看 …后抓名词短语
_INTEREST_RE = re.compile(
    r"(?:喜欢|热爱|感兴趣|关注|常看|爱看|偏好|沉迷|最近在学|最近也在学|最近在看|在学|在看)"
    r"[^，。！？\n]{0,10}?([\u4e00-\u9fa5A-Za-z0-9]{2,12})"
)

# AI 提取 prompt（要求输出 JSON）
_EXTRACT_SYSTEM_PROMPT = (
    "你是个人画像提取器。从用户的内容中提取用户的兴趣与关注主题"
    "（简短名词短语，如 爬山、AI编程、投资理财、健身），最多 8 个。"
    "只输出 JSON 数组，不要任何其他文字。例如 [\"爬山\",\"编程\"]"
)


class PersonalProfileExtractor:
    """个人画像提取器（规则 + AI 双路径）。"""

    def __init__(
        self,
        raw_item_repo: Any,
        user_profile_service: Any,
        ai_client: Any = None,
    ) -> None:
        self.raw_item_repo = raw_item_repo
        self.user_profile_service = user_profile_service
        self.ai_client = ai_client

    # ── 主入口 ─────────────────────────────────────────────

    def extract(self, limit: int = 200) -> dict[str, Any]:
        """扫描未处理的 raw items，提取画像并写回。

        Returns:
            {scanned, contacts_added, interests_added, focus_set,
             processed, error?}
        """
        items = self.raw_item_repo.list(processed=0, limit=limit)
        if not items:
            return {"scanned": 0, "contacts_added": 0, "interests_added": 0,
                    "focus_set": "", "processed": 0}

        texts = [str(it.get("content") or "") for it in items]

        # 1. 联系人（规则，来自消息发送人）
        contacts = self._collect_contacts(items)

        # 2. 兴趣（AI → 规则兜底）
        interests = self._extract_interests(texts)

        # 3. 最近焦点（词频最高的兴趣词）
        focus = interests[0] if interests else self._guess_focus(texts)

        # 4. 写回画像
        profile = self.user_profile_service.load()
        if contacts:
            self.user_profile_service.merge_key_people(contacts)
        if interests:
            self.user_profile_service.merge_focus_topics(interests)
        if focus:
            self.user_profile_service.set_current_focus(focus)

        # 5. 标记已处理
        processed = self.raw_item_repo.mark_processed_many([it["id"] for it in items])

        return {
            "scanned": len(items),
            "contacts_added": len(contacts),
            "interests_added": len(interests),
            "focus_set": focus,
            "processed": processed,
        }

    # ── 联系人 ─────────────────────────────────────────────

    def _collect_contacts(self, items: list[dict[str, Any]]) -> list[str]:
        seen: list[str] = []
        for it in items:
            meta = it.get("meta") or {}
            sender = str(meta.get("sender") or "").strip()
            if not sender or sender in _CONTACT_BLACKLIST:
                continue
            if sender not in seen:
                seen.append(sender)
        return seen[:30]

    # ── 兴趣 ───────────────────────────────────────────────

    def _extract_interests(self, texts: list[str]) -> list[str]:
        joined = "\n".join(t for t in texts if t)[:6000]
        if not joined:
            return []

        # AI 路径（JSON 数组）
        ai_topics = self._extract_interests_via_ai(joined)
        if ai_topics:
            return ai_topics

        # 规则兜底
        return self._extract_interests_by_rule(joined)

    def _extract_interests_via_ai(self, text: str) -> list[str]:
        if self.ai_client is None:
            return []
        try:
            response = self.ai_client.chat_completion(
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": text[:4000]},
                ],
                temperature=0.2,
                max_tokens=300,
            )
            raw = response
            if isinstance(raw, dict):
                raw = raw.get("content", raw.get("text", ""))
            if not isinstance(raw, str):
                return []
            # 提取 JSON 数组（容忍 markdown 代码块/前后缀）
            m = re.search(r"\[.*\]", raw, re.S)
            if not m:
                return []
            data = json.loads(m.group(0))
            if not isinstance(data, list):
                return []
            cleaned = []
            for t in data:
                s = str(t).strip().strip("\"'")
                if s and s not in _STOPWORDS and s not in cleaned:
                    cleaned.append(s)
            return cleaned[:8]
        except Exception as exc:
            logger.debug("AI 兴趣提取失败，走规则兜底: %s", exc)
            return []

    def _extract_interests_by_rule(self, text: str) -> list[str]:
        found: list[str] = []
        for m in _INTEREST_RE.finditer(text):
            word = m.group(1).strip()
            if not word or word in _STOPWORDS or len(word) < 2:
                continue
            if word not in found:
                found.append(word)
        return found[:8]

    # ── 最近焦点 ───────────────────────────────────────────

    def _guess_focus(self, texts: list[str]) -> str:
        """无 AI/规则兴趣时的兜底：2-4 字高频词。"""
        counter: Counter[str] = Counter()
        for t in texts:
            for seg in re.findall(r"[\u4e00-\u9fa5]{2,4}", t):
                if seg not in _STOPWORDS:
                    counter[seg] += 1
        if not counter:
            return ""
        # 取频次 > 2 的最高词，避免单次噪音
        for word, cnt in counter.most_common(20):
            if cnt >= 2:
                return word
        return counter.most_common(1)[0][0] if counter else ""
