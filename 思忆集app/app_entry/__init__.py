"""
思忆集 app entry point.

Designed to host desktop_main.py and startup_checks.py,
applicable to the desktop client bootstrap sequence.

Ideally this package should stay thin — import the runtime
context and launch the main window, nothing more.
"""
from __future__ import annotations

from app_entry.main import main
