"""Remove methods that were extracted to mixins from main_window.py."""
import re

FILE = r"D:\h\3.25\思忆集\思忆集app\ui\main_window.py"

# Methods to remove (extracted to mixins)
REMOVE_METHODS = {
    # --- main_window_panels.py ---
    "_show_main_page",
    "_on_nav_changed",
    "_on_ai_panel_send",
    "toggle_ai_panel",
    "show_ai_panel",
    "_wrap_suggestions_card",
    "_wrap_activity_card",
    "_refresh_activity_card",
    "_open_full_activity_log",
    "_create_card_container",
    "_create_recent_list",
    "_wrap_list_card",
    "_get_recent_card_config",
    "_update_summary_cards",
    "_update_summary_news_preview",
    "_summary_news_filter_label",
    "_refresh_summary_news_filter_buttons",
    "_set_summary_news_filter",
    "_summary_news_matches_filter",
    "_is_news_item_today",
    "_open_summary_news_item",
    "_update_focus_label",
    "_build_focus_meta",
    "_update_status_label",
    "_refresh_sync_labels",
    "_update_suggestions",
    "_make_suggestion_widget",
    "_dismiss_suggestion",
    "_convert_suggestion",
    "_build_suggestions",
    "_update_recent_lists",
    "_update_recent_card_meta",
    "_populate_recent_list",
    "_create_recent_entry_widget",
    "_build_recent_item_summary",
    "_extract_recent_time",
    "_sort_recent",
    "_recent_item_sort_key",
    "_format_timestamp",
    "_handle_suggestion_activation",
    "_focus_inbox_capture",
    "_build_inbox_brief",
    "_format_assistant_task_status_label",
    "_filter_assistant_agent_tasks",
    "_build_summary_agent_task_badge",
    "_style_summary_agent_task_item",
    "_format_assistant_agent_task_item_label",
    "_update_summary_agent_task_preview",
    # --- main_window_handlers.py ---
    "_build_assistant_tool_registry",
    "_create_assistant_event",
    "_record_assistant_event",
    "_attach_event_metadata",
    "_build_assistant_subtitle_text",
    "_derive_subtitle_tone",
    "_assistant_subtitle_anchor_point",
    "_show_assistant_subtitle",
    "_apply_assistant_tone",
    "_drain_browser_bridge_events",
    "_consume_browser_bridge_event",
    "_get_assistant_task_lifecycle",
    "_maybe_create_agent_task_from_browser_event",
    "_map_browser_recommended_action_to_tool_id",
    "_build_browser_bridge_event_message",
    "_build_browser_bridge_subtitle",
    "_summarize_schedule_results",
    # --- main_window_menus.py ---
    "_bind_shortcuts",
    "_setup_nav_overflow_menu",
    "_register_global_hotkeys",
    "_unregister_global_hotkeys",
    # --- main_window_toolbar.py ---
    "_load_workbench_preferences",
    "_persist_workbench_preferences",
    "_refresh_workbench_controls",
    "_refresh_card_span_buttons",
    "_refresh_layout_preset_combo",
    "_collect_current_layout_state",
    "_apply_layout_preset",
    "apply_workbench_layout_preset",
    "reload_workbench_preferences",
    "_save_current_layout_preset",
    "_apply_selected_layout_preset",
    "_delete_selected_layout_preset",
    "_set_today_view_button_state",
    "_toggle_today_work_view",
    "_apply_today_work_view_style",
    "_refresh_card_size_buttons",
    "_toggle_workbench_controls_panel",
    "_toggle_secondary_actions",
    "_apply_workbench_visibility",
    "_set_workbench_template",
    "_set_workbench_density",
    "_get_workbench_density_profile",
    "_toggle_section_visibility",
    "_toggle_card_visibility",
    "_apply_card_visibility",
    "_cycle_card_size_override",
    "_cycle_card_span_override",
    "_get_card_scale_factor",
    "_card_label",
    "_populate_card_order_list",
    "_on_card_order_rows_moved",
    "_rebuild_workbench_grid",
    "_reset_workbench_preferences",
}

# Property/static methods (unique prefixes)
REMOVE_PROPERTIES = {
    "active_workbench_template",
    "workbench_density",
    "section_visibility",
    "card_visibility",
    "card_order",
    "card_size_overrides",
    "card_span_overrides",
    "layout_presets",
    "default_layout_preset",
    "today_work_view_enabled",
    "alarms",
    "reminder_service",
}

# Properties and their decorators to track
HAS_GETTER = REMOVE_PROPERTIES.copy()

with open(FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

in_class = False
in_extracted = False
extracted_nesting = 0
output = []
i = 0

while i < len(lines):
    line = lines[i]

    # Detect class MainWindow
    if re.match(r'^class MainWindow\(', line):
        in_class = True
        output.append(line)
        i += 1
        continue

    if not in_class:
        output.append(line)
        i += 1
        continue

    # Check if we're at a method/property we want to remove
    stripped = line.rstrip('\n')

    # Match method definitions
    method_match = re.match(r'^    def (\w+)\(', stripped)
    if not method_match:
        # Check for @property on next line
        if stripped.strip() == '@property' and i + 1 < len(lines):
            next_line = lines[i + 1].rstrip('\n')
            prop_match = re.match(r'^    def (\w+)\(', next_line)
            if prop_match and prop_match.group(1) in HAS_GETTER:
                # Remove this property getter (and setter if follows)
                name = prop_match.group(1)
                # Skip @property line
                i += 1
                # Skip def line
                i += 1
                # Skip body until next method at same indent
                while i < len(lines) and not re.match(r'^    (?:@|\w)', lines[i]) and not re.match(r'^\S', lines[i]):
                    i += 1
                # Check if a setter follows
                if i < len(lines) and re.match(r'^    @\w+\.setter', lines[i]):
                    # Check if it's the setter for same property
                    setter_match = re.match(r'^    @(\w+)\.setter', lines[i])
                    if setter_match and setter_match.group(1) == name:
                        i += 1  # Skip @setter line
                        i += 1  # Skip def line
                        while i < len(lines) and not re.match(r'^    (?:@|\w)', lines[i]) and not re.match(r'^\S', lines[i]):
                            i += 1
                continue

        output.append(line)
        i += 1
        continue

    method_name = method_match.group(1)

    if method_name in REMOVE_METHODS:
        # Skip this method line
        i += 1
        # Skip body
        while i < len(lines):
            next_line = lines[i]
            # Stop at next method, property, or decorator at class level (4 spaces)
            if re.match(r'^    def \w+\(', next_line) or re.match(r'^    @(?:property|\w+\.setter)', next_line) or re.match(r'^    @staticmethod\b', next_line):
                break
            # Stop at class-level comment blocks (##)
            if re.match(r'^    # ──', next_line):
                break
            # Stop at end of class
            if re.match(r'^\S', next_line) and not next_line.startswith(' '):
                break
            i += 1
        continue

    # Check for @staticmethod before method
    if stripped.strip() == '@staticmethod' and i + 1 < len(lines):
        next_line = lines[i + 1].rstrip('\n')
        static_match = re.match(r'^    def (\w+)\(', next_line)
        if static_match and static_match.group(1) in REMOVE_METHODS:
            i += 1
            # Skip the static def
            i += 1
            while i < len(lines):
                nl = lines[i]
                if re.match(r'^    (?:def |@)', nl) or re.match(r'^\S', nl):
                    break
                i += 1
            continue

    output.append(line)
    i += 1

# Write back
with open(FILE, "w", encoding="utf-8") as f:
    f.writelines(output)

print(f"Done. {len(REMOVE_METHODS)} methods + {len(REMOVE_PROPERTIES)} properties removed from {FILE}")
print(f"Original: ~{len(lines)} lines -> New: ~{len(output)} lines")
