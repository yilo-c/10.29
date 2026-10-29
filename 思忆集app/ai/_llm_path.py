"""Ensure the project root is on sys.path so ``llm_resilience`` is importable.

A single shared location for the path setup so that ``ai_client_base`` and
``ai_client_fallback`` don't duplicate the same 4-line block.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
