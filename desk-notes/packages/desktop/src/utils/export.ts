import { TodoItem, FloatingNote, Category, Conversation, CustomTheme, AIConfigProfile, Folder } from '../types'
import { getCurrentLocale } from '../i18n'
import type { CanvasState, SupportedLocale, UserProfile, ViewMode } from '@desk-notes/shared'
import { useStore } from '../store/useStore'

interface ExportData {
  todos: TodoItem[]
  categories: Category[]
  floatingNotes: FloatingNote[]
}

function getChildTodos(todos: TodoItem[], parentId: string): TodoItem[] {
  return todos.filter(t => t.parentId === parentId)
    .sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
}

function renderTodoLine(t: TodoItem, indent: number, catMap?: Map<string, Category>): string {
  const prefix = '  '.repeat(indent)
  const checkbox = t.done ? '[x]' : '[ ]'
  const parts: string[] = [`${prefix}- ${checkbox} ${t.text}`]
  if (t.category && catMap?.has(t.category)) {
    parts.push(` [${catMap.get(t.category)!.label}]`)
  }
  if (t.priority !== undefined) {
    const labels = ['🔴', '🟡', '⚪']
    parts.push(` ${labels[t.priority]}`)
  }
  if (t.dueDate) {
    parts.push(` 📅${new Date(t.dueDate).toLocaleDateString(getCurrentLocale())}`)
  }
  return parts.join('')
}

function renderTodoTree(todos: TodoItem[], catMap?: Map<string, Category>, parentId?: string, indent = 0): string[] {
  const children = parentId
    ? getChildTodos(todos, parentId)
    : todos.filter(t => !t.parentId).sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
  const lines: string[] = []
  for (const t of children) {
    lines.push(renderTodoLine(t, indent, catMap))
    lines.push(...renderTodoTree(todos, catMap, t.id, indent + 1))
  }
  return lines
}

/**
 * Generate Markdown export from all notes data
 */
export function exportToMarkdown(data: ExportData): string {
  const locale = getCurrentLocale()
  const lines: string[] = []
  lines.push('# 思忆便签导出')
  lines.push('')
  lines.push(`> 导出时间：${new Date().toLocaleString(locale)}`)
  lines.push(`> 待办：${data.todos.length} | 便签：${data.floatingNotes.length}`)
  lines.push('')

  // --- Categories used ---
  const usedCatIds = new Set<string>()
  data.todos.forEach(t => { if (t.category) usedCatIds.add(t.category) })
  data.floatingNotes.forEach(n => { if (n.todos) n.todos.forEach(t => { if (t.category) usedCatIds.add(t.category) }) })
  const catMap = new Map(data.categories.map(c => [c.id, c]))

  // --- Todo list section ---
  lines.push('## 📋 待办清单')
  lines.push('')

  const activeTodos = data.todos.filter(t => !t.done)
  const doneTodos = data.todos.filter(t => t.done)

  if (activeTodos.length > 0) {
    lines.push('### 未完成')
    lines.push(...renderTodoTree(activeTodos, catMap))
    lines.push('')
  }

  if (doneTodos.length > 0) {
    lines.push('### 已完成')
    lines.push(...renderTodoTree(doneTodos, catMap))
    lines.push('')
  }

  // --- Floating notes section ---
  lines.push('---')
  lines.push('')
  lines.push('## 📌 浮动便签')
  lines.push('')

  if (data.floatingNotes.length === 0) {
    lines.push('*暂无浮动便签*')
    lines.push('')
  } else {
    for (const n of data.floatingNotes) {
      lines.push(`### ${n.title}`)
      lines.push('')

      if (n.type === 'text') {
        if (n.content) {
          // Strip HTML tags for clean Markdown export
          const text = n.content.replace(/<[^>]*>/g, '').trim()
          if (text) {
            lines.push(text)
            lines.push('')
          }
        }
      } else if (n.type === 'todo' && n.todos && n.todos.length > 0) {
        for (const t of n.todos) {
          lines.push(t.done ? `- [x] ${t.text}` : `- [ ] ${t.text}`)
        }
        lines.push('')
      }
    }
  }

  return lines.join('\n')
}

/**
 * Generate JSON export from all notes data
 */
export function exportToJSON(data: ExportData): string {
  return JSON.stringify(
    {
      todos: data.todos,
      categories: data.categories,
      floatingNotes: data.floatingNotes.map(n => ({
        ...n,
        todos: n.todos || [],
      })),
      exportedAt: new Date().toISOString(),
      version: 1,
    },
    null,
    2
  )
}

/**
 * Trigger a file download via the browser
 */
export function downloadAsFile(content: string, filename: string, mimeType: string = 'text/markdown;charset=utf-8') {
  const ei = window.electronAPI as ElectronAPI | undefined
  const isElectron = !!ei

  if (isElectron && ei.showSaveDialog) {
    // Electron: use native save dialog (the dialog sends content back via IPC)
    ei.showSaveDialog({ filename, content, mimeType })
    return
  }

  // Browser: standard download via ObjectURL
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/* ── Backup / Restore system ── */

export interface BackupData {
  version: 2
  exportedAt: string
  appVersion?: string
  todos: TodoItem[]
  categories: Category[]
  floatingNotes: FloatingNote[]
  folders: Folder[]
  conversations: Conversation[]
  aiProfiles: AIConfigProfile[]
  /** @deprecated use aiEnabled/aiAutoTagging instead */
  aiConfig?: { enabled: boolean; apiUrl: string; model: string; protocol: string; autoTagging: boolean }
  aiEnabled: boolean
  aiAutoTagging: boolean
  activeProfileId: string | null
  savedThemes: CustomTheme[]
  activeThemeId: string | null
  theme: 'dark' | 'black' | 'light'
  backgroundMode: 'acrylic' | 'pure-white' | 'pure-black'
  locale: SupportedLocale
  userProfile: UserProfile
  canvas: CanvasState
  defaultTemplateId: string | null
  referenceMode: 'simple' | 'full'
  defaultReminder: number
  focusDuration: number
  pomodoroSessionCount: number
  sortMode: 'created' | 'priority' | 'dueDate' | 'alpha'
  viewMode: ViewMode
  notesViewMode: 'canvas' | 'list'
  activeCat: string
  activeFolderId: string | null
  filterStatus: 'all' | 'active' | 'completed' | 'overdue'
  filterPriority: number[]
  appPin: string | null
  proactiveEnabled: boolean
}

export interface RestoreSummary {
  todos: number
  notes: number
  conversations: number
  folders: number
  categories: number
  themes: number
  aiProfiles: number
}

export interface RestoreResult {
  data: BackupData
  summary: RestoreSummary
  warnings: string[]
}

type StoreState = ReturnType<typeof import('../store/useStore')['useStore']['getState']>

function summarizeRestore(data: BackupData): RestoreSummary {
  return {
    todos: data.todos?.length ?? 0,
    notes: data.floatingNotes?.length ?? 0,
    conversations: data.conversations?.length ?? 0,
    folders: data.folders?.length ?? 0,
    categories: data.categories?.length ?? 0,
    themes: data.savedThemes?.length ?? 0,
    aiProfiles: data.aiProfiles?.length ?? 0,
  }
}

/**
 * Collect all user data from store into a full BackupData object
 */
export function exportAllToJSON(getState: () => StoreState): string {
  const s = getState()
  const data: BackupData = {
    version: 2,
    exportedAt: new Date().toISOString(),
    appVersion: '1.2.0',
    todos: s.todos ?? [],
    categories: s.categories ?? [],
    floatingNotes: s.floatingNotes ?? [],
    folders: s.folders ?? [],
    conversations: s.conversations ?? [],
    aiEnabled: s.aiEnabled ?? true,
    aiAutoTagging: s.aiAutoTagging ?? false,
    aiProfiles: s.aiProfiles ?? [],
    activeProfileId: s.activeProfileId ?? null,
    savedThemes: s.savedThemes ?? [],
    activeThemeId: s.activeThemeId ?? null,
    theme: s.theme ?? 'dark',
    backgroundMode: s.backgroundMode ?? 'acrylic',
    locale: s.locale ?? 'zh-CN',
    userProfile: s.userProfile ?? { birthDate: null, birthHour: null, gender: null, city: '' },
    canvas: s.canvas ?? { offsetX: 0, offsetY: 0, zoom: 1 },
    defaultTemplateId: s.defaultTemplateId ?? null,
    referenceMode: s.referenceMode ?? 'simple',
    defaultReminder: s.defaultReminder ?? 15,
    focusDuration: s.focusDuration ?? 25,
    pomodoroSessionCount: s.pomodoroSessionCount ?? 0,
    sortMode: s.sortMode ?? 'created',
    viewMode: s.viewMode ?? 'list',
    notesViewMode: s.notesViewMode ?? 'canvas',
    activeCat: s.activeCat ?? 'all',
    activeFolderId: s.activeFolderId ?? null,
    filterStatus: s.filterStatus ?? 'all',
    filterPriority: s.filterPriority ?? [],
    appPin: s.appPin ?? null,
    proactiveEnabled: s.proactiveEnabled ?? true,
  }
  return JSON.stringify(data, null, 2)
}

/**
 * Parse a backup JSON string and validate its structure.
 * Returns RestoreResult with summary + warnings, or throws on critical failure.
 */
export function parseAndValidateBackup(content: string): RestoreResult {
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(content)
  } catch {
    throw new Error('Invalid JSON')
  }

  if (!parsed || typeof parsed !== 'object') {
    throw new Error('Invalid backup format')
  }

  const version = parsed.version as number | undefined
  if (version !== 1 && version !== 2) {
    throw new Error(`Unsupported backup version: ${version}`)
  }

  const warnings: string[] = []
  const requiredArrays = ['todos', 'floatingNotes', 'categories', 'conversations', 'savedThemes', 'aiProfiles', 'folders']
  for (const key of requiredArrays) {
    if (!Array.isArray(parsed[key])) {
      if (version === 1) {
        parsed[key] = []
        warnings.push(`Missing field "${key}" — initialized to empty array`)
      } else {
        throw new Error(`Backup is missing required field: ${key}`)
      }
    }
  }

  // Version 1 → 2 migration: add defaults for missing fields
  if (version === 1) {
    parsed = {
      version: 2,
      exportedAt: parsed.exportedAt ?? new Date().toISOString(),
      todos: parsed.todos ?? [],
      categories: parsed.categories ?? [],
      floatingNotes: parsed.floatingNotes ?? [],
      folders: parsed.folders ?? [],
      conversations: parsed.conversations ?? [],
      aiEnabled: (parsed as any).aiEnabled ?? (parsed as any).aiConfig?.enabled ?? true,
      aiAutoTagging: (parsed as any).aiAutoTagging ?? (parsed as any).aiConfig?.autoTagging ?? false,
      aiProfiles: parsed.aiProfiles ?? [],
      activeProfileId: parsed.activeProfileId ?? null,
      savedThemes: parsed.savedThemes ?? [],
      activeThemeId: parsed.activeThemeId ?? null,
      theme: parsed.theme ?? 'dark',
      backgroundMode: parsed.backgroundMode ?? 'acrylic',
      locale: parsed.locale ?? 'zh-CN',
      userProfile: parsed.userProfile ?? { birthDate: null, birthHour: null, gender: null, city: '' },
      canvas: parsed.canvas ?? { offsetX: 0, offsetY: 0, zoom: 1 },
      defaultTemplateId: parsed.defaultTemplateId ?? null,
      referenceMode: parsed.referenceMode ?? 'simple',
      defaultReminder: parsed.defaultReminder ?? 15,
      focusDuration: parsed.focusDuration ?? 25,
      pomodoroSessionCount: parsed.pomodoroSessionCount ?? 0,
      sortMode: parsed.sortMode ?? 'created',
      viewMode: parsed.viewMode ?? 'list',
      notesViewMode: parsed.notesViewMode ?? 'canvas',
      activeCat: parsed.activeCat ?? 'all',
      activeFolderId: parsed.activeFolderId ?? null,
      filterStatus: parsed.filterStatus ?? 'all',
      filterPriority: parsed.filterPriority ?? [],
      appPin: parsed.appPin ?? null,
      proactiveEnabled: parsed.proactiveEnabled ?? true,
    }
    warnings.push('Migrated from backup version 1 to version 2')
  }

  const data = parsed as unknown as BackupData
  const summary = summarizeRestore(data)
  return { data, summary, warnings }
}

/**
 * Apply a parsed BackupData to the Zustand store.
 * Uses setState (merge) so transient fields not in BackupData survive.
 */
export function applyBackupToStore(data: BackupData): void {
  useStore.setState({
    todos: data.todos,
    categories: data.categories,
    floatingNotes: data.floatingNotes,
    folders: data.folders,
    conversations: data.conversations,
    aiEnabled: data.aiEnabled ?? data.aiConfig?.enabled ?? true,
    aiAutoTagging: data.aiAutoTagging ?? data.aiConfig?.autoTagging ?? false,
    aiProfiles: data.aiProfiles,
    activeProfileId: data.activeProfileId,
    savedThemes: data.savedThemes,
    activeThemeId: data.activeThemeId,
    theme: data.theme,
    backgroundMode: data.backgroundMode,
    locale: data.locale,
    userProfile: data.userProfile,
    canvas: data.canvas,
    defaultTemplateId: data.defaultTemplateId,
    referenceMode: data.referenceMode,
    defaultReminder: data.defaultReminder,
    focusDuration: data.focusDuration,
    pomodoroSessionCount: data.pomodoroSessionCount,
    sortMode: data.sortMode,
    viewMode: data.viewMode,
    notesViewMode: data.notesViewMode,
    activeCat: data.activeCat,
    activeFolderId: data.activeFolderId,
    filterStatus: data.filterStatus,
    filterPriority: data.filterPriority,
    appPin: data.appPin,
    proactiveEnabled: data.proactiveEnabled,
  })
}
