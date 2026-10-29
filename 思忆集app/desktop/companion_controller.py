"""Desktop companion controller — system tray icon + floating orb entry management.

Construction pattern (mirrors dialog_coordinator.py):
    controller = DesktopCompanionController(main_window)
All UI callbacks delegate through self._mw.xxx().
"""
from __future__ import annotations

import logging


class DesktopCompanionController:
    """Manage system tray icon lifecycle and floating assistant visibility."""

    def __init__(self, main_window):
        """Create the controller and store a weak-ish reference to the main window.

        Parameters
        ----------
        main_window : MainWindow
            The owning main window (QMainWindow subclass).  All UI-facing calls
            are forwarded through ``self._mw``.
        """
        self._mw = main_window
        self._log = logging.getLogger("siyi.desktop_companion")

    # ------------------------------------------------------------------
    #  public API required by the decoupling plan
    # ------------------------------------------------------------------

    def setup_system_tray(self):
        """Create/initialise the system tray icon via the MenuBarManager.

        戻り値
        -------
        QSystemTrayIcon or None
        """
        menu_bar_manager = getattr(self._mw, "_menu_bar_manager", None)
        if menu_bar_manager is None:
            self._log.warning("MenuBarManager not available; tray icon skipped")
            return None

        tray_icon = menu_bar_manager.setup_system_tray()
        # keep the reference on main_window so closeEvent / hide behaviour still works
        self._mw.tray_icon = tray_icon
        return tray_icon

    def update_tray_menu(self):
        """Rebuild the tray context menu (delegates to MenuBarManager)."""
        menu_bar_manager = getattr(self._mw, "_menu_bar_manager", None)
        if menu_bar_manager is not None and hasattr(menu_bar_manager, "update_tray_menu"):
            menu_bar_manager.update_tray_menu()
        elif menu_bar_manager is not None:
            # fallback: re-initialise the tray altogether
            self.setup_system_tray()

    def show_floating_entry(self):
        """Open the floating orb assistant widget."""
        mw = self._mw
        if hasattr(mw, "open_floating_assistant_dialog"):
            return mw.open_floating_assistant_dialog()
        return None

    def hide_floating_entry(self):
        """Close the floating orb widget if visible."""
        mw = self._mw
        if getattr(mw, "floating_orb_widget", None) is not None:
            try:
                mw.floating_orb_widget.close()
            except Exception:
                self._log.debug("failed to close floating orb widget")
