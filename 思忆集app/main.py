"""Shim: re-exports app_entry.main for backward compatibility."""
from __future__ import annotations
import os

import importlib.util
import sys

_shim_dir = os.path.dirname(os.path.abspath(__file__))
# Ensure the app root is in sys.path so app_entry/main.py can import app_runtime, etc.
if _shim_dir not in sys.path:
    sys.path.insert(0, _shim_dir)

_spec = importlib.util.spec_from_file_location(
    "app_entry.main",
    os.path.join(_shim_dir, "app_entry/main.py")
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
sys.modules[__name__] = _mod

if __name__ == "__main__":
    sys.exit(_mod.main())
