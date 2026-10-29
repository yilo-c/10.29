/**
 * 收件箱桥 — 让本地 `inboxManager` 的条目和桌面 `inbox.*` 互通。
 *
 * 设计：
 *   - 本地仍然是真源（离线优先），保存到 localStorage；
 *   - 当 WS 可用 / 配对完成时，调用 `inbox.add` 把条目镜像到桌面；
 *   - 启动时拉一次 `inbox.list` 把桌面侧未处理条目合并入本地（按内容 + 时间戳去重）；
 *   - 订阅 `inbox.new` 事件（由 eventBridge 扇出）将远端新增的条目并入本地。
 */
import { callRpc } from '../rpcClient'
import { addInboxItem, getInboxItems, saveInboxItems, type InboxItem } from '../inboxManager'
import { onBackendEvent } from '../eventBridge'

interface RemoteInboxEntry {
  id?: string
  entry_id?: string
  content?: string
  text?: string
  type?: string
  source?: string
  created_at?: number | string
  archived?: boolean
  processed?: boolean
}

function asTimestamp(value: unknown): number {
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const t = Date.parse(value)
    if (!Number.isNaN(t)) return t
  }
  return Date.now()
}

function normalizeRemote(entry: RemoteInboxEntry): InboxItem | null {
  const text = (entry.content ?? entry.text ?? '').toString().trim()
  if (!text) return null
  const type = (entry.type === 'task' ? 'task' : 'note') as InboxItem['type']
  const source = (entry.source === 'share' || entry.source === 'sync' || entry.source === 'quick_capture')
    ? entry.source as InboxItem['source']
    : 'sync'
  return {
    id: entry.entry_id ?? entry.id ?? `${asTimestamp(entry.created_at)}-${Math.random().toString(36).slice(2, 6)}`,
    text,
    type,
    source,
    createdAt: asTimestamp(entry.created_at),
    processed: Boolean(entry.archived ?? entry.processed ?? false),
  }
}

function mergeIntoLocal(remote: InboxItem[]): number {
  if (!remote.length) return 0
  const local = getInboxItems()
  const seen = new Set(local.map(i => `${i.text}|${Math.floor(i.createdAt / 1000)}`))
  let added = 0
  for (const r of remote) {
    const key = `${r.text}|${Math.floor(r.createdAt / 1000)}`
    if (seen.has(key)) continue
    local.unshift(r)
    seen.add(key)
    added += 1
  }
  if (added > 0) {
    local.sort((a, b) => b.createdAt - a.createdAt)
    saveInboxItems(local)
  }
  return added
}

/** 把本地新加项也镜像到桌面端（失败静默）。 */
export async function pushInboxItemRemote(item: Omit<InboxItem, 'id' | 'createdAt' | 'processed'>): Promise<void> {
  try {
    await callRpc('inbox.add', {
      content: item.text,
      type: item.type,
      source: item.source,
    })
  } catch {
    /* offline-first: local already saved */
  }
}

/** 本地 + 远端双写。返回本地条目 id。 */
export async function captureToInbox(item: Omit<InboxItem, 'id' | 'createdAt' | 'processed'>): Promise<void> {
  addInboxItem(item)
  await pushInboxItemRemote(item)
}

/** 拉取远端列表合并入本地。 */
export async function pullInbox(): Promise<number> {
  try {
    // V1 修复（2026-09-16）：服务端 inbox.list 默认只回 50 条（上限 200），且单帧超 4MiB
    // 会被出站守卫拦成 -32007。合并入本地需要全量口径，故按页拉满。
    const PAGE_FETCH = 200
    const MAX_PAGES = 10
    let entries: RemoteInboxEntry[] = []
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const res = await callRpc<unknown>('inbox.list', {
        include_archived: false,
        limit: PAGE_FETCH,
        offset: page * PAGE_FETCH,
      })
      let pageEntries: RemoteInboxEntry[] = []
      if (Array.isArray(res)) pageEntries = res as RemoteInboxEntry[]
      else if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
        pageEntries = (res as { items: RemoteInboxEntry[] }).items
      }
      if (pageEntries.length === 0) break
      entries = entries.concat(pageEntries)
      if (pageEntries.length < PAGE_FETCH) break
    }
    const normalized = entries.map(normalizeRemote).filter((x): x is InboxItem => x !== null)
    return mergeIntoLocal(normalized)
  } catch {
    return 0
  }
}

/** 让 AI 给单条条目自动分类（笔记/任务）。 */
export async function classifyInboxEntry(entryId: string): Promise<unknown> {
  return callRpc('inbox.classify', { entry_id: entryId })
}

let bridgeStarted = false
let unsubInboxNew: (() => void) | null = null

/** 启动入口：在 App 启动或首次配对成功后调用。 */
export function startInboxBridge(): void {
  if (bridgeStarted) return
  bridgeStarted = true

  // 启动时拉一次（不阻塞 UI）
  void pullInbox()

  unsubInboxNew = onBackendEvent('inbox.new', (event) => {
    const payload = (event.payload ?? {}) as RemoteInboxEntry
    const item = normalizeRemote(payload)
    if (!item) return
    mergeIntoLocal([item])
  })
}

export function stopInboxBridge(): void {
  bridgeStarted = false
  if (unsubInboxNew) {
    try { unsubInboxNew() } catch { /* ignore */ }
    unsubInboxNew = null
  }
}
