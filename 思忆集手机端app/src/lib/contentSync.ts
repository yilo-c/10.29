/**
 * Content sync — JSON-RPC bridge to desktop backend (notes / tasks /
 * translations / wordbooks).
 *
 * Architecture status (P1-1 review):
 * - This module IS the live sync path used by useAutoSync + App.tsx.
 * - `syncEngine.ts` was an aspirational REST + cursor + tombstone design that
 *   targets a `/personal/notes` / `/personal/tasks` HTTP API the desktop
 *   backend never grew (backend is WebSocket JSON-RPC). It is currently
 *   referenced only by SyncStatusView for status display, not for actual
 *   syncing. Until the backend grows REST endpoints, contentSync remains the
 *   active engine.
 *
 * Bug history (this file):
 * - H14: syncTasks deduplicated by `text`, treating any two same-title todos
 *   as the same — done/priority changes were lost. Fixed: dedup by id.
 * - H15-adjacent: cloudSync.ts merged todos by createdAt (never changes
 *   after creation, so remote edits were never pulled). Fixed there: use
 *   updatedAt with createdAt fallback.
 * - V2.1 批次4（b4-m2）：syncNotes/syncTasks 升级为 LWW（Last-Write-Wins）
 *   合并 —— 之前只在「本地不存在」时新增，远程对已有条的编辑永远拉不下来，
 *   本地的离线编辑也永远不会推回。现在：
 *     1) 远程 updatedAt 更新 → 覆盖本地（直接 setState，保留远程时间戳，
 *        避免走 updateFloatingNote 的同步副作用造成 push 回环）；
 *     2) 本地 updatedAt 更新 → 推回桌面（携带 updated_at，后端保留远端时间戳）；
 *     3) 墓碑：notes.list_trashed / tasks.list_trashed 的 deleted_at 晚于本地
 *        更新时间 → 本地删除（笔记与任务均已覆盖）。
 *   任务的 updatedAt 由 todoSlice 各 mutation 维护（见 TodoItem 类型注释 P0-9/H15）。
 * - V2.1 批次4（b4-m2）：离线写接 PendingOpManager —— 推送桌面失败（离线/瞬断）不再
 *   静默吞掉，而是入 PendingOpManager 队列；每次 fullContentSync 开始时先重放队列
 *   （best-effort，重试上限 5 次后丢弃），保证本地编辑在恢复连通后补推，不丢数据。
 */

import { callRpc } from './rpcClient'
import { tokenManager } from './tokenManager'
import { useStore, type FloatingNote } from '@desk-notes/shared'
import { swallowError } from './swallowError'
import { PendingOpManager, type PendingOp } from './pendingOps'
import { showToast } from '../components/ui/Toast'

interface SyncedNote {
  id: string | number
  title?: string
  content?: string
  tags?: string[]
  updated_at?: string
  updatedAt?: number
  created_at?: string
  createdAt?: number
  deleted_at?: string
  deletedAt?: number
}

interface SyncedTask {
  id: string | number
  title?: string
  text?: string
  done?: boolean
  completed?: boolean
  priority?: number
  category?: string
  dueDate?: number
  updated_at?: string
  updatedAt?: number
  createdAt?: number
  deleted_at?: string
  deletedAt?: number
}

/** 归一化远程时间戳（ISO 字符串或 epoch ms）→ epoch ms；无法解析返回 null。 */
function toEpochMs(value: string | number | undefined | null): number | null {
  if (value === undefined || value === null) return null
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const ms = new Date(value).getTime()
    if (Number.isFinite(ms)) return ms
  }
  return null
}

/** epoch ms → ISO 字符串（推回时让后端保留本地时间戳，见 _note.py update_note P1-3）。 */
function isoFromEpoch(ms: number): string {
  return new Date(ms).toISOString()
}

/** 本地条目 LWW 基准时间：优先 updatedAt，缺失回落 createdAt。 */
function localVersionOf(item: { updatedAt?: number; createdAt?: number }): number {
  if (typeof item.updatedAt === 'number' && Number.isFinite(item.updatedAt)) return item.updatedAt
  if (typeof item.createdAt === 'number' && Number.isFinite(item.createdAt)) return item.createdAt
  return 0
}

/* ── 离线写接 PendingOpManager（b4-m2）────────────────────────────── */

/** contentSync 专用待办队列：推送桌面失败时入队，下次同步开始时重放。 */
export const pendingOps = new PendingOpManager()

/** payload 里 tags 用 JSON 字符串承载（PendingOp.payload 是 Record<string,string>）。 */
function parseTags(raw: string | undefined): string[] {
  if (!raw) return []
  try {
    const v = JSON.parse(raw)
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}

/** 把队列里的离线 op 重放为 JSON-RPC 调用（best-effort，失败返回 false 触发重试计数）。 */
async function replayPendingOp(op: PendingOp): Promise<boolean> {
  try {
    switch (op.type) {
      case 'note_create':
        await callRpc('notes.create', {
          title: op.payload.title ?? '',
          content: op.payload.content ?? '',
          tags: parseTags(op.payload.tags),
        })
        return true
      case 'note_update':
        await callRpc('notes.update', {
          note_id: op.entityId,
          title: op.payload.title ?? '',
          content: op.payload.content ?? '',
          tags: parseTags(op.payload.tags),
          updated_at: op.payload.updated_at,
        })
        return true
      case 'note_delete':
        await callRpc('notes.delete', { note_id: op.entityId })
        return true
      case 'task_create':
        await callRpc('tasks.create', {
          text: op.payload.text ?? '',
          done: op.payload.done === 'true',
          priority: Number(op.payload.priority ?? 0),
        })
        return true
      case 'task_update':
        await callRpc('tasks.update', {
          task_id: op.entityId,
          title: op.payload.title ?? '',
          completed: op.payload.completed === 'true',
          priority: Number(op.payload.priority ?? 0),
          category: op.payload.category ?? '',
          updated_at: op.payload.updated_at,
        })
        return true
      case 'task_delete':
        await callRpc('tasks.delete', { task_id: op.entityId })
        return true
      default:
        return false
    }
  } catch { return false }
}

pendingOps.setReplayHandler(replayPendingOp)

/** C-2：队列条目重试超限被丢弃 → toast 可见提示（移动端 i18n 惯例为直接中文文案） */
const PENDING_OP_LABEL: Record<string, string> = {
  note_create: '新建笔记',
  note_update: '笔记修改',
  note_delete: '笔记删除',
  task_create: '新建待办',
  task_update: '待办修改',
  task_delete: '待办删除',
  quick_relay: '快速转发',
}
pendingOps.onDrop = (op) => {
  showToast({
    message: `离线改动重试 ${op.retryCount} 次仍失败，已放弃：${PENDING_OP_LABEL[op.type] ?? op.type}`,
    duration: 5000,
  })
}

/** Check if desktop is reachable */
async function isDesktopOnline(): Promise<boolean> {
  if (!tokenManager.isPaired) return false
  try {
    const result = await callRpc<unknown>('ping')
    return result === 'pong' || result !== undefined
  } catch { return false }
}

/** Pull notes from desktop, merge with local Zustand store (LWW by updatedAt) */
async function syncNotes(): Promise<number> {
  if (!(await isDesktopOnline())) return 0
  try {
    const [remote, trashed] = await Promise.all([
      callRpc<SyncedNote[]>('notes.list', { limit: 200 }),
      callRpc<SyncedNote[]>('notes.list_trashed', {}),
    ])
    if (!remote || !Array.isArray(remote)) return 0

    const store = useStore.getState()
    const localNotes = store.floatingNotes || []
    const localMap = new Map(localNotes.map((n) => [String(n.id), n]))
    // 本地更新的笔记，稍后推回桌面（携带本地 updated_at，避免后端覆盖时间戳）
    const pushBack: Array<{ id: string; title: string; content: string; tags: string[]; updatedAt: number }> = []
    let changed = 0

    for (const note of remote) {
      const id = String(note.id)
      const remoteUpdated = toEpochMs(note.updatedAt ?? note.updated_at)
      const local = localMap.get(id)

      if (!local) {
        // 桌面有、本地无 → 新增（保留远程时间戳，P1-19）
        const createdAt = toEpochMs(note.createdAt ?? note.created_at) ?? Date.now()
        const updatedAt = toEpochMs(note.updatedAt ?? note.updated_at) ?? createdAt
        const newNote = {
          id,
          title: note.title || '无标题',
          content: note.content || '',
          type: 'text' as const,
          x: 0, y: 0, zIndex: 1, width: 320, height: 200,
          createdAt,
          updatedAt,
          tags: note.tags || [],
          editMode: 'markdown' as const,
        }
        // P1 修复：直接 setState 绕过 addFloatingNote 的同步副作用，避免 push 回环
        useStore.setState((s) => ({
          floatingNotes: [...(s.floatingNotes || []), newNote],
        }))
        changed++
        continue
      }

      // 已存在 → LWW：远端更新覆盖本地，本地更新推回桌面
      const localUpdated = localVersionOf(local as { updatedAt?: number; createdAt?: number })
      if (remoteUpdated === null || remoteUpdated <= localUpdated) {
        if (remoteUpdated !== null && remoteUpdated < localUpdated) {
          // 本地更新 → 把本地版本推回桌面（携带本地 updated_at 保留时间戳）
          pushBack.push({
            id,
            title: local.title || '',
            content: local.content || '',
            tags: local.tags || [],
            updatedAt: localUpdated,
          })
        }
        continue
      }
      // 直接 setState（非 updateFloatingNote），避免再次触发 notes.update 造成回环
      useStore.setState((s) => ({
        floatingNotes: (s.floatingNotes || []).map((n): FloatingNote =>
          String(n.id) === id
            ? {
                ...n,
                title: (note.title || '').trim() || n.title,
                content: note.content || '',
                tags: note.tags || [],
                updatedAt: remoteUpdated,
              }
            : n
        ),
      }))
      changed++
    }

    // 墓碑：桌面软删除的笔记，deleted_at 晚于本地更新时间 → 本地删除
    if (Array.isArray(trashed)) {
      for (const tomb of trashed) {
        const id = String(tomb.id)
        const deletedAt = toEpochMs(tomb.deleted_at ?? tomb.deletedAt)
        const local = localMap.get(id)
        if (!local || deletedAt === null) continue
        if (deletedAt > localVersionOf(local as { updatedAt?: number; createdAt?: number })) {
          useStore.setState((s) => ({
            floatingNotes: (s.floatingNotes || []).filter((n) => String(n.id) !== id),
          }))
          changed++
        }
      }
    }

    // 推回本地更新的笔记（best-effort；携带 updated_at 让后端保留本地时间戳）
    // b4-m2：失败入 PendingOpManager，下次同步开始重放（不再静默丢数据）
    for (const p of pushBack) {
      try {
        await callRpc('notes.update', {
          note_id: p.id, title: p.title, content: p.content, tags: p.tags,
          updated_at: isoFromEpoch(p.updatedAt),
        })
      } catch (e) {
        swallowError('contentSync:pushBackNote', e)
        pendingOps.enqueueNoteOp('note_update', p.id, {
          title: p.title, content: p.content,
          tags: JSON.stringify(p.tags), updated_at: isoFromEpoch(p.updatedAt),
        })
      }
    }

    return changed
  } catch { return 0 }
}

/** Push a new/updated note from mobile to desktop */
async function pushNote(_id: string, title: string, content: string, tags: string[]): Promise<void> {
  try {
    await callRpc('notes.create', { title, content, tags })
  } catch (e) {
    // b4-m2：离线/失败入队，下次同步重放（note_create 无 id，entityId 用占位符）
    swallowError('contentSync:94', e)
    pendingOps.enqueueNoteOp('note_create', `local-${Date.now()}`, {
      title, content, tags: JSON.stringify(tags),
    })
  }
}

/** Pull tasks from desktop, merge with local Zustand store (LWW by updatedAt) */
async function syncTasks(): Promise<number> {
  if (!(await isDesktopOnline())) return 0
  try {
    const [remote, trashed] = await Promise.all([
      callRpc<SyncedTask[]>('tasks.list', { limit: 200 }),
      callRpc<SyncedTask[]>('tasks.list_trashed', {}),
    ])
    if (!remote || !Array.isArray(remote)) return 0

    const store = useStore.getState()
    const localTodos = store.todos || []
    // H14 修复：用 id 而非 text 去重。
    // 原本 `localTexts.has(t.text)` 把任意同标题待办（"今天晨会"）当成同一条 →
    // 编辑/完成状态变更永远拉不下来。改用 id 后既能识别新增也能给后续
    // upsert/update 留出口（addOrUpdateTodo 见 store 实现）。
    const localMap = new Map(localTodos.map((t) => [String(t.id), t]))
    // 本地更新的待办，稍后推回桌面（携带本地 updated_at，避免后端覆盖时间戳）
    const pushBack: Array<{ id: string; title: string; done: boolean; priority?: number; category?: string; updatedAt: number }> = []
    let changed = 0

    for (const task of remote) {
      const id = String(task.id)
      const title = task.title || task.text
      if (!title) continue
      const remoteUpdated = toEpochMs(task.updated_at ?? (task as { updatedAt?: number }).updatedAt)
      const local = localMap.get(id)

      if (!local) {
        // 桌面有、本地无 → 新增
        // P1 审查修复：直接 setState 保留 task.id，避免 addTodo 生成新 id 导致回环
        // P1-19 修复：保留远程时间戳（旧代码 createdAt/updatedAt 都用 Date.now()，
        //   导致远程的更新时间被本地拉取时间覆盖，后续 LWW 合并会把远程编辑误判为旧数据）。
        const createdAt = toEpochMs(task.createdAt) ?? remoteUpdated ?? Date.now()
        const updatedAt = remoteUpdated ?? createdAt
        useStore.setState((s) => ({
          todos: [...s.todos, {
            id,
            text: title,
            done: !!task.completed,
            category: task.category || 'general',
            createdAt,
            updatedAt,
          }],
        }))
        changed++
        continue
      }

      // 已存在 → LWW：远端更新覆盖本地，本地更新推回桌面
      const localUpdated = localVersionOf(local as { updatedAt?: number; createdAt?: number })
      if (remoteUpdated === null || remoteUpdated <= localUpdated) {
        if (remoteUpdated !== null && remoteUpdated < localUpdated) {
          // 本地更新 → 回推【本地】字段（本地是更新的真相，不能用远端旧数据覆盖桌面）
          pushBack.push({
            id,
            title: local.text || '',
            done: !!local.done,
            priority: local.priority,
            category: local.category,
            updatedAt: localUpdated,
          })
        }
        continue
      }
      // 远端更新 → 覆盖本地（直接 setState，不触发 syncCall 回环）
      const pri = Number(task.priority)
      const priority = [0, 1, 2].includes(pri) ? pri as 0 | 1 | 2 : local.priority
      useStore.setState((s) => ({
        todos: s.todos.map(t =>
          String(t.id) === id ? {
            ...t,
            text: title,
            done: !!task.completed,
            priority,
            category: task.category || t.category,
            updatedAt: remoteUpdated,
          } : t
        ),
      }))
      changed++
    }

    // 推回本地更新的待办（best-effort；携带 updated_at 让后端保留本地时间戳）
    // b4-m2：失败入 PendingOpManager，下次同步开始重放（不再静默丢数据）
    for (const p of pushBack) {
      try {
        await callRpc('tasks.update', {
          task_id: p.id, title: p.title, completed: p.done,
          priority: p.priority ?? 0, category: p.category ?? '',
          updated_at: isoFromEpoch(p.updatedAt),
        })
      } catch (e) {
        swallowError('contentSync:pushBackTask', e)
        pendingOps.enqueueTaskOp('task_update', p.id, {
          title: p.title, completed: String(p.done),
          priority: String(p.priority ?? 0), category: p.category ?? '',
          updated_at: isoFromEpoch(p.updatedAt),
        })
      }
    }

    // 墓碑：桌面软删除的任务，deleted_at 晚于本地更新时间 → 本地删除（LWW）
    // 批次4 b4-m2：后端 tasks.list_trashed 返回含 deleted_at 的软删任务。
    if (Array.isArray(trashed)) {
      for (const tomb of trashed) {
        const id = String(tomb.id)
        const deletedAt = toEpochMs(tomb.deleted_at ?? tomb.deletedAt)
        const local = localMap.get(id)
        if (!local || deletedAt === null) continue
        if (deletedAt > localVersionOf(local as { updatedAt?: number; createdAt?: number })) {
          useStore.setState((s) => ({
            todos: (s.todos || []).filter((t) => String(t.id) !== id),
          }))
          changed++
        }
      }
    }

    return changed
  } catch { return 0 }
}

/** Push a new/updated todo from mobile to desktop */
async function pushTask(text: string, done: boolean, priority: number): Promise<void> {
  try {
    await callRpc('tasks.create', { text, done, priority })
  } catch (e) {
    // b4-m2：离线/失败入队，下次同步重放（task_create 无 id，entityId 用占位符）
    swallowError('contentSync:147', e)
    pendingOps.enqueueTaskOp('task_create', `local-${Date.now()}`, {
      text, done: String(done), priority: String(priority),
    })
  }
}

/** Pull translations from desktop */
async function syncTranslations(): Promise<number> {
  if (!(await isDesktopOnline())) return 0
  try {
    const remote = await callRpc<Array<{ source: string; target: string; sourceText: string; translatedText: string; timestamp: number }>>('translations.list', { limit: 50 })
    if (!remote || !Array.isArray(remote)) return 0
    const existing = JSON.parse(localStorage.getItem('siyi-translations') || '[]')
    // P1-19 修复：用完整 sourceText 做去重键，避免两条前 30 字符相同但内容不同的长文
    //   互相覆盖（旧代码 .slice(0, 30) 会把"同段开头但结尾不同"的翻译误判为重复）。
    //   Set 存全文字符串的内存代价远小于丢翻译的代价（最长 200 条 × ~500 字）。
    const existingKeys = new Set(existing.map((e: { sourceText: string }) => e.sourceText || ''))
    let count = 0
    for (const item of remote) {
      const key = item.sourceText || ''
      if (!existingKeys.has(key)) { existing.unshift(item); count++; existingKeys.add(key) }
    }
    localStorage.setItem('siyi-translations', JSON.stringify(existing.slice(0, 200)))
    return count
  } catch { return 0 }
}

/** Pull wordbooks from desktop */
async function syncWordbooks(): Promise<number> {
  if (!(await isDesktopOnline())) return 0
  try {
    const wbs = await callRpc<Array<{ id: number; name: string }>>('wordbook.list')
    if (!wbs || !Array.isArray(wbs)) return 0
    const existing = JSON.parse(localStorage.getItem('siyi-wordbooks') || '[]')
    const existingIds = new Set(existing.map((e: { id: number }) => e.id))
    let count = 0
    // Fetch words for each wordbook in parallel
    const wordResults = await Promise.allSettled(
      wbs.filter(wb => !existingIds.has(wb.id)).map(async (wb) => {
        existing.push(wb); count++; existingIds.add(wb.id)
        const words = await callRpc<Array<{ id: number; word: string; meaning: string; example: string }>>('word.list', { wordbook_id: wb.id })
        if (words) localStorage.setItem(`siyi-words-${wb.id}`, JSON.stringify(words.slice(0, 500)))
      })
    )
    for (const r of wordResults) {
      if (r.status === 'rejected') console.warn('[sync] failed to pull words for wordbook', r.reason)
    }
    localStorage.setItem('siyi-wordbooks', JSON.stringify(existing.slice(0, 50)))
    return count
  } catch { return 0 }
}

/** Full sync: replay queued offline writes, then pull desktop notes + tasks + translations + wordbooks */
export async function fullContentSync(): Promise<{ notes: number; tasks: number; translations: number; wordbooks: number }> {
  // b4-m2：先重放离线期间堆积的写操作（best-effort；桌面在线时补推成功即出队）
  try {
    await pendingOps.replayAll()
  } catch { /* 重放失败不影响拉取 */ }

  const [notes, tasks, translations, wordbooks] = await Promise.all([
    syncNotes(), syncTasks(), syncTranslations(), syncWordbooks(),
  ])
  return { notes, tasks, translations, wordbooks }
}

/** 待办队列长度（SyncStatusView 展示「待处理操作」用）。 */
export function getPendingOpsCount(): number {
  return pendingOps.count
}

/** Push a note to desktop when created on mobile */
export async function syncNoteToDesktop(id: string, title: string, content: string, tags: string[] = []): Promise<void> {
  await pushNote(id, title, content, tags)
}

/** Push a task to desktop when created on mobile */
export async function syncTaskToDesktop(text: string, done: boolean, priority: number): Promise<void> {
  await pushTask(text, done, priority)
}
