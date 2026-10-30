/**
 * 桥接层契约测试 / Bridge contract tests
 *
 * 为什么需要这一套：
 *   后端 RPC 的 handler 签名末尾普遍带 `**kwargs`，而且是
 *   「失败时返回 {error} 而不抛异常」。这两点叠加造成了一整类**静默失效**：
 *     参数名写错 → 不会报错，直接被 kwargs 吞掉 → 后端拿不到参数
 *     → 返回错误信封 → 前端不看返回值就 return true → 界面显示成功
 *
 *   实测查出十几处，例如：
 *     - info.items.list 用 section/unread_only/saved_only/query，
 *       后端要的是 category/read/saved/search → 四个过滤条件全失效
 *     - capability.approve 用 capability_id，后端要 cap_id → 永远失败
 *     - memory.save_conversation_anchor 用 anchor，后端要 text → 恒 false
 *     - api_keys.create 用 key，后端要 key_value → 建不出来
 *
 *   这些单元测试逐个锁定参数名与返回结构，避免以后再被「改回去」。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

/** 记录所有发出的 RPC 调用 */
let calls: Array<{ method: string; params: Record<string, unknown> }> = []
/** 可编程的返回值 */
let nextResult: unknown = { success: true }

vi.mock('@capacitor/preferences', () => ({
  Preferences: { get: async () => ({ value: null }), set: async () => {}, remove: async () => {} },
}))

vi.mock('../rpcClient', () => ({
  callRpc: async (method: string, params: Record<string, unknown> = {}) => {
    calls.push({ method, params })
    return nextResult
  },
  callRpcStream: async function* () { /* 未使用 */ },
  configureWs: () => {},
  configureWsAuthToken: () => {},
  isDesktopConnected: () => false,
  onEvent: () => () => {},
  getConnectionStatus: () => 'disconnected',
}))

beforeEach(() => {
  calls = []
  nextResult = { success: true }
  vi.resetModules()
})

describe('infoBridge — 参数名与后端签名一致', () => {
  it('listItems 把前端字段映射为后端字段（category/read/saved/search）', async () => {
    nextResult = { items: [] }
    const { listItems } = await import('../bridges/infoBridge')
    await listItems({ section: 'tech', unread_only: true, saved_only: true, query: 'AI', limit: 20 })

    expect(calls).toHaveLength(1)
    const p = calls[0].params
    // 后端要的四个名字
    expect(p.category).toBe('tech')
    expect(p.read).toBe(false)      // unread_only → read:false
    expect(p.saved).toBe(true)      // saved_only → saved:true
    expect(p.search).toBe('AI')
    // 绝不能出现前端旧名，否则会被 kwargs 吞掉
    expect(p).not.toHaveProperty('section')
    expect(p).not.toHaveProperty('unread_only')
    expect(p).not.toHaveProperty('saved_only')
    expect(p).not.toHaveProperty('query')
  })

  it('getItem 从 {item} 信封里取值', async () => {
    nextResult = { item: { id: 1, title: '标题' } }
    const { getItem } = await import('../bridges/infoBridge')
    const item = await getItem(1)
    expect(item).toEqual({ id: 1, title: '标题' })
  })

  it('stats 从 {stats} 信封里取值', async () => {
    nextResult = { stats: { total: 5, unread: 2 } }
    const { stats } = await import('../bridges/infoBridge')
    const s = await stats()
    expect(s?.total).toBe(5)
  })

  it('removeSource 用 name 而不是 source_id，且失败时不假成功', async () => {
    nextResult = { success: false, error: '缺少 name 参数' }
    const { removeSource } = await import('../bridges/infoBridge')
    const ok = await removeSource('zhihu')
    expect(calls[0].params.name).toBe('zhihu')
    expect(calls[0].params).not.toHaveProperty('source_id')
    // 后端返回 error 时必须为 false（原实现会盲目 return true）
    expect(ok).toBe(false)
  })

  it('fetchNow 用 source 而不是 source_id', async () => {
    nextResult = { items: [] }
    const { fetchNow } = await import('../bridges/infoBridge')
    await fetchNow('weibo')
    expect(calls[0].method).toBe('info.fetch_proxy')
    expect(calls[0].params.source).toBe('weibo')
    expect(calls[0].params).not.toHaveProperty('source_id')
  })

  it('addSource 用 parser_type 而不是 type', async () => {
    nextResult = { name: 'x' }
    const { addSource } = await import('../bridges/infoBridge')
    await addSource({ url: 'https://a', name: 'x', type: 'json' })
    expect(calls[0].params.parser_type).toBe('json')
    expect(calls[0].params).not.toHaveProperty('type')
  })

  it('aiSuggestKeywords 用 name 而不是 section_id', async () => {
    nextResult = { keywords: ['a'] }
    const { aiSuggestKeywords } = await import('../bridges/infoBridge')
    const kws = await aiSuggestKeywords('科技板块')
    expect(calls[0].params.name).toBe('科技板块')
    expect(calls[0].params).not.toHaveProperty('section_id')
    expect(kws).toEqual(['a'])
  })
})

describe('systemBridge — 参数名与假成功修复', () => {
  it('approveCapability 用 cap_id', async () => {
    nextResult = { success: true, cap_id: 'c1' }
    const { approveCapability } = await import('../bridges/systemBridge')
    const ok = await approveCapability('c1')
    expect(calls[0].params.cap_id).toBe('c1')
    expect(calls[0].params).not.toHaveProperty('capability_id')
    expect(ok).toBe(true)
  })

  it('approveCapability 在后端报错时返回 false', async () => {
    nextResult = { error: 'cap_id required' }
    const { approveCapability } = await import('../bridges/systemBridge')
    expect(await approveCapability('c1')).toBe(false)
  })

  it('recordUsage 用 action/domain', async () => {
    nextResult = { success: true }
    const { recordUsage } = await import('../bridges/systemBridge')
    const ok = await recordUsage('open_note', { domain: 'notes' })
    expect(calls[0].params.action).toBe('open_note')
    expect(calls[0].params.domain).toBe('notes')
    expect(calls[0].params).not.toHaveProperty('event')
    expect(ok).toBe(true)
  })
})

describe('memoryBridge — 锚点保存', () => {
  it('saveConversationAnchor 用 text 字段，并如实返回后端布尔结果', async () => {
    nextResult = true
    const { saveConversationAnchor } = await import('../bridges/memoryBridge')
    const ok = await saveConversationAnchor('s1', '锚点内容')
    expect(calls[0].params.text).toBe('锚点内容')
    expect(calls[0].params.session_id).toBe('s1')
    expect(calls[0].params).not.toHaveProperty('anchor')
    expect(ok).toBe(true)
  })

  it('后端返回 false 时不能报成功', async () => {
    nextResult = false
    const { saveConversationAnchor } = await import('../bridges/memoryBridge')
    expect(await saveConversationAnchor('s1', 'x')).toBe(false)
  })
})

describe('providerBridge — api_keys.create', () => {
  it('用 key_value 而不是 key', async () => {
    nextResult = { id: 1, name: 'k' }
    const { createApiKey } = await import('../bridges/providerBridge')
    await createApiKey('我的Key', 'sk-xxx')
    expect(calls[0].params.key_value).toBe('sk-xxx')
    expect(calls[0].params).not.toHaveProperty('key')
  })

  it('后端拒绝时返回 null 而不是假成功对象', async () => {
    nextResult = { success: false, error: 'key_value is required' }
    const { createApiKey } = await import('../bridges/providerBridge')
    expect(await createApiKey('n', '')).toBeNull()
  })
})

describe('agentBridge — run_now 返回结构', () => {
  it('从 invocation 字段取结果（不是 result）', async () => {
    nextResult = { success: true, invocation: { invocation_id: 'i1', status: 'completed' } }
    const { runNow } = await import('../bridges/agentBridge')
    const r = await runNow({ agentId: 'a1', task: 't' })
    expect(r.success).toBe(true)
    expect(r.result).toEqual({ invocation_id: 'i1', status: 'completed' })
  })
})

describe('dailyBridge — daily.get 信封', () => {
  it('从 note.content 里取正文', async () => {
    nextResult = { note: { content: '今天做了很多事', updated_at: 123 }, date: '2026-10-30' }
    const { getDaily } = await import('../bridges/dailyBridge')
    const d = await getDaily('2026-10-30')
    expect(d?.content).toBe('今天做了很多事')
    expect(d?.updated_at).toBe(123)
  })
})

describe('auditBridge — stats 字段映射', () => {
  it('rollbackable_count 映射到 rollbackable', async () => {
    nextResult = { total: 10, rollbackable_count: 3 }
    const { stats } = await import('../bridges/auditBridge')
    const s = await stats()
    expect(s?.rollbackable).toBe(3)
  })
})
