"""统一内容整理动作菜单。

所有对话框（news / notes / translate）共用同一套"整理当前内容"动作菜单，
确保"转笔记、转任务、稍后看、忽略"动作链和状态语义一致。

用法::

    from .organize_action_helper import build_organize_menu

    menu = build_organize_menu(
        parent_widget=self,
        on_convert_to_note=self._my_convert_to_note,
        on_convert_to_task=self._my_convert_to_task,
        on_save_for_later=self._my_save_for_later,
        on_dismiss=self._my_dismiss,
    )
    button.setMenu(menu)
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtWidgets import QMenu, QWidget


def build_organize_menu(
    parent_widget: QWidget,
    *,
    on_convert_to_note: Callable[[], Any] | None = None,
    on_convert_to_task: Callable[[], Any] | None = None,
    on_save_for_later: Callable[[], Any] | None = None,
    on_dismiss: Callable[[], Any] | None = None,
    allow_save_for_later: bool = True,
    allow_dismiss: bool = True,
) -> QMenu:
    """构建统一内容整理菜单。

    Parameters
    ----------
    parent_widget : QWidget
        父级控件
    on_convert_to_note : Callable | None
        转笔记回调
    on_convert_to_task : Callable | None
        转任务回调
    on_save_for_later : Callable | None
        稍后看/收藏回调
    on_dismiss : Callable | None
        忽略回调
    allow_save_for_later : bool
        是否显示"稍后看"选项
    allow_dismiss : bool
        是否显示"忽略"选项

    Returns
    -------
    QMenu
        配置好的菜单实例
    """
    menu = QMenu("整理当前内容", parent_widget)

    note_action = menu.addAction("馃摑 转笔记")
    assert note_action is not None
    if on_convert_to_note is not None:
        note_action.triggered.connect(on_convert_to_note)
    else:
        note_action.setEnabled(False)

    task_action = menu.addAction("馃搵 转任务")
    assert task_action is not None
    if on_convert_to_task is not None:
        task_action.triggered.connect(on_convert_to_task)
    else:
        task_action.setEnabled(False)

    if allow_save_for_later:
        later_action = menu.addAction("馃敄 稍后看")
        assert later_action is not None
        if on_save_for_later is not None:
            later_action.triggered.connect(on_save_for_later)
        else:
            later_action.setEnabled(False)

    if allow_dismiss:
        menu.addSeparator()
        dismiss_action = menu.addAction("✕ 忽略")
        assert dismiss_action is not None
        if on_dismiss is not None:
            dismiss_action.triggered.connect(on_dismiss)
        else:
            dismiss_action.setEnabled(False)

    return menu
