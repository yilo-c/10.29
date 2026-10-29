# Interface Freeze Contract (M3)

> Freeze date: 2026-05-02
> Status: **FROZEN** — no interface expansion without M3+ approval

## 1. assistant_task — Minimum Interface

### Status Constants

| Constant | Value |
|----------|-------|
| `STATUS_PENDING` | `"pending"` |
| `STATUS_RUNNING` | `"running"` |
| `STATUS_COMPLETED` | `"completed"` |
| `STATUS_FAILED` | `"failed"` |
| `STATUS_BLOCKED` | `"blocked"` |
| `STATUS_DEGRADED` | `"degraded"` |

### State Transitions

```
pending → running → completed
                  → failed
                  → blocked
                  → degraded
```

### Entry Methods

- `create_task(goal, *, source, delivery_channel, chat_session_id, profile_snapshot)` → AgentTask
- `from_chat(text, *, chat_session_id, profile_snapshot)` → AgentTask
- `from_rule_center(goal, *, rule_id, profile_snapshot)` → AgentTask
- `from_timer(goal, *, event_name, profile_snapshot)` → AgentTask
- `from_browser(goal, *, url, profile_snapshot)` → AgentTask
- `execute_task(task, steps, executor, authorize_callback, *, user_profile)` → dict

### Validation

- `is_valid_transition(current, next_status)` → bool
- `status_label(status)` → str

### Freeze Rules

- No new entry-point-specific `from_*` methods without documented approval.
- No new status values outside the six defined above.
- No new fields on `AgentTask` without M3+ contract review.

## 2. delivery_route — Minimum Interface

### Canonical Channels

| Alias | Resolved Channel |
|-------|-----------------|
| `"chat"`, `"chat_output"` | `CHANNEL_CHAT` |
| `"inbox"` | `CHANNEL_INBOX` |
| `"notification"` | `CHANNEL_NOTIFICATION` |
| `"both"` | `CHANNEL_BOTH` |
| `"floating"`, `"floating_output"` | `CHANNEL_FLOATING` |
| `"subtitle"`, `"subtitle_output"` | `CHANNEL_SUBTITLE` |

### Functions

- `resolve_channel(raw)` → str
- `is_visible_channel(channel)` → bool
- `build_task_progress_subtitle(...)` → str
- `build_subtitle_tone(summary)` → str

### DeliveryRecord

Fields: `channel`, `label`, `inbox_item_id`, `subtitle_text`, `delivered_to`

### Freeze Rules

- No new canonical channels without documented approval.
- No new `DeliveryRecord` fields without M3+ review.
- All task results must route through delivery_route — no direct popups from capability layer.
