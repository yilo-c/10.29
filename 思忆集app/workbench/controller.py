"""
工作台控制器模块 (WorkbenchController)

浠?ui_main_window.py 一?袙耦出的工作台布局、卡鐗囩?鐞嗐?佸亸好持久化、模板切换等逻辑。?

用法 (鍦?MainWindow.__init__ 一?:
    from .controller import WorkbenchController
    self.workbench_controller = WorkbenchController(self)
    self.workbench_controller.load_preferences()  # 替代 _load_workbench_preferences()
    self.workbench_controller.apply_visibility()   # 替代 _apply_workbench_visibility()
"""
from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from . import card_renderer as wcr
from core.observability import SEVERITY_DEGRADED, SEVERITY_INFO
from plugins.protocol import CardPluginSpec, DataSourcePluginSpec, QuickActionPluginSpec
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QWidget,
)
from PyQt6.QtGui import QCursor
from .service import WorkbenchLayoutService

logger = logging.getLogger("siyi.workbench_controller")


class WorkbenchController:
    """Workbench controller for managing workbench state."""

    WORKBENCH_REBUILD_TARGET_MS = 120

    def __init__(self, main_window):
        """
        Args:
            main_window: 主窗鍙?(MainWindow) 实例。控制器通过 main_window 访问
                         UI 组件鍜?app_context。?
        """
        self._mw = main_window
        self.logger = logging.getLogger("siyi.workbench_controller")

        # 鈹?鈹? 布局鐘舵?佹湇鍔?鈹?鈹?
        repo = getattr(getattr(main_window, "app_context", None), "settings_repository", None)
        self._layout = WorkbenchLayoutService(settings_repository=repo)

    # 鈹?鈹? 灞炴?褑浆发（保持鍏煎?性） 鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?鈹?

    @property
    def active_workbench_template(self) -> str:
        return self._layout.active_template

    @active_workbench_template.setter
    def active_workbench_template(self, value: str) -> None:
        self._layout.active_template = value

    @property
    def workbench_density(self) -> str:
        return self._layout.density

    @workbench_density.setter
    def workbench_density(self, value: str) -> None:
        self._layout.density = value

    @property
    def card_order(self) -> list[str]:
        return self._layout.card_order

    @card_order.setter
    def card_order(self, value: list[str]) -> None:
        self._layout.card_order = value

    @property
    def card_size_overrides(self) -> dict[str, str]:
        return self._layout.card_size_overrides

    @card_size_overrides.setter
    def card_size_overrides(self, value: dict[str, str]) -> None:
        self._layout.card_size_overrides = value

    @property
    def card_span_overrides(self) -> dict[str, int]:
        return self._layout.card_span_overrides

    @card_span_overrides.setter
    def card_span_overrides(self, value: dict[str, int]) -> None:
        self._layout.card_span_overrides = value

    @property
    def section_visibility(self) -> dict[str, bool]:
        return self._layout.section_visibility

    @section_visibility.setter
    def section_visibility(self, value: dict[str, bool]) -> None:
        self._layout.section_visibility = value

    @property
    def card_visibility(self) -> dict[str, bool]:
        return self._layout.card_visibility

    @card_visibility.setter
    def card_visibility(self, value: dict[str, bool]) -> None:
        self._layout.card_visibility = value

    @property
    def layout_presets(self) -> dict[str, Any]:
        return self._layout.layout_presets

    @layout_presets.setter
    def layout_presets(self, value: dict[str, Any]) -> None:
        self._layout.layout_presets = value

    @property
    def default_layout_preset(self) -> str:
        return self._layout.default_layout_preset

    @default_layout_preset.setter
    def default_layout_preset(self, value: str) -> None:
        self._layout.default_layout_preset = value

    @property
    def today_work_view_enabled(self) -> bool:
        return self._layout.today_view_enabled

    @today_work_view_enabled.setter
    def today_work_view_enabled(self, value: bool) -> None:
        self._layout.today_view_enabled = value

    # ══════════════════════════════════════════════════════鈺?
    # 1. 偏好加载 / 持久鍖?
    # ══════════════════════════════════════════════════════鈺?

    def load_preferences(self) -> None:
        """浠?settings_repository 加载工作台偏好，刷新鎵?有控浠躲?"""
        mw = self._mw
        # 加载性能历史（仍鍦?MainWindow 上）
        repo = mw._get_settings_repository()
        if repo is not None:
            mw._load_performance_history(repo)

        self._layout.load_from_repository()

        # 如果设置了默认快照，鎭㈠?瀹?
        if self.default_layout_preset and self.default_layout_preset in self.layout_presets:
            self._layout.apply_layout_preset(self.default_layout_preset)
            self.persist_preferences()

        # 对话区永远可瑙?
        self.section_visibility["assistant"] = True

        # 刷新鎵?鏈?UI 控件
        self._refresh_workbench_controls()
        self._refresh_layout_preset_combo()
        self._populate_card_order_list()
        self._apply_card_visibility()
        self._set_today_view_button_state()
        self._apply_today_work_view_style()

    def persist_preferences(self) -> None:
        """持久化当前工作台鎵?有配缃??"""
        mw = self._mw
        repo = mw._get_settings_repository()
        if repo is None:
            return
        mw._save_performance_history(repo)
        self._layout.persist_to_repository()

    def reload_preferences(self) -> bool:
        """重新加载偏好并刷鏂?UI锛堝?外接口）。"""
        self.load_preferences()
        self._layout.section_visibility["assistant"] = True
        self._apply_workbench_visibility()
        self._apply_card_visibility()
        self._set_today_view_button_state()
        self._apply_today_work_view_style()
        self._refresh_layout_preset_combo()
        return True

    # ══════════════════════════════════════════════════════鈺?
    # 2. 控件刷新
    # ══════════════════════════════════════════════════════鈺?

    def _refresh_workbench_controls(self) -> None:
        """刷新模板、密搴︺?佸崱片尺寸和区域鍙??性按閽?殑鐘舵?併?"""
        mw = self._mw
        from ui.theme import set_button_role

        for tkey, btn in mw.template_buttons.items():
            active = tkey == self.active_workbench_template
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            set_button_role(btn, "primary" if active else "secondary")

        for dkey, btn in mw.density_buttons.items():
            active = dkey == self.workbench_density
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            set_button_role(btn, "primary" if active else "secondary")

        self._refresh_card_size_buttons()
        self._refresh_card_span_buttons()

        for skey, btn in mw.section_toggle_buttons.items():
            visible = self.section_visibility.get(skey, True)
            btn.blockSignals(True)
            btn.setChecked(visible)
            btn.blockSignals(False)
            set_button_role(btn, "primary" if visible else "secondary")

    def _refresh_card_size_buttons(self) -> None:
        mw = self._mw
        from ui.theme import set_button_role
        if not getattr(mw, "card_size_buttons", None):
            return
        label_map = {"small": "灏?", "medium": "一?", "large": "澶?"}
        for ckey, btn in mw.card_size_buttons.items():
            size = self.card_size_overrides.get(ckey, "medium")
            btn.setText(f"{wcr.get_card_label(ckey)}:{label_map.get(size, '一?')}")
            set_button_role(btn, "primary" if size != "medium" else "secondary")

    def _refresh_card_span_buttons(self) -> None:
        mw = self._mw
        from ui.theme import set_button_role
        if not getattr(mw, "card_span_buttons", None):
            return
        for ckey, btn in mw.card_span_buttons.items():
            span = int(self.card_span_overrides.get(ckey, 1))
            label = "双列" if span == 2 else "单列"
            btn.setText(f"{wcr.get_card_label(ckey)}:{label}")
            set_button_role(btn, "primary" if span == 2 else "secondary")

    def _refresh_layout_preset_combo(self) -> None:
        mw = self._mw
        if not hasattr(mw, "layout_preset_combo"):
            return
        mw.layout_preset_combo.blockSignals(True)
        mw.layout_preset_combo.clear()
        names = sorted(self.layout_presets.keys())
        if not names:
            mw.layout_preset_combo.addItem("(暂无蹇?収)")
        else:
            for name in names:
                suffix = " (榛樿?)" if name == self.default_layout_preset else ""
                mw.layout_preset_combo.addItem(f"{name}{suffix}", name)
            target = self.default_layout_preset
            for i in range(mw.layout_preset_combo.count()):
                if mw.layout_preset_combo.itemData(i) == target:
                    mw.layout_preset_combo.setCurrentIndex(i)
                    break
        mw.layout_preset_combo.blockSignals(False)

    # ══════════════════════════════════════════════════════鈺?
    # 3. 布局棰勮?蹇?収
    # ══════════════════════════════════════════════════════鈺?

    def _save_current_layout_preset(self) -> None:
        mw = self._mw
        name, ok = QInputDialog.getText(mw, "保存布局蹇?収", "请输入快照名称：")
        if not ok or not str(name).strip():
            return
        name = str(name).strip()
        if not self._layout.save_layout_preset(name):
            return
        self._refresh_layout_preset_combo()
        self.persist_preferences()
        QMessageBox.information(mw, "成功", f"布局蹇?収已保存：{name}")

    def _apply_selected_layout_preset(self) -> None:
        mw = self._mw
        combo = getattr(mw, "layout_preset_combo", None)
        if combo is None:
            return
        selected = str(combo.currentData() or "").strip()
        if not selected:
            QMessageBox.warning(mw, "提示", "请先选择一?一?彲用快鐓?")
            return
        self._apply_layout_preset(selected, persist=True)

    def _delete_selected_layout_preset(self) -> None:
        mw = self._mw
        combo = getattr(mw, "layout_preset_combo", None)
        if combo is None:
            return
        selected = str(combo.currentData() or "").strip()
        if not selected:
            return
        self._layout.delete_layout_preset(selected)
        self._refresh_layout_preset_combo()
        self.persist_preferences()

    def _apply_layout_preset(self, preset_name: str, persist: bool = True) -> None:
        mw = self._mw
        if not self._layout.apply_layout_preset(preset_name):
            return
        self._apply_workbench_visibility()
        self._apply_card_visibility()
        self._set_today_view_button_state()
        self._apply_today_work_view_style()

        if persist:
            self.default_layout_preset = preset_name
            self.persist_preferences()

        mw._track_event("layout.preset_applied", SEVERITY_INFO, {"preset": preset_name})
        self._refresh_layout_preset_combo()

    def apply_workbench_layout_preset(self, preset_name: str, persist: bool = True) -> bool:
        """瀵瑰?：应用指定名称的布局蹇?収。"""
        name = str(preset_name or "").strip()
        if not name or name not in self.layout_presets:
            return False
        self._apply_layout_preset(name, persist=persist)
        return True

    # ══════════════════════════════════════════════════════鈺?
    # 4. 模板 / 密度 / 鍙??性切鎹?
    # ══════════════════════════════════════════════════════鈺?

    def _apply_workbench_visibility(self) -> None:
        mw = self._mw
        if hasattr(mw, "summary_section"):
            mw.summary_section.setVisible(False)
        if hasattr(mw, "input_section"):
            mw.input_section.setVisible(True)
        if hasattr(mw, "life_section"):
            mw.life_section.setVisible(False)
        self._refresh_workbench_controls()

    def set_workbench_template(self, template_key: str) -> None:
        """切换工作台模鏉裤?"""
        if not self._layout.set_template(template_key):
            return
        self._apply_workbench_visibility()
        self.persist_preferences()

    def set_workbench_density(self, density_key: str) -> None:
        if not self._layout.set_density(density_key):
            return
        self._refresh_workbench_controls()
        self._rebuild_workbench_grid()
        self.persist_preferences()

    def toggle_section_visibility(self, section_key: str) -> None:
        self._layout.toggle_section_visibility(section_key)
        self._apply_workbench_visibility()
        self.persist_preferences()

    def toggle_card_visibility(self, card_key: str) -> None:
        self._layout.toggle_card_visibility(card_key)
        self._apply_card_visibility()
        self.persist_preferences()

    def _apply_card_visibility(self) -> None:
        mw = self._mw
        from ui.theme import set_button_role

        for ckey, cw in getattr(mw, "workbench_cards", {}).items():
            if cw is not None:
                cw.setVisible(self.card_visibility.get(ckey, True))
        for ckey, btn in getattr(mw, "card_toggle_buttons", {}).items():
            visible = self.card_visibility.get(ckey, True)
            btn.blockSignals(True)
            btn.setChecked(visible)
            btn.blockSignals(False)
            set_button_role(btn, "primary" if visible else "secondary")

        self._rebuild_workbench_grid()
        self._populate_card_order_list()

    # ══════════════════════════════════════════════════════鈺?
    # 5. 卡片灏哄? / 跨度
    # ══════════════════════════════════════════════════════鈺?

    def cycle_card_size_override(self, card_key: str) -> None:
        self._layout.cycle_card_size(card_key)
        self._refresh_workbench_controls()
        self._rebuild_workbench_grid()
        self.persist_preferences()

    def cycle_card_span_override(self, card_key: str) -> None:
        self._layout.cycle_card_span(card_key)
        self._refresh_workbench_controls()
        self._rebuild_workbench_grid()
        self.persist_preferences()

    def get_card_scale_factor(self, card_key: str) -> float:
        return wcr.get_card_scale_factor(card_key, self.card_size_overrides)

    # ══════════════════════════════════════════════════════鈺?
    # 6. 卡片排序
    # ══════════════════════════════════════════════════════鈺?

    def _populate_card_order_list(self) -> None:
        mw = self._mw
        if not hasattr(mw, "card_order_list"):
            return
        mw.card_order_list.blockSignals(True)
        mw.card_order_list.clear()
        for ckey in self.card_order:
            if ckey not in self.card_visibility:
                continue
            suffix = "显示一?" if self.card_visibility.get(ckey, True) else "已隐钘?"
            item = QListWidgetItem(f"{wcr.get_card_label(ckey)} · {suffix}")
            item.setData(Qt.ItemDataRole.UserRole, ckey)
            mw.card_order_list.addItem(item)
        mw.card_order_list.blockSignals(False)

    def on_card_order_rows_moved(self, *_args) -> None:
        mw = self._mw
        if not hasattr(mw, "card_order_list"):
            return
        new_order: list[str] = []
        for i in range(mw.card_order_list.count()):
            item = mw.card_order_list.item(i)
            ckey = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if ckey and ckey not in new_order:
                new_order.append(ckey)
        self._layout.set_card_order(new_order)
        self._rebuild_workbench_grid()
        self.persist_preferences()

    # ══════════════════════════════════════════════════════鈺?
    # 7. 网格重建
    # ══════════════════════════════════════════════════════鈺?

    def _rebuild_workbench_grid(self) -> None:
        mw = self._mw
        if not hasattr(mw, "workbench_grid"):
            return
        t0 = perf_counter()

        visible = self._layout.get_visible_card_keys()
        visible = [k for k in visible if k in mw.workbench_cards]

        placements = wcr.compute_grid_placements(
            visible, self.active_workbench_template, self.card_span_overrides,
        )
        wcr.apply_grid_placements(
            mw.workbench_grid,
            placements,
            mw.workbench_cards,
            getattr(mw, "workbench_card_lists", {}),
            self.workbench_density,
            self.card_size_overrides,
        )

        elapsed_ms = (perf_counter() - t0) * 1000
        mw._record_performance_sample("workbench_rebuild_ms", elapsed_ms)
        if elapsed_ms > self.WORKBENCH_REBUILD_TARGET_MS:
            logger.warning(
                "workbench rebuild exceeded target: %.1fms > %dms",
                elapsed_ms, self.WORKBENCH_REBUILD_TARGET_MS,
            )
            mw._track_event(
                "layout.rebuild_exceeded",
                SEVERITY_DEGRADED,
                {"elapsed_ms": elapsed_ms, "target_ms": self.WORKBENCH_REBUILD_TARGET_MS},
            )

    # ══════════════════════════════════════════════════════鈺?
    # 8. 今日工作视图
    # ══════════════════════════════════════════════════════鈺?

    def _set_today_view_button_state(self) -> None:
        mw = self._mw
        from ui.theme import set_button_role
        btn = getattr(mw, "today_work_view_btn", None)
        if btn is None:
            return
        btn.blockSignals(True)
        btn.setChecked(self.today_work_view_enabled)
        btn.blockSignals(False)
        set_button_role(btn, "primary" if self.today_work_view_enabled else "secondary")

    def toggle_today_work_view(self) -> None:
        self._layout.toggle_today_view()
        self._set_today_view_button_state()
        self._apply_today_work_view_style()
        self._apply_card_visibility()
        self.persist_preferences()

    def _apply_today_work_view_style(self) -> None:
        mw = self._mw
        from ui.theme import set_surface
        cards = getattr(mw, "workbench_cards", {})
        if not cards:
            return
        for ckey, cw in cards.items():
            if cw is None:
                continue
            if self.today_work_view_enabled and ckey in {"tasks", "inbox", "suggestions"}:
                set_surface(cw, "priority-accent")
            elif ckey == "suggestions":
                set_surface(cw, "priority-accent")
            elif ckey == "tasks":
                set_surface(cw, "warm-accent")
            elif ckey == "notes":
                set_surface(cw, "success-accent")
            elif ckey == "news":
                set_surface(cw, "accent")
            else:
                set_surface(cw, "card")

    # ══════════════════════════════════════════════════════鈺?
    # 9. 重置
    # ══════════════════════════════════════════════════════鈺?

    def reset_workbench_preferences(self) -> None:
        self._layout.reset_to_defaults()
        self._apply_workbench_visibility()
        self._apply_card_visibility()
        self._set_today_view_button_state()
        self._apply_today_work_view_style()
        self._refresh_layout_preset_combo()
        self.persist_preferences()

    # ══════════════════════════════════════════════════════鈺?
    # 10. 内置插件注册
    # ══════════════════════════════════════════════════════鈺?

    def register_builtin_card_plugins(self) -> None:
        """注册内置卡片和数鎹?簮插件鍒?plugin_registry。"""
        mw = self._mw
        registry = getattr(mw.app_context, "plugin_registry", None)
        if registry is None:
            return
        try:
            from .service import WORKBENCH_DEFAULT_CARD_ORDER
            for ckey in WORKBENCH_DEFAULT_CARD_ORDER:
                registry.register_card_plugin(
                    CardPluginSpec(
                        plugin_id=f"builtin.card.{ckey}",
                        name=ckey,
                        version="1.0.0",
                        namespace=f"card.{ckey}",
                        description="builtin workbench card",
                        default_visible=self.card_visibility.get(ckey, True),
                    )
                )
            for aid, alabel in [
                ("refresh_dashboard", "刷新姒傝?"),
                ("open_settings", "设置"),
                ("capture_inbox", "蹇?嵎收件"),
                ("open_doctor", "桌面 Doctor"),
                ("open_translate", "翻译"),
                ("open_word_bank", "单词鏈?"),
            ]:
                registry.register_quick_action_plugin(
                    QuickActionPluginSpec(
                        plugin_id=f"builtin.action.{aid}",
                        name=alabel,
                        version="1.0.0",
                        action_id=aid,
                        description=f"内置蹇?嵎动作: {alabel}",
                    )
                )
            for sid, slabel, modes in [
                ("tasks", "任务", ["local", "cloud_push", "cloud_pull"]),
                ("notes", "笔记?", ["local", "cloud_push", "cloud_pull"]),
                ("news", "新闻", ["local", "rss_pull"]),
                ("translations", "翻译", ["local", "cloud_push", "cloud_pull"]),
                ("inbox", "收件绠?", ["local"]),
            ]:
                registry.register_data_source_plugin(
                    DataSourcePluginSpec(
                        plugin_id=f"builtin.datasource.{sid}",
                        name=slabel,
                        version="1.0.0",
                        source_id=sid,
                        description=f"内置数据婧? {slabel}",
                        sync_modes=modes,
                    )
                )
        except Exception as exc:
            self.logger.warning("内置插件注册失败（不影响主功能）: %s", exc)

    # ══════════════════════════════════════════════════════鈺?
    # 11. 构建工作台面鏉?
    # ══════════════════════════════════════════════════════鈺?

    def build_workbench_panel(self) -> QWidget:
        """
        构建完整的工作台面板。?
        """
        mw = self._mw
        from ui.theme import set_button_role, set_ui_class

        panel = mw._create_card_container("工作鍙?")
        panel.layout().setContentsMargins(14, 12, 14, 12)
        panel.layout().setSpacing(8)

        subtitle = QLabel("把最近任鍔掳?佺瑪璁般?佹柊闂汇?佹敹件和寤鸿?直接收在首页，减少来回找入口。?")
        set_ui_class(subtitle, "muted")
        subtitle.setWordWrap(True)
        panel.layout().addWidget(subtitle)

        # 工具鏉?
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        mw.workbench_settings_toggle_btn = QPushButton("布局设置")
        set_button_role(mw.workbench_settings_toggle_btn, "secondary")
        mw.workbench_settings_toggle_btn.setCheckable(True)
        mw.workbench_settings_toggle_btn.clicked.connect(lambda: self._toggle_workbench_controls_panel())
        toolbar.addWidget(mw.workbench_settings_toggle_btn)

        mw.performance_panel_btn = QPushButton("性能统计?")
        set_button_role(mw.performance_panel_btn, "secondary")
        mw.performance_panel_btn.clicked.connect(mw._open_performance_panel)
        toolbar.addWidget(mw.performance_panel_btn)

        mw.today_work_view_btn = QPushButton("今日工作视图")
        mw.today_work_view_btn.setCheckable(True)
        set_button_role(mw.today_work_view_btn, "secondary")
        mw.today_work_view_btn.clicked.connect(self.toggle_today_work_view)
        toolbar.addWidget(mw.today_work_view_btn)

        mw.workbench_tech_digest_btn = QPushButton("鎶?鏈?懆鎶?")
        set_button_role(mw.workbench_tech_digest_btn, "secondary")
        mw.workbench_tech_digest_btn.clicked.connect(mw._trigger_weekly_tech_digest)
        toolbar.addWidget(mw.workbench_tech_digest_btn)

        toolbar.addStretch(1)
        panel.layout().addLayout(toolbar)

        # 控件面板（由 wcr 鏋勯?狅級
        controls = wcr.build_workbench_controls_panel(
            on_template_change=self.set_workbench_template,
            on_density_change=self.set_workbench_density,
            on_section_toggle=self.toggle_section_visibility,
            on_card_toggle=self.toggle_card_visibility,
            on_card_size_cycle=self.cycle_card_size_override,
            on_card_span_cycle=self.cycle_card_span_override,
            on_reset=self.reset_workbench_preferences,
            on_save_preset=self._save_current_layout_preset,
            on_apply_preset=self._apply_selected_layout_preset,
            on_delete_preset=self._delete_selected_layout_preset,
            on_card_order_moved=self.on_card_order_rows_moved,
        )
        mw.workbench_controls_panel = controls["panel"]
        mw.template_buttons.update(controls["template_buttons"])
        mw.density_buttons.update(controls["density_buttons"])
        mw.section_toggle_buttons.update(controls["section_toggle_buttons"])
        mw.card_toggle_buttons = controls["card_toggle_buttons"]
        mw.card_size_buttons.update(controls["card_size_buttons"])
        mw.card_span_buttons.update(controls["card_span_buttons"])
        mw.reset_workbench_btn = controls["reset_btn"]
        mw.layout_preset_combo = controls["layout_preset_combo"]
        mw.save_layout_preset_btn = controls["save_preset_btn"]
        mw.apply_layout_preset_btn = controls["apply_preset_btn"]
        mw.delete_layout_preset_btn = controls["delete_preset_btn"]
        mw.card_order_list = controls["card_order_list"]
        panel.layout().addWidget(mw.workbench_controls_panel)

        # 网格布局
        mw.workbench_grid = QGridLayout()
        mw.workbench_grid.setHorizontalSpacing(8)
        mw.workbench_grid.setVerticalSpacing(8)

        # 卡片和列琛?
        mw.recent_tasks_list = mw._create_recent_list(mw.open_tasks_dialog)
        mw.recent_notes_list = mw._create_recent_list(mw.open_notes_dialog)
        mw.recent_translations_list = mw._create_recent_list(mw.open_translate_dialog)
        mw.recent_inbox_list = mw._create_recent_list(mw._focus_inbox_capture)
        mw.suggestions_list = QListWidget()
        mw.suggestions_list.setSpacing(4)
        mw.suggestions_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; } "
            "QListWidget::item { border: none; padding: 2px 0; background: transparent; }"
        )
        mw.suggestions_list.itemActivated.connect(mw._handle_suggestion_activation)
        mw.activity_list = mw._create_recent_list(mw._open_full_activity_log)

        mw.workbench_cards = {
            "tasks": mw._wrap_list_card(mw.recent_tasks_list, "tasks"),
            "notes": mw._wrap_list_card(mw.recent_notes_list, "notes"),
            "news": mw._wrap_list_card(mw.recent_news_list, "news"),
            "translate": mw._wrap_list_card(mw.recent_translations_list, "translate"),
            "inbox": mw._wrap_list_card(mw.recent_inbox_list, "inbox"),
            "suggestions": mw._wrap_suggestions_card(),
            "activity": mw._wrap_activity_card(),
        }

        # Phase 4: 鍙?偣击卡片体 '?点击导航鍒板?应内联面鏉?
        _nav_map = {
            "tasks": "tasks", "notes": "notes", "news": "news",
            "translate": "translate", "inbox": "inbox",
        }
        for ckey, cw in mw.workbench_cards.items():
            target = _nav_map.get(ckey)
            if target is not None and isinstance(cw, QWidget):
                _nav_id = target
                _mw_ref = mw

                def _nav_card_changed(ev, nid=_nav_id, ref=_mw_ref) -> None:
                    ref._on_nav_changed(nid)

                setattr(cw, "mousePressEvent", _nav_card_changed)
                cw.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        mw.workbench_card_lists = {
            "tasks": mw.recent_tasks_list,
            "notes": mw.recent_notes_list,
            "news": mw.recent_news_list,
            "translate": mw.recent_translations_list,
            "inbox": mw.recent_inbox_list,
            "suggestions": mw.suggestions_list,
            "activity": mw.activity_list,
        }

        panel.layout().addLayout(mw.workbench_grid)

        self._populate_card_order_list()
        self._rebuild_workbench_grid()

        return panel

    def _toggle_workbench_controls_panel(self) -> None:
        mw = self._mw
        is_enabled = bool(mw.workbench_settings_toggle_btn.isChecked())
        mw.workbench_controls_panel.setVisible(is_enabled)
        mw.workbench_settings_toggle_btn.setText("收起设置" if is_enabled else "布局设置")
