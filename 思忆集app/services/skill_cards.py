"""services/skill_cards.py — 内核技能卡加载器（M4 技能卡；对齐 DSH 技能形态）。

技能卡是 cron → 执行内核 的预置任务定义：``data/skills/<id>.md``，front-matter（YAML）
+ 正文。cron_actions 注册表读取技能卡，组装 prompt 经 ``gateway.submit`` 直走 dsh。

与 butler 侧 B4 SkillRegistry 共享同一批 ``data/skills/<id>.md`` 文件形态，但本模块是
内核执行路径（cron 触发），不注入 system prompt。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("siyi.skill_cards")

SKILL_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"

# 成本档 → dsh profile（与 routing._PROFILE_DEFAULTS 同源；技能卡走固定映射，
# 不读 settings，避免与 chat 路径的成本分级耦合）。
KERNEL_TO_DSH_PROFILE: dict[str, str] = {
    "fast": "siyi-fast",
    "deep": "siyi-deep",
}
DEFAULT_DSH_PROFILE = "siyi-fast"


@dataclass
class SkillCard:
    skill_id: str
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.skill_id)

    @property
    def kernel(self) -> str:
        return str(self.meta.get("kernel") or "fast").strip().lower()

    def resolve_profile(self) -> str:
        """技能卡声明的成本档 → 实际 dsh profile。"""
        return KERNEL_TO_DSH_PROFILE.get(self.kernel, DEFAULT_DSH_PROFILE)


def list_skill_cards() -> list[str]:
    if not SKILL_DIR.is_dir():
        return []
    return sorted(p.stem for p in SKILL_DIR.glob("*.md"))


def load_skill_card(skill_id: str) -> "SkillCard | None":
    """读取并解析一张技能卡；不存在返回 None。"""
    path = SKILL_DIR / f"{skill_id}.md"
    if not path.is_file():
        logger.warning("skill card not found: %s", skill_id)
        return None
    text = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(text)
    return SkillCard(skill_id=skill_id, meta=meta, body=body.strip())


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 `---\\n<yaml>\\n---` front-matter 与正文。"""
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    # parts: ["", "<front-matter>", "<body>"]
    if len(parts) < 3:
        return {}, text.strip()
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        logger.warning("skill card front-matter parse failed: %s", exc)
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, parts[2].strip()


def render_skill_prompt(card: "SkillCard", payload: dict[str, Any] | None = None) -> str:
    """组装给 dsh 内核的一次性任务 prompt。"""
    payload = payload or {}
    lines: list[str] = [
        "你是思忆集的执行内核（DSH）。请按下列技能卡完成任务，只调用其声明的工具。",
        "",
        f"# 技能：{card.name}",
    ]
    desc = card.meta.get("description")
    if desc:
        lines.append(f"描述：{desc}")
    tools = card.meta.get("tools")
    if isinstance(tools, list):
        lines.append("可用工具：" + ", ".join(str(t) for t in tools))
    lines.append(f"成本档：{card.kernel}（映射到 dsh profile {card.resolve_profile()}）")
    lines.append("")
    if card.body:
        lines.append(card.body)

    ctx = payload.get("context") or payload.get("task") or ""
    if ctx:
        lines.append("")
        lines.append("## 本次任务上下文")
        lines.append(str(ctx))
    extra = {k: v for k, v in payload.items() if k not in ("context", "task", "action_id", "target")}
    if extra:
        lines.append("")
        lines.append("## 额外参数")
        for k, v in extra.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)
