"""
@ 引用自动补全输入框 + 图片粘贴支持

在 QTextEdit 中检测 @ 输入并弹出分类自动补全菜单。
支持键盘（方向键 + Enter）和鼠标选取。
支持粘贴剪贴板图片。
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.events import AppEvent, event_bus

logger = logging.getLogger("siyi.at_chat_ui")

# 类别颜色映射
CATEGORY_COLORS = {
    "memory": "#0f766e",  # 青色
    "file": "#7c3aed",    # 紫色
    "device": "#b45309",  # 橙色
    "time": "#2563eb",    # 蓝色
}

CATEGORY_LABELS = {
    "memory": "[记忆]",
    "file": "[文件]",
    "device": "[设备]",
    "time": "[时间]",
}


class AtReferencePopup(QFrame):
    """@ 引用的自动补全弹出菜单。"""

    reference_selected = pyqtSignal(object)  # AtReference

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            AtReferencePopup {
                background: #ffffff;
                border: 1px solid #d8e0e8;
                border-radius: 8px;
            }
        """)
        self._engine = None
        self._current_query = ""
        self._current_references: list[Any] = []
        self._category_items: dict[str, list[QListWidgetItem]] = {}
        self._category_widgets: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 提示头
        self._header = QLabel("输入关键词搜索引用...")
        self._header.setStyleSheet("color: #94a3b8; padding: 6px 12px; font-size: 12px;")
        self._header.setFixedHeight(28)
        layout.addWidget(self._header)

        # 分类列表
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                border: none;
                background: transparent;
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 12px;
                border-radius: 4px;
                min-height: 24px;
            }
            QListWidget::item:selected {
                background: #eef2ff;
                color: #1e293b;
            }
            QListWidget::item:hover {
                background: #f1f5f9;
            }
        """)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemActivated.connect(self._on_item_activated)
        self._list.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self._list)

        self.setMinimumWidth(320)
        self.setMaximumWidth(480)

    def set_engine(self, engine):
        """设置 @ 引用引擎。"""
        self._engine = engine

    def search_and_show(self, query: str, parent_widget: QWidget, cursor_rect=None):
        """搜索并显示弹出菜单。"""
        self._current_query = query
        self._list.clear()
        self._category_items.clear()

        if self._engine is None:
            self.hide()
            return

        categorized = self._engine.search_categorized(query)
        total = sum(len(items) for items in categorized.values())
        if total == 0:
            self._header.setText("无匹配结果")
            self._list.clear()
            self.show()
            self._resize_and_position(parent_widget, cursor_rect)
            return

        self._current_references = []
        self._header.setText(f"找到 {total} 个引用（继续输入缩小范围）")

        has_any = False
        for ref_type in ("memory", "file", "device", "time"):
            items = categorized.get(ref_type, [])
            if not items:
                continue
            has_any = True

            # 分类标题
            title_item = QListWidgetItem()
            label = CATEGORY_LABELS.get(ref_type, f"[{ref_type}]")
            color = CATEGORY_COLORS.get(ref_type, "#64748b")
            title_item.setText(f"  {label}")
            title_item.setFlags(Qt.ItemFlag.NoItemFlags)
            title_item.setForeground(Qt.GlobalColor.gray)
            font = title_item.font()
            font.setBold(True)
            font.setPointSize(font.pointSize() - 1)
            title_item.setFont(font)
            self._list.addItem(title_item)

            for ref in items:
                self._current_references.append(ref)
                list_item = QListWidgetItem()
                ref_id = len(self._current_references) - 1

                # 显示名称 + 来源信息
                display = ref.display_name
                if ref.source_info:
                    display += f"  — {ref.source_info}"
                list_item.setText(display)
                list_item.setData(Qt.ItemDataRole.UserRole, ref_id)
                self._list.addItem(list_item)

        if not has_any:
            self._header.setText("无匹配结果")

        self.show()
        self._resize_and_position(parent_widget, cursor_rect)

    def _resize_and_position(self, parent_widget: QWidget, cursor_rect=None):
        """调整菜单大小和位置。"""
        count = self._list.count()
        item_height = 36
        height = min(28 + count * item_height + 8, 360)
        width = max(320, min(480, parent_widget.width() - 24))
        self.resize(width, height)

        if cursor_rect:
            # 在光标下方显示
            global_pos = parent_widget.mapToGlobal(cursor_rect.bottomLeft())
            # 确保不超出屏幕底部
            screen_geo = parent_widget.screen().availableGeometry() if parent_widget.screen() else None
            if screen_geo and global_pos.y() + height > screen_geo.bottom():
                global_pos.setY(global_pos.y() - height - cursor_rect.height())
            self.move(global_pos)
        else:
            # 默认位置
            pos = parent_widget.mapToGlobal(QPoint(0, parent_widget.height()))
            self.move(pos)

    def _on_item_activated(self, item: QListWidgetItem):
        """选中一个引用项。"""
        ref_id = item.data(Qt.ItemDataRole.UserRole)
        if ref_id is None:
            return
        if 0 <= ref_id < len(self._current_references):
            ref = self._current_references[ref_id]
            self.reference_selected.emit(ref)
            self._emit_mention_selected(ref)
            self.hide()

    def _emit_mention_selected(self, ref) -> None:
        """发布 mention.selected 事件到 EventBus。"""
        try:
            event_bus.emit(AppEvent(
                event_type="mention.selected",
                source="assistant_chat",
                payload={
                    "ref_type": ref.ref_type,
                    "ref_id": ref.ref_id,
                    "display_name": ref.display_name,
                    "category_label": getattr(ref, "category_label", ""),
                    "query": self._current_query,
                },
            ))
        except Exception:
            logger.debug("EventBus emit mention.selected failed", exc_info=True)

    def select_next(self):
        """选择下一项 (↓)。"""
        current = self._list.currentRow()
        if current < self._list.count() - 1:
            self._list.setCurrentRow(current + 1)
        else:
            self._list.setCurrentRow(0)

    def select_prev(self):
        """选择上一项 (↑)。"""
        current = self._list.currentRow()
        if current > 0:
            self._list.setCurrentRow(current - 1)
        else:
            self._list.setCurrentRow(self._list.count() - 1)

    def get_selected_reference(self):
        """获取当前选中项对应的引用。"""
        item = self._list.currentItem()
        if item is None:
            return None
        ref_id = item.data(Qt.ItemDataRole.UserRole)
        if ref_id is not None and 0 <= ref_id < len(self._current_references):
            return self._current_references[ref_id]
        return None

    def has_items(self) -> bool:
        """是否有可选项。"""
        return any(
            self._list.item(i).data(Qt.ItemDataRole.UserRole) is not None
            for i in range(self._list.count())
        )


class AtTextEdit(QTextEdit):
    """支持 @ 引用自动补全的文本输入框。"""

    at_reference_selected = pyqtSignal(object)   # AtReference
    at_references_updated = pyqtSignal(list)     # list[AtReference]
    submit_requested = pyqtSignal()              # 用户请求发送消息（Ctrl+Enter 或 Enter）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._popup = AtReferencePopup(self)
        self._popup.reference_selected.connect(self._on_popup_reference_selected)
        self._popup.hide()

        # 当前正在输入的 @ 关键词
        self._at_active = False
        self._at_start_pos = -1

        # 已选的引用列表
        self._selected_references: list[Any] = []

        # 已粘贴的图片列表: [{"index": int, "base64": str, "format": str, "label": str}]
        self._attached_images: list[dict[str, Any]] = []
        self._image_counter = 0

        # 延迟搜索定时器（防抖）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._do_search)

        self.textChanged.connect(self._on_text_changed)

        # 提示 chip 样式
        self._chip_style = (
            "background: #eef2ff; color: #4338ca; border-radius: 4px; "
            "padding: 2px 6px; font-size: 12px;"
        )

    def set_at_engine(self, engine):
        """设置 @ 引用引擎。"""
        self._engine = engine
        self._popup.set_engine(engine)

    # ── 按键处理 ─────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if self._popup.isVisible():
            if event.key() == Qt.Key.Key_Down:
                self._popup.select_next()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Up:
                self._popup.select_prev()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                ref = self._popup.get_selected_reference()
                if ref is not None and self._popup.has_items():
                    self._insert_reference(ref)
                    event.accept()
                    return
                # 没有选中项时，关闭 popup 并提交
                self._popup.hide()
                self._at_active = False
                super().keyPressEvent(event)
                return
            if event.key() == Qt.Key.Key_Escape:
                self._popup.hide()
                self._at_active = False
                event.accept()
                return

        # Popup 不可见时：Ctrl+Enter 或单独 Enter 触发发送
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+Enter 始终发送
                self.submit_requested.emit()
                event.accept()
                return
            # 单 Enter：发送（不插入换行）
            self.submit_requested.emit()
            event.accept()
            return

        super().keyPressEvent(event)

        # @ 符号检测（在 super 之后以确保字符已输入）
        if event.text() == "@":
            self._at_active = True
            self._at_start_pos = self.textCursor().position() - 1
            self._search_timer.start()

    # ── 文本变更处理 ─────────────────────────────────────────────

    def _on_text_changed(self):
        """文本变更时检测 @ 触发搜索。"""
        if not self._at_active:
            return

        cursor = self.textCursor()
        current_pos = cursor.position()

        # 提取 @ 之后的关键词
        text = self.toPlainText()
        at_idx = text.rfind("@", self._at_start_pos, current_pos)
        if at_idx < 0:
            self._at_active = False
            self._popup.hide()
            return

        # 检查 @ 后面是否是合法字符（直到当前光标位置）
        after_at = text[at_idx + 1:current_pos]
        # 如果包含空格或换行，结束 @ 输入模式
        if " " in after_at or "\n" in after_at:
            self._at_active = False
            self._popup.hide()
            return

        self._search_timer.start()

    def _do_search(self):
        """执行延迟搜索。"""
        if not self._at_active or self._engine is None:
            return

        text = self.toPlainText()
        cursor = self.textCursor()
        current_pos = cursor.position()

        at_idx = text.rfind("@", self._at_start_pos, current_pos)
        if at_idx < 0:
            return

        query = text[at_idx + 1:current_pos]

        # 获取光标矩形用于定位 popup
        cursor_rect = self.cursorRect(cursor)
        self._popup.search_and_show(query, self, cursor_rect)

    # ── 引用插入 ─────────────────────────────────────────────────

    def _on_popup_reference_selected(self, ref):
        """Popup 中选中引用。"""
        self._insert_reference(ref)

    def _insert_reference(self, ref):
        """将引用插入到文本中（替换 @关键词 为标记）。"""
        cursor = self.textCursor()
        current_pos = cursor.position()

        text = self.toPlainText()
        at_idx = text.rfind("@", 0, current_pos)
        if at_idx < 0:
            return

        # 计算替换范围：从 @ 到当前光标
        replace_len = current_pos - at_idx

        # 插入 [@type:id:display_name] 标记格式，可被 parse_mark 反向解析
        from .at_reference import AtReferenceEngine
        insert_text = AtReferenceEngine.format_mark(ref.ref_type, ref.ref_id, ref.display_name) + " "

        cursor.setPosition(at_idx)
        cursor.setPosition(current_pos, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(insert_text)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, len(insert_text))

        # 保存引用
        self._selected_references.append(ref)
        self.at_reference_selected.emit(ref)
        self.at_references_updated.emit(list(self._selected_references))

        # 通过 EventBus 发布 mention.selected 事件
        self._emit_mention_eventbus(ref)

        # 清除状态
        self._at_active = False
        self._popup.hide()

        # 显示引用提示
        self._show_reference_indicator()

    def _emit_mention_eventbus(self, ref) -> None:
        """发布 mention.selected 事件（从键盘选中时）。"""
        try:
            event_bus.emit(AppEvent(
                event_type="mention.selected",
                source="assistant_chat_input",
                payload={
                    "ref_type": ref.ref_type,
                    "ref_id": ref.ref_id,
                    "display_name": ref.display_name,
                    "category_label": getattr(ref, "category_label", ""),
                },
            ))
        except Exception:
            logger.debug("EventBus emit mention.selected failed", exc_info=True)

    # ── 引用管理 ─────────────────────────────────────────────────

    def get_selected_references(self) -> list[Any]:
        """获取当前所有已选的 @ 引用。"""
        return list(self._selected_references)

    def clear_references(self):
        """清空引用列表。"""
        self._selected_references.clear()
        self.at_references_updated.emit([])

    def _show_reference_indicator(self):
        """在输入框内显示引用选择提示（通过 tooltip）。"""
        count = len(self._selected_references)
        if count > 0:
            names = [r.display_name for r in self._selected_references[-3:]]
            summary = "引用: " + ", ".join(names)
            if count > 3:
                summary += f" +{count - 3}"
            self.setToolTip(summary)
        else:
            self.setToolTip("")

    # ── 图片粘贴支持 ──────────────────────────────────────────────

    def canInsertFromMimeData(self, source) -> bool:
        if source.hasImage():
            return True
        if source.hasHtml() or source.hasText():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasImage():
            self._insert_clipboard_image(source)
            return
        super().insertFromMimeData(source)

    def _insert_clipboard_image(self, source):
        """将剪贴板图片插入为内联占位标记。"""
        image = source.imageData()
        if image is None:
            return
        self._image_counter += 1
        idx = self._image_counter
        # 转换为 PNG base64
        try:
            from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
            from PyQt6.QtGui import QImage
            io_buf = QBuffer()
            io_buf.open(QIODevice.OpenModeFlag.WriteOnly)
            if isinstance(image, QImage):
                if not image.save(io_buf, "PNG"):
                    logger.warning("图片保存到 buffer 失败")
                    return
                fmt = "png"
                raw_bytes = io_buf.data()
            else:
                pix = image
                if not pix.save(io_buf, "PNG"):
                    logger.warning("图片保存到 buffer 失败")
                    return
                fmt = "png"
                raw_bytes = io_buf.data()
            b64_str = base64.b64encode(bytes(raw_bytes)).decode("ascii")
        except Exception as exc:
            logger.warning("剪贴板图片编码失败: %s", exc)
            return

        image_info = {"index": idx, "base64": b64_str, "format": fmt, "label": f"图片 {idx}"}
        self._attached_images.append(image_info)
        # 插入可视占位标记
        label = f"[馃摲 {idx}] "
        cursor = self.textCursor()
        cursor.insertText(label)
        self.setTextColor(self._get_image_placeholder_color())
        cursor.insertText("(图片)" if self.toPlainText().strip() else "(图片)")
        self.setTextColor(self._default_text_color())
        self.setToolTip(f"已附加 {len(self._attached_images)} 张图片")

    @staticmethod
    def _get_image_placeholder_color():
        from PyQt6.QtGui import QColor
        return QColor("#7c3aed")

    @staticmethod
    def _default_text_color():
        from PyQt6.QtGui import QColor
        return QColor("#1e293b")

    def get_attached_images(self) -> list[dict[str, Any]]:
        """获取当前附加的所有图片数据。"""
        return list(self._attached_images)

    def clear_images(self):
        """清空附加图片。"""
        self._attached_images.clear()
        self._image_counter = 0
        self.setToolTip("")

    def insert_image_data(self, base64_str: str, fmt: str = "png", label: str = "截图") -> None:
        """以编程方式插入图片数据（非粘贴），供截图等功能使用。

        Args:
            base64_str: 图片 base64 编码
            fmt: 图片格式（png/jpeg/webp）
            label: 图片标签，显示在占位标记中
        """
        self._image_counter += 1
        idx = self._image_counter
        image_info = {"index": idx, "base64": base64_str, "format": fmt, "label": label}
        self._attached_images.append(image_info)
        marker = f"[馃摲 {idx}] ({label})"
        cursor = self.textCursor()
        prefix = marker + "\n" if self.toPlainText().strip() else marker
        cursor.insertText(prefix)
        self.setToolTip(f"已附加 {len(self._attached_images)} 张图片")

    # ── 公共方法 ─────────────────────────────────────────────────

    def get_text_without_reference_markers(self) -> str:
        """获取去除 @ 标记后的纯文本（移除 [@type:id:name] 标记）。"""
        from .at_reference import AtReferenceEngine
        text = self.toPlainText()
        # 将 [@type:id:name] 替换为可读的 @名 形式
        result = AtReferenceEngine.MARK_PATTERN.sub(
            lambda m: f"@{m.group(3)}",
            text,
        )
        return result.strip()

    def focusOutEvent(self, event):
        """失去焦点时隐藏 popup。"""
        if self._popup and self._popup.isVisible():
            self._popup.hide()
        super().focusOutEvent(event)
