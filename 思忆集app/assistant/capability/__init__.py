"""Capability domain subpackage — extracted from capability_router.py."""
from __future__ import annotations

from ._file_ops import FileCapabilityDomain
from ._knowledge import KnowledgeCapabilityDomain
from ._shell import ShellCapabilityDomain
from ._translation import TranslationCapabilityDomain
from ._browser import BrowserCapabilityDomain
from ._browser_automation import BrowserAutomationDomain
from ._desktop import DesktopAutomationCapabilityDomain
from ._trae import TraeCapabilityDomain

__all__ = [
    "FileCapabilityDomain",
    "KnowledgeCapabilityDomain",
    "ShellCapabilityDomain",
    "TranslationCapabilityDomain",
    "BrowserCapabilityDomain",
    "BrowserAutomationDomain",
    "DesktopAutomationCapabilityDomain",
    "TraeCapabilityDomain",
]
