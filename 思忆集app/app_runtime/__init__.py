"""
思忆集 application runtime.

Shared dependency injection root and lifecycle management.
Contains AppContext, desktop companion controller, and
startup/shutdown coordination.

Modules in this package are consumed across the entire
app — keep their public surface stable.
"""

from app_runtime.service_registry import (
    AmbiguousServiceError,
    ServiceNotFoundError,
    ServiceRegistry,
    service_registry,
)

__all__ = [
    "AmbiguousServiceError",
    "AppContext",
    "CopilotEngine",
    "ServiceNotFoundError",
    "ServiceRegistry",
    "service_registry",
]


def __getattr__(name: str):
    """Lazy-import types to avoid circular imports at module load time."""
    if name == "AppContext":
        from app_runtime.app_context import AppContext as _AppContext

        return _AppContext
    if name == "CopilotEngine":
        from services.copilot_engine import CopilotEngine as _CopilotEngine

        return _CopilotEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
