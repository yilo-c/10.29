import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { getPlatformStorage } from './storage'
import { getWsClient } from '../utils/ai'
import { createTodoSlice, setTrashHandler as setTodoTrashHandler } from './slices/todoSlice'
import { createNoteSlice, setTrashHandler as setNoteTrashHandler } from './slices/noteSlice'
import { createConversationSlice } from './slices/conversationSlice'
import { createChatSessionSlice } from './slices/chatSessionSlice'
import { createUiSlice, getDefaultSidebarConfig } from './slices/uiSlice'
import { createContextSlice } from './slices/contextSlice'
import { createPermissionSlice } from './slices/permissionSlice'
import { pickSyncableSettings, getChangedKeys } from './settingsSync'

import type { Store } from './types'

// ── Migration validation helper ──────────────────────────────────────────
/**
 * ★ S4 修复：验证迁移后的状态是否有效
 * 返回错误数组，空数组表示验证通过
 */
function validateMigratedState(state: Record<string, unknown>): string[] {
  const errors: string[] = []
  
  // 验证关键字段类型
  const requiredArrays = ['todos', 'floatingNotes', 'conversations']
  for (const key of requiredArrays) {
    if (key in state && !Array.isArray(state[key])) {
      errors.push(`Field "${key}" should be an array, got ${typeof state[key]}`)
    }
  }
  
  const requiredObjects = ['categories', 'folders']
  for (const key of requiredObjects) {
    if (key in state && (typeof state[key] !== 'object' || state[key] === null)) {
      errors.push(`Field "${key}" should be an object, got ${typeof state[key]}`)
    }
  }
  
  // 验证枚举值
  const validThemes = ['light', 'dark', 'auto']
  if ('theme' in state && !validThemes.includes(state.theme as string)) {
    errors.push(`Field "theme" has invalid value: ${state.theme}`)
  }
  
  return errors
}

// ── Injectable trash handler (platform-specific) ──────────────────────────
type TrashPayload = { id: string; type: 'note' | 'todo'; data: unknown; deletedAt: number }
export function setTrashHandler(handler: (item: TrashPayload) => void) {
  setTodoTrashHandler(handler)
  setNoteTrashHandler(handler)
}

export const useStore = create<Store>()(
  persist(
    (...a) => ({
      ...createTodoSlice(...a),
      ...createNoteSlice(...a),
      ...createConversationSlice(...a),
      ...createChatSessionSlice(...a),
      ...createContextSlice(...a),
      ...createPermissionSlice(...a),
      ...createUiSlice(...a),
    }),
    {
      name: 'desk-notes-storage',
      version: 6,
      migrate: (persistedState: unknown, version: number) => {
        // ★ S4 修复：增强迁移安全性，添加错误处理和回滚机制
        const CURRENT_VERSION = 6
        
        // 1. 输入验证
        if (!persistedState || typeof persistedState !== 'object') {
          console.warn('[Store Migration] Invalid persisted state, returning empty object')
          return {} as never
        }
        
        // 2. 深拷贝原始状态作为回滚备份
        let backup: Record<string, unknown>
        try {
          backup = JSON.parse(JSON.stringify(persistedState))
        } catch {
          // 如果无法序列化，使用浅拷贝
          backup = { ...(persistedState as Record<string, unknown>) }
        }
        
        const state = persistedState as Record<string, unknown>
        const migrated = { ...state }
        
        try {
          // 版本 1 → 2: 迁移 floatingNotes 结构（如果存在）
          if (version < 2) {
            // 如果 floatingNotes 是旧格式（数组），保持不变
            // 新格式兼容旧格式，无需特殊处理
            console.info('[Store Migration] v1 → v2: floatingNotes structure preserved')
          }
          
          // 版本 2 → 3: 添加新的持久化字段默认值
          if (version < 3) {
            // 为新字段添加默认值，但不覆盖已有数据
            if (migrated.preferredModelProvider === undefined) {
              migrated.preferredModelProvider = 'deepseek'
            }
            if (migrated.preferredModelName === undefined) {
              // deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役
              migrated.preferredModelName = 'deepseek-v4-flash'
            }
            console.info('[Store Migration] v2 → v3: added default model preferences')
          }

          // 版本 3 → 4: 清除指向已停用模型的旧偏好缓存(模型选择改为从后端 provider 同步)
          // 注意：下面判断里的 'deepseek-chat' 是「待清除的历史脏值」，不是默认值，
          // 随模型名变更一起替换会导致老用户的脏偏好清不掉（勿改）。
          if (version < 4) {
            if (migrated.preferredModelProvider === 'deepseek' || migrated.preferredModelName === 'deepseek-chat') {
              migrated.preferredModelProvider = null
              migrated.preferredModelName = null
            }
            console.info('[Store Migration] v3 → v4: cleared stale preferred model')
          }

          // 版本 4 → 5: 重置侧边栏配置为默认,纳入新增模块(rss/automation 等)
          if (version < 5) {
            migrated.sidebarConfig = getDefaultSidebarConfig()
            console.info('[Store Migration] v4 → v5: refreshed sidebar config')
          }

          // 版本 5 → 6: 彻底移除持久化的 sidebarConfig,改用默认(含新增模块)
          if (version < 6) {
            delete migrated.sidebarConfig
            console.info('[Store Migration] v5 → v6: removed persisted sidebarConfig')
          }

          // 3. 迁移后验证：确保关键字段存在且类型正确
          const validationErrors = validateMigratedState(migrated)
          if (validationErrors.length > 0) {
            console.error('[Store Migration] Validation failed:', validationErrors)
            console.warn('[Store Migration] Rolling back to backup')
            return backup as never
          }
          
          console.info(`[Store Migration] Migrated from version ${version} to ${CURRENT_VERSION}, preserved ${Object.keys(migrated).length} keys`)
          return migrated as never
        } catch (error) {
          // 4. 异常处理：迁移过程中出现任何错误，回滚到备份
          console.error('[Store Migration] Migration failed, rolling back:', error)
          return backup as never
        }
      },
      storage: createJSONStorage(() => getPlatformStorage()),
      partialize: (state) => {
        const s = state as unknown as Record<string, unknown>
        // Omit transient UI state — everything else persists
        const TRANSIENT_KEYS: Set<string> = new Set([
          'searchModalOpen', 'searchQuery', 'noteSearchQuery', 'searchHighlight',
          'editingNoteId',
          'infoLoading', 'infoLastFetch', 'infoError',
          'proactiveBriefLoading', 'proactiveSuggestions',
          'focusTimer', 'filterStatus', 'filterPriority', 'filterSubject',
          'bootPhase', 'bootError',
          'chatSessions', // 瞬态流式状态，不持久化
          'sidebarConfig', // 不持久化,每次启动用默认(含新模块),避免旧缓存覆盖
          'noteConflict', // V1-4 乐观锁冲突是会话内待裁决状态，刷新即失效，不落盘
          'moduleAvailability', // U4 后端模块可用状态快照：会话级，每次启动重新拉取
        ])
        const persistable: Record<string, unknown> = {}
        for (const key of Object.keys(s)) {
          if (!TRANSIENT_KEYS.has(key)) persistable[key] = s[key]
        }
        // Electron: skip floatingNotes (stored via fileStore IPC)
        if (typeof window !== 'undefined' && !!(window as unknown as Record<string, unknown>).electronAPI) {
          delete persistable.floatingNotes
        }
        return persistable as Record<string, unknown>
      },
    }
  )
)

// ── AI auto-tagging (debounced per note) ─────────────────────────────
{
  const tagTimers = new Map<string, ReturnType<typeof setTimeout>>()
  const contentHashes = new Map<string, string>()

  useStore.subscribe((state) => {
    if (!state.aiEnabled || !state.aiAutoTagging) return
    const notes = state.floatingNotes

    const activeIds = new Set(notes.map(n => n.id))
    for (const id of contentHashes.keys()) {
      if (!activeIds.has(id)) contentHashes.delete(id)
    }

    for (const note of notes) {
      if (!note.content || note.type !== 'text') continue
      const hash = `${note.content}|${note.title}`
      const prev = contentHashes.get(note.id)
      if (prev === hash) continue
      contentHashes.set(note.id, hash)
      if (prev === undefined) continue

      const existing = tagTimers.get(note.id)
      if (existing) clearTimeout(existing)

      const noteId = note.id
      const content = note.content
      const existingTags = note.tags || []

      const timer = setTimeout(async () => {
        tagTimers.delete(noteId)
        try {
          let tags: string[] = []

          try {
            const client = getWsClient()
            if (client.status === 'connected') {
              tags = await client.call('notes.auto_tag', { content, existing_tags: existingTags })
            }
          } catch { /* RPC failed */ }

          if (tags.length > 0) {
            useStore.getState().setSuggestedTags(noteId, tags)
          }
        } catch { /* Auto-tagging is best-effort */ }
      }, 3000)
      tagTimers.set(note.id, timer)
    }
  })
}

// ── AI note summary generation (lazy, one per note) ───────────────────────
{
  const summaryQueued = new Set<string>()
  let summaryRunning = false

  useStore.subscribe((state) => {
    if (!state.aiEnabled) return
    for (const note of state.floatingNotes) {
      if (note.type !== 'text' || !note.content || note.summary) continue
      if (summaryQueued.has(note.id)) continue
      summaryQueued.add(note.id)
    }
    processSummaryQueue()
  })

  async function processSummaryQueue() {
    if (summaryRunning) return
    summaryRunning = true
    while (summaryQueued.size > 0) {
      const it = summaryQueued.values().next()
      if (it.done || it.value === undefined) break
      const noteId: string = it.value
      summaryQueued.delete(noteId)
      try {
        const state = useStore.getState()
        const note = state.floatingNotes.find(n => n.id === noteId)
        if (!note || !note.content || note.summary) continue
        const plainText = note.content.replace(/<[^>]*>/g, '').trim()
        if (plainText.length < 20) continue

        let summary = ''

        try {
          const client = getWsClient()
          if (client.status === 'connected') {
            summary = await client.call('notes.generate_summary', { content: plainText })
          }
        } catch { /* RPC failed */ }

        if (summary && summary.length < 200) {
          useStore.getState().updateFloatingNote(noteId, { summary })
        }
      } catch { /* Best-effort */ }
    }
    summaryRunning = false
  }
}

// ── Settings auto-sync to backend (debounced 300ms) ──────────────────────
{
  let prevSnapshot = pickSyncableSettings(useStore.getState() as unknown as Record<string, unknown>)
  let syncTimer: ReturnType<typeof setTimeout> | null = null

  useStore.subscribe((state) => {
    const current = pickSyncableSettings(state as unknown as Record<string, unknown>)
    const changed = getChangedKeys(prevSnapshot, current)
    if (changed.length === 0) return
    prevSnapshot = current

    if (syncTimer) clearTimeout(syncTimer)
    syncTimer = setTimeout(() => {
      const client = getWsClient()
      if (client.status !== 'connected') return
      for (const { backendKey, value } of changed) {
        client.call('settings.set', { key: backendKey, value }).catch(() => {})
      }
    }, 300)
  })
}
