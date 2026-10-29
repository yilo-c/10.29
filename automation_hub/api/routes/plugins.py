"""Plugin management API routes.

Provides CRUD and lifecycle management for plugins.
In-memory storage — TODO: replace with database.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Any
import logging

from automation_hub.api.auth.deps import require_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


# ── In-memory storage ──────────────────────────────────────────────

_PLUGINS: dict[str, dict[str, Any]] = {}
_MANIFESTS: dict[str, dict[str, Any]] = {}
_CONFIGS: dict[str, dict[str, Any]] = {}
_INSTALL_TIMES: dict[str, str] = {}


# ── Models ─────────────────────────────────────────────────────────

class PluginItem(BaseModel):
    """Basic plugin info for list responses."""

    id: str
    name: str
    version: str
    type: str
    state: str
    capabilities: list[str] = Field(default_factory=list)


class PluginDetail(PluginItem):
    """Full plugin detail including config and metadata."""

    config: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    description: str = ""
    installed_at: str | None = None


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any]


class ToggleRequest(BaseModel):
    enabled: bool


class PluginTypeCount(BaseModel):
    type: str
    count: int


# ── Helpers ────────────────────────────────────────────────────────


def _get_plugin_or_404(plugin_id: str) -> dict[str, Any]:
    plugin = _PLUGINS.get(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"plugin not found: {plugin_id}")
    return plugin


def _build_plugin_item(
    plugin_id: str, manifest: dict[str, Any], plugin: dict[str, Any]
) -> PluginItem:
    return PluginItem(
        id=plugin_id,
        name=manifest.get("name", plugin_id),
        version=manifest.get("version", "0.0.0"),
        type=manifest.get("type", "unknown"),
        state=plugin.get("state", "unknown"),
        capabilities=list(manifest.get("capabilities", [])),
    )


def _build_plugin_detail(plugin_id: str) -> PluginDetail:
    plugin = _get_plugin_or_404(plugin_id)
    manifest = _MANIFESTS.get(plugin_id, {})
    return PluginDetail(
        id=plugin_id,
        name=manifest.get("name", plugin_id),
        version=manifest.get("version", "0.0.0"),
        type=manifest.get("type", "unknown"),
        state=plugin.get("state", "unknown"),
        capabilities=list(manifest.get("capabilities", [])),
        config=_CONFIGS.get(plugin_id, {}),
        permissions=list(manifest.get("permissions", [])),
        description=manifest.get("description", ""),
        installed_at=_INSTALL_TIMES.get(plugin_id),
    )


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("", response_model=list[PluginItem])
async def list_plugins(
    include_disabled: bool = False,
    token=Depends(require_scope("plugins:admin")),
):
    """List all installed plugins.

    By default only returns enabled plugins. Set ``include_disabled=true``
    to include disabled and errored plugins.
    """
    try:
        result: list[PluginItem] = []
        for pid, plugin in _PLUGINS.items():
            if not include_disabled and plugin.get("state") in ("disabled", "error"):
                continue
            manifest = _MANIFESTS.get(pid, {})
            result.append(_build_plugin_item(pid, manifest, plugin))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list plugins failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/types", response_model=list[PluginTypeCount])
async def list_plugin_types(
    token=Depends(require_scope("plugins:admin")),
):
    """Return plugin counts grouped by type (enabled plugins only)."""
    try:
        counts: dict[str, int] = {}
        for pid, plugin in _PLUGINS.items():
            if plugin.get("state") in ("disabled", "error"):
                continue
            manifest = _MANIFESTS.get(pid, {})
            t = manifest.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        return [
            PluginTypeCount(type=t, count=c)
            for t, c in sorted(counts.items())
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list plugin types failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{plugin_id}", response_model=PluginDetail)
async def get_plugin(
    plugin_id: str,
    token=Depends(require_scope("plugins:admin")),
):
    """Get full details for a specific plugin."""
    try:
        return _build_plugin_detail(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get plugin failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{plugin_id}/config", response_model=dict[str, Any])
async def get_plugin_config(
    plugin_id: str,
    token=Depends(require_scope("plugins:admin")),
):
    """Get the current configuration for a plugin."""
    try:
        _get_plugin_or_404(plugin_id)
        return _CONFIGS.get(plugin_id, {})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get plugin config failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{plugin_id}/config", response_model=dict[str, Any])
async def update_plugin_config(
    plugin_id: str,
    body: ConfigUpdateRequest,
    token=Depends(require_scope("plugins:admin")),
):
    """Update configuration for a plugin. Merges with existing config."""
    try:
        _get_plugin_or_404(plugin_id)
        existing = _CONFIGS.get(plugin_id, {}).copy()
        existing.update(body.config)
        _CONFIGS[plugin_id] = existing
        return _CONFIGS[plugin_id]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update plugin config failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{plugin_id}/toggle", response_model=PluginDetail)
async def toggle_plugin(
    plugin_id: str,
    body: ToggleRequest,
    token=Depends(require_scope("plugins:admin")),
):
    """Enable or disable a plugin."""
    try:
        plugin = _get_plugin_or_404(plugin_id)
        plugin["state"] = "enabled" if body.enabled else "disabled"
        logger.info(
            "plugin %s toggled to %s", plugin_id, plugin["state"]
        )
        return _build_plugin_detail(plugin_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("toggle plugin failed")
        raise HTTPException(status_code=500, detail=str(e))
