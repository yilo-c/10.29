"""MilestoneDetector — 从记忆/笔记中检测里程碑事件。

里程碑定义：
- 第一次对话
- 重要决定（考研、换工作、搬家等）
- 长期目标设定
- 重大情绪转折
- 首次使用的功能
"""

from __future__ import annotations

import json as _json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("siyi.memory.narrative.milestone")

# ── 泛词治理（2026-09-16，AI-2 案）────────────────────────────────
# 旧版把 30 个中文泛词（决定/计划/通过/重要/关键…）放进同一张表，任何一次
# 子串命中即判里程碑，导致产品内置 demo 笔记被"通过/关键"命中后连续霸榜周报。
# 现将词表分级：
#   - 强词：本身即代表人生/项目节点，单独命中即可算里程碑；
#   - 弱泛词：日常高频，单独命中不足以判里程碑，必须与"数字/感叹"这类更强
#     的情绪/量化证据共现才计（且命中多个弱词本身不叠加为里程碑）。
_STRONG_KEYWORDS: list[str] = [
    "第一次",
    "初次",
    "首次",
    "考研",
    "入职",
    "辞职",
    "搬家",
    "转行",
    "跳槽",
    "毕业",
    "上岸",
    "上线",
    "求婚",
    "结婚",
    "离婚",
    "出生",
    "离世",
    "去世",
    "终于",
    "转折",
    "milestone",
    "breakthrough",
    "first time",
]

_WEAK_KEYWORDS: list[str] = [
    "决定",
    "打算",
    "计划",
    "目标",
    "开始",
    "新的",
    "工作",
    "通过",
    "成功",
    "完成",
    "完成了",
    "改变",
    "重要",
    "关键",
    "重大",
    "declare",
    "decide",
    "plan",
    "goal",
    "begin",
    "start",
    "important",
]

# 向后兼容：保留旧名指向合并后的全集（外部若有引用不至于 ImportError）。
_MILESTONE_KEYWORDS: list[str] = _STRONG_KEYWORDS + _WEAK_KEYWORDS

# 弱词升级为里程碑所需的"更强证据"：阿拉伯数字或中英文感叹号。
_EVIDENCE_RE = re.compile(r"[0-9０-９!！]")

# ── demo/种子来源排除（2026-09-16，AI-2 案）────────────────────────
# 产品内置示例笔记（如《📖 思忆便签使用指南》）不是用户记忆，绝不能进里程碑选材。
# 当前笔记 dict 结构上没有 is_example / source 标记，暂以「📖 前缀 + 已知内置标题」
# 兜底识别；详见下方 _is_demo_entry 的 TODO。
_DEMO_TITLE_PREFIXES: tuple[str, ...] = ("📖",)
_DEMO_TITLES: frozenset[str] = frozenset(
    {
        "思忆便签使用指南",
        "📖 思忆便签使用指南",
    }
)


def _is_demo_entry(entry: dict[str, Any]) -> bool:
    """判断一条记忆/笔记是否为产品内置 demo（示例笔记）。

    TODO(数据面/S4): 更干净的做法是给所有 demo 笔记在落库时打
    frontmatter / 字段标记（如 ``is_example: true`` 或 ``source: "builtin_demo"``），
    选材处直接按标记过滤。在字段标记补齐前，这里按「📖 标题前缀 + 已知内置标题」
    启发式排除，避免内置帮助文档被当成用户里程碑。
    """
    if not isinstance(entry, dict):
        return False
    # 优先信任显式标记（若上游已补齐则直接生效）
    if entry.get("is_example") or entry.get("is_demo"):
        return True
    src = entry.get("source") or entry.get("source_type")
    if isinstance(src, str) and src.lower() in {"builtin", "builtin_demo", "demo", "example", "seed"}:
        return True
    title = str(entry.get("title", "") or "").strip()
    if not title:
        return False
    if title in _DEMO_TITLES:
        return True
    for prefix in _DEMO_TITLE_PREFIXES:
        if title.startswith(prefix):
            # 「📖」既是内置指南前缀，也可能被用户误用；仅当命中内置标题清单才排除，
            # 单纯的 📖 前缀不足以判定为 demo——保守放行给用户真实笔记。
            core = title.lstrip("📖 \t")
            if core in _DEMO_TITLES or "使用指南" in core or "快速上手" in core:
                return True
    return False


def _summarize_preview(text: str, max_len: int = 80) -> str:
    """取标题级摘要（≤max_len 字），禁止把原文直接截断塞进周报正文。

    优先取第一非空行并剥离 markdown 标题/列表符号；无换行时按句首取。
    """
    if not text:
        return ""
    # 归一化：逐行找第一条有内容的行
    first_line = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if not first_line:
        first_line = text.strip()
    # 剥离 markdown 结构符号（#、>、-、*、数字列表、**加粗标记）
    cleaned = re.sub(r"^#{1,6}\s*", "", first_line)
    cleaned = re.sub(r"^>\s*", "", cleaned)
    cleaned = re.sub(r"^[-*+]\s+", "", cleaned)
    cleaned = re.sub(r"^\d+[.)、]\s+", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("`", "").strip()
    if not cleaned:
        cleaned = first_line
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


# ── LLM 判评（2026-09-16，S3-1 / AI-2 案，token 成本已拍板授权）────────
# 弱泛词命中（即使带数字/感叹证据）只算「候选」，最终是否里程碑交给一次
# 批量 LLM 判评：周候选 ≤50 条、单次 flash 调用，打分 ≥0.8 才入选。
# 强词命中不送判评（词本身即人生节点，省 token 也避免模型误杀）。
_JUDGE_THRESHOLD = 0.8
_JUDGE_MAX_CANDIDATES = 50
_JUDGE_SNIPPET_CHARS = 220
_JUDGE_SYSTEM_PROMPT = (
    "你在帮一个个人记忆产品挑选「这一周值得记住的事」。"
    "判断每条候选是否真的是用户人生的节点事件（里程碑）：第一次做成某事、"
    "重要决定、长期坚持达成、重大情绪转折、人生阶段变化等。"
    "普通日程记录、待办、购物清单、工作流水账、帮助文档式的泛泛描述都不算。"
    '只输出 JSON：{"scores":[{"index":<整数>,"score":<0到1小数>}]}，'
    "index 对应输入编号，score 表示它是里程碑的置信度。"
)

# 判评不可用（未配置 AI / 调用失败）时对弱词候选的处置：
#   drop = 宁缺毋滥（默认，符合本项「泛词塌方」治理目标）
#   keep = 维持共现即入选的旧行为，留给想放宽的部署
_JUDGE_UNAVAILABLE_POLICY = "drop"


def _has_strong_keyword(matched: list[str]) -> bool:
    """判评分流用：命中集里是否含强词（_find_milestone_keywords 命中强词时
    只返回强词，返回集含弱词即等价于「纯弱词」）。"""
    strong_lower = {k.lower() for k in _STRONG_KEYWORDS}
    return any(k.lower() in strong_lower for k in matched)


def _judge_candidates_via_llm(ai_text_service: Any, snippets: list[str]) -> list[float] | None:
    """一次批量 LLM 判评 → 与 snippets 等长的分数列表；不可用/失败返回 None。

    走 AiTextService.complete(json_mode=True)（壳内轻量 AI，契约 I3：
    单轮、无工具调用）。模型漏答的 index 视为 0 分。
    """
    if ai_text_service is None or not snippets:
        return None
    lines = [f"{i}. {s}" for i, s in enumerate(snippets)]
    user_prompt = "候选列表：\n" + "\n".join(lines)
    try:
        resp = ai_text_service.complete(
            user_prompt=user_prompt,
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=900,
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 — 判评失败绝不让周报生成崩（I6 降级不崩溃）
        logger.debug("milestone LLM 判评调用失败: %s", exc)
        return None
    payload = resp
    if isinstance(payload, str):
        raw = payload.strip()
        if raw.startswith("```"):
            raw = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("```")).strip()
        try:
            payload = _json.loads(raw)
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    scores_raw = payload.get("scores")
    if not isinstance(scores_raw, list):
        return None
    out: list[float] = [0.0] * len(snippets)
    seen_any = False
    for item in scores_raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item["index"])
            val = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < len(out):
            out[idx] = max(0.0, min(1.0, val))
            seen_any = True
    return out if seen_any else None


class MilestoneDetector:
    """从现有记忆/笔记中检测里程碑事件。"""

    def __init__(self, memory_service=None, note_repository=None, ai_text_service=None):
        self._memory_service = memory_service
        self._note_repository = note_repository
        # S3-1：弱词候选的批量 LLM 判评端口（None = 不判评，按 policy 处置）。
        self._ai_text_service = ai_text_service

    def detect_from_memories(self, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从记忆列表中检测里程碑。

        治理点（2026-09-16，AI-2 案 / S3-1）：
        1. 排除产品内置 demo/示例笔记；
        2. 泛词分级：强词直接入选；弱词只算候选，须经一次批量 LLM 判评
           打分 ≥0.8 才入选（判评不可用时按 _JUDGE_UNAVAILABLE_POLICY）；
        3. preview 改为标题级摘要（≤80 字），不再截断原文塞正文。
        """
        milestones: list[dict[str, Any]] = []
        weak_candidates: list[dict[str, Any]] = []
        for mem in memories:
            if _is_demo_entry(mem):
                continue
            content = _get_text(mem)
            if not content:
                continue
            # 证据/关键词扫描同时看标题与正文，避免量化信息只写在标题里时漏判。
            scan_text = f"{mem.get('title', '') or ''}\n{content}"
            matched = _find_milestone_keywords(scan_text)
            if not matched:
                continue
            entry: dict[str, Any] = {
                "type": "memory_milestone",
                "source_id": mem.get("id", ""),
                "date": mem.get("created_at", ""),
                "matched_keywords": matched,
                "preview": _summarize_preview(content),
            }
            if _has_strong_keyword(matched):
                milestones.append(entry)
            else:
                # 纯弱词命中 → 候选，送批量判评（判评文本=标题+正文摘要）
                entry["_scan_text"] = scan_text[:_JUDGE_SNIPPET_CHARS]
                weak_candidates.append(entry)

        milestones.extend(self._judge_weak_candidates(weak_candidates))
        return milestones

    def _judge_weak_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """弱词候选批量判评；返回入选项（剔除内部 _scan_text 键）。"""
        if not candidates:
            return []
        batch = candidates[:_JUDGE_MAX_CANDIDATES]
        if len(candidates) > _JUDGE_MAX_CANDIDATES:
            logger.debug("里程碑弱词候选 %d 条超上限，仅判评前 %d 条", len(candidates), _JUDGE_MAX_CANDIDATES)
        snippets = [str(c.get("_scan_text") or "") for c in batch]
        scores = _judge_candidates_via_llm(self._ai_text_service, snippets)
        if scores is None:
            if _JUDGE_UNAVAILABLE_POLICY == "keep":
                survivors = batch
            else:
                logger.debug("里程碑判评不可用 → 丢弃 %d 条弱词候选（宁缺毋滥）", len(batch))
                survivors = []
        else:
            survivors = [c for c, s in zip(batch, scores) if s >= _JUDGE_THRESHOLD]
        for c in survivors:
            c.pop("_scan_text", None)
        return survivors

    async def scan_recent(self, days: int = 30) -> list[dict[str, Any]]:
        """扫描近期记忆和笔记中的里程碑。

        Args:
            days: 回溯天数

        Returns:
            检测到的里程碑列表
        """
        milestones: list[dict[str, Any]] = []
        since = _days_ago(days)

        # 从 memory_service 获取近期记忆
        if self._memory_service:
            try:
                recent = await self._memory_service.search_by_date(since)
                milestones.extend(self.detect_from_memories(recent))
            except Exception as exc:
                logger.debug("Failed to scan memories for milestones: %s", exc)

        # 从 note_repository 获取近期笔记
        if self._note_repository:
            try:
                notes = await self._note_repository.list_recent(limit=100)
                milestones.extend(self.detect_from_memories(notes))
            except Exception as exc:
                logger.debug("Failed to scan notes for milestones: %s", exc)

        # 去重、排序
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for m in milestones:
            key = m.get("source_id", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(m)
        unique.sort(key=lambda x: x.get("date", ""), reverse=True)
        return unique

    @staticmethod
    def is_milestone_text(text: str) -> bool:
        """判断文本是否包含里程碑信号。"""
        if not text:
            return False
        return bool(_find_milestone_keywords(text))


def _get_text(entry: dict[str, Any]) -> str:
    """从记忆/笔记条目中提取文本内容。"""
    return str(entry.get("content", entry.get("text", entry.get("summary", ""))))


def _find_milestone_keywords(text: str) -> list[str]:
    """分级查找里程碑关键词（2026-09-16，AI-2 案）。

    规则：
    - 命中任一强词 → 直接判里程碑，返回强词；
    - 仅命中弱泛词时：要求与"数字/感叹"共现（更强证据），否则返回空。
      多个弱词共现**不**叠加为里程碑——旧版正是"通过+关键"两弱词命中即霸榜。

    返回：命中并升级为里程碑的关键词列表；空列表表示不构成里程碑。
    """
    if not text:
        return []
    lower = text.lower()
    strong_hit = [kw for kw in _STRONG_KEYWORDS if kw.lower() in lower]
    if strong_hit:
        return strong_hit
    weak_hit = [kw for kw in _WEAK_KEYWORDS if kw.lower() in lower]
    if weak_hit and _EVIDENCE_RE.search(text):
        return weak_hit
    return []


def _days_ago(days: int) -> str:
    """返回 N 天前的 ISO 日期字符串。"""
    from datetime import timedelta

    d = datetime.now(timezone.utc) - timedelta(days=days)
    return d.isoformat()
