import { describe, it, expect, vi, beforeEach } from 'vitest'

// P4-KG-DETAIL 前端消费侧：详情拉取与「可跳」判定的回归。
// 关键纪律：后端 jumpable=true 不等于前端能跳 —— 未接线路由的模块不给按钮，
// 否则就是让用户点了报错。

const { mockGetWsClient } = vi.hoisted(() => ({ mockGetWsClient: vi.fn() }))
vi.mock('../../../utils/ai', () => ({ getWsClient: mockGetWsClient }))

import { canJumpOrigin, fetchEntityDetail, JUMPABLE_TARGET_MODULES, type EntityOrigin } from '../graphService'

function origin(over: Partial<EntityOrigin> = {}): EntityOrigin {
  return {
    raw: 'note:42',
    kind: 'note',
    source_id: '42',
    label: '笔记',
    target_module: 'notes',
    jumpable: true,
    title: '周会记录',
    resolved: true,
    ...over,
  }
}

describe('canJumpOrigin', () => {
  it('已接线路由 + 后端可跳 + 有 id → 可跳', () => {
    expect(canJumpOrigin(origin())).toBe(true)
  })

  it('后端说可跳但前端没接该模块路由 → 不给假链接', () => {
    expect(JUMPABLE_TARGET_MODULES.has('notes')).toBe(true)
    expect(canJumpOrigin(origin({ target_module: 'todos' }))).toBe(false)
    expect(canJumpOrigin(origin({ target_module: 'info' }))).toBe(false)
    expect(canJumpOrigin(origin({ target_module: '' }))).toBe(false)
  })

  it('后端判不可跳（外部平台镜像）→ 一律不可跳', () => {
    expect(canJumpOrigin(origin({ target_module: 'notes', jumpable: false }))).toBe(false)
    expect(canJumpOrigin(origin({ source_id: '' }))).toBe(false)
    expect(canJumpOrigin(null)).toBe(false)
  })
})

describe('fetchEntityDetail', () => {
  const call = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ status: 'connected', call })
  })

  it('剥掉前端 bk- 前缀再问后端（后端只认裸实体 id）', async () => {
    call.mockResolvedValueOnce({ found: true, origins: [], relations: [] })

    await fetchEntityDetail('bk-ent_a')

    expect(call).toHaveBeenCalledWith('knowledge.entity.detail', { entity_id: 'ent_a' })
  })

  it('后端 not found / RPC 异常 → 返回 null，让面板降级而不是渲染半截详情', async () => {
    call.mockResolvedValueOnce({ found: false, error: 'not found' })
    expect(await fetchEntityDetail('bk-ent_a')).toBeNull()

    call.mockRejectedValueOnce(new Error('ws down'))
    expect(await fetchEntityDetail('bk-ent_a')).toBeNull()
  })

  it('未连接后端时不发请求（本地视图仍可用）', async () => {
    mockGetWsClient.mockReturnValue({ status: 'disconnected', call })

    expect(await fetchEntityDetail('bk-ent_a')).toBeNull()
    expect(call).not.toHaveBeenCalled()
  })

  it('本地派生节点（无 bk- 且后端无此实体）不白问', async () => {
    expect(await fetchEntityDetail('bk-')).toBeNull()
    expect(call).not.toHaveBeenCalled()
  })

  // P4-KG-DETAIL（2026-10-15）：entity.detail 透传 —— 面板头部渲染
  // 「这个实体是什么」就靠这个字段；空串 = 存量未 backfill（面板显示暂无详情）。
  it('entity.detail 透传不丢（面板渲染「这个实体是什么」的来源）', async () => {
    call.mockResolvedValueOnce({
      found: true,
      entity: { id: 'ent_a', name: '蓝鲸计划', detail: '数据平台重构项目' },
      origins: [],
      relations: [],
    })

    const res = await fetchEntityDetail('bk-ent_a')

    expect(res?.entity?.detail).toBe('数据平台重构项目')
  })

  it('entity.detail 缺失/空串时如实为空（不编造占位）', async () => {
    call.mockResolvedValueOnce({ found: true, entity: { id: 'ent_a', name: 'X' }, origins: [], relations: [] })
    expect((await fetchEntityDetail('bk-ent_a'))?.entity?.detail).toBeUndefined()

    call.mockResolvedValueOnce({ found: true, entity: { id: 'ent_a', name: 'X', detail: '' }, origins: [], relations: [] })
    expect((await fetchEntityDetail('bk-ent_a'))?.entity?.detail).toBe('')
  })
})
