/**
 * SyncEngine — RESTful sync with cursor + tombstone semantics.
 *
 * STATUS (P1-1 audit, 2026-06-18): currently DORMANT.
 *
 * This engine targets `GET /personal/notes?since=...&limit=...`-style HTTP
 * endpoints with `next_cursor` pagination and `is_deleted` tombstones — a
 * design intended for Phase 2 (cloud server). The desktop backend is
 * WebSocket JSON-RPC and has no such endpoints. The actively-used sync code
 * path is `lib/contentSync.ts` (RPC bridge to desktop). SyncStatusView still
 * imports SyncEngine for status display; do not delete.
 *
 * If/when a REST cloud server lands, switch `useAutoSync.ts` from
 * `fullContentSync()` to `new SyncEngine(apiClient).syncAll()` and remove
 * contentSync.
 */

import { ApiClient, type NoteResponse, type TaskResponse } from './apiClient'
import { PendingOpManager } from './pendingOps'

const CURSOR_PREFIX = 'sync_cursor_'
const LAST_SYNC_KEY = 'siyi_last_sync_time'

export interface ResourceSyncResult {
  success: boolean
  pushed: number
  pulled: number
  deleted: number
  error?: string
}

export interface SyncResult {
  notes: ResourceSyncResult
  tasks: ResourceSyncResult
  translations: ResourceSyncResult
  wordbooks: ResourceSyncResult
  lastSyncTime: string | null
}

type Fetcher<T> = (since: string | undefined) => Promise<{ items: T[]; next_cursor?: string }>
type Saver<T> = (items: T[]) => Promise<void>
type TombstoneMarker = (items: unknown[]) => string[] /* deleted IDs */

export class SyncEngine {
  private pendingOps: PendingOpManager

  constructor(
    private api: ApiClient,
  ) {
    this.pendingOps = new PendingOpManager()
    this._replaying = false
  }

  // deno-lint-ignore
  private _replaying: boolean

  getPendingOps(): PendingOpManager {
    return this.pendingOps
  }

  getLastSyncTime(): string | null {
    return localStorage.getItem(LAST_SYNC_KEY)
  }

  /* ── Core sync primitive ── */

  private async syncResource<T extends { id?: string }>(
    resourceType: string,
    fetcher: Fetcher<T>,
    saver: Saver<T>,
    tombstoneMarker: TombstoneMarker,
  ): Promise<ResourceSyncResult> {
    let pushed = 0
    let pulled = 0
    let deleted = 0

    try {
      // Phase 1: Push dirty local ops first
      if (!this._replaying) { this._replaying = true; try { await this.pendingOps.replayAll() } finally { this._replaying = false } }
      pushed = 0 // pendingOps tracks internally; count not exposed per resource

      // Phase 2: Pull incremental
      const cursor = this.getCursor(resourceType)
      const response = await fetcher(cursor ?? undefined)

      if (!response.items || response.items.length === 0) {
        return { success: true, pushed, pulled, deleted }
      }

      // Phase 3: Tombstone — identify deletions
      const deletedIds = tombstoneMarker(response.items)
      deleted = deletedIds.length

      // Phase 4: Save non-deleted items
      const nonDeleted = response.items.filter(
        item => !(item.id && deletedIds.includes(item.id))
      )
      if (nonDeleted.length > 0) {
        await saver(nonDeleted)
        pulled = nonDeleted.length
      }

      // Phase 5: Advance cursor
      if (response.next_cursor) {
        this.setCursor(resourceType, response.next_cursor)
      } else {
        this.setCursor(resourceType, String(Date.now()))
      }

      return { success: true, pushed, pulled, deleted }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'sync failed'
      return { success: false, pushed, pulled, deleted, error: msg }
    }
  }

  /* ── Resource-specific sync ── */

  async syncNotes(): Promise<ResourceSyncResult> {
    const fetcher = async (since: string | undefined) => {
      const res = await this.api.listNotes(since)
      return {
        items: res.notes as NoteResponse[],
        next_cursor: res.next_cursor,
      }
    }

    const saver = async (notes: NoteResponse[]) => {
      // Persist pulled notes to localStorage
      const existing = this.loadLocalNotes()
      for (const note of notes) {
        const idx = existing.findIndex(n => n.id === note.id)
        if (idx >= 0) {
          existing[idx] = note
        } else {
          existing.push(note)
        }
      }
      this.saveLocalNotes(existing)
    }

    const tombstone = (items: unknown[]) => {
      return (items as { is_deleted?: boolean; id?: string }[])
        .filter(item => item.is_deleted && item.id)
        .map(item => item.id!)
    }

    return this.syncResource('notes', fetcher, saver, tombstone)
  }

  async syncTasks(): Promise<ResourceSyncResult> {
    const fetcher = async (since: string | undefined) => {
      const res = await this.api.listTasks(since)
      return {
        items: res.tasks as TaskResponse[],
        next_cursor: res.next_cursor,
      }
    }

    const saver = async (tasks: TaskResponse[]) => {
      const existing = this.loadLocalTasks()
      for (const task of tasks) {
        const idx = existing.findIndex(t => t.id === task.id)
        if (idx >= 0) {
          existing[idx] = task
        } else {
          existing.push(task)
        }
      }
      this.saveLocalTasks(existing)
    }

    const tombstone = (items: unknown[]) => {
      return (items as { is_deleted?: boolean; id?: string }[])
        .filter(item => item.is_deleted && item.id)
        .map(item => item.id!)
    }

    return this.syncResource('tasks', fetcher, saver, tombstone)
  }

  /* ── Translations & Wordbooks ── */

  async syncTranslations(): Promise<ResourceSyncResult> {
    const fetcher = async (since: string | undefined) => {
      const res = await this.api.listTranslations(since)
      return { items: res.translations, next_cursor: res.next_cursor }
    }
    const saver = async (items: Record<string, unknown>[]) => {
      const existing = this.loadLocal<Record<string, unknown>>('siyi_synced_translations')
      for (const item of items) {
        const idx = existing.findIndex(t => t.id === item.id)
        if (idx >= 0) existing[idx] = item
        else existing.push(item)
      }
      this.saveLocal('siyi_synced_translations', existing)
    }
    const tombstone = (items: unknown[]) =>
      (items as { is_deleted?: boolean; id?: string }[])
        .filter(i => i.is_deleted && i.id).map(i => i.id!)
    return this.syncResource('translations', fetcher, saver, tombstone)
  }

  async syncWordbooks(): Promise<ResourceSyncResult> {
    const fetcher = async (since: string | undefined) => {
      const res = await this.api.listWordbooks(since)
      return { items: res.wordbooks, next_cursor: res.next_cursor }
    }
    const saver = async (items: Record<string, unknown>[]) => {
      const existing = this.loadLocal<Record<string, unknown>>('siyi_synced_wordbooks')
      for (const item of items) {
        const idx = existing.findIndex(w => w.id === item.id)
        if (idx >= 0) existing[idx] = item
        else existing.push(item)
      }
      this.saveLocal('siyi_synced_wordbooks', existing)
    }
    const tombstone = (items: unknown[]) =>
      (items as { is_deleted?: boolean; id?: string }[])
        .filter(i => i.is_deleted && i.id).map(i => i.id!)
    return this.syncResource('wordbooks', fetcher, saver, tombstone)
  }

  async syncAll(): Promise<SyncResult> {
    const [notes, tasks, translations, wordbooks] = await Promise.all([
      this.syncNotes(),
      this.syncTasks(),
      this.syncTranslations(),
      this.syncWordbooks(),
    ])

    const now = new Date().toISOString()
    localStorage.setItem(LAST_SYNC_KEY, now)

    return { notes, tasks, translations, wordbooks, lastSyncTime: now }
  }

  /* ── Cursor persistence ── */

  private getCursor(type: string): string | null {
    return localStorage.getItem(CURSOR_PREFIX + type)
  }

  private setCursor(type: string, cursor: string): void {
    localStorage.setItem(CURSOR_PREFIX + type, cursor)
  }

  /* ── Local data helpers ── */

  private loadLocalNotes(): NoteResponse[] {
    try {
      const raw = localStorage.getItem('siyi_synced_notes')
      return raw ? JSON.parse(raw) : []
    } catch {
      return []
    }
  }

  private saveLocalNotes(notes: NoteResponse[]): void {
    localStorage.setItem('siyi_synced_notes', JSON.stringify(notes))
  }

  private loadLocalTasks(): TaskResponse[] {
    try {
      const raw = localStorage.getItem('siyi_synced_tasks')
      return raw ? JSON.parse(raw) : []
    } catch {
      return []
    }
  }

  private saveLocalTasks(tasks: TaskResponse[]): void {
    localStorage.setItem('siyi_synced_tasks', JSON.stringify(tasks))
  }

  private loadLocal<T>(key: string): T[] {
    try {
      const raw = localStorage.getItem(key)
      return raw ? JSON.parse(raw) : []
    } catch {
      return []
    }
  }

  private saveLocal(key: string, data: unknown[]): void {
    localStorage.setItem(key, JSON.stringify(data))
  }
}
