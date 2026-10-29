import { describe, it, expect, beforeEach, vi } from 'vitest'

// vitest 默认 node 环境无 localStorage —— 用 Object.defineProperty 注入一个内存版
function mockLocalStorage() {
  let store: Record<string, string> = {}
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value },
      removeItem: (key: string) => { delete store[key] },
      clear: () => { store = {} },
      get length() { return Object.keys(store).length },
      key: (i: number) => Object.keys(store)[i] ?? null,
    },
    configurable: true,
  })
}

// Mock @desk-notes/shared store
const fakeStore = {
  floatingNotes: [] as any[],
  todos: [] as any[],
  addFloatingNote: vi.fn(),
  addTodo: vi.fn(),
}
vi.mock('@desk-notes/shared', () => ({
  // Review fix #3: syncTasks 改用 setState 直接写入 todos，mock 需支持 setState
  useStore: {
    getState: () => fakeStore,
    setState: (fn: any) => {
      const partial = typeof fn === 'function' ? fn(fakeStore) : fn
      Object.assign(fakeStore, partial)
    },
  },
}))

// Mock @capacitor/preferences
vi.mock('@capacitor/preferences', () => ({
  Preferences: { get: async () => ({ value: null }), set: async () => {}, remove: async () => {} },
}))

// Mock callRpc & tokenManager — programmable per-test
const callRpcMock = vi.fn()
vi.mock('../rpcClient', () => ({
  callRpc: (...args: any[]) => callRpcMock(...args),
}))
vi.mock('../tokenManager', () => ({
  tokenManager: { isPaired: true },
}))

describe('contentSync.syncTasks (H14: must dedup by id, not by text)', () => {
  beforeEach(() => {
    callRpcMock.mockReset()
    fakeStore.todos = []
    fakeStore.addTodo = vi.fn()
  })

  // fullContentSync calls 4 sync* in parallel; each starts with a ping.
  // Mock callRpc to dispatch by method name so we don't depend on call order.
  function setupRpc(handlers: Record<string, any>) {
    callRpcMock.mockImplementation((method: string, _params?: any) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (handlers[method] !== undefined) return Promise.resolve(handlers[method])
      return Promise.resolve(null)
    })
  }

  it('does not skip a remote task just because a local task has the same text', async () => {
    fakeStore.todos = [
      { id: 'local-1', text: '今天晨会', done: false, createdAt: 1 },
    ]
    setupRpc({
      'tasks.list': [
        // tasks.list 返回 completed 字段（与 tasks.toggle 参数一致）
        { id: 'remote-7', text: '今天晨会', completed: true },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(1)
    // Review fix #3: syncTasks 现在用 setState 直接写入 todos（保留 remote id），
    // 不再调用 addTodo（会生成新 id 触发回环）
    expect(fakeStore.todos).toHaveLength(2)
    expect(fakeStore.todos[1]).toMatchObject({ id: 'remote-7', text: '今天晨会', done: true })
  })

  it('skips a remote task that already exists locally by id', async () => {
    fakeStore.todos = [
      { id: 'remote-7', text: '已经存在', done: false, createdAt: 1 },
    ]
    setupRpc({
      'tasks.list': [
        { id: 'remote-7', text: '已经存在', completed: true },  // same id → skip
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(0)
    expect(fakeStore.todos).toHaveLength(1)  // unchanged
  })

  it('handles numeric ids by string-coercing on both sides', async () => {
    fakeStore.todos = [{ id: 7, text: 'x', done: false, createdAt: 1 }]
    setupRpc({
      'tasks.list': [{ id: '7', text: 'x', done: true }],  // string id from server
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(0)
  })
})

describe('contentSync.syncTasks (P1-19: preserve remote timestamps, not Date.now())', () => {
  beforeEach(() => {
    callRpcMock.mockReset()
    fakeStore.todos = []
    fakeStore.addTodo = vi.fn()
  })

  function setupRpc(handlers: Record<string, any>) {
    callRpcMock.mockImplementation((method: string, _params?: any) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (handlers[method] !== undefined) return Promise.resolve(handlers[method])
      return Promise.resolve(null)
    })
  }

  it('preserves remote updated_at as local updatedAt (not Date.now)', async () => {
    // 远程 updated_at = 2026-06-20T10:00:00Z → 应转为该时刻的毫秒数，而非当前时间
    const remoteTs = '2026-06-20T10:00:00Z'
    const expectedMs = new Date(remoteTs).getTime()
    setupRpc({
      'tasks.list': [{ id: 'ts-1', text: '远端任务', updated_at: remoteTs }],
    })

    const { fullContentSync } = await import('../contentSync')
    await fullContentSync()

    expect(fakeStore.todos).toHaveLength(1)
    const pulled = fakeStore.todos[0]
    expect(pulled.updatedAt).toBe(expectedMs)
    expect(pulled.createdAt).toBe(expectedMs)  // createdAt 缺失时回落到 updated_at
  })

  it('falls back to Date.now() only when both remote timestamps are absent', async () => {
    const before = Date.now()
    setupRpc({
      'tasks.list': [{ id: 'ts-2', text: '无时间戳' }],
    })

    const { fullContentSync } = await import('../contentSync')
    await fullContentSync()
    const after = Date.now()

    expect(fakeStore.todos).toHaveLength(1)
    const pulled = fakeStore.todos[0]
    // 没有远程时间戳时，createdAt/updatedAt 应该都是合理的当前时间
    expect(pulled.createdAt).toBeGreaterThanOrEqual(before)
    expect(pulled.createdAt).toBeLessThanOrEqual(after)
    expect(pulled.updatedAt).toBeGreaterThanOrEqual(before)
    expect(pulled.updatedAt).toBeLessThanOrEqual(after)
  })

  it('preserves remote createdAt when only createdAt is provided', async () => {
    // createdAt 是数字（毫秒），updated_at 缺失 → updatedAt 回落到 createdAt
    const created = 1718000000000
    setupRpc({
      'tasks.list': [{ id: 'ts-3', text: '只有创建时间', createdAt: created }],
    })

    const { fullContentSync } = await import('../contentSync')
    await fullContentSync()

    expect(fakeStore.todos).toHaveLength(1)
    const pulled = fakeStore.todos[0]
    expect(pulled.createdAt).toBe(created)
    expect(pulled.updatedAt).toBe(created)  // 缺 updated_at 时回落到 createdAt
  })
})

describe('contentSync.syncTranslations (P1-19: full-text dedup, not slice(0,30))', () => {
  beforeEach(() => {
    callRpcMock.mockReset()
    fakeStore.todos = []
    mockLocalStorage()
    localStorage.clear()
  })

  function setupRpc(handlers: Record<string, any>) {
    callRpcMock.mockImplementation((method: string, _params?: any) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (handlers[method] !== undefined) return Promise.resolve(handlers[method])
      return Promise.resolve(null)
    })
  }

  it('keeps two translations that share first 30 chars but differ later', async () => {
    // 两条前 30 字符相同但全文不同的翻译 — 旧 .slice(0,30) 会丢一条，新逻辑应保留两条
    const sharedPrefix = '这是一段非常非常非常非常非常非常非常非常非常非常非常非常长的开头'  // >30 chars
    setupRpc({
      'translations.list': [
        { source: 'zh', target: 'en', sourceText: sharedPrefix + ' 变体A。', translatedText: 'variant A', timestamp: 1 },
        { source: 'zh', target: 'en', sourceText: sharedPrefix + ' 变体B。', translatedText: 'variant B', timestamp: 2 },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.translations).toBe(2)
    const stored = JSON.parse(localStorage.getItem('siyi-translations') || '[]')
    expect(stored).toHaveLength(2)
  })

  it('still dedups genuinely identical sourceText', async () => {
    setupRpc({
      'translations.list': [
        { source: 'zh', target: 'en', sourceText: '完全相同的句子', translatedText: 'A', timestamp: 1 },
        { source: 'zh', target: 'en', sourceText: '完全相同的句子', translatedText: 'B', timestamp: 2 },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.translations).toBe(1)  // 真重复仍去重
    const stored = JSON.parse(localStorage.getItem('siyi-translations') || '[]')
    expect(stored).toHaveLength(1)
  })

  it('merges new translations into an existing localStorage cache without losing old ones', async () => {
    localStorage.setItem('siyi-translations', JSON.stringify([
      { source: 'zh', target: 'en', sourceText: '旧翻译', translatedText: 'old', timestamp: 0 },
    ]))
    setupRpc({
      'translations.list': [
        { source: 'zh', target: 'en', sourceText: '旧翻译', translatedText: 'old', timestamp: 0 },  // dup
        { source: 'zh', target: 'en', sourceText: '新翻译', translatedText: 'new', timestamp: 1 },  // new
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.translations).toBe(1)  // 只新增 1 条
    const stored = JSON.parse(localStorage.getItem('siyi-translations') || '[]')
    expect(stored).toHaveLength(2)
  })
})

describe('contentSync LWW (b4-m2: Last-Write-Wins merge)', () => {
  beforeEach(() => {
    callRpcMock.mockReset()
    fakeStore.todos = []
    fakeStore.floatingNotes = []
    mockLocalStorage()
    localStorage.clear()
  })

  function setupRpc(handlers: Record<string, any>) {
    callRpcMock.mockImplementation((method: string, _params?: any) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (handlers[method] !== undefined) return Promise.resolve(handlers[method])
      return Promise.resolve(null)
    })
  }

  // ── tasks ──────────────────────────────────────────────
  it('pulls a remote edit onto an existing local task when remote updatedAt is newer', async () => {
    fakeStore.todos = [
      { id: 't1', text: '旧标题', done: false, createdAt: 1000, updatedAt: 1000 },
    ]
    setupRpc({
      'tasks.list': [
        { id: 't1', title: '新标题', completed: true, updated_at: '2026-07-01T00:00:00Z' },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    // 远程编辑被拉到本地（计数含更新）
    expect(result.tasks).toBe(1)
    expect(fakeStore.todos).toHaveLength(1)
    expect(fakeStore.todos[0]).toMatchObject({ id: 't1', text: '新标题', done: true })
    expect(fakeStore.todos[0].updatedAt).toBe(new Date('2026-07-01T00:00:00Z').getTime())
    // 远端更新覆盖本地，不应触发回推
    const updateCalls = callRpcMock.mock.calls.filter((c: any[]) => c[0] === 'tasks.update')
    expect(updateCalls).toHaveLength(0)
  })

  it('pushes a local edit back to desktop when local updatedAt is newer (with preserved timestamp)', async () => {
    fakeStore.todos = [
      { id: 't2', text: '本地新标题', done: false, priority: 2, category: 'work', createdAt: 1000, updatedAt: 1785542400000 },  // 2026-08-01，晚于远端 2026-06-01
    ]
    setupRpc({
      'tasks.list': [
        { id: 't2', title: '远端旧标题', completed: true, updated_at: '2026-06-01T00:00:00Z' },  // 远端旧
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(0)  // 本地没被覆盖
    expect(fakeStore.todos).toHaveLength(1)
    expect(fakeStore.todos[0].text).toBe('本地新标题')  // 保留本地编辑
    const updateCalls = callRpcMock.mock.calls.filter((c: any[]) => c[0] === 'tasks.update')
    expect(updateCalls).toHaveLength(1)
    expect(updateCalls[0][1]).toMatchObject({
      task_id: 't2', title: '本地新标题', completed: false, priority: 2, category: 'work',
    })
    // 必须携带本地 updated_at（ISO），让后端保留时间戳而不是用 now 覆盖
    expect(updateCalls[0][1].updated_at).toBe(new Date(1785542400000).toISOString())
  })

  it('ignores a remote task with no timestamp when local version exists', async () => {
    fakeStore.todos = [
      { id: 't3', text: '本地版本', done: false, createdAt: 1 },
    ]
    setupRpc({
      'tasks.list': [{ id: 't3', title: '远端无时间戳', completed: true }],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(0)
    expect(fakeStore.todos).toHaveLength(1)
    expect(fakeStore.todos[0].text).toBe('本地版本')  // 无时间戳不覆盖本地
  })

  it('removes a local task when the desktop tombstone is newer (deleted_at > local updatedAt)', async () => {
    fakeStore.todos = [
      { id: 't4', text: '将被删除', done: false, createdAt: 1000, updatedAt: 1000 },
    ]
    setupRpc({
      'tasks.list': [],
      'tasks.list_trashed': [
        { id: 't4', title: '将被删除', deleted_at: '2026-07-04T00:00:00Z' },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(1)
    expect(fakeStore.todos).toHaveLength(0)  // 本地已删
  })

  it('keeps a local task that was edited after the desktop delete (local newer wins)', async () => {
    fakeStore.todos = [
      { id: 't5', text: '本地后来编辑过', done: false, createdAt: 1000, updatedAt: 1785542400000 },  // 2026-08-01，晚于删除时间 2026-07-04
    ]
    setupRpc({
      'tasks.list': [],
      'tasks.list_trashed': [
        { id: 't5', title: '远端已删', deleted_at: '2026-07-04T00:00:00Z' },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.tasks).toBe(0)
    expect(fakeStore.todos).toHaveLength(1)  // 保留本地
  })

  // ── notes ──────────────────────────────────────────────
  it('pulls a remote note edit onto an existing local note when remote updatedAt is newer', async () => {
    fakeStore.floatingNotes = [
      { id: 'n1', title: '旧标题', content: '旧内容', createdAt: 1000, updatedAt: 1000 },
    ]
    setupRpc({
      'notes.list': [
        { id: 'n1', title: '新标题', content: '新内容', tags: ['a'], updated_at: '2026-07-02T00:00:00Z' },
      ],
      'notes.list_trashed': [],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.notes).toBe(1)
    expect(fakeStore.floatingNotes).toHaveLength(1)
    expect(fakeStore.floatingNotes[0]).toMatchObject({ id: 'n1', title: '新标题', content: '新内容', tags: ['a'] })
    expect(fakeStore.floatingNotes[0].updatedAt).toBe(new Date('2026-07-02T00:00:00Z').getTime())
    const updateCalls = callRpcMock.mock.calls.filter((c: any[]) => c[0] === 'notes.update')
    expect(updateCalls).toHaveLength(0)  // 不回推
  })

  it('pushes a local note edit back to desktop when local updatedAt is newer', async () => {
    fakeStore.floatingNotes = [
      { id: 'n2', title: '本地标题', content: '本地内容', tags: ['x'], createdAt: 1000, updatedAt: 1785542400000 },  // 2026-08-01，晚于远端 2026-06-02
    ]
    setupRpc({
      'notes.list': [
        { id: 'n2', title: '远端标题', content: '远端内容', updated_at: '2026-06-02T00:00:00Z' },
      ],
      'notes.list_trashed': [],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.notes).toBe(0)
    expect(fakeStore.floatingNotes).toHaveLength(1)
    expect(fakeStore.floatingNotes[0].title).toBe('本地标题')  // 保留本地
    const updateCalls = callRpcMock.mock.calls.filter((c: any[]) => c[0] === 'notes.update')
    expect(updateCalls).toHaveLength(1)
    // 本地更新 → 回推的是【本地】字段（本地是更新的真相，不能用远端旧数据覆盖桌面）
    expect(updateCalls[0][1]).toMatchObject({ note_id: 'n2', title: '本地标题', content: '本地内容', tags: ['x'] })
    expect(updateCalls[0][1].updated_at).toBe(new Date(1785542400000).toISOString())
  })

  it('removes a local note when the desktop tombstone is newer (deleted_at > local updatedAt)', async () => {
    fakeStore.floatingNotes = [
      { id: 'n3', title: '将被删除', content: '内容', createdAt: 1000, updatedAt: 1000 },
    ]
    setupRpc({
      'notes.list': [],
      'notes.list_trashed': [
        { id: 'n3', title: '将被删除', content: '内容', deleted_at: '2026-07-03T00:00:00Z' },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.notes).toBe(1)
    expect(fakeStore.floatingNotes).toHaveLength(0)  // 本地已删
  })

  it('keeps a local note that was edited after the desktop delete (local newer wins)', async () => {
    fakeStore.floatingNotes = [
      { id: 'n4', title: '本地后来编辑过', content: '内容', createdAt: 1000, updatedAt: 1785542400000 },  // 2026-08-01，晚于桌面删除时间 2026-07-03
    ]
    setupRpc({
      'notes.list': [],
      'notes.list_trashed': [
        { id: 'n4', title: '远端已删', content: '内容', deleted_at: '2026-07-03T00:00:00Z' },
      ],
    })

    const { fullContentSync } = await import('../contentSync')
    const result = await fullContentSync()

    expect(result.notes).toBe(0)
    expect(fakeStore.floatingNotes).toHaveLength(1)  // 保留本地
  })
})

describe('contentSync PendingOpManager (b4-m2: 离线写入队 + 同步时重放)', () => {
  beforeEach(() => {
    callRpcMock.mockReset()
    fakeStore.todos = []
    fakeStore.floatingNotes = []
    mockLocalStorage()
    localStorage.clear()
  })

  it('queues a failed task push-back as a pending op instead of dropping it', async () => {
    const { fullContentSync, pendingOps } = await import('../contentSync')
    pendingOps.clear()
    fakeStore.todos = [
      { id: 't6', text: '本地新标题', done: false, createdAt: 1000, updatedAt: 1785542400000 },  // 2026-08-01
    ]
    // tasks.update 抛错 → 模拟桌面瞬断：回推失败必须入队而非静默丢失
    callRpcMock.mockImplementation((method: string) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (method === 'tasks.list') return Promise.resolve([{ id: 't6', title: '远端旧', updated_at: '2026-06-01T00:00:00Z' }])
      if (method === 'tasks.update') return Promise.reject(new Error('offline'))
      return Promise.resolve(null)
    })

    await fullContentSync()

    expect(pendingOps.count).toBe(1)
    const stored = JSON.parse(localStorage.getItem('siyi_pending_ops') || '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0]).toMatchObject({ type: 'task_update', entityId: 't6' })
    expect(stored[0].payload.title).toBe('本地新标题')
  })

  it('dedupes queued ops by (type, entityId) — latest payload wins', async () => {
    const { pendingOps } = await import('../contentSync')
    pendingOps.clear()
    pendingOps.enqueueTaskOp('task_update', 't7', { title: 'v1', updated_at: 'A' })
    pendingOps.enqueueTaskOp('task_update', 't7', { title: 'v2', updated_at: 'B' })
    expect(pendingOps.count).toBe(1)  // 同键去重，不膨胀队列
    const stored = JSON.parse(localStorage.getItem('siyi_pending_ops') || '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0].payload.title).toBe('v2')
  })

  it('replays queued ops at the start of fullContentSync and drains the queue on success', async () => {
    const { fullContentSync, pendingOps } = await import('../contentSync')
    pendingOps.clear()
    pendingOps.enqueueTaskOp('task_update', 't8', {
      title: '离线编辑', completed: 'true', priority: '2', category: 'work', updated_at: '2026-08-02T00:00:00.000Z',
    })

    const updateCalls: any[] = []
    callRpcMock.mockImplementation((method: string, params?: any) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (method === 'tasks.update') { updateCalls.push(params); return Promise.resolve({ ok: true }) }
      if (method === 'tasks.list') return Promise.resolve([])
      if (method === 'tasks.list_trashed') return Promise.resolve([])
      if (method === 'notes.list') return Promise.resolve([])
      if (method === 'notes.list_trashed') return Promise.resolve([])
      return Promise.resolve(null)
    })

    await fullContentSync()

    expect(updateCalls).toHaveLength(1)
    expect(updateCalls[0]).toMatchObject({
      task_id: 't8', title: '离线编辑', completed: true, priority: 2, category: 'work',
      updated_at: '2026-08-02T00:00:00.000Z',
    })
    expect(pendingOps.count).toBe(0)  // 重放成功 → 出队
    expect(localStorage.getItem('siyi_pending_ops')).toBe('[]')
  })

  it('keeps the op queued (retryCount++ ) when replay fails while desktop is still offline', async () => {
    const { fullContentSync, pendingOps } = await import('../contentSync')
    pendingOps.clear()
    pendingOps.enqueueTaskOp('task_update', 't9', { title: 'x', updated_at: 'A' })

    callRpcMock.mockImplementation((method: string) => {
      if (method === 'ping') return Promise.resolve('pong')
      if (method === 'tasks.update') return Promise.reject(new Error('still offline'))
      return Promise.resolve(null)
    })

    await fullContentSync()

    expect(pendingOps.count).toBe(1)  // 重放失败仍保留
    const stored = JSON.parse(localStorage.getItem('siyi_pending_ops') || '[]')
    expect(stored).toHaveLength(1)
    expect(stored[0].retryCount).toBe(1)  // 计数递增，未达上限不丢弃
  })
})
