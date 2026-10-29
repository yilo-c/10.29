from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from plugins.protocol import (
    CardPluginSpec,
    DataSourcePluginSpec,
    FloatingAssistantPluginSpec,
    PluginRegistry,
    QuickActionPluginSpec,
)


def test_plugin_registry_describes_extension_points_and_counts():
    registry = PluginRegistry()
    registry.register_card_plugin(
        CardPluginSpec(
            plugin_id="builtin.card.tasks",
            name="tasks",
            version="1.0.0",
            namespace="card.tasks",
        )
    )
    registry.register_quick_action_plugin(
        QuickActionPluginSpec(
            plugin_id="builtin.quick.capture",
            name="capture",
            version="1.0.0",
            action_id="capture",
        )
    )
    registry.register_floating_assistant_plugin(
        FloatingAssistantPluginSpec(
            plugin_id="builtin.floating.panel",
            name="panel",
            version="1.0.0",
            entry_id="floating.panel",
        )
    )
    registry.register_data_source_plugin(
        DataSourcePluginSpec(
            plugin_id="builtin.source.news",
            name="news",
            version="1.0.0",
            source_id="news.feed",
        )
    )

    contract = registry.describe_contract()

    assert len(contract["extension_points"]) == 4
    assert contract["counts"]["cards"] == 1
    assert contract["counts"]["quick_actions"] == 1
    assert contract["counts"]["floating_entries"] == 1
    assert contract["counts"]["data_sources"] == 1
