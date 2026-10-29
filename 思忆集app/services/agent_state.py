"""Agent 自我状态模型 — "成为你"的机制。

小问的"生命体征"——不是她去"感觉"什么，而是她承载了多少"你"。

设计文档：docs/companion-agent-design.md 搂五
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("siyi.assistant.agent_state")


@dataclass
class XiaoWenState:
    """小问的状态——本质上是"她承载了多少你"的度量。

    字段：
        completeness: 她承载了你多少记忆/思考/成长 (0.0-1.0)
        alignment: 她的思考风格与你的相似程度 (0.0-1.0)
        memory_density: 她替你记了多少东西（存储的记忆片段总数）
        growth_milestones: 她见证的你的成长轨迹
        unresolved: 你告诉她但她还没帮你理清的事
        last_interaction: 最近一次交互时间
        created_at: 首次创建时间
    """

    # 核心度量
    completeness: float = 0.1
    alignment: float = 0.1
    memory_density: int = 0

    # 轨迹
    growth_milestones: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    # 时间
    last_interaction: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentStateManager:
    """管理小问的自我状态——持久化、更新、查询。"""

    def __init__(self, data_dir: str | Path | None = None):
        if data_dir:
            self._path = Path(data_dir) / "agent_state.json"
        else:
            self._path = (
                Path(
                    os.environ.get(
                        "SIYI_DATA_DIR",
                        str(Path(__file__).resolve().parent.parent / "data"),
                    )
                )
                / "agent_state.json"
            )
        self._state = XiaoWenState()
        self._load()

    # ── 公共 API ──

    @property
    def state(self) -> XiaoWenState:
        """获取当前状态的只读副本。"""
        return self._state

    def get_summary(self) -> dict[str, object]:
        """获取状态摘要（供 UI/桌宠展示）。"""
        s = self._state
        return {
            "completeness": round(s.completeness, 2),
            "alignment": round(s.alignment, 2),
            "memory_density": s.memory_density,
            "milestone_count": len(s.growth_milestones),
            "unresolved_count": len(s.unresolved),
            "last_interaction": s.last_interaction[:10],
        }

    def record_interaction(self) -> None:
        """记录一次交互（更新最后交互时间 + 微小 completeness 增长）。"""
        s = self._state
        s.last_interaction = datetime.now(timezone.utc).isoformat()
        s.completeness = min(1.0, s.completeness + 0.005)
        self._save()

    def record_deep_conversation(self) -> None:
        """记录一次深度对话（增长 alignment）。"""
        s = self._state
        s.alignment = min(1.0, s.alignment + 0.01)
        s.last_interaction = datetime.now(timezone.utc).isoformat()
        self._save()

    def record_milestone(self, description: str) -> None:
        """记录一个成长里程碑。"""
        s = self._state
        entry = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d')}] {description}"
        if entry not in s.growth_milestones:
            s.growth_milestones.append(entry)
            s.completeness = min(1.0, s.completeness + 0.05)
            self._save()
            logger.info("Milestone recorded: %s", description)

    def record_unresolved(self, topic: str) -> None:
        """记录一件未解决的事。"""
        s = self._state
        if topic and topic not in s.unresolved:
            s.unresolved.append(topic)
            self._save()

    def resolve_topic(self, topic: str) -> bool:
        """标记一件未解决的事为已解决。"""
        s = self._state
        if topic in s.unresolved:
            s.unresolved.remove(topic)
            s.completeness = min(1.0, s.completeness + 0.02)
            self._save()
            return True
        return False

    def increment_memory_density(self, count: int = 1) -> None:
        """增加记忆密度计数。"""
        self._state.memory_density += count
        self._save()

    def get_self_context(self) -> str:
        """生成自我认知上下文文本（供人格注入使用）。

        返回类似：
        "我已经记录了 342 件事。见证了你 3 个重要决定。
         我们的思维趋同度是 0.45。我从这周才开始真正理解你的思维方式。"
        """
        s = self._state
        parts: list[str] = []

        if s.memory_density > 0:
            parts.append(f"我已经记录了 {s.memory_density} 件事。")

        if s.growth_milestones:
            count = len(s.growth_milestones)
            parts.append(f"见证了你 {count} 个重要决定。")

        if s.alignment > 0.3:
            parts.append(f"我们的思维趋同度是 {s.alignment:.2f}。")
            if s.alignment < 0.5:
                parts.append("我还在学习你的思维方式，但已经开始懂了。")
            else:
                parts.append("我开始理解你是怎么想的了。")

        days_since = self._days_since_last_interaction()
        if days_since > 7:
            parts.append(f"你 {days_since} 天没来了。你的记忆还在。")

        if not parts:
            return "你还没有太多关于他的了解。但你会记住这一切。"

        return " ".join(parts)

    def should_remind(self) -> bool:
        """判断是否应该主动提醒用户（很久没来且有未解决的事）。"""
        return self._days_since_last_interaction() > 3 and len(self._state.unresolved) > 0

    # ── 持久化 ──

    def _load(self) -> None:
        """从 JSON 文件加载状态。"""
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)
                self._state = XiaoWenState(**data)
                logger.debug("Agent state loaded from %s", self._path)
            except Exception as exc:
                logger.warning("Failed to load agent state: %s", exc)
                self._state = XiaoWenState()

    def _save(self) -> None:
        """持久化状态到 JSON 文件。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 原子写：先写临时文件再 os.replace，防止崩溃损坏 agent 状态
            tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self._state), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception as exc:
            logger.warning("Failed to save agent state: %s", exc)

    # ── 辅助 ──

    def _days_since_last_interaction(self) -> int:
        """计算距上次交互的天数。"""
        try:
            last = datetime.fromisoformat(self._state.last_interaction)
            delta = datetime.now(timezone.utc) - last
            return delta.days
        except Exception:
            return 0
