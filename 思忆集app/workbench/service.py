from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PersonalWorkbenchService:
    """围绕收件、整理、建议和监督式自优化的本地工作台服务。"""

    DEFAULT_PRIVATE_SPACE_ROOT = r"D:\BaiduSyncdisk\个人\思忆集\忆集\日记"
    PRIVATE_SPACE_ROOT_SETTING_KEY = "private_space_root_dir"
    PRIVATE_SPACE_SUBDIRS = ("inbox", "daily", "insights")

    def __init__(self, settings_repository, note_repository, inbox_repository, proposal_repository):
        self.settings_repository = settings_repository
        self.note_repository = note_repository
        self.inbox_repository = inbox_repository
        self.proposal_repository = proposal_repository

    def get_private_space_root(self):
        if self.settings_repository is None:
            return self.DEFAULT_PRIVATE_SPACE_ROOT
        configured = str(
            self.settings_repository.get_setting(self.PRIVATE_SPACE_ROOT_SETTING_KEY, self.DEFAULT_PRIVATE_SPACE_ROOT)
            or self.DEFAULT_PRIVATE_SPACE_ROOT
        ).strip()
        return configured or self.DEFAULT_PRIVATE_SPACE_ROOT

    def capture_quick_entry(self, text, source="manual", title=None, tags=None, metadata=None):
        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            raise ValueError("没有可收件的内容。")
        resolved_title = str(title or "").strip() or self._infer_title(cleaned_text)
        inbox_file_path = self._write_private_space_inbox_capture(resolved_title, cleaned_text)
        entry_id = self.inbox_repository.add_entry(
            {
                "title": resolved_title,
                "content": cleaned_text,
                "source": source,
                "status": "new",
                "tags": list(tags or []),
                "metadata": {
                    **dict(metadata or {}),
                    "inbox_file_path": inbox_file_path,
                },
                "inbox_file_path": inbox_file_path,
            }
        )
        return self.inbox_repository.get_entry(entry_id) or {"id": entry_id, "title": resolved_title}

    def summarize_current_content(self, snapshot, current_text=""):
        digest = self.build_current_digest(snapshot, current_text=current_text)
        lines = [digest["headline"], ""]
        lines.extend(f"- {item}" for item in digest["bullets"])
        if digest["current_text_summary"]:
            lines.extend(["", "当前输入", digest["current_text_summary"]])
        if digest["next_actions"]:
            lines.extend(["", "建议下一步"])
            lines.extend(f"- {item}" for item in digest["next_actions"])
        return "\n".join(lines).strip()

    def build_context_suggestions(self, snapshot):
        suggestions = []
        pending_tasks = snapshot.get("pending_tasks") or []
        inbox_entries = snapshot.get("inbox_entries") or []
        notes = snapshot.get("notes") or []
        translations = snapshot.get("translations") or []
        favorite_news = snapshot.get("favorite_news_keys") or []
        proposals = snapshot.get("improvement_proposals") or []

        if inbox_entries:
            suggestions.append(
                {
                    "text": f"收件箱里还有 {len(inbox_entries)} 条未整理内容，先做一次快速归档。",
                    "action": "organize_current",
                    "reason": f"收件箱有 {len(inbox_entries)} 条未整理，已接近或超过整理节奏",
                    "convertible_actions": [
                        {"label": "转笔记", "action": "notes"},
                        {"label": "整理归档", "action": "organize_current"},
                    ],
                }
            )
        else:
            suggestions.append(
                {
                    "text": "今天还没有新收件，先把当前想法收进收件箱。",
                    "action": "capture",
                    "reason": "收件箱为空，适合主动收一条当前输入",
                    "convertible_actions": [
                        {"label": "快速收件", "action": "capture"},
                    ],
                }
            )

        if pending_tasks:
            suggestions.append(
                {
                    "text": f"当前有 {len(pending_tasks)} 个待办，优先清掉最影响注意力的一项。",
                    "action": "tasks",
                    "reason": f"待办 {len(pending_tasks)} 项未完成，注意力容易被切碎",
                    "convertible_actions": [
                        {"label": "打开待办", "action": "tasks"},
                    ],
                }
            )
        if notes:
            suggestions.append(
                {
                    "text": "最近已经有笔记沉淀，适合把一条收件内容整理成正式笔记。",
                    "action": "notes",
                    "reason": f"已有 {len(notes)} 条笔记沉淀，适合继续扩展",
                    "convertible_actions": [
                        {"label": "打开笔记", "action": "notes"},
                        {"label": "新建笔记", "action": "notes"},
                    ],
                }
            )
        else:
            suggestions.append(
                {
                    "text": "笔记区还比较空，建议先沉淀一条今日工作笔记。",
                    "action": "notes",
                    "reason": "笔记区尚无沉淀，建议从今日输入开始",
                    "convertible_actions": [
                        {"label": "新建笔记", "action": "notes"},
                    ],
                }
            )

        if favorite_news:
            suggestions.append(
                {
                    "text": "新闻收藏已经形成素材池，可以抽一条转成自己的总结。",
                    "action": "news",
                    "reason": f"已收藏 {len(favorite_news)} 条新闻素材",
                    "convertible_actions": [
                        {"label": "打开新闻", "action": "news"},
                        {"label": "转笔记", "action": "notes"},
                    ],
                }
            )
        if translations:
            suggestions.append(
                {
                    "text": "最近翻译历史可继续沉淀到单词本，减少重复查询。",
                    "action": "translate",
                    "reason": f"有 {len(translations)} 条翻译记录可沉淀",
                    "convertible_actions": [
                        {"label": "打开翻译", "action": "translate"},
                    ],
                }
            )
        suggestions.append(
            {
                "text": "打开悬浮助手，形成最小闭环：问一句、收一条、整理一下。",
                "action": "floating",
                "reason": "悬浮助手指尖可达，适合快速操作",
                "convertible_actions": [
                    {"label": "打开悬浮", "action": "floating"},
                ],
            }
        )
        if proposals and proposals[0].get("status") == "pending_review":
            suggestions.append(
                {
                    "text": "已有待审阅自优化提案，先判断是否值得采纳。",
                    "action": "self_optimize",
                    "reason": "提案待审，需人工判断是否采纳",
                    "convertible_actions": [
                        {"label": "审阅提案", "action": "self_optimize"},
                    ],
                }
            )
        return suggestions[:6]

    def create_self_optimization_proposal(self, snapshot):
        digest = self.build_current_digest(snapshot)
        title = f"自优化提案 {datetime.now().strftime('%m-%d %H:%M')}"
        content_lines = [
            "当前阻塞",
            *[f"- {item}" for item in digest["frictions"]],
            "",
            "建议动作",
            *[f"- {item}" for item in digest["next_actions"]],
            "",
            "执行边界",
            "- 仅生成提案，不自动改代码、不自动改配置。",
            "- 需要你人工确认后再执行。",
        ]
        proposal_id = self.proposal_repository.add_proposal(
            {
                "title": title,
                "content": "\n".join(content_lines).strip(),
                "status": "pending_review",
                "proposal_file_path": self._write_private_space_insight(title, "\n".join(content_lines).strip()),
            }
        )
        return self.proposal_repository.get_proposal(proposal_id) or {"id": proposal_id, "title": title}

    # ── 三层流转 ──────────────────────────────────────────────

    def move_to_daily(self, entry_id, entry_title=None, entry_content=None) -> dict[str, Any]:
        """将收件箱条目从 inbox 层转移到 daily 层。

        执行：
        - 将 entry 的状态标记为 organized，layer 改为 daily
        - 写入 private space daily 子目录
        - 更新 inbox repository 中的条目记录

        Returns:
            {"success": True, "file_path": ..., "title": ...}
            or {"success": False, "error": ...}
        """
        if self.inbox_repository is None:
            return {"success": False, "error": "收件箱仓储未就绪"}

        entry = self.inbox_repository.get_entry(entry_id) if entry_id else None
        if entry is None and not (entry_title and entry_content):
            return {"success": False, "error": "未找到对应条目，且未提供回退内容"}

        title = entry_title or str((entry or {}).get("title") or "每日整理").strip()
        content = entry_content or str((entry or {}).get("content") or "").strip()

        folder_path = self._ensure_private_space_daily_dir()
        file_name = self._build_file_name(f"daily-{title}")
        file_path = os.path.join(folder_path, file_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        markdown = f"# {title}\n\n> 转入 daily 时间：{timestamp}\n\n{content}\n"
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(markdown)
        except OSError as exc:
            logger.warning("Failed to write daily file: %s", exc)
            return {"success": False, "error": f"写入 daily 文件夹失败: {exc}"}

        if entry_id is not None:
            from domain.inbox_models import LAYER_DAILY
            self.inbox_repository.update_entry(entry_id, {
                "status": "organized",
                "layer": LAYER_DAILY,
                "daily_at": datetime.now(timezone.utc).isoformat(),
                "daily_file_path": file_path,
            })

        return {"success": True, "file_path": file_path, "title": title}

    def move_to_insights(self, entry_id, entry_title=None, entry_content=None) -> dict[str, Any]:
        """将收件箱条目从 daily 层转移到 insights 层。

        执行：
        - 将 entry 的状态标记为 organized，layer 改为 insights
        - 写入 private space insights 子目录
        - 更新 inbox repository 中的条目记录

        Returns:
            {"success": True, "file_path": ..., "title": ...}
            or {"success": False, "error": ...}
        """
        if self.inbox_repository is None:
            return {"success": False, "error": "收件箱仓储未就绪"}

        entry = self.inbox_repository.get_entry(entry_id) if entry_id else None
        if entry is None and not (entry_title and entry_content):
            return {"success": False, "error": "未找到对应条目，且未提供回退内容"}

        title = entry_title or str((entry or {}).get("title") or "深度沉淀").strip()
        content = entry_content or str((entry or {}).get("content") or "").strip()

        folder_path = self._ensure_private_space_insights_dir()
        file_name = self._build_file_name(f"insight-{title}")
        file_path = os.path.join(folder_path, file_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        markdown = f"# {title}\n\n> 转入 insights 时间：{timestamp}\n\n{content}\n"
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(markdown)
        except OSError as exc:
            logger.warning("Failed to write insights file: %s", exc)
            return {"success": False, "error": f"写入 insights 文件夹失败: {exc}"}

        if entry_id is not None:
            from domain.inbox_models import LAYER_INSIGHTS
            self.inbox_repository.update_entry(entry_id, {
                "status": "organized",
                "layer": LAYER_INSIGHTS,
                "insights_at": datetime.now(timezone.utc).isoformat(),
                "insights_file_path": file_path,
            })

        return {"success": True, "file_path": file_path, "title": title}

    def organize_current_context(self, snapshot, current_text="", entry_id=None):
        digest = self.build_current_digest(snapshot, current_text=current_text)
        organized_title = digest["organize_title"]
        folder_path = self._ensure_private_space_daily_dir()
        file_name = self._build_file_name(organized_title)
        file_path = os.path.join(folder_path, file_name)
        markdown = self._build_markdown_document(digest)
        try:
            with open(file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(markdown)
        except OSError as exc:
            logger.warning("Failed to write daily organize file: %s", exc)
            file_path = ""

        note_id = None
        if self.note_repository is not None:
            note_id = self.note_repository.add_note(
                {
                    "title": organized_title,
                    "content": markdown,
                    "category": "私人空间整理",
                    "tags": ["整理", "私人空间"],
                    "folder_path": "/私人空间",
                }
            )

        if entry_id is not None and self.inbox_repository is not None:
            self.inbox_repository.update_entry(
                entry_id,
                {
                    "status": "organized",
                    "organized_at": datetime.now(timezone.utc).isoformat(),
                    "organized_file_path": file_path,
                    "linked_note_id": note_id,
                },
            )

        return {
            "title": organized_title,
            "file_path": file_path,
            "note_id": note_id,
            "summary": digest["headline"],
        }

    def build_current_digest(self, snapshot, current_text=""):
        pending_tasks = snapshot.get("pending_tasks") or []
        notes = snapshot.get("notes") or []
        news_records = snapshot.get("news_records") or []
        translations = snapshot.get("translations") or []
        inbox_entries = snapshot.get("inbox_entries") or []
        proposals = snapshot.get("improvement_proposals") or []
        cleaned_text = str(current_text or "").strip()
        headline = f"当前工作台：待办 {len(pending_tasks)}，收件 {len(inbox_entries)}，笔记 {len(notes)}。"
        bullets = [
            f"待办任务 {len(pending_tasks)} 个，未完成项仍是主线。",
            f"动态收件箱 {len(inbox_entries)} 条，适合先把零散输入收束。",
            f"本地笔记 {len(notes)} 条，已经有一定沉淀基础。",
            f"新闻记录 {len(news_records)} 条，翻译记录 {len(translations)} 条。",
        ]
        if proposals:
            bullets.append(f"最近自优化提案 {len(proposals)} 条，最新状态 {proposals[0].get('status', 'unknown')}。")

        frictions = []
        if len(inbox_entries) >= 3:
            frictions.append("收件输入开始堆积，说明整理节奏慢于输入节奏。")
        if len(pending_tasks) >= 5:
            frictions.append("待办数量偏多，注意力容易被切碎。")
        if cleaned_text and len(cleaned_text) >= 120:
            frictions.append("当前输入内容较长，适合立即收件并生成摘要。")
        if not frictions:
            frictions.append("当前压力总体可控，重点是保持收件后及时整理。")

        next_actions = []
        if cleaned_text:
            next_actions.append("先把当前输入收进动态收件箱，再决定是否整理成私人空间文档。")
        if inbox_entries:
            next_actions.append("从收件箱挑一条最重要的输入，整理成正式沉淀。")
        if pending_tasks:
            next_actions.append("关闭一个最重要的待办，避免整理动作变成逃避任务。")
        else:
            next_actions.append("趁主线压力不高，把今天的输入整理成一条完整笔记。")
        next_actions.append("需要随手记录时，优先使用悬浮助手，而不是另开流程。")

        current_text_summary = ""
        organize_title = datetime.now().strftime("%Y-%m-%d 工作整理")
        if cleaned_text:
            current_text_summary = self._truncate(cleaned_text.replace("\n", " "), 180)
            organize_title = self._infer_title(cleaned_text)

        return {
            "headline": headline,
            "bullets": bullets,
            "frictions": frictions,
            "next_actions": next_actions,
            "current_text_summary": current_text_summary,
            "organize_title": organize_title,
            "snapshot": snapshot,
        }

    def _build_markdown_document(self, digest):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"# {digest['organize_title']}",
            "",
            f"> 生成时间：{timestamp}",
            "",
            "## 当前概览",
            digest["headline"],
            "",
            "## 要点",
            *[f"- {item}" for item in digest["bullets"]],
            "",
            "## 当前阻力",
            *[f"- {item}" for item in digest["frictions"]],
            "",
            "## 下一步",
            *[f"- {item}" for item in digest["next_actions"]],
        ]
        if digest["current_text_summary"]:
            lines.extend(["", "## 当前输入摘要", digest["current_text_summary"]])
        return "\n".join(lines).strip() + "\n"

    def _ensure_private_space_layout(self):
        root_dir = self.get_private_space_root()
        os.makedirs(root_dir, exist_ok=True)
        for directory_name in self.PRIVATE_SPACE_SUBDIRS:
            os.makedirs(os.path.join(root_dir, directory_name), exist_ok=True)
        return root_dir

    def _ensure_private_space_daily_dir(self):
        root_dir = self._ensure_private_space_layout()
        dated_dir = os.path.join(root_dir, "daily", datetime.now().strftime("%Y-%m"))
        os.makedirs(dated_dir, exist_ok=True)
        return dated_dir

    def _ensure_private_space_inbox_dir(self):
        root_dir = self._ensure_private_space_layout()
        dated_dir = os.path.join(root_dir, "inbox", datetime.now().strftime("%Y-%m"))
        os.makedirs(dated_dir, exist_ok=True)
        return dated_dir

    def _ensure_private_space_insights_dir(self):
        root_dir = self._ensure_private_space_layout()
        dated_dir = os.path.join(root_dir, "insights", datetime.now().strftime("%Y-%m"))
        os.makedirs(dated_dir, exist_ok=True)
        return dated_dir

    def _write_private_space_inbox_capture(self, title, text):
        try:
            inbox_dir = self._ensure_private_space_inbox_dir()
        except OSError as exc:
            logger.warning("Failed to create inbox dir: %s", exc)
            return ""
        file_name = self._build_file_name(f"inbox-{title}")
        file_path = os.path.join(inbox_dir, file_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        payload = f"# {title}\n\n> 收件时间：{timestamp}\n\n{text}\n"
        try:
            with open(file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(payload)
        except OSError as exc:
            logger.warning("Failed to write inbox capture: %s", exc)
            return ""
        return file_path

    def _write_private_space_insight(self, title, content):
        try:
            insights_dir = self._ensure_private_space_insights_dir()
        except OSError as exc:
            logger.warning("Failed to create insights dir: %s", exc)
            return ""
        file_name = self._build_file_name(f"insight-{title}")
        file_path = os.path.join(insights_dir, file_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        payload = f"# {title}\n\n> 生成时间：{timestamp}\n\n{content}\n"
        try:
            with open(file_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(payload)
        except OSError as exc:
            logger.warning("Failed to write insight: %s", exc)
            return ""
        return file_path

    def _build_file_name(self, title):
        safe_title = re.sub(r"[\\/:*?\"<>|]+", "-", str(title or "整理记录")).strip(" .-") or "整理记录"
        return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_title[:48]}.md"

    def _infer_title(self, text):
        first_line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
        if not first_line:
            return datetime.now().strftime("%Y-%m-%d 收件")
        return self._truncate(first_line, 36)

    def _truncate(self, text, limit):
        cleaned = str(text or "").strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 3)] + "..."

# ─────────────────────────────────────────────────────────
# WorkbenchLayoutService — pure layout state management
# ─────────────────────────────────────────────────────────

WORKBENCH_DEFAULT_CARD_ORDER = [
    "news", "tasks", "notes", "activity", "inbox", "suggestions", "translate",
]
_WORKBENCH_TEMPLATES = ("balanced", "focus", "daily", "custom")
_WORKBENCH_DENSITIES = ("compact", "standard", "comfortable")


class WorkbenchLayoutService:
    """工作台布局状态管理 — 纯数据服务，无 Qt 依赖。

    Responsibilities:
      - 持有运行时布局状态（模板、密度、卡片可见性等）
      - 从 settings_repository 加载/持久化状态
      - 提供状态查询和修改方法
    """

    def __init__(self, settings_repository: Any | None = None):
        self.settings_repository = settings_repository
        self.active_template: str = "focus"
        self.density: str = "standard"
        self.section_visibility: dict[str, bool] = {
            "summary": False, "assistant": True, "life": False,
        }
        self.card_visibility: dict[str, bool] = {
            "tasks": True, "notes": True, "news": True,
            "translate": True, "inbox": True, "suggestions": True,
            "activity": True,
        }
        self.card_order: list[str] = list(WORKBENCH_DEFAULT_CARD_ORDER)
        self.card_size_overrides: dict[str, str] = {}
        self.card_span_overrides: dict[str, int] = {}
        self.layout_presets: dict[str, Any] = {}
        self.default_layout_preset: str = ""
        self.today_view_enabled: bool = False

    # ── Load / Persist ────────────────────────────────────

    def load_from_repository(self) -> bool:
        """从 settings_repository 加载布局配置。返回是否成功。"""
        if self.settings_repository is None:
            return False
        from .layout_config import (
            get_workbench_layout_config,
            migrate_workbench_layout_config,
        )
        migrate_workbench_layout_config(self.settings_repository)
        config = get_workbench_layout_config(self.settings_repository)

        template = str(config.get("template") or "balanced")
        self.active_template = template if template in _WORKBENCH_TEMPLATES else "balanced"

        density = str(config.get("density") or "standard")
        self.density = density if density in _WORKBENCH_DENSITIES else "standard"

        self.section_visibility = dict(
            config.get("section_visibility") or self.section_visibility
        )
        self.card_visibility = dict(
            config.get("card_visibility") or self.card_visibility
        )
        self.card_order = list(
            config.get("card_order") or WORKBENCH_DEFAULT_CARD_ORDER
        )
        self.card_size_overrides = dict(config.get("card_sizes") or {})
        self.card_span_overrides = dict(config.get("card_spans") or {})
        self.layout_presets = dict(config.get("layout_presets") or {})
        self.default_layout_preset = str(config.get("default_layout_preset") or "")
        self.today_view_enabled = bool(config.get("today_view", False))

        # 对话区始终可见
        self.section_visibility["assistant"] = True
        return True

    def persist_to_repository(self) -> bool:
        """将当前布局状态持久化到 settings_repository。"""
        if self.settings_repository is None:
            return False
        from .layout_config import save_workbench_layout_config

        save_workbench_layout_config(
            self.settings_repository,
            {
                "template": self.active_template,
                "density": self.density,
                "section_visibility": dict(self.section_visibility),
                "card_visibility": dict(self.card_visibility),
                "card_order": list(self.card_order),
                "card_sizes": dict(self.card_size_overrides),
                "card_spans": dict(self.card_span_overrides),
                "layout_presets": dict(self.layout_presets),
                "default_layout_preset": self.default_layout_preset,
                "today_view": self.today_view_enabled,
            },
            sync_legacy=True,
        )
        return True

    # ── Query ─────────────────────────────────────────────

    def get_visible_card_keys(self) -> list[str]:
        """按 card_order 返回当前可见的卡片键列表。"""
        return [
            k for k in self.card_order
            if self.card_visibility.get(k, True)
        ]

    def collect_current_state(self) -> dict[str, Any]:
        """收集当前布局状态字典（用于快照保存）。"""
        return {
            "template": self.active_template,
            "density": self.density,
            "section_visibility": dict(self.section_visibility),
            "card_visibility": dict(self.card_visibility),
            "card_order": list(self.card_order),
            "card_sizes": dict(self.card_size_overrides),
            "card_spans": dict(self.card_span_overrides),
            "today_view": self.today_view_enabled,
        }

    # ── Mutation ──────────────────────────────────────────

    def set_template(self, template_key: str) -> bool:
        if template_key not in _WORKBENCH_TEMPLATES:
            return False
        self.active_template = template_key
        template_map = {
            "balanced": {"summary": True, "assistant": True, "life": True},
            "focus": {"summary": True, "assistant": True, "life": False},
            "daily": {"summary": True, "assistant": True, "life": True},
            "custom": {"summary": True, "assistant": True, "life": False},
        }
        self.section_visibility.update(template_map.get(template_key, template_map["balanced"]))
        return True

    def set_density(self, density_key: str) -> bool:
        if density_key not in _WORKBENCH_DENSITIES:
            return False
        self.density = density_key
        return True

    def toggle_card_visibility(self, card_key: str) -> bool:
        if card_key not in self.card_visibility:
            return False
        self.card_visibility[card_key] = not self.card_visibility[card_key]
        return True

    def toggle_section_visibility(self, section_key: str) -> bool:
        if section_key not in self.section_visibility:
            return False
        self.section_visibility[section_key] = not self.section_visibility[section_key]
        self.active_template = "custom"
        return True

    def cycle_card_size(self, card_key: str) -> bool:
        if card_key not in self.card_visibility:
            return False
        seq = ["small", "medium", "large"]
        cur = self.card_size_overrides.get(card_key, "medium")
        try:
            nxt = (seq.index(cur) + 1) % len(seq)
        except ValueError:
            nxt = 1
        self.card_size_overrides[card_key] = seq[nxt]
        return True

    def cycle_card_span(self, card_key: str) -> bool:
        if card_key not in self.card_visibility:
            return False
        cur = int(self.card_span_overrides.get(card_key, 1))
        self.card_span_overrides[card_key] = 2 if cur == 1 else 1
        return True

    def set_card_order(self, new_order: list[str]) -> None:
        cleaned = []
        for k in new_order:
            if k in self.card_visibility and k not in cleaned:
                cleaned.append(k)
        for k in WORKBENCH_DEFAULT_CARD_ORDER:
            if k not in cleaned:
                cleaned.append(k)
        self.card_order = cleaned

    def toggle_today_view(self) -> None:
        self.today_view_enabled = not self.today_view_enabled
        if self.today_view_enabled:
            prioritized = ["tasks", "inbox", "suggestions"]
            new_order: list[str] = []
            for ck in prioritized:
                if ck not in new_order:
                    new_order.append(ck)
                self.card_visibility[ck] = True
            for ck in self.card_order:
                if ck not in new_order:
                    new_order.append(ck)
            for ck in WORKBENCH_DEFAULT_CARD_ORDER:
                if ck not in new_order:
                    new_order.append(ck)
            self.card_order = new_order

    def reset_to_defaults(self) -> None:
        from .layout_config import DEFAULT_CARD_VISIBILITY, DEFAULT_SECTION_VISIBILITY
        self.active_template = "balanced"
        self.density = "standard"
        self.section_visibility = dict(DEFAULT_SECTION_VISIBILITY)
        self.card_visibility = dict(DEFAULT_CARD_VISIBILITY)
        self.card_order = list(WORKBENCH_DEFAULT_CARD_ORDER)
        self.card_size_overrides = {}
        self.card_span_overrides = {}
        self.layout_presets = {}
        self.default_layout_preset = ""
        self.today_view_enabled = False

    # ── Preset management ─────────────────────────────────

    def save_layout_preset(self, name: str) -> bool:
        name = str(name or "").strip()
        if not name:
            return False
        self.layout_presets[name] = self.collect_current_state()
        self.default_layout_preset = name
        return True

    def apply_layout_preset(self, name: str) -> bool:
        payload = self.layout_presets.get(name)
        if not isinstance(payload, dict):
            return False
        template = str(payload.get("template") or "").strip()
        if template in _WORKBENCH_TEMPLATES:
            self.active_template = template
        density = str(payload.get("density") or "").strip()
        if density in _WORKBENCH_DENSITIES:
            self.density = density
        sv = payload.get("section_visibility")
        if isinstance(sv, dict):
            for k in self.section_visibility:
                if k in sv:
                    self.section_visibility[k] = bool(sv[k])
        cv = payload.get("card_visibility")
        if isinstance(cv, dict):
            for k in self.card_visibility:
                if k in cv:
                    self.card_visibility[k] = bool(cv[k])
        co = payload.get("card_order")
        if isinstance(co, list):
            self.set_card_order(co)
        sizes = payload.get("card_sizes")
        if isinstance(sizes, dict):
            self.card_size_overrides = {
                str(k).strip(): str(v).strip()
                for k, v in sizes.items()
                if str(k).strip() in self.card_visibility
                and str(v).strip() in {"small", "medium", "large"}
            }
        spans = payload.get("card_spans")
        if isinstance(spans, dict):
            new_spans: dict[str, int] = {}
            for k, v in spans.items():
                nk = str(k).strip()
                try:
                    nv = int(v)
                except (TypeError, ValueError):
                    continue
                if nk in self.card_visibility and nv in {1, 2}:
                    new_spans[nk] = nv
            self.card_span_overrides = new_spans
        self.today_view_enabled = bool(payload.get("today_view", False))
        return True

    def delete_layout_preset(self, name: str) -> bool:
        name = str(name or "").strip()
        if not name or name not in self.layout_presets:
            return False
        self.layout_presets.pop(name, None)
        if self.default_layout_preset == name:
            self.default_layout_preset = ""
        return True
