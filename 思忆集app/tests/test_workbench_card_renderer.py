"""workbench_card_renderer 纯函数 / widget 工厂 单元测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
pytest.skip("ui 包 / PyQt6 旧桌面路径已退役移除（headless venv 有意不装 desktop extras），守卫对象不存在——本文件去留待统一决策", allow_module_level=True)
from workbench.card_renderer import (
    CARD_CONFIG_MAP,
    CARD_LABEL_MAP,
    DENSITY_PROFILES,
    compute_grid_placements,
    get_card_config,
    get_card_label,
    get_card_scale_factor,
    get_density_profile,
)

# ── 卡片配置查表 ─────────────────────────────────────────────

class TestCardConfig:
    def test_label_map_covers_all_standard_keys(self):
        for key in ["tasks", "notes", "news", "translate", "inbox", "suggestions"]:
            assert key in CARD_LABEL_MAP

    def test_get_card_label_returns_chinese(self):
        assert get_card_label("tasks") == "任务"
        assert get_card_label("unknown") == "unknown"

    def test_config_map_has_required_fields(self):
        for key, cfg in CARD_CONFIG_MAP.items():
            assert {"eyebrow", "title", "subtitle", "surface", "button"} <= set(cfg), key

    def test_get_card_config_raises_on_missing_key(self):
        with pytest.raises(KeyError):
            get_card_config("nonexistent")


# ── 密度档位 ─────────────────────────────────────────────────

class TestDensityProfile:
    def test_all_profiles_have_required_keys(self):
        required = {"grid_spacing", "default_card_min_height", "news_card_min_height",
                     "suggestions_card_min_height", "list_min_height",
                     "news_list_min_height", "suggestions_list_min_height"}
        for name, profile in DENSITY_PROFILES.items():
            assert required <= set(profile), name

    def test_unknown_density_falls_back_to_standard(self):
        assert get_density_profile("unknown") == DENSITY_PROFILES["standard"]

    def test_spacing_increases_with_comfort(self):
        assert (DENSITY_PROFILES["compact"]["grid_spacing"]
                < DENSITY_PROFILES["standard"]["grid_spacing"]
                < DENSITY_PROFILES["comfortable"]["grid_spacing"])


# ── 缩放因子 ─────────────────────────────────────────────────

class TestCardScale:
    def test_default_medium_returns_one(self):
        assert get_card_scale_factor("news", {}) == 1.0

    def test_small_returns_less_than_one(self):
        assert get_card_scale_factor("news", {"news": "small"}) < 1.0

    def test_large_returns_greater_than_one(self):
        assert get_card_scale_factor("tasks", {"tasks": "large"}) > 1.0


# ── 网格排布算法 ─────────────────────────────────────────────

class TestGridPlacements:
    def test_empty_visible_returns_empty(self):
        assert compute_grid_placements([], "balanced", {}) == []

    def test_balanced_two_cards_same_row(self):
        placements = compute_grid_placements(["tasks", "notes"], "balanced", {})
        assert len(placements) == 2
        assert placements[0] == ("tasks", 0, 0, 1, 1)
        assert placements[1] == ("notes", 0, 1, 1, 1)

    def test_balanced_three_cards_wraps_row(self):
        placements = compute_grid_placements(["tasks", "notes", "news"], "balanced", {})
        assert len(placements) == 3
        assert placements[2][1] == 1  # third card on row 1

    def test_focus_puts_news_first_spanning_two(self):
        placements = compute_grid_placements(
            ["tasks", "news", "notes"], "focus", {},
        )
        news_placement = [p for p in placements if p[0] == "news"][0]
        assert news_placement == ("news", 0, 0, 1, 2)

    def test_focus_without_news_is_balanced(self):
        placements = compute_grid_placements(["tasks", "notes"], "focus", {})
        assert placements == [("tasks", 0, 0, 1, 1), ("notes", 0, 1, 1, 1)]

    def test_daily_template_reorders_and_spans_news(self):
        placements = compute_grid_placements(
            ["notes", "tasks", "inbox", "news"], "daily", {},
        )
        keys = [p[0] for p in placements]
        assert keys.index("tasks") < keys.index("news")
        news_placement = [p for p in placements if p[0] == "news"][0]
        assert news_placement[4] == 2  # col_span = 2

    def test_span_override_forces_double_column(self):
        placements = compute_grid_placements(
            ["tasks", "notes"], "balanced", {"tasks": 2},
        )
        tasks_placement = [p for p in placements if p[0] == "tasks"][0]
        assert tasks_placement[4] == 2

    def test_span_override_pushes_next_card_to_new_row(self):
        placements = compute_grid_placements(
            ["tasks", "notes", "news"], "balanced", {"notes": 2},
        )
        # tasks at (0,0), notes spans so pushed to row 1, news at row 2
        assert placements[0] == ("tasks", 0, 0, 1, 1)
        notes_p = [p for p in placements if p[0] == "notes"][0]
        assert notes_p[1] == 1 and notes_p[4] == 2
