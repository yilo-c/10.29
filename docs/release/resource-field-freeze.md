# Resource Field Freeze Contract (M3)

> Freeze date: 2026-05-02
> Status: **FROZEN** — no field additions without M3+ approval

## Resources

### 1. notes (personal_notes)

| Field | Type | Sync | Tombstone |
|-------|------|------|-----------|
| `id` | TEXT PK | — | — |
| `user_id` | TEXT FK | — | — |
| `remote_id` | TEXT UNIQUE | sync key | — |
| `title` | TEXT | synced | — |
| `content` | TEXT | synced | — |
| `tags_json` | TEXT DEFAULT '[]' | synced | — |
| `category` | TEXT | synced | — |
| `folder_path` | TEXT DEFAULT '/' | synced | — |
| `favorite` | INT DEFAULT 0 | synced | — |
| `related_news_url` | TEXT | synced | — |
| `linked_task_id` | TEXT | synced | — |
| `created_at` | TEXT | — | — |
| `updated_at` | TEXT | cursor | — |
| `deleted_at` | TEXT | — | tombstone marker |
| `version` | INT DEFAULT 1 | conflict detection | — |
| `device_id` | TEXT | source identity | — |
| `sync_state` | TEXT DEFAULT 'synced' | — | — |
| `last_synced_at` | TEXT | — | — |

### 2. personal_tasks

| Field | Type | Sync | Tombstone |
|-------|------|------|-----------|
| `id` | TEXT PK | — | — |
| `user_id` | TEXT FK | — | — |
| `remote_id` | TEXT UNIQUE | sync key | — |
| `name` | TEXT | synced | — |
| `note` | TEXT | synced | — |
| `is_completed` | INT DEFAULT 0 | synced | — |
| `reminder_time` | TEXT | synced | — |
| `linked_note_id` | TEXT | synced | — |
| `created_at` | TEXT | — | — |
| `updated_at` | TEXT | cursor | — |
| `deleted_at` | TEXT | — | tombstone marker |
| `version` | INT DEFAULT 1 | conflict detection | — |
| `device_id` | TEXT | source identity | — |
| `sync_state` | TEXT DEFAULT 'synced' | — | — |
| `last_synced_at` | TEXT | — | — |

### 3. saved_articles (personal_news_items)

| Field | Type | Sync | Tombstone |
|-------|------|------|-----------|
| `id` | TEXT PK | — | — |
| `user_id` | TEXT FK | — | — |
| `remote_id` | TEXT UNIQUE | sync key | — |
| `item_key` | TEXT | synced | — |
| `title` | TEXT | synced | — |
| `url` | TEXT | synced | — |
| `summary` | TEXT | synced | — |
| `content` | TEXT | synced | — |
| `source` | TEXT | synced | — |
| `category` | TEXT | synced | — |
| `published_at` | TEXT | synced | — |
| `image_url` | TEXT | synced | — |
| `is_favorite` | INT DEFAULT 0 | synced | — |
| `created_at` | TEXT | — | — |
| `updated_at` | TEXT | cursor | — |
| `deleted_at` | TEXT | — | tombstone marker |
| `version` | INT DEFAULT 1 | conflict detection | — |
| `device_id` | TEXT | source identity | — |
| `sync_state` | TEXT DEFAULT 'synced' | — | — |
| `last_synced_at` | TEXT | — | — |

### 4. translation_history (personal_translation_history)

| Field | Type | Sync | Tombstone |
|-------|------|------|-----------|
| `id` | TEXT PK | — | — |
| `user_id` | TEXT FK | — | — |
| `remote_id` | TEXT UNIQUE | sync key | — |
| `history_key` | TEXT | synced | — |
| `source_text` | TEXT | synced | — |
| `translated_text` | TEXT | synced | — |
| `from_lang` | TEXT | synced | — |
| `to_lang` | TEXT | synced | — |
| `raw_result_json` | TEXT | synced | — |
| `created_at` | TEXT | — | — |
| `updated_at` | TEXT | cursor | — |
| `deleted_at` | TEXT | — | tombstone marker |
| `version` | INT DEFAULT 1 | conflict detection | — |
| `device_id` | TEXT | source identity | — |
| `sync_state` | TEXT DEFAULT 'synced' | — | — |
| `last_synced_at` | TEXT | — | — |

### 5. wordbooks (personal_wordbooks)

| Field | Type | Sync | Tombstone |
|-------|------|------|-----------|
| `id` | TEXT PK | — | — |
| `user_id` | TEXT FK | — | — |
| `remote_id` | TEXT UNIQUE | sync key | — |
| `book_key` | TEXT | synced | — |
| `name` | TEXT | synced | — |
| `description` | TEXT | synced | — |
| `created_at` | TEXT | — | — |
| `updated_at` | TEXT | cursor | — |
| `deleted_at` | TEXT | — | tombstone marker |
| `version` | INT DEFAULT 1 | conflict detection | — |
| `device_id` | TEXT | source identity | — |
| `sync_state` | TEXT DEFAULT 'synced' | — | — |
| `last_synced_at` | TEXT | — | — |

## Sync Cursor Semantics

1. **Cursor field**: `updated_at` (ISO 8601) — used as the incremental sync cursor in `since_updated_at` or `cursor` query parameters.
2. **Fallback**: When the server returns no cursor for a single-page response, the sync orchestrator uses `datetime.utcnow().isoformat() + "Z"` as a timestamp-based fallback cursor.
3. **Cursor storage**: Cursors are stored per `(provider, resource_name)` in `integration_sync_cursors` / `sync_cursors`.

## Tombstone Rules

1. **Soft delete**: All resources use `deleted_at` as a tombstone marker (NULL = alive, non-NULL = deleted).
2. **Local-only deletion**: If `deleted_at` is set AND `remote_id` is NULL (never synced), the local record is resolved via `resolve_local_only_deleted_*` — no remote call is made.
3. **Remote deletion**: If `deleted_at` is set AND `remote_id` exists, the orchestrator issues a DELETE to the remote and marks the record as synced locally.
4. **Sync_state field**: `"synced"` = clean, `"dirty"` = pending local changes. During merge, dirty records with conflicting remote changes raise a conflict entry.

## Freeze Enforcement

- No new fields may be added to the above five resources without documented approval.
- Any new sync resource must follow the same cursor/tombstone/sync_state pattern.
- The existing `api_sync_orchestrator.py` `ResourceSyncSpec` must be updated to register new resources.
