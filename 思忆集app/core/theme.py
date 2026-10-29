"""UI Theme Token — 思忆集设计系统 token 定义。

设计参考：
- openclaw 的 CSS Custom Properties token 层
- Material Design 3 的 color/typography/spacing token 体系

用途：
    统一管理颜色、字体、间距等设计 token，避免在 QSS/代码中硬编码魔法值。
    支持 light/dark 双主题切换。

使用方式：
    from core.theme import current_tokens, ThemeMode

    tokens = current_tokens(ThemeMode.LIGHT)
    widget.setStyleSheet(f"background-color: {tokens.surface}; color: {tokens.on_surface};")
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ThemeMode(Enum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class ThemeTokens:
    """不可变设计 token 集合。"""
    # --- 颜色 ---
    primary: str
    on_primary: str
    surface: str
    on_surface: str
    surface_variant: str
    on_surface_variant: str
    accent: str
    error: str
    on_error: str
    warning: str
    success: str
    border: str
    border_subtle: str

    # --- 字体 ---
    font_family: str
    font_size_xs: int
    font_size_sm: int
    font_size_base: int
    font_size_lg: int
    font_size_xl: int
    font_size_hero: int

    # --- 间距 ---
    spacing_xs: int
    spacing_sm: int
    spacing_md: int
    spacing_lg: int
    spacing_xl: int
    spacing_section: int

    # --- 圆角 ---
    radius_sm: int
    radius_md: int
    radius_lg: int

    # --- 动画 ---
    duration_fast_ms: int
    duration_normal_ms: int


_LIGHT_TOKENS = ThemeTokens(
    primary="#2563EB",
    on_primary="#FFFFFF",
    surface="#FAFAFA",
    on_surface="#1A1A1A",
    surface_variant="#F0F0F0",
    on_surface_variant="#555555",
    accent="#7C3AED",
    error="#DC2626",
    on_error="#FFFFFF",
    warning="#D97706",
    success="#16A34A",
    border="#E0E0E0",
    border_subtle="#F0F0F0",
    font_family="'Microsoft YaHei UI', 'Segoe UI', sans-serif",
    font_size_xs=10,
    font_size_sm=12,
    font_size_base=14,
    font_size_lg=16,
    font_size_xl=20,
    font_size_hero=28,
    spacing_xs=4,
    spacing_sm=8,
    spacing_md=12,
    spacing_lg=16,
    spacing_xl=24,
    spacing_section=32,
    radius_sm=4,
    radius_md=8,
    radius_lg=12,
    duration_fast_ms=150,
    duration_normal_ms=300,
)

_DARK_TOKENS = ThemeTokens(
    primary="#60A5FA",
    on_primary="#0A0A0A",
    surface="#1E1E1E",
    on_surface="#E8E8E8",
    surface_variant="#2A2A2A",
    on_surface_variant="#AAAAAA",
    accent="#A78BFA",
    error="#F87171",
    on_error="#1A1A1A",
    warning="#FBBF24",
    success="#4ADE80",
    border="#3A3A3A",
    border_subtle="#2A2A2A",
    font_family="'Microsoft YaHei UI', 'Segoe UI', sans-serif",
    font_size_xs=10,
    font_size_sm=12,
    font_size_base=14,
    font_size_lg=16,
    font_size_xl=20,
    font_size_hero=28,
    spacing_xs=4,
    spacing_sm=8,
    spacing_md=12,
    spacing_lg=16,
    spacing_xl=24,
    spacing_section=32,
    radius_sm=4,
    radius_md=8,
    radius_lg=12,
    duration_fast_ms=150,
    duration_normal_ms=300,
)


def current_tokens(mode: ThemeMode = ThemeMode.LIGHT) -> ThemeTokens:
    """返回指定主题模式下的 token 集合。"""
    if mode == ThemeMode.DARK:
        return _DARK_TOKENS
    return _LIGHT_TOKENS


def tokens_to_qss_variables(tokens: ThemeTokens) -> str:
    """将 token 生成为 QSS 变量注释块（用于开发参考）。"""
    lines = [f"/* Theme Token: {field} = {getattr(tokens, field)} */" for field in tokens.__dataclass_fields__]
    return "\n".join(lines)
