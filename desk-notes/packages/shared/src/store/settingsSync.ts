/** Settings sync map and helpers for Zustand → backend auto-sync. */

/** Map of Zustand state path → backend settings key */
export const SETTINGS_SYNC_MAP: Record<string, string> = {
  // Already synced (subscriber replaces manual calls)
  theme: 'ui_theme',
  locale: 'ui_language',
  aiEnabled: 'ai_enabled',
  aiAutoTagging: 'ai_auto_tagging',
  backgroundMode: 'ui_background',
  'panel.opacity': 'panel_opacity',
  'panel.color': 'panel_color',
  infoTopics: 'info_topics',
  // New sync targets
  focusDuration: 'focus_duration',
  referenceMode: 'reference_mode',
  defaultReminder: 'default_reminder',
  proactiveEnabled: 'proactive_enabled',
  autoInfoEnabled: 'auto_info_enabled',
  autoInfoInterval: 'auto_info_interval',
  viewMode: 'view_mode',
  notesViewMode: 'notes_view_mode',
  sortMode: 'sort_mode',
  defaultTemplateId: 'default_template_id',
  savedThemes: 'saved_themes',
  activeThemeId: 'active_theme_id',
  preferredModelProvider: 'preferred_model_provider',
  preferredModelName: 'preferred_model_name',
}

/** Extract a flat snapshot (backendKey → value) from Zustand state. */
export function pickSyncableSettings(state: Record<string, unknown>): Record<string, unknown> {
  const snapshot: Record<string, unknown> = {}
  for (const [path, backendKey] of Object.entries(SETTINGS_SYNC_MAP)) {
    const value = getNestedValue(state, path)
    if (value !== undefined) {
      snapshot[backendKey] = value
    }
  }
  return snapshot
}

function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
  let current: unknown = obj
  for (const part of path.split('.')) {
    if (current === null || current === undefined || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return current
}

/** Compare prev/curr snapshots, return changed backend keys with values. */
export function getChangedKeys(
  prev: Record<string, unknown>,
  curr: Record<string, unknown>,
): Array<{ backendKey: string; value: unknown }> {
  const changed: Array<{ backendKey: string; value: unknown }> = []
  for (const key of Object.keys(curr)) {
    if (prev[key] !== curr[key]) {
      changed.push({ backendKey: key, value: curr[key] })
    }
  }
  return changed
}
