"""Centralized app version — single source of truth."""

import importlib.metadata
import logging

logger = logging.getLogger("siyi.app_version")

_VERSION: str | None = None


def get_app_version() -> str:
    global _VERSION
    if _VERSION is not None:
        return _VERSION

    # Try package metadata first (installed via pip)
    try:
        _VERSION = importlib.metadata.version("siyi_app")
        return _VERSION
    except (importlib.metadata.PackageNotFoundError, Exception):
        logger.debug("Package version not found, falling back to pyproject.toml", exc_info=True)

    # Fallback: read pyproject.toml
    import tomllib
    from pathlib import Path

    candidates = [
        Path(__file__).resolve().parent / "pyproject.toml",
        Path(__file__).resolve().parents[1] / "pyproject.toml",
    ]
    for path in candidates:
        if path.is_file():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                ver = data.get("project", {}).get("version") or data.get("tool", {}).get("poetry", {}).get("version")
                if ver:
                    _VERSION = str(ver)
                    return str(ver)
            except Exception:
                logger.debug("failed to parse %s", path)

    _VERSION = "0.0.0"
    return "0.0.0"
