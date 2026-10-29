from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

WORKBENCH_LAYOUT_CONFIG_KEY = "main_workbench_layout_config"
WORKBENCH_LAYOUT_CONFIG_VERSION = 2

LEGACY_KEYS = {
    "template": "main_workbench_template",
    "sections": "main_workbench_sections",
    "cards": "main_workbench_cards",
    "order": "main_workbench_card_order",
    "density": "main_workbench_density",
    "sizes": "main_workbench_card_sizes",
    "spans": "main_workbench_card_spans",
    "presets": "main_workbench_layout_presets",
    "default_preset": "main_workbench_default_preset",
    "today_view": "main_workbench_today_view_default",
}

DEFAULT_CARD_ORDER = ["news", "tasks", "notes", "inbox", "suggestions", "translate"]
DEFAULT_CARD_VISIBILITY = {
    "tasks": True,
    "notes": True,
    "news": True,
    "translate": False,
    "inbox": False,
    "suggestions": False,
}
DEFAULT_SECTION_VISIBILITY = {"summary": True, "assistant": False, "life": True}


def _safe_dict(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else dict(fallback)


def _safe_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_card_order(card_order: Any) -> list[str]:
    normalized = []
    for card_key in _safe_list(card_order, DEFAULT_CARD_ORDER):
        if card_key in DEFAULT_CARD_VISIBILITY and card_key not in normalized:
            normalized.append(card_key)
    for card_key in DEFAULT_CARD_ORDER:
        if card_key not in normalized:
            normalized.append(card_key)
    return normalized


def _normalize_card_sizes(value: Any) -> dict[str, str]:
    result = {}
    raw = value if isinstance(value, dict) else {}
    for card_key, size_key in raw.items():
        normalized_card_key = str(card_key).strip()
        normalized_size = str(size_key).strip()
        if normalized_card_key in DEFAULT_CARD_VISIBILITY and normalized_size in {"small", "medium", "large"}:
            result[normalized_card_key] = normalized_size
    return result


def _normalize_card_spans(value: Any) -> dict[str, int]:
    result = {}
    raw = value if isinstance(value, dict) else {}
    for card_key, span in raw.items():
        normalized_card_key = str(card_key).strip()
        if normalized_card_key not in DEFAULT_CARD_VISIBILITY:
            continue
        try:
            normalized_span = int(span)
        except (TypeError, ValueError):
            normalized_span = 1
        result[normalized_card_key] = 2 if normalized_span == 2 else 1
    return result


def _normalize_bool_map(raw: Any, defaults: dict[str, bool]) -> dict[str, bool]:
    normalized = dict(defaults)
    if not isinstance(raw, dict):
        return normalized
    for key in defaults:
        if key in raw:
            normalized[key] = bool(raw.get(key))
    return normalized


def _normalize_presets(raw: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, Any] = {}

    if not isinstance(raw, dict):
        return result
    for preset_name, payload in raw.items():
        normalized_name = str(preset_name).strip()
        if not normalized_name or not isinstance(payload, dict):
            continue
        result[normalized_name] = normalize_workbench_layout_config(payload)
    return result


def default_workbench_layout_config() -> dict[str, Any]:
    return {
        "version": WORKBENCH_LAYOUT_CONFIG_VERSION,
        "template": "balanced",
        "density": "standard",
        "section_visibility": dict(DEFAULT_SECTION_VISIBILITY),
        "card_visibility": dict(DEFAULT_CARD_VISIBILITY),
        "card_order": list(DEFAULT_CARD_ORDER),
        "card_sizes": {},
        "card_spans": {},
        "layout_presets": {},
        "default_layout_preset": "",
        "today_view": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_workbench_layout_config(raw: Any) -> dict[str, Any]:
    defaults = default_workbench_layout_config()
    if not isinstance(raw, dict):
        return defaults

    template = str(raw.get("template") or defaults["template"]).strip()
    density = str(raw.get("density") or defaults["density"]).strip()

    normalized: dict[str, Any] = {
        "version": WORKBENCH_LAYOUT_CONFIG_VERSION,
        "template": template if template in {"balanced", "focus", "daily", "custom"} else defaults["template"],
        "density": density if density in {"compact", "standard", "comfortable"} else defaults["density"],
        "section_visibility": _normalize_bool_map(raw.get("section_visibility"), DEFAULT_SECTION_VISIBILITY),
        "card_visibility": _normalize_bool_map(raw.get("card_visibility"), DEFAULT_CARD_VISIBILITY),
        "card_order": _normalize_card_order(raw.get("card_order")),
        "card_sizes": _normalize_card_sizes(raw.get("card_sizes")),
        "card_spans": _normalize_card_spans(raw.get("card_spans")),
        "layout_presets": _normalize_presets(raw.get("layout_presets")),
        "default_layout_preset": str(raw.get("default_layout_preset") or "").strip(),
        "today_view": bool(raw.get("today_view", False)),
        "updated_at": str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    }

    if normalized["default_layout_preset"] and normalized["default_layout_preset"] not in normalized["layout_presets"]:
        normalized["default_layout_preset"] = ""

    return normalized


def build_layout_config_from_legacy(settings_repository) -> dict[str, Any]:
    defaults = default_workbench_layout_config()
    legacy = {
        "template": settings_repository.get_setting(LEGACY_KEYS["template"], defaults["template"]),
        "section_visibility": settings_repository.get_setting(LEGACY_KEYS["sections"], defaults["section_visibility"]),
        "card_visibility": settings_repository.get_setting(LEGACY_KEYS["cards"], defaults["card_visibility"]),
        "card_order": settings_repository.get_setting(LEGACY_KEYS["order"], defaults["card_order"]),
        "density": settings_repository.get_setting(LEGACY_KEYS["density"], defaults["density"]),
        "card_sizes": settings_repository.get_setting(LEGACY_KEYS["sizes"], defaults["card_sizes"]),
        "card_spans": settings_repository.get_setting(LEGACY_KEYS["spans"], defaults["card_spans"]),
        "layout_presets": settings_repository.get_setting(LEGACY_KEYS["presets"], defaults["layout_presets"]),
        "default_layout_preset": settings_repository.get_setting(LEGACY_KEYS["default_preset"], defaults["default_layout_preset"]),
        "today_view": settings_repository.get_setting(LEGACY_KEYS["today_view"], defaults["today_view"]),
    }
    return normalize_workbench_layout_config(legacy)


def get_workbench_layout_config(settings_repository) -> dict[str, Any]:
    raw = settings_repository.get_setting(WORKBENCH_LAYOUT_CONFIG_KEY, None)
    if isinstance(raw, dict) and raw:
        return normalize_workbench_layout_config(raw)
    return build_layout_config_from_legacy(settings_repository)


def save_workbench_layout_config(settings_repository, config: dict[str, Any], sync_legacy: bool = True) -> dict[str, Any]:
    normalized = normalize_workbench_layout_config(config)
    normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
    settings_repository.set_setting(WORKBENCH_LAYOUT_CONFIG_KEY, normalized)
    if sync_legacy:
        settings_repository.set_setting(LEGACY_KEYS["template"], normalized["template"])
        settings_repository.set_setting(LEGACY_KEYS["sections"], normalized["section_visibility"])
        settings_repository.set_setting(LEGACY_KEYS["cards"], normalized["card_visibility"])
        settings_repository.set_setting(LEGACY_KEYS["order"], normalized["card_order"])
        settings_repository.set_setting(LEGACY_KEYS["density"], normalized["density"])
        settings_repository.set_setting(LEGACY_KEYS["sizes"], normalized["card_sizes"])
        settings_repository.set_setting(LEGACY_KEYS["spans"], normalized["card_spans"])
        settings_repository.set_setting(LEGACY_KEYS["presets"], normalized["layout_presets"])
        settings_repository.set_setting(LEGACY_KEYS["default_preset"], normalized["default_layout_preset"])
        settings_repository.set_setting(LEGACY_KEYS["today_view"], normalized["today_view"])
    return normalized


def migrate_workbench_layout_config(settings_repository) -> tuple[dict[str, Any], bool]:
    raw = settings_repository.get_setting(WORKBENCH_LAYOUT_CONFIG_KEY, None)
    if isinstance(raw, dict) and int(raw.get("version", 0) or 0) >= WORKBENCH_LAYOUT_CONFIG_VERSION:
        normalized = normalize_workbench_layout_config(raw)
        changed = normalized != raw
        if changed:
            save_workbench_layout_config(settings_repository, normalized, sync_legacy=True)
        return normalized, changed

    normalized = normalize_workbench_layout_config(raw if isinstance(raw, dict) else build_layout_config_from_legacy(settings_repository))
    save_workbench_layout_config(settings_repository, normalized, sync_legacy=True)
    return normalized, True


def rename_layout_preset(settings_repository, old_name: str, new_name: str) -> tuple[bool, str]:
    config = get_workbench_layout_config(settings_repository)
    old_key = str(old_name or "").strip()
    new_key = str(new_name or "").strip()
    if not old_key:
        return False, "旧快照名称不能为空"
    if old_key not in config["layout_presets"]:
        return False, "找不到要重命名的快照"
    if not new_key:
        return False, "新快照名称不能为空"
    if new_key != old_key and new_key in config["layout_presets"]:
        return False, "新快照名称已存在"

    payload = deepcopy(config["layout_presets"].pop(old_key))
    config["layout_presets"][new_key] = payload
    if config.get("default_layout_preset") == old_key:
        config["default_layout_preset"] = new_key
    save_workbench_layout_config(settings_repository, config, sync_legacy=True)
    return True, ""


def delete_layout_preset(settings_repository, preset_name: str) -> tuple[bool, str]:
    config = get_workbench_layout_config(settings_repository)
    preset_key = str(preset_name or "").strip()
    if not preset_key:
        return False, "快照名称不能为空"
    if preset_key not in config["layout_presets"]:
        return False, "找不到要删除的快照"
    config["layout_presets"].pop(preset_key, None)
    if config.get("default_layout_preset") == preset_key:
        config["default_layout_preset"] = ""
    save_workbench_layout_config(settings_repository, config, sync_legacy=True)
    return True, ""


def export_layout_presets_to_json(settings_repository, file_path: str) -> tuple[int, str]:
    config = get_workbench_layout_config(settings_repository)
    payload = {
        "schema": "siyi-workbench-layout-presets",
        "version": WORKBENCH_LAYOUT_CONFIG_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "default_layout_preset": config.get("default_layout_preset", ""),
        "layout_presets": config.get("layout_presets", {}),
    }
    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)
    return len(payload["layout_presets"]), file_path


def import_layout_presets_from_json(settings_repository, file_path: str, replace_existing: bool = False) -> dict[str, int]:
    with open(file_path, encoding="utf-8") as file_handle:
        payload = json.load(file_handle)

    imported_presets = payload.get("layout_presets", {}) if isinstance(payload, dict) else {}
    if not isinstance(imported_presets, dict):
        imported_presets = {}

    config = get_workbench_layout_config(settings_repository)
    current = dict(config.get("layout_presets", {})) if not replace_existing else {}
    added = 0
    updated = 0

    for preset_name, preset_payload in imported_presets.items():
        normalized_name = str(preset_name or "").strip()
        if not normalized_name or not isinstance(preset_payload, dict):
            continue
        normalized_payload = normalize_workbench_layout_config(preset_payload)
        if normalized_name in current:
            updated += 1
        else:
            added += 1
        current[normalized_name] = normalized_payload

    config["layout_presets"] = current
    imported_default = str((payload or {}).get("default_layout_preset") or "").strip()
    if imported_default and imported_default in current:
        config["default_layout_preset"] = imported_default

    save_workbench_layout_config(settings_repository, config, sync_legacy=True)
    return {"added": added, "updated": updated, "total": len(current)}
