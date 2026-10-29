"""皮肤服务 — 管理"皮肤插件包"（skin.json）的发现、激活与持久化。

皮肤包 = ``skins/<id>/skin.json``，colors 与前端 CustomTheme.colors 一一对应。
线 C 把字段体系从 7 个扩展为 13 个（C1/C2，向后兼容）：

必填 7（老皮肤包必须齐全，否则不加载 —— 见 REQUIRED_COLOR_FIELDS）::
    panelBg      面板背景（半透明主面板）
    panelBgSolid 面板背景（不透明，弹层/下拉）
    textPrimary  主文字
    textSecondary次要文字
    accent       强调色（品牌色，对应前端 --brand/--accent）
    borderColor  描边
    bodyBg       窗口背景

可选 6（老皮肤包缺省时自动补 DEFAULT_OPTIONAL_COLORS，不破坏加载 —— C2）::
    success     成功/正向语义色（对应前端 --success）
    warning     警告/提醒语义色
    danger      危险/删除语义色（对应前端 --danger）
    accentWeak  浅色强调底（tag/badge 底色）
    hoverBg     悬停底色
    overlayBg   遮罩/蒙层底色

前端通过 ``skin.list`` / ``skin.active`` / ``skin.activate`` RPC 调用本服务，
再用既有 ``applyCustomTheme`` 把激活皮肤的 colors 注入 CSS 变量。

内置两套样例皮肤（深海蓝 / 樱花粉），在无皮肤包目录时兜底可用；
任意步骤失败只影响皮肤功能，不影响主流程。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("siyi.services.skin")

KEY_ACTIVE_SKIN = "active_skin"

BUILTIN_SKINS: tuple[dict[str, Any], ...] = (
    {
        "id": "deep_ocean",
        "name": "深海蓝",
        "version": "1.0.0",
        "description": "深邃的深海蓝色调，专注工作场景。",
        "colors": {
            "panelBg": "#1e2a3a",
            "panelBgSolid": "#2a3a52",
            "textPrimary": "#e8eef7",
            "textSecondary": "#9fb3cc",
            "accent": "#3b82f6",
            "borderColor": "#33445c",
            "bodyBg": "#121c2a",
            "success": "#22c55e",
            "warning": "#f59e0b",
            "danger": "#ef4444",
            "accentWeak": "#24344d",
            "hoverBg": "#2a3a52",
            "overlayBg": "#000000cc",
        },
    },
    {
        "id": "sakura",
        "name": "樱花粉",
        "version": "1.0.0",
        "description": "温柔的樱花色调，适合轻松休闲。",
        "colors": {
            "panelBg": "#fff5f7",
            "panelBgSolid": "#ffe9ee",
            "textPrimary": "#3d2a35",
            "textSecondary": "#a0808f",
            "accent": "#ec4899",
            "borderColor": "#f4d4de",
            "bodyBg": "#fceef2",
            "success": "#16a34a",
            "warning": "#d97706",
            "danger": "#dc2626",
            "accentWeak": "#fde2ea",
            "hoverBg": "#ffe9ee",
            "overlayBg": "#00000066",
        },
    },
)

# 必填 7：向后兼容基线，老皮肤包缺任意一个即视为无效、不加载（C2）。
REQUIRED_COLOR_FIELDS = (
    "panelBg",
    "panelBgSolid",
    "textPrimary",
    "textSecondary",
    "accent",
    "borderColor",
    "bodyBg",
)

# 可选 6：线 C 新增（C1）。老皮肤包可完全不写，缺省用 DEFAULT_OPTIONAL_COLORS 补齐。
OPTIONAL_COLOR_FIELDS = (
    "success",
    "warning",
    "danger",
    "accentWeak",
    "hoverBg",
    "overlayBg",
)

# 全量 13 字段清单（必填在前、可选在后，保持与前端 CustomTheme.colors 对齐）。
ALL_COLOR_FIELDS = REQUIRED_COLOR_FIELDS + OPTIONAL_COLOR_FIELDS

# 可选字段的内置缺省值（偏中性/暗色系，仅作兜底；正经皮肤应显式给值）。
DEFAULT_OPTIONAL_COLORS: dict[str, str] = {
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "accentWeak": "#3f3f4a",
    "hoverBg": "#33333d",
    "overlayBg": "#00000099",
}


def _resolve_colors(raw: dict[str, Any]) -> dict[str, str]:
    """把皮肤包 colors 补齐为全量 13 字段。

    必填字段原样取（调用方须先校验齐全）；可选字段缺失或为空时回落
    DEFAULT_OPTIONAL_COLORS，保证老皮肤包（仅 7 字段）加载后仍拿到完整 13 字段。
    """
    resolved = {key: str(raw[key]) for key in REQUIRED_COLOR_FIELDS}
    for key in OPTIONAL_COLOR_FIELDS:
        value = raw.get(key)
        text = str(value).strip() if value is not None else ""
        resolved[key] = text or DEFAULT_OPTIONAL_COLORS[key]
    return resolved


@dataclass
class SkinDefinition:
    """皮肤定义（对应前端 CustomTheme.colors）。"""

    id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    colors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "colors": dict(self.colors),
        }

    def to_custom_theme(self) -> dict[str, Any]:
        """转换为前端 CustomTheme 结构（可直接喂给 applyCustomTheme）。"""
        return {
            "id": self.id,
            "name": self.name,
            "createdAt": 0,
            "colors": dict(self.colors),
        }


def _parse_skin_file(path: str) -> SkinDefinition | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("skin 解析失败 %s: %s", path, exc)
        return None

    skin_id = str(data.get("id") or "").strip()
    if not skin_id or not str(data.get("name") or "").strip():
        return None

    colors = data.get("colors") or {}
    if not all(key in colors for key in REQUIRED_COLOR_FIELDS):
        return None

    return SkinDefinition(
        id=skin_id,
        name=str(data["name"]),
        version=str(data.get("version", "1.0.0")),
        description=str(data.get("description", "")),
        colors=_resolve_colors(colors),
    )


class SkinService:
    """皮肤发现、激活与持久化。"""

    def __init__(
        self,
        settings_repository: Any = None,
        skins_dir: str | None = None,
    ) -> None:
        self.settings_repository = settings_repository
        if skins_dir is None:
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            skins_dir = os.path.join(app_root, "skins")
        self.skins_dir = skins_dir

    # ── 发现 ─────────────────────────────────────────────────────

    def list(self) -> list[dict[str, Any]]:
        """返回所有可用皮肤（内置样例 + skins/ 目录下的皮肤包）。"""
        discovered = self._discover()
        merged: dict[str, SkinDefinition] = {}
        for builtin in BUILTIN_SKINS:
            payload = dict(builtin)
            payload["colors"] = _resolve_colors(builtin.get("colors") or {})
            merged[payload["id"]] = SkinDefinition(**payload)
        for skin in discovered:
            merged[skin.id] = skin
        return [skin.to_dict() for skin in merged.values()]

    def get(self, skin_id: str) -> dict[str, Any] | None:
        for skin_dict in self.list():
            if skin_dict["id"] == skin_id:
                return skin_dict
        return None

    def _discover(self) -> list[SkinDefinition]:
        if not os.path.isdir(self.skins_dir):
            return []
        skins: list[SkinDefinition] = []
        for entry in os.listdir(self.skins_dir):
            entry_path = os.path.join(self.skins_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            manifest_path = os.path.join(entry_path, "skin.json")
            if not os.path.isfile(manifest_path):
                continue
            skin = _parse_skin_file(manifest_path)
            if skin is not None:
                skins.append(skin)
        return skins

    # ── 激活 / 持久化 ────────────────────────────────────────────

    def active(self) -> dict[str, Any] | None:
        """返回当前激活皮肤（未激活返回 None）。"""
        active_id = self._get_active_id()
        if not active_id:
            return None
        return self.get(active_id)

    def activate(self, skin_id: str) -> tuple[bool, str | None]:
        """激活皮肤并持久化。

        Returns:
            (success, error)。失败时 error 为原因，成功时为 None。
        """
        skin_id = str(skin_id or "").strip()
        if not skin_id:
            return False, "skin_id required"
        if self.get(skin_id) is None:
            return False, f"skin not found: {skin_id}"
        if self.settings_repository is None:
            logger.warning("settings_repository 不可用，无法持久化激活皮肤 %s", skin_id)
            return False, "settings unavailable"
        try:
            self.settings_repository.set_setting(KEY_ACTIVE_SKIN, skin_id)
        except Exception as exc:
            logger.warning("激活皮肤持久化失败 %s: %s", skin_id, exc)
            return False, str(exc)
        logger.info("已激活皮肤: %s", skin_id)
        return True, None

    def _get_active_id(self) -> str:
        if self.settings_repository is None:
            return ""
        try:
            value = self.settings_repository.get_setting(KEY_ACTIVE_SKIN, "")
        except Exception:
            return ""
        return str(value or "").strip()
