"""工作台卡片构建与网格布局 — 从 ui_main_window.py 提取的纯 UI 工厂。

本模块只负责：
- 卡片配置查表（标签、颜色、描述文本）
- 密度档位定义
- 卡片容器 / 列表卡片 / 建议卡片 / 总览卡片 / 标题卡片 的 widget 构造
- 网格排布算法（不持有状态，接收参数返回布局放置列表）

不负责：事件处理、数据更新、偏好持久化、对话框打开。
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from ui.theme import set_button_role, set_surface, set_ui_class

logger = logging.getLogger("siyi.workbench.card_renderer")


# ── 卡片配置常量 ─────────────────────────────────────────────

CARD_LABEL_MAP: dict[str, str] = {
    "tasks": "任务",
    "notes": "笔记",
    "news": "新闻",
    "translate": "翻译",
    "inbox": "收件",
    "suggestions": "建议",
    "activity": "动态",
}

CARD_CONFIG_MAP: dict[str, dict[str, str]] = {
    "tasks": {
        "eyebrow": "任务",
        "title": "最近任务",
        "subtitle": "优先看今天刚更新或还未完成的条目。",
        "surface": "warm-accent",
        "button": "打开",
    },
    "notes": {
        "eyebrow": "笔记",
        "title": "最近笔记",
        "subtitle": "继续补充最近沉淀的内容，而不是重新找入口。",
        "surface": "success-accent",
        "button": "打开",
    },
    "news": {
        "eyebrow": "新闻",
        "title": "今日新闻流",
        "subtitle": "首页优先显示当天世界局势、政策与 AI / 互联网动态。",
        "surface": "accent",
        "button": "打开",
    },
    "translate": {
        "eyebrow": "翻译",
        "title": "最近翻译",
        "subtitle": "回到最近查过的表达，继续沉淀到单词本。",
        "surface": "card",
        "button": "打开",
    },
    "inbox": {
        "eyebrow": "收件箱",
        "title": "个人动态收件箱",
        "subtitle": "先收，再整理，再沉淀到私人空间。",
        "surface": "priority-accent",
        "button": "收件",
    },
    "activity": {
        "eyebrow": "动态",
        "title": "最近活动",
        "subtitle": "笔记、任务、同步等操作的最新记录。",
        "surface": "card",
        "button": "查看",
    },
}

CARD_SIZE_SCALE: dict[str, float] = {"small": 0.85, "medium": 1.0, "large": 1.2}

DENSITY_PROFILES: dict[str, dict[str, int]] = {
    "compact": {
        "grid_spacing": 6,
        "default_card_min_height": 152,
        "news_card_min_height": 208,
        "suggestions_card_min_height": 176,
        "list_min_height": 74,
        "news_list_min_height": 84,
        "suggestions_list_min_height": 90,
    },
    "standard": {
        "grid_spacing": 8,
        "default_card_min_height": 168,
        "news_card_min_height": 232,
        "suggestions_card_min_height": 188,
        "list_min_height": 92,
        "news_list_min_height": 92,
        "suggestions_list_min_height": 112,
    },
    "comfortable": {
        "grid_spacing": 10,
        "default_card_min_height": 188,
        "news_card_min_height": 264,
        "suggestions_card_min_height": 212,
        "list_min_height": 104,
        "news_list_min_height": 116,
        "suggestions_list_min_height": 124,
    },
}


# ── 纯函数 ───────────────────────────────────────────────────

def get_card_label(card_key: str) -> str:
    return CARD_LABEL_MAP.get(card_key, card_key)


def get_card_config(action_key: str) -> dict[str, str]:
    return CARD_CONFIG_MAP[action_key]


def get_card_scale_factor(card_key: str, size_overrides: dict[str, str]) -> float:
    size_key = size_overrides.get(card_key, "medium")
    return float(CARD_SIZE_SCALE.get(size_key, 1.0))


def get_density_profile(density_key: str) -> dict[str, int]:
    return DENSITY_PROFILES.get(density_key, DENSITY_PROFILES["standard"])


def compute_grid_placements(
    visible_cards: list[str],
    template: str,
    span_overrides: dict[str, int],
) -> list[tuple[str, int, int, int, int]]:
    """根据模板和跨列配置，计算每张卡片的 (card_key, row, col, row_span, col_span)。"""
    if not visible_cards:
        return []

    placements: list[tuple[str, int, int, int, int]] = []

    if template == "focus" and "news" in visible_cards:
        placements.append(("news", 0, 0, 1, 2))
        remaining = [k for k in visible_cards if k != "news"]
        base_row = 1
    elif template == "daily":
        ordered = []
        for k in ["tasks", "inbox", "news", "notes", "suggestions", "translate"]:
            if k in visible_cards and k not in ordered:
                ordered.append(k)
        for k in visible_cards:
            if k not in ordered:
                ordered.append(k)
        visible_cards = ordered

        if "news" in visible_cards:
            top = [k for k in visible_cards if k in {"tasks", "inbox"}][:2]
            for idx, k in enumerate(top):
                placements.append((k, 0, idx, 1, 1))
            placements.append(("news", 1, 0, 1, 2))
            remaining = [k for k in visible_cards if k not in set(top + ["news"])]
            base_row = 2
        else:
            remaining = list(visible_cards)
            base_row = 0
    else:
        remaining = list(visible_cards)
        base_row = 0

    row, col = base_row, 0
    for card_key in remaining:
        col_span = 2 if int(span_overrides.get(card_key, 1)) == 2 else 1
        if col_span == 2:
            if col != 0:
                row += 1
                col = 0
            placements.append((card_key, row, 0, 1, 2))
            row += 1
            col = 0
            continue
        placements.append((card_key, row, col, 1, 1))
        if col == 0:
            col = 1
        else:
            col = 0
            row += 1

    return placements


# ── Widget 工厂 ──────────────────────────────────────────────

class _CardHoverFilter(QObject):
    """轻量 hover 效果：进入时升高阴影，离开时恢复。"""

    def __init__(self, widget):
        super().__init__(widget)
        widget.installEventFilter(self)
        self._widget = widget
        self._shadow = QGraphicsDropShadowEffect()
        self._shadow.setBlurRadius(12)
        self._shadow.setXOffset(0)
        self._shadow.setYOffset(2)
        self._shadow.setColor(QColor(0, 0, 0, 20))
        self._hovered = False

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.Enter and not self._hovered:
            self._hovered = True
            self._shadow.setBlurRadius(28)
            self._shadow.setYOffset(6)
            self._shadow.setColor(QColor(0, 0, 0, 40))
            self._widget.setGraphicsEffect(self._shadow)
        elif et == QEvent.Type.Leave and self._hovered:
            self._hovered = False
            self._widget.setGraphicsEffect(None)
        return False


def create_card_container(title: str) -> QWidget:
    card = QWidget()
    set_surface(card, "card")
    card.setMinimumHeight(96)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(18, 16, 18, 16)
    card_layout.setSpacing(8)

    title_label = QLabel(title)
    set_ui_class(title_label, "section-title")
    card_layout.addWidget(title_label)
    return card


def create_recent_list(on_item_activated) -> QListWidget:
    recent_list = QListWidget()
    set_ui_class(recent_list, "dashboard-list")
    recent_list.setSpacing(6)
    recent_list.itemActivated.connect(on_item_activated)
    return recent_list


# 别名：main_window_panels 通过 wcr.create_card_list 调用
create_card_list = create_recent_list


def wrap_list_card(list_widget: QListWidget, action_key: str) -> QWidget:
    """包装一个已有的列表控件为卡片。"""
    card, _ = create_list_card(list_widget, action_key, lambda: None)
    return card


def create_list_card(
    list_widget: QListWidget,
    action_key: str,
    on_action_click,
) -> tuple[QWidget, QLabel]:
    """构建一张带标题、副标题、操作按钮和列表的卡片。返回 (card_widget, meta_label)。"""
    config = get_card_config(action_key)
    card = QWidget()
    card.setObjectName(f"wbCard_{action_key}")
    set_surface(card, config["surface"])
    card.setMinimumHeight(168)
    _CardHoverFilter(card)  # hover 阴影升高效果
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(14, 12, 14, 12)
    card_layout.setSpacing(6)

    eyebrow_label = QLabel(config["eyebrow"])
    set_ui_class(eyebrow_label, "eyebrow")
    card_layout.addWidget(eyebrow_label)

    header_row = QHBoxLayout()
    header_row.setSpacing(8)
    title_column = QVBoxLayout()
    title_column.setSpacing(1)

    title_label = QLabel(config["title"])
    set_ui_class(title_label, "recent-card-title")
    title_column.addWidget(title_label)

    subtitle_label = QLabel(config["subtitle"])
    set_ui_class(subtitle_label, "recent-card-subtitle")
    subtitle_label.setWordWrap(True)
    title_column.addWidget(subtitle_label)
    header_row.addLayout(title_column, 1)

    open_button = QPushButton(config["button"])
    set_button_role(open_button, "secondary")
    open_button.setMinimumHeight(24)
    open_button.clicked.connect(on_action_click)
    header_row.addWidget(open_button, 0, Qt.AlignmentFlag.AlignTop)
    card_layout.addLayout(header_row)

    meta_label = QLabel("正在读取最近记录...")
    set_ui_class(meta_label, "list-meta")
    meta_label.setWordWrap(True)
    card_layout.addWidget(meta_label)

    list_widget.setMinimumHeight(92)
    card_layout.addWidget(list_widget)
    return card, meta_label


def create_suggestions_card(
    suggestions_list: QListWidget,
    on_proposal_click,
) -> tuple[QWidget, QLabel]:
    """构建建议卡片。返回 (card_widget, meta_label)。"""
    card = QWidget()
    set_surface(card, "priority-accent")
    card.setMinimumHeight(188)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(14, 12, 14, 12)
    card_layout.setSpacing(8)

    eyebrow_label = QLabel("建议")
    set_ui_class(eyebrow_label, "eyebrow")
    card_layout.addWidget(eyebrow_label)

    header_row = QHBoxLayout()
    header_row.setSpacing(8)
    title_column = QVBoxLayout()
    title_column.setSpacing(1)

    title_label = QLabel("上下文建议")
    set_ui_class(title_label, "recent-card-title")
    title_column.addWidget(title_label)

    subtitle_label = QLabel("结合收件、待办和最近沉淀，给出当前最值得做的动作。")
    set_ui_class(subtitle_label, "recent-card-subtitle")
    subtitle_label.setWordWrap(True)
    title_column.addWidget(subtitle_label)
    header_row.addLayout(title_column, 1)

    action_button = QPushButton("提案")
    set_button_role(action_button, "secondary")
    action_button.setMinimumHeight(24)
    action_button.clicked.connect(on_proposal_click)
    header_row.addWidget(action_button, 0, Qt.AlignmentFlag.AlignTop)
    card_layout.addLayout(header_row)

    meta_label = QLabel("正在整理建议...")
    set_ui_class(meta_label, "list-meta")
    meta_label.setWordWrap(True)
    card_layout.addWidget(meta_label)

    suggestions_list.setMinimumHeight(120)
    suggestions_list.setStyleSheet(
        "QListWidget { background: transparent; border: none; } "
        "QListWidget::item { border: none; padding: 3px 0; }"
    )
    card_layout.addWidget(suggestions_list)
    return card, meta_label


def create_activity_card(
    activity_list: QListWidget,
    on_action_click,
) -> tuple[QWidget, QLabel]:
    """构建活动时间线卡片。返回 (card_widget, meta_label)。"""
    config = get_card_config("activity")
    card = QWidget()
    set_surface(card, config["surface"])
    card.setMinimumHeight(168)
    _CardHoverFilter(card)  # hover 阴影升高效果
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(14, 12, 14, 12)
    card_layout.setSpacing(6)

    eyebrow_label = QLabel(config["eyebrow"])
    set_ui_class(eyebrow_label, "eyebrow")
    card_layout.addWidget(eyebrow_label)

    header_row = QHBoxLayout()
    header_row.setSpacing(8)
    title_column = QVBoxLayout()
    title_column.setSpacing(1)

    title_label = QLabel(config["title"])
    set_ui_class(title_label, "recent-card-title")
    title_column.addWidget(title_label)

    subtitle_label = QLabel(config["subtitle"])
    set_ui_class(subtitle_label, "recent-card-subtitle")
    subtitle_label.setWordWrap(True)
    title_column.addWidget(subtitle_label)
    header_row.addLayout(title_column, 1)

    open_button = QPushButton(config["button"])
    set_button_role(open_button, "secondary")
    open_button.setMinimumHeight(24)
    open_button.clicked.connect(on_action_click)
    header_row.addWidget(open_button, 0, Qt.AlignmentFlag.AlignTop)
    card_layout.addLayout(header_row)

    meta_label = QLabel("最近操作会显示在这里")
    set_ui_class(meta_label, "list-meta")
    meta_label.setWordWrap(True)
    card_layout.addWidget(meta_label)

    activity_list.setMinimumHeight(92)
    card_layout.addWidget(activity_list)
    return card, meta_label


def create_header_card(on_refresh, on_settings) -> tuple[QWidget, QLabel, QPushButton, QPushButton]:
    """构建首页标题卡.返回 (card, status_label, refresh_btn, settings_btn)。"""
    header_card = QWidget()
    header_card.setObjectName("heroCard")
    set_surface(header_card, "hero")
    header_card.setMinimumHeight(148)
    header_layout = QVBoxLayout(header_card)
    header_layout.setContentsMargins(24, 22, 24, 22)
    header_layout.setSpacing(12)

    eyebrow_label = QLabel("助手工作台")
    set_ui_class(eyebrow_label, "eyebrow")
    header_layout.addWidget(eyebrow_label)

    top_row = QHBoxLayout()
    top_row.setSpacing(12)

    title_column = QVBoxLayout()
    title_column.setSpacing(6)
    title_label = QLabel("思忆集")
    set_ui_class(title_label, "page-title")
    title_column.addWidget(title_label)

    subtitle_label = QLabel("把高频信息、主行动和对话入口收束在一个首页里，完整模块入口放到菜单栏。")
    set_ui_class(subtitle_label, "muted")
    subtitle_label.setWordWrap(True)
    title_column.addWidget(subtitle_label)
    top_row.addLayout(title_column, 1)

    action_column = QVBoxLayout()
    action_column.setSpacing(8)
    refresh_btn = QPushButton("刷新概览")
    set_button_role(refresh_btn, "primary")
    refresh_btn.clicked.connect(on_refresh)
    action_column.addWidget(refresh_btn)

    settings_btn = QPushButton("设置")
    set_button_role(settings_btn, "secondary")
    settings_btn.clicked.connect(on_settings)
    action_column.addWidget(settings_btn)
    top_row.addLayout(action_column)
    header_layout.addLayout(top_row)

    status_label = QLabel("正在读取本地助手概览...")
    set_ui_class(status_label, "status-pill")
    status_label.setWordWrap(True)
    header_layout.addWidget(status_label)

    helper_label = QLabel("首页只保留你每天最常看、最常点、最常说的内容。")
    set_ui_class(helper_label, "muted")
    helper_label.setWordWrap(True)
    header_layout.addWidget(helper_label)
    return header_card, status_label, refresh_btn, settings_btn


def create_greeting_bar(
    username: str = "用户",
    on_weather_click=None,
) -> QWidget:
    """构建问候栏：时段问候 + 天气 + 同步状态。"""
    bar = QWidget()
    bar.setObjectName("greetingBar")
    set_surface(bar, "hero")
    bar.setMinimumHeight(56)
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(20, 10, 20, 10)
    layout.setSpacing(16)

    from datetime import datetime
    hour = datetime.now().hour
    if hour < 6:
        greeting = "夜深了"
    elif hour < 9:
        greeting = "早上好"
    elif hour < 12:
        greeting = "上午好"
    elif hour < 14:
        greeting = "中午好"
    elif hour < 18:
        greeting = "下午好"
    else:
        greeting = "晚上好"

    greeting_label = QLabel(f"{greeting}，{username}")
    set_ui_class(greeting_label, "page-title")
    layout.addWidget(greeting_label, 1)

    weather_container = QWidget()
    weather_container.setCursor(Qt.CursorShape.PointingHandCursor)
    weather_layout = QHBoxLayout(weather_container)
    weather_layout.setContentsMargins(0, 0, 0, 0)
    weather_layout.setSpacing(6)

    weather_icon_label = QLabel("☀️")
    set_ui_class(weather_icon_label, "greeting-weather-icon")
    weather_layout.addWidget(weather_icon_label)

    weather_title = QLabel("--掳")
    set_ui_class(weather_title, "greeting-weather-temp")
    weather_layout.addWidget(weather_title)

    weather_subtitle = QLabel("加载中...")
    set_ui_class(weather_subtitle, "greeting-weather-desc")
    weather_layout.addWidget(weather_subtitle)

    if on_weather_click:
        weather_container.mousePressEvent = lambda ev, cb=on_weather_click: cb()  # type: ignore[method-assign, misc]

    layout.addWidget(weather_container)

    return bar


def create_metrics_card(
    label: str,
    value: str,
    accent_color: str,
    on_click=None,
) -> tuple[QWidget, QLabel]:
    """构建单个可点击指标卡片。"""
    card = QWidget()
    card.setCursor(Qt.CursorShape.PointingHandCursor)
    set_surface(card, "card")
    card.setMinimumSize(140, 80)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(2)

    value_label = QLabel(value)
    value_label.setStyleSheet(f"font-size: 26px; font-weight: 700; color: {accent_color};")
    layout.addWidget(value_label)

    label_widget = QLabel(label)
    set_ui_class(label_widget, "muted")
    layout.addWidget(label_widget)

    if on_click:
        card.mousePressEvent = lambda ev, cb=on_click: cb()  # type: ignore[method-assign, misc]

    return card, value_label


def create_metrics_row(
    metrics: list[dict],
) -> QWidget:
    """构建指标行（4 格网格）。

    metrics: [{"key": str, "label": str, "value": str, "color": str, "on_click": callable}, ...]
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    for m in metrics:
        card, _ = create_metrics_card(
            label=m["label"],
            value=m["value"],
            accent_color=m.get("color", "#3b82f6"),
            on_click=m.get("on_click"),
        )
        layout.addWidget(card, 1)

    return row


def apply_grid_placements(
    grid: QGridLayout,
    placements: list[tuple[str, int, int, int, int]],
    cards: dict[str, QWidget],
    card_lists: dict[str, QListWidget],
    density_key: str,
    size_overrides: dict[str, str],
) -> None:
    """将计算好的布局放置应用到实际 QGridLayout。"""
    density = get_density_profile(density_key)
    grid.setHorizontalSpacing(density["grid_spacing"])
    grid.setVerticalSpacing(density["grid_spacing"])

    while grid.count():
        grid.takeAt(0)

    for card_key, row, col, row_span, col_span in placements:
        widget = cards.get(card_key)
        if widget is None:
            continue
        scale = get_card_scale_factor(card_key, size_overrides)
        if card_key == "news":
            base_h = density["news_card_min_height"]
        elif card_key == "suggestions":
            base_h = density["suggestions_card_min_height"]
        else:
            base_h = density["default_card_min_height"]
        widget.setMinimumHeight(max(132, int(base_h * scale)))
        grid.addWidget(widget, row, col, row_span, col_span)

    for card_key, list_widget in card_lists.items():
        scale = get_card_scale_factor(card_key, size_overrides)
        if card_key == "news":
            base_h = density["news_list_min_height"]
        elif card_key == "suggestions":
            base_h = density["suggestions_list_min_height"]
        else:
            base_h = density["list_min_height"]
        list_widget.setMinimumHeight(max(64, int(base_h * scale)))


# ── 工作台设置面板工厂 ─────────────────────────────────────────

_CARD_KEYS = ["tasks", "notes", "news", "translate", "inbox", "suggestions"]

_TEMPLATE_OPTIONS = [("balanced", "均衡"), ("focus", "专注"), ("daily", "日常")]
_DENSITY_OPTIONS = [("compact", "紧凑"), ("standard", "标准"), ("comfortable", "舒展")]
_SECTION_OPTIONS = [("summary", "概览"), ("assistant", "助手"), ("life", "生活角")]
_CARD_OPTIONS = [("tasks", "任务"), ("notes", "笔记"), ("news", "新闻"),
                 ("translate", "翻译"), ("inbox", "收件"), ("suggestions", "建议")]


def build_workbench_controls_panel(
    *,
    on_template_change,
    on_density_change,
    on_section_toggle,
    on_card_toggle,
    on_card_size_cycle,
    on_card_span_cycle,
    on_reset,
    on_save_preset,
    on_apply_preset,
    on_delete_preset,
    on_card_order_moved,
) -> dict[str, Any]:
    """构建工作台布局设置面板，返回所有 widget 引用的字典。

    返回值键:
        panel, template_buttons, density_buttons, section_toggle_buttons,
        card_toggle_buttons, card_size_buttons, card_span_buttons,
        reset_btn, layout_preset_combo, save_preset_btn, apply_preset_btn,
        delete_preset_btn, card_order_list
    """
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    template_buttons: dict[str, QPushButton] = {}
    density_buttons: dict[str, QPushButton] = {}
    section_toggle_buttons: dict[str, QPushButton] = {}
    card_toggle_buttons: dict[str, QPushButton] = {}
    card_size_buttons: dict[str, QPushButton] = {}
    card_span_buttons: dict[str, QPushButton] = {}

    # 模板行
    template_row = QHBoxLayout()
    template_row.setSpacing(8)
    template_row.addWidget(QLabel("模板"))
    for key, label in _TEMPLATE_OPTIONS:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _c, k=key: on_template_change(k))
        template_buttons[key] = btn
        template_row.addWidget(btn)
    reset_btn = QPushButton("恢复默认")
    set_button_role(reset_btn, "secondary")
    reset_btn.clicked.connect(on_reset)
    template_row.addWidget(reset_btn)
    template_row.addStretch(1)
    layout.addLayout(template_row)

    # 密度行
    density_row = QHBoxLayout()
    density_row.setSpacing(8)
    density_row.addWidget(QLabel("密度"))
    for key, label in _DENSITY_OPTIONS:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _c, k=key: on_density_change(k))
        density_buttons[key] = btn
        density_row.addWidget(btn)
    density_row.addStretch(1)
    layout.addLayout(density_row)

    # 区块行
    section_row = QHBoxLayout()
    section_row.setSpacing(8)
    section_row.addWidget(QLabel("首页区块"))
    for key, label in _SECTION_OPTIONS:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.clicked.connect(lambda _c, k=key: on_section_toggle(k))
        section_toggle_buttons[key] = btn
        section_row.addWidget(btn)
    section_row.addStretch(1)
    layout.addLayout(section_row)

    # 卡片行
    card_row = QHBoxLayout()
    card_row.setSpacing(8)
    card_row.addWidget(QLabel("卡片"))
    for key, label in _CARD_OPTIONS:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(True)
        btn.clicked.connect(lambda _c, k=key: on_card_toggle(k))
        card_toggle_buttons[key] = btn
        card_row.addWidget(btn)
    card_row.addStretch(1)
    layout.addLayout(card_row)

    # 尺寸行
    size_row = QHBoxLayout()
    size_row.setSpacing(8)
    size_row.addWidget(QLabel("尺寸"))
    for key in _CARD_KEYS:
        btn = QPushButton()
        btn.clicked.connect(lambda _c=False, k=key: on_card_size_cycle(k))
        card_size_buttons[key] = btn
        size_row.addWidget(btn)
    size_row.addStretch(1)
    layout.addLayout(size_row)

    # 跨列行
    span_row = QHBoxLayout()
    span_row.setSpacing(8)
    span_row.addWidget(QLabel("跨列"))
    for key in _CARD_KEYS:
        btn = QPushButton()
        btn.clicked.connect(lambda _c=False, k=key: on_card_span_cycle(k))
        card_span_buttons[key] = btn
        span_row.addWidget(btn)
    span_row.addStretch(1)
    layout.addLayout(span_row)

    # 布局快照行
    preset_row = QHBoxLayout()
    preset_row.setSpacing(8)
    preset_row.addWidget(QLabel("布局快照"))
    layout_preset_combo = QComboBox()
    layout_preset_combo.setMinimumWidth(200)
    preset_row.addWidget(layout_preset_combo)
    save_preset_btn = QPushButton("保存为快照")
    set_button_role(save_preset_btn, "secondary")
    save_preset_btn.clicked.connect(on_save_preset)
    preset_row.addWidget(save_preset_btn)
    apply_preset_btn = QPushButton("应用")
    set_button_role(apply_preset_btn, "secondary")
    apply_preset_btn.clicked.connect(on_apply_preset)
    preset_row.addWidget(apply_preset_btn)
    delete_preset_btn = QPushButton("删除")
    set_button_role(delete_preset_btn, "secondary")
    delete_preset_btn.clicked.connect(on_delete_preset)
    preset_row.addWidget(delete_preset_btn)
    preset_row.addStretch(1)
    layout.addLayout(preset_row)

    # 卡片排序
    sort_layout = QVBoxLayout()
    sort_layout.setSpacing(6)
    sort_label = QLabel("卡片排序")
    set_ui_class(sort_label, "section-title")
    sort_layout.addWidget(sort_label)
    sort_hint = QLabel("在下方拖动排序，首页会按你的顺序重新排布并自动保存。")
    set_ui_class(sort_hint, "muted")
    sort_hint.setWordWrap(True)
    sort_layout.addWidget(sort_hint)
    card_order_list = QListWidget()
    card_order_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    card_order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
    card_order_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    card_order_list.setMinimumHeight(140)
    set_ui_class(card_order_list, "dashboard-list")
    card_model = card_order_list.model()
    if card_model is not None:
        card_model.rowsMoved.connect(on_card_order_moved)
    sort_layout.addWidget(card_order_list)
    layout.addLayout(sort_layout)

    panel.setVisible(False)

    return {
        "panel": panel,
        "template_buttons": template_buttons,
        "density_buttons": density_buttons,
        "section_toggle_buttons": section_toggle_buttons,
        "card_toggle_buttons": card_toggle_buttons,
        "card_size_buttons": card_size_buttons,
        "card_span_buttons": card_span_buttons,
        "reset_btn": reset_btn,
        "layout_preset_combo": layout_preset_combo,
        "save_preset_btn": save_preset_btn,
        "apply_preset_btn": apply_preset_btn,
        "delete_preset_btn": delete_preset_btn,
        "card_order_list": card_order_list,
    }
