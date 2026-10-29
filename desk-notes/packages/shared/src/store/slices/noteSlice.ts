import type { StateCreator } from 'zustand'
import type { Store } from '../types'
import { FloatingNote, Folder } from '../../types'
import { uid } from '../../utils/helpers'
import { syncCall } from '../../utils/syncCall'
import { getWsClient } from '../../utils/ai'
import { errorStore } from '../errorStore'
import { tStatic } from '../../i18n'
import { RpcMethods } from '../../rpc/methods'

// ── Trash handler (injected from desktop/store/useStore.ts) ──
type TrashPayload = { id: string; type: 'note' | 'todo'; data: unknown; deletedAt: number }
let _trashHandler: (item: TrashPayload) => void = () => {}
export function setTrashHandler(handler: (item: TrashPayload) => void) {
  _trashHandler = handler
}

// ── Notes loader (injected from desktop/store/useStore.ts) ──
type NotesLoader = () => Promise<Record<string, unknown>[]>
let _notesLoader: NotesLoader | null = null
export function setNotesLoader(loader: NotesLoader) {
  _notesLoader = loader
}

// ── V1-4 笔记保存链路：编辑防抖 + 同笔记串行写入 + version 乐观锁 ──────
//
// 旧实现：每次 updateFloatingNote 直接 fire-and-forget `syncCall('notes.update')`。
// 一次打字会话会打出 N 个并发 RPC，服务端乱序落库 → 后打的字被先打的覆盖（"丢字"）。
// 后端 `notes.update`（思忆集app/backend/handlers/_notes.py:321-373）只把 new_info
// 透传给 `repo.update_note`，**没有** version 条件写（repo 仅在 update 后自增
// version，见 repositories/_note.py:149-150），所以乐观锁只能在前端做：
// ① 防抖合并连续编辑 + 失焦/切页/窗口隐藏时可 await 的强制 flush
// ② 每条笔记一条串行写入链，杜绝同笔记并发乱序
// ③ 写前 `notes.get` 预检 version：服务端版本 ≠ 本地基线且内容也确实不同
//    → 挂起该笔记写入并抛冲突给 UI 做「保留本地 / 取回云端」裁决，禁静默覆盖
const NOTE_SAVE_DEBOUNCE_MS = 500

interface NoteWriteLock {
  /** 我们认定的服务端 version 基线（= 上次成功写入后服务端的版本） */
  version: number
  /** 基线对应的内容快照，用于区分"真冲突"与"仅 tags 等旁路字段变动" */
  title?: string
  content?: string
}

/** 乐观锁冲突信息（供编辑器行内提示渲染） */
export interface NoteConflictInfo {
  id: string
  title: string
  localContent: string
  remoteContent: string
  localVersion: number
  remoteVersion: number
}

/** noteId → 防抖窗口内待写入的字段（后写覆盖前写，天然合并） */
const _pendingFields = new Map<string, Record<string, unknown>>()
const _saveTimers = new Map<string, ReturnType<typeof setTimeout>>()
const _writeChains = new Map<string, Promise<void>>()
const _writeLocks = new Map<string, NoteWriteLock>()
/** 冲突未裁决的笔记：排队写入挂起，避免继续覆盖云端 */
const _blocked = new Set<string>()

function strField(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function versionOf(v: unknown): number {
  if (v === undefined || v === null) return NaN
  return Number(v)
}

export interface NoteSlice {
  floatingNotes: FloatingNote[]
  folders: Folder[]
  activeFolderId: string | null
  nextZ: number
  suggestedTags: Record<string, string[]>
  /** V1-4：非 null = 该笔记写入已挂起，等待「保留本地 / 取回云端」裁决 */
  noteConflict: NoteConflictInfo | null

  addFloatingNote: (n: FloatingNote) => void
  updateFloatingNote: (id: string, data: Partial<FloatingNote>) => void
  /** 强制落盘待发笔记写入并等待完成（失焦 / 切页 / 窗口隐藏时调用） */
  flushNoteSave: (id?: string) => Promise<void>
  /** 是否仍有未落盘的笔记写入（防抖窗口内或在途中） */
  hasPendingNoteSave: (id?: string) => boolean
  /** 裁决乐观锁冲突：保留本地（强制覆盖）或取回云端（采纳服务端内容） */
  resolveNoteConflict: (choice: 'keep-local' | 'take-cloud') => Promise<void>
  removeFloatingNote: (id: string) => void
  /** 永久删除笔记。默认走后端真实 RPC（await result===true 才本地移除）；
   *  `localOnly=true` 仅供跨窗口缓存失效内部路径使用（源窗口已删，勿重复发 RPC）。 */
  permanentlyDeleteNote: (id: string, localOnly?: boolean) => void | Promise<void>
  focusNote: (id: string) => void
  moveNote: (id: string, x: number, y: number) => void
  setActiveFolder: (id: string | null) => void
  addFolder: (name: string, parentId?: string) => string
  removeFolder: (id: string) => void
  renameFolder: (id: string, name: string) => void
  reorderFolders: (folders: Folder[]) => void
  moveNoteToFolder: (noteId: string, folderId: string | null) => void
  loadNotesFromDisk: () => Promise<void>
  getNoteById: (id: string) => FloatingNote | undefined
  incrementPomodoro: (noteId: string) => void
  incrementRefCount: (id: string) => void
  batchDeleteNotes: (ids: string[]) => void
  batchArchiveNotes: (ids: string[], archived: boolean) => void
  batchMoveNotesToFolder: (ids: string[], folderId: string | null) => void
  setSuggestedTags: (noteId: string, tags: string[]) => void
  acceptSuggestedTags: (noteId: string) => void
}

export const createNoteSlice: StateCreator<Store, [], [], NoteSlice> = (set, get, _api) => {
  // ── 保存链路（闭包内可取 set/get；模块级 Map 保证多调用点共用同一状态机） ──

  /** 把字段并入待发队列并按 NOTE_SAVE_DEBOUNCE_MS 防抖 */
  function scheduleNoteSave(id: string, fields: Record<string, unknown>): void {
    _pendingFields.set(id, { ...(_pendingFields.get(id) ?? {}), ...fields })
    const timer = _saveTimers.get(id)
    if (timer) clearTimeout(timer)
    _saveTimers.set(id, setTimeout(() => {
      _saveTimers.delete(id)
      void enqueueWrite(id)
    }, NOTE_SAVE_DEBOUNCE_MS))
  }

  /** 同一笔记的写入串成一条链，杜绝并发乱序覆盖（后打的字被先打的盖掉） */
  function enqueueWrite(id: string): Promise<void> {
    const prev = _writeChains.get(id) ?? Promise.resolve()
    const next = prev.then(() => runWrite(id)).catch(() => { /* 单条失败不阻断后续写入 */ })
    _writeChains.set(id, next)
    return next
  }

  async function runWrite(id: string): Promise<void> {
    const fields = _pendingFields.get(id)
    if (!fields) return
    _pendingFields.delete(id)
    if (_blocked.has(id)) {
      // 冲突未裁决 → 把最新内容留在待发队列，等 resolveNoteConflict 再落
      _pendingFields.set(id, fields)
      return
    }
    await writeOnce(id, fields, false)
  }

  /** 单次带乐观锁预检的写入；force=true 时跳过预检（用户已裁决保留本地） */
  async function writeOnce(id: string, fields: Record<string, unknown>, force: boolean): Promise<void> {
    let client: ReturnType<typeof getWsClient> | null = null
    try {
      client = getWsClient()
    } catch {
      client = null
    }
    if (!client || client.status !== 'connected') {
      // 离线：保持原有持久化队列语义（重连 FIFO 补发），版本基线作废待重新校准
      syncCall('notes.update', { note_id: id, ...fields })
      _writeLocks.delete(id)
      return
    }

    let remote: Record<string, unknown> | null = null
    try {
      remote = await client.call<Record<string, unknown> | null>('notes.get', { note_id: id })
    } catch {
      remote = null
    }

    const remoteVersion = versionOf(remote?.version)
    const remoteTitle = strField(remote?.title)
    const remoteContent = strField(remote?.content)
    const sendTitle = strField(fields.title)
    const sendContent = strField(fields.content)

    if (Number.isFinite(remoteVersion)) {
      // 云端已是本次要写的内容 → 跳过写入（避免无谓 version bump 与 versions 快照堆积）
      const alreadyOurs = (sendContent === undefined || remoteContent === sendContent)
        && (sendTitle === undefined || remoteTitle === sendTitle)
      if (alreadyOurs) {
        _writeLocks.set(id, { version: remoteVersion, title: remoteTitle, content: remoteContent })
        return
      }
      const lock = _writeLocks.get(id)
      if (!force && lock && remoteVersion !== lock.version) {
        const touched = (sendContent !== undefined && remoteContent !== lock.content)
          || (sendTitle !== undefined && remoteTitle !== lock.title)
        if (touched) {
          // 乐观锁失败：挂起写入，交给 UI 做「保留本地 / 取回云端」，禁静默覆盖
          _blocked.add(id)
          const localNote = get().floatingNotes.find(n => n.id === id)
          set({
            noteConflict: {
              id,
              title: remoteTitle ?? localNote?.title ?? '',
              localContent: sendContent ?? localNote?.content ?? '',
              remoteContent: remoteContent ?? '',
              localVersion: lock.version,
              remoteVersion,
            },
          })
          return
        }
      }
    }

    try {
      await client.call('notes.update', { note_id: id, ...fields })
    } catch {
      // 写入失败 → 退回持久化队列，重连补发（基线保持不动，下次写前重新校准）
      syncCall('notes.update', { note_id: id, ...fields })
      return
    }
    const base = Number.isFinite(remoteVersion) ? remoteVersion : baselineVersion(id)
    _writeLocks.set(id, {
      version: base + 1,
      title: sendTitle ?? remoteTitle ?? _writeLocks.get(id)?.title,
      content: sendContent ?? remoteContent ?? _writeLocks.get(id)?.content,
    })
  }

  function baselineVersion(id: string): number {
    const v = _writeLocks.get(id)?.version
    return Number.isFinite(v) ? (v as number) : 0
  }

  function targetIds(noteId?: string): string[] {
    const pool = noteId
      ? [noteId]
      : Array.from(new Set([..._pendingFields.keys(), ..._writeChains.keys()]))
    return pool.filter(nid => _pendingFields.has(nid) || _writeChains.has(nid))
  }

  async function flushNoteSave(noteId?: string): Promise<void> {
    for (const nid of targetIds(noteId)) {
      const timer = _saveTimers.get(nid)
      if (timer) clearTimeout(timer)
      _saveTimers.delete(nid)
    }
    // 最多三轮：覆盖"等待在途写入时用户又打字"的情况，保证 flush 语义是"落干净"
    for (let round = 0; round < 3; round++) {
      const ids = targetIds(noteId)
      if (ids.length === 0) return
      const waits = ids.map(nid => (_pendingFields.has(nid) ? enqueueWrite(nid) : _writeChains.get(nid) ?? Promise.resolve()))
      await Promise.all(waits.map(p => p.catch(() => undefined)))
      const pendingTimers = ids.some(nid => _saveTimers.has(nid))
      if (!pendingTimers && !ids.some(nid => _pendingFields.has(nid))) return
    }
  }

  function hasPendingNoteSave(noteId?: string): boolean {
    if (noteId) return _saveTimers.has(noteId) || _pendingFields.has(noteId)
    return _saveTimers.size > 0 || _pendingFields.size > 0
  }

  /** 笔记被删除时清理其保存链路残留（定时器 / 待发字段 / 基线 / 冲突挂起） */
  function cancelNoteSave(id: string): void {
    const timer = _saveTimers.get(id)
    if (timer) clearTimeout(timer)
    _saveTimers.delete(id)
    _pendingFields.delete(id)
    _writeLocks.delete(id)
    _blocked.delete(id)
    if (get().noteConflict?.id === id) set({ noteConflict: null })
  }

  async function resolveNoteConflict(choice: 'keep-local' | 'take-cloud'): Promise<void> {
    const conflict = get().noteConflict
    if (!conflict) return
    _blocked.delete(conflict.id)
    set({ noteConflict: null })
    if (choice === 'take-cloud') {
      // 取回云端：把服务端内容落回本地，并以其为新高水位（不回写，避免自我覆盖）
      _pendingFields.delete(conflict.id)
      _writeLocks.set(conflict.id, { version: conflict.remoteVersion, title: conflict.title, content: conflict.remoteContent })
      set((s: NoteSlice) => ({
        floatingNotes: s.floatingNotes.map(n => n.id === conflict.id
          ? { ...n, title: conflict.title || n.title, content: conflict.remoteContent, updatedAt: Date.now() }
          : n),
      }))
      return
    }
    // 保留本地：强制覆盖云端（跳过预检），并把本地最新内容写出去
    const note = get().floatingNotes.find(n => n.id === conflict.id)
    const fields: Record<string, unknown> = {}
    const queued = _pendingFields.get(conflict.id)
    _pendingFields.delete(conflict.id)
    const merged = { ...queued } as Record<string, unknown>
    if (note) {
      if (merged.content === undefined) merged.content = note.content ?? ''
      if (merged.title === undefined) merged.title = note.title ?? ''
    }
    Object.assign(fields, merged)
    try {
      await writeOnce(conflict.id, fields, true)
    } catch (e) {
      errorStore.error(tStatic('noteEditor.conflictKeepLocalFailed'))
      throw e
    }
  }

  return {
    floatingNotes: [{
      // 固定 ID：避免每次 store 初始化都生成新 ID 的欢迎笔记副本
      // （历史 bug：用 uid() 每次随机，导致磁盘上累积大量重复的欢迎笔记 .md 文件）
      id: 'welcome-guide',
      type: 'text',
      title: '📖 思忆便签使用指南',
      content: `# 📖 思忆便签使用指南

欢迎使用 **思忆便签** — 一款轻量级、全功能的桌面便签与待办管理工具。

---

## 🗒️ 笔记功能

- **新建笔记**：点击底部工具栏的「新建笔记」按钮，或在笔记面板点击「+」
- **Markdown 编辑**：支持完整的 Markdown 语法（标题、列表、表格、代码块等）
- **富文本编辑**：通过格式工具栏可切换富文本/Markdown 模式，支持加粗、斜体、列表、引用等
- **Wiki 链接**：使用 \`[[笔记标题]]\` 语法创建笔记之间的双向链接
- **图片插入**：支持粘贴或拖拽图片嵌入笔记，可调整尺寸和对齐方式
- **AI 写作辅助**：选中文本后弹出 AI 工具栏，支持润色、翻译、续写、总结、整理
- **版本历史**：保存笔记快照，可查看差异并回退到历史版本
- **标签管理**：为笔记添加标签，便于分类筛选
- **笔记导出**：支持导出为 Markdown、JSON 或完整备份

## ✅ 待办管理

- **新建待办**：点击底部工具栏的「新建待办」按钮
- **分类管理**：创建自定义分类，拖拽待办到分类卡片
- **优先级**：设置 P0/P1/P2 优先级
- **截止日期**：为待办设置截止日期，超过日期自动标记为逾期
- **重复周期**：支持每日/每周/每月重复的周期性待办
- **子任务**：在待办下添加子任务，逐步完成复杂事项
- **视图模式**：支持列表、看板、日历三种视图
- **搜索筛选**：支持关键词搜索、按状态/优先级筛选
- **奇门遁甲分析**：右键待办可进行奇门遁甲择时分析

## 📅 日历视图

- **月视图/周视图**：切换日历显示粒度
- **拖拽设置日期**：将待办拖拽到日历日期上快速设置截止日
- **智能简报**：显示当日待办统计（待办数、逾期数、高优先级）
- **农历显示**：自动显示当日农历日期和节气
- **黄历宜忌**：查看今日黄历宜忌信息
- **八字命理**：填写出生信息后查看当日八字分析和综合建议
- **奇门遁甲**：支持分领域的奇门遁甲占卜分析，含吉方、吉时、策略建议

## 🤖 AI 功能

- **AI 设置**：支持 Ollama 本地模型和 OpenAI 兼容 API
- **AI 对话**：内置 AI 聊天面板，可切换不同模型预设
- **AI 整理**：笔记内容混乱时，一键 AI 整理排版
- **自动标签**：启用后 AI 自动为笔记建议标签
- **智能搜索**：支持自然语言提问，AI 检索笔记后给出回答

## 🎨 个性化定制

- **主题切换**：深色/黑色/浅色三种主题
- **背景模式**：亚克力模糊 / 纯白 / 纯黑
- **自定义主题**：创建并保存自己的配色主题
- **笔记颜色**：为每条便签设置独立的颜色标识
- **透明度调节**：调整便签窗口的透明度
- **字体大小**：自由调节编辑区域的字体大小

## ⌨️ 快捷键

| 快捷键 | 功能 |
|--------|------|
| Alt + N | 新建笔记 |
| Alt + T | 新建待办 |
| Alt + Shift + H | 切换显示/隐藏 |
| Ctrl + Z | 撤销 |
| Ctrl + Shift + Z | 重做 |

## 📦 数据管理

- **导入**：支持 .md / .json / .txt / .todo 格式导入
- **导出备份**：一键导出全部笔记和待办的 JSON 备份
- **回收站**：删除的笔记和待办进入回收站，可恢复或永久删除
- **自动备份**：桌面版自动备份笔记到本地文件系统

---

> 思忆便签 — 记录灵感，管理生活。
> 如有问题或建议，欢迎在 GitHub 提交 Issue。`,
      x: 80,
      y: 80,
      width: 520,
      height: 600,
      zIndex: 1,
      pinned: false,
      createdAt: Date.now(),
    }],
    folders: [{ id: 'default', name: '全部笔记', sortOrder: 0, createdAt: Date.now() }],
    activeFolderId: null,
    nextZ: 1,
    suggestedTags: {},
    noteConflict: null,
    flushNoteSave,
    hasPendingNoteSave,
    resolveNoteConflict,

    addFloatingNote: (n) => {
      if (!n.id || typeof n.id !== 'string') {
        console.error('[noteSlice] addFloatingNote rejected: missing valid id', n)
        return
      }
      set((s: NoteSlice) => ({
        floatingNotes: [...s.floatingNotes, {
          ...n,
          zIndex: n.zIndex ?? s.nextZ,
          color: n.color ?? undefined,
          createdAt: n.createdAt ?? Date.now(),
        }],
        nextZ: s.nextZ + 1
      }))
      syncCall('notes.create', {
        title: n.title || '', content: n.content || '',
        tags: n.tags ?? [], folder: n.folderId ?? '',
        id: n.id,
      })
    },

    updateFloatingNote: (id, data) => {
      // 清洗 null 字节：AI 流式响应或剪贴板粘贴可能携带 \u0000，
      // 导致数据库出现内容全是 null 字符的"伪空笔记"。
      const cleanedData = { ...data }
      if (typeof cleanedData.content === 'string') {
        cleanedData.content = cleanedData.content.split('\0').join('')
      }
      if (typeof cleanedData.title === 'string') {
        cleanedData.title = cleanedData.title.split('\0').join('')
      }
      set((s: NoteSlice) => ({
        floatingNotes: s.floatingNotes.map(n =>
          n.id === id
            ? { ...n, ...cleanedData, updatedAt: cleanedData.updatedAt ?? Date.now() }
            : n
        )
      }))
      const noteFields: Record<string, unknown> = {}
      if (cleanedData.title !== undefined) noteFields.title = cleanedData.title
      if (cleanedData.content !== undefined) noteFields.content = cleanedData.content
      if (cleanedData.tags !== undefined) noteFields.tags = cleanedData.tags
      if (cleanedData.folderId !== undefined) noteFields.folder = cleanedData.folderId
      if (Object.keys(noteFields).length > 0) {
        // V1-4：防抖 + 串行 + 乐观锁，替代旧的"每次编辑一个并发 fire-and-forget RPC"
        scheduleNoteSave(id, noteFields)
      }
    },

    removeFloatingNote: (id) => {
      const note = get().floatingNotes.find((n: FloatingNote) => n.id === id)
      if (note) {
        _trashHandler({ id, type: 'note', data: note, deletedAt: Date.now() })
      }
      cancelNoteSave(id)
      set((s: NoteSlice) => ({ floatingNotes: s.floatingNotes.filter(n => n.id !== id) }))
      syncCall('notes.delete', { note_id: id })
    },

    permanentlyDeleteNote: async (id, localOnly = false) => {
      cancelNoteSave(id)
      // 跨窗口缓存失效内部路径（App.tsx onNoteDeleted）：源窗口已在后端硬删，
      // 这里仅抹本地内存条目，绝不重复发 RPC（否则对已消失的笔记再删 → 假失败刷屏）。
      if (localOnly) {
        set((s: NoteSlice) => ({
          floatingNotes: s.floatingNotes.filter(n => n.id !== id)
        }))
        return
      }
      // S0-2：旧实现是纯本地 filter 的「假删」路径——后端 .md 根本没删，用户
      // 看到条目消失以为删成功，实际盘上还在。现补发真实 RPC，且只有在后端
      // 确认 result===true 之后才本地移除。
      //   - true     ：已硬删 → 本地 filter
      //   - pending  ：R4 审批闸门（notes.permanent_delete 注册为 RISK_R4）
      //                → 不动本地，提示去审批卡批准（批准后后端广播 notes.changed 收敛）
      //   - false/异常：未真删（ghost/探活失败）→ errorStore 提示，不抹除
      let result: unknown = false
      try {
        const client = getWsClient()
        result = await client.call<unknown>(RpcMethods.notes.permanentDelete, { note_id: id })
      } catch (e) {
        console.error('[noteSlice] permanentlyDeleteNote RPC failed', e)
        errorStore.error(`${tStatic('trashView.permanentDelete')} ${tStatic('ux.deleteFailedSuffix')}`)
        return
      }
      if (result === true) {
        set((s: NoteSlice) => ({
          floatingNotes: s.floatingNotes.filter(n => n.id !== id)
        }))
        return
      }
      if (result && typeof result === 'object' && (result as { pending?: boolean }).pending) {
        // 审批 pending：后端尚未删，绝不能本地抹除（否则又是假删）
        errorStore.info(tStatic('ux.permDeletePendingHint'))
        return
      }
      errorStore.error(`${tStatic('trashView.permanentDelete')} ${tStatic('ux.deleteFailedSuffix')}`)
    },

    focusNote: (id) => set((s: NoteSlice) => {
      const z = s.nextZ
      const target = s.floatingNotes.find(n => n.id === id)
      if (!target) return {}
      if (target.pinned) return {}
      return {
        floatingNotes: s.floatingNotes.map(n =>
          n.id === id ? { ...n, zIndex: z } : n
        ),
        nextZ: z + 1,
      }
    }),

    moveNote: (id, x, y) => set((s: NoteSlice) => ({
      floatingNotes: s.floatingNotes.map(n => n.id === id ? { ...n, x, y } : n)
    })),

    setActiveFolder: (id) => set({ activeFolderId: id }),

    addFolder: (name, parentId) => {
      const id = uid()
      set((s: NoteSlice) => ({
        folders: [...s.folders, { id, name, parentId, sortOrder: s.folders.length, createdAt: Date.now() }]
      }))
      return id
    },

    removeFolder: (id) => set((s: NoteSlice) => {
      const collectDescendants = (parentId: string): string[] => {
        const children = s.folders.filter(f => f.parentId === parentId)
        return [
          parentId,
          ...children.flatMap(c => collectDescendants(c.id)),
        ]
      }
      const toRemove = collectDescendants(id)
      return {
        folders: s.folders.filter(f => !toRemove.includes(f.id)),
        activeFolderId: toRemove.includes(s.activeFolderId ?? '') ? null : s.activeFolderId,
        floatingNotes: s.floatingNotes.map(n =>
          toRemove.includes(n.folderId ?? '') ? { ...n, folderId: undefined } : n
        ),
      }
    }),

    renameFolder: (id, name) => set((s: NoteSlice) => ({
      folders: s.folders.map(f => f.id === id ? { ...f, name } : f)
    })),

    reorderFolders: (folders) => set(() => ({ folders })),

    moveNoteToFolder: (noteId, folderId) => set((s: NoteSlice) => ({
      floatingNotes: s.floatingNotes.map(n =>
        n.id === noteId ? { ...n, folderId: folderId || undefined } : n
      )
    })),

    loadNotesFromDisk: async () => {
      if (!_notesLoader) return
      try {
        const notes = await _notesLoader()
        if (notes.length === 0) return
        // 过滤掉无有效 id 的笔记
        const validNotes = notes.filter((n: Record<string, unknown>) =>
          n.id && typeof n.id === 'string' && n.id !== 'undefined'
        )
        if (validNotes.length === 0) return
        const existingIds = new Set(get().floatingNotes.map((n: FloatingNote) => n.id))
        const newNotes = validNotes.filter((n: Record<string, unknown>) => !existingIds.has(n.id as string))
        if (newNotes.length === 0) return
        set((s: NoteSlice) => {
          const maxZ = Math.max(0, ...s.floatingNotes.map((n: FloatingNote) => n.zIndex ?? 0))
          // 磁盘已加载到真实笔记后，移除内存中的初始欢迎笔记（固定 ID 'welcome-guide'），
          // 避免独立窗口启动时初始欢迎笔记 + 磁盘笔记同时存在导致重复累积。
          // 仅当新加载的笔记中不包含欢迎笔记时才移除（避免误删用户保留的欢迎笔记）。
          const loadedIds = new Set(newNotes.map((n: Record<string, unknown>) => n.id as string))
          const shouldDropWelcome = loadedIds.has('welcome-guide') === false
          const baseNotes = shouldDropWelcome
            ? s.floatingNotes.filter((n: FloatingNote) => n.id !== 'welcome-guide')
            : s.floatingNotes
          return {
            floatingNotes: [
              ...baseNotes,
              ...newNotes.map((n: Record<string, unknown>) => ({
                ...(n as unknown as FloatingNote),
                pinned: (n as Record<string, unknown>).pinned ?? false,
                zIndex: (n as Record<string, unknown>).pinned ? 999999 : ((n as Record<string, unknown>).zIndex ?? maxZ + 1),
              })) as FloatingNote[],
            ],
            nextZ: maxZ + newNotes.length + 1,
          }
        })
        // V1-4：磁盘/后端带来的 version 作为乐观锁基线种子（无 version 的笔记保持"未知"，
        // 首次写入前用 notes.get 校准，不做冲突判定）
        for (const n of newNotes) {
          const v = versionOf((n as Record<string, unknown>).version)
          const nid = n.id as string
          if (typeof nid === 'string' && Number.isFinite(v) && !_writeLocks.has(nid)) {
            _writeLocks.set(nid, { version: v, title: strField(n.title), content: strField(n.content) })
          }
        }
      } catch (e: unknown) {
        console.error('[noteSlice] loadNotesFromDisk failed:', e)
      }
    },

    getNoteById: (id) => get().floatingNotes.find((n: FloatingNote) => n.id === id),

    incrementPomodoro: (noteId) => set((s: NoteSlice) => ({
      floatingNotes: s.floatingNotes.map(n =>
        n.id === noteId ? { ...n, pomodoroCompleted: (n.pomodoroCompleted || 0) + 1 } : n
      )
    })),

    incrementRefCount: (id) => set((s: NoteSlice) => ({
      floatingNotes: s.floatingNotes.map(n =>
        n.id === id ? { ...n, refCount: (n.refCount || 0) + 1 } : n
      )
    })),

    batchDeleteNotes: (ids) => {
      const idSet = new Set(ids)
      for (const id of ids) {
        const note = get().floatingNotes.find((n: FloatingNote) => n.id === id)
        if (note) _trashHandler({ id, type: 'note', data: note, deletedAt: Date.now() })
        cancelNoteSave(id)
        syncCall('notes.delete', { note_id: id })
      }
      set((s: NoteSlice) => ({ floatingNotes: s.floatingNotes.filter(n => !idSet.has(n.id)) }))
    },

    batchArchiveNotes: (ids, archived) => set((s: NoteSlice) => {
      const idSet = new Set(ids)
      return { floatingNotes: s.floatingNotes.map(n =>
        idSet.has(n.id) ? { ...n, archived, updatedAt: Date.now() } : n
      )}
    }),

    batchMoveNotesToFolder: (ids, folderId) => set((s: NoteSlice) => {
      const idSet = new Set(ids)
      return { floatingNotes: s.floatingNotes.map(n =>
        idSet.has(n.id) ? { ...n, folderId: folderId || undefined, updatedAt: Date.now() } : n
      )}
    }),

    setSuggestedTags: (noteId, tags) => set((s: NoteSlice) => ({
      suggestedTags: { ...s.suggestedTags, [noteId]: tags }
    })),

    acceptSuggestedTags: (noteId) => {
      const state = get()
      const tags = state.suggestedTags[noteId]
      if (!tags || tags.length === 0) return
      const restTags = { ...state.suggestedTags }
      delete restTags[noteId]
      const note = state.floatingNotes.find(n => n.id === noteId)
      if (!note) {
        set({ suggestedTags: restTags })
        return
      }
      const merged = [...new Set([...(note.tags || []), ...tags])]
      set((s: NoteSlice) => ({
        floatingNotes: s.floatingNotes.map(n => n.id === noteId ? { ...n, tags: merged } : n),
        suggestedTags: restTags,
      }))
      // 建议标签此前只改内存不发 RPC，这里一并接入保存链路
      scheduleNoteSave(noteId, { tags: merged })
    },
  }
}
