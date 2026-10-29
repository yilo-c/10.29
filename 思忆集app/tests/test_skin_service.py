"""线 C · C1+C2 —— skin.json 皮肤字段 7→13 扩展的向后兼容单测。

验收对应规划 deliverables/kernel-shell-finalization-and-ai-enhancement-plan：
  - C1：字段体系扩到 13（必填 7 + 可选 6）
  - C2：REQUIRED_COLOR_FIELDS 仍保持 7；新增 6 个可选，缺省用内置默认值补齐；
        现有 skins/moonlight/skin.json 不改仍可加载。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.skin_service import (  # noqa: E402
    ALL_COLOR_FIELDS,
    BUILTIN_SKINS,
    DEFAULT_OPTIONAL_COLORS,
    OPTIONAL_COLOR_FIELDS,
    REQUIRED_COLOR_FIELDS,
    SkinService,
    _parse_skin_file,
    _resolve_colors,
)


def _required_colors() -> dict[str, str]:
    return {
        "panelBg": "#101014",
        "panelBgSolid": "#1b1b22",
        "textPrimary": "#f2f2f5",
        "textSecondary": "#9a9aa6",
        "accent": "#818cf8",
        "borderColor": "#33333d",
        "bodyBg": "#0a0a0d",
    }


def _write_skin(skins_dir: Path, skin_id: str, colors: dict[str, Any]) -> Path:
    skin_dir = skins_dir / skin_id
    skin_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": skin_id, "name": skin_id, "version": "1.0.0", "colors": colors}
    path = skin_dir / "skin.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_field_catalog_is_seven_required_plus_six_optional():
    assert len(REQUIRED_COLOR_FIELDS) == 7
    assert len(OPTIONAL_COLOR_FIELDS) == 6
    assert ALL_COLOR_FIELDS == REQUIRED_COLOR_FIELDS + OPTIONAL_COLOR_FIELDS
    assert len(ALL_COLOR_FIELDS) == 13
    assert set(OPTIONAL_COLOR_FIELDS) == {
        "success",
        "warning",
        "danger",
        "accentWeak",
        "hoverBg",
        "overlayBg",
    }


def test_moonlight_sample_unchanged_still_loads_with_thirteen_fields():
    """C2 核心验收：不改现有 skins/moonlight/skin.json（仅 7 字段）仍可加载且补齐 13 字段。"""
    moonlight_path = APP_ROOT / "skins" / "moonlight" / "skin.json"
    raw = json.loads(moonlight_path.read_text(encoding="utf-8"))
    # 前提：样例文件确实只写了 7 个必填字段（未提前改动）。
    assert set(raw["colors"].keys()) == set(REQUIRED_COLOR_FIELDS)

    skin = _parse_skin_file(str(moonlight_path))
    assert skin is not None
    assert set(skin.colors.keys()) == set(ALL_COLOR_FIELDS)
    # 必填字段保持文件原值。
    for key in REQUIRED_COLOR_FIELDS:
        assert skin.colors[key] == str(raw["colors"][key])
    # 可选字段回落到内置默认值。
    for key in OPTIONAL_COLOR_FIELDS:
        assert skin.colors[key] == DEFAULT_OPTIONAL_COLORS[key]


def test_legacy_seven_field_skin_gets_defaults(tmp_path: Path):
    _write_skin(tmp_path, "legacy", _required_colors())
    service = SkinService(skins_dir=str(tmp_path))
    got = service.get("legacy")
    assert got is not None
    colors = got["colors"]
    assert set(colors) == set(ALL_COLOR_FIELDS)
    for key in OPTIONAL_COLOR_FIELDS:
        assert colors[key] == DEFAULT_OPTIONAL_COLORS[key]


def test_full_thirteen_field_skin_overrides_defaults(tmp_path: Path):
    colors = dict(_required_colors())
    custom_optional = {
        "success": "#00ff00",
        "warning": "#ffff00",
        "danger": "#ff0000",
        "accentWeak": "#123456",
        "hoverBg": "#654321",
        "overlayBg": "#abcdef",
    }
    colors.update(custom_optional)
    _write_skin(tmp_path, "full", colors)
    service = SkinService(skins_dir=str(tmp_path))
    got = service.get("full")
    assert got is not None
    for key, value in custom_optional.items():
        assert got["colors"][key] == value


def test_blank_optional_field_falls_back_to_default(tmp_path: Path):
    colors = dict(_required_colors())
    colors["success"] = "   "  # 空白视为缺省
    _write_skin(tmp_path, "blank", colors)
    service = SkinService(skins_dir=str(tmp_path))
    got = service.get("blank")
    assert got is not None
    assert got["colors"]["success"] == DEFAULT_OPTIONAL_COLORS["success"]


def test_missing_required_field_is_rejected(tmp_path: Path):
    colors = _required_colors()
    colors.pop("accent")  # 缺 1 个必填 → 整体不加载
    _write_skin(tmp_path, "broken", colors)
    service = SkinService(skins_dir=str(tmp_path))
    assert service.get("broken") is None


def test_resolve_colors_keeps_required_and_defaults_only_missing_optional():
    raw = dict(_required_colors())
    raw["danger"] = "#ff0000"
    resolved = _resolve_colors(raw)
    assert resolved["danger"] == "#ff0000"
    assert resolved["success"] == DEFAULT_OPTIONAL_COLORS["success"]


def test_builtin_skins_expose_all_thirteen_fields():
    service = SkinService(skins_dir=str(Path(__file__).parent / "_no_such_skins_dir"))
    listing = {item["id"]: item for item in service.list()}
    for builtin in BUILTIN_SKINS:
        assert builtin["id"] in listing
        assert set(listing[builtin["id"]]["colors"]) == set(ALL_COLOR_FIELDS)


def test_to_custom_theme_carries_all_fields():
    colors = dict(_required_colors())
    colors["warning"] = "#eeee00"
    # 经解析链构造，验证 to_custom_theme 透传全量 13 字段。
    skin = _parse_dict_skin({"id": "x", "name": "X", "colors": colors})
    theme = skin.to_custom_theme()
    assert set(theme["colors"]) == set(ALL_COLOR_FIELDS)
    assert theme["colors"]["warning"] == "#eeee00"


def _parse_dict_skin(data: dict[str, Any]):
    """把 dict 经临时文件走一遍解析链，复用 _resolve_colors 逻辑。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "skin.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        skin = _parse_skin_file(str(p))
    assert skin is not None
    return skin
