import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { KnowledgeGraphData, GraphNode } from '../../../types'

// P2-PD-NODEL 前端接线（2026-09-21）：deleteEntity 的回执语义单测。
// 核心不变量：R4 未批准 / RPC 异常时**本地不得先删**（否则刷新后复活＝假删）。

const { mockGetWsClient } = vi.hoisted(() => ({ mockGetWsClient: vi.fn() }))

vi.mock('../../../utils/ai', () => ({ getWsClient: mockGetWsClient }))

import { deleteEntity } from '../graphService'

const backendNode: GraphNode = { id: 'bk-ent_1', label: '误提取实体', type: 'concept' }
const localNode: GraphNode = { id: 'n-tag_foo', label: 'foo', type: 'tag' }

function makeData(): KnowledgeGraphData {
  return {
    nodes: [backendNode, localNode, { id: 'bk-ent_2', label: '另一端', type: 'concept' }],
    edges: [{ source: 'bk-ent_1', target: 'bk-ent_2', type: 'related_to' }],
  }
}

describe('graphService.deleteEntity', () => {
  const call = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ status: 'connected', call })
    // 无 electronAPI → saveGraphData 走 best-effort 空实现
    ;(globalThis as unknown as { window: { electronAPI?: unknown } }).window.electronAPI = undefined
  })

  it('后端确认删除 → 节点与其悬挂边一并从视图移除', async () => {
    call.mockResolvedValueOnce({ success: true, deleted: true, entity_id: 'ent_1' })

    const res = await deleteEntity(makeData(), backendNode)

    expect(call).toHaveBeenCalledWith('knowledge.entity.delete', { entity_id: 'ent_1' })
    expect(res.outcome).toBe('deleted')
    expect(res.data.nodes.map(n => n.id)).not.toContain('bk-ent_1')
    expect(res.data.edges).toHaveLength(0)
  })

  it('R4 pending（只入队未执行）→ 图保持原样，不假删', async () => {
    call.mockResolvedValueOnce({ pending: true, requires_approval: true, pending_id: 'p1', message: '[等待用户批准]' })

    const data = makeData()
    const res = await deleteEntity(data, backendNode)

    expect(res.outcome).toBe('pending')
    expect(res.data.nodes).toHaveLength(3)
    expect(res.data.nodes.map(n => n.id)).toContain('bk-ent_1')
  })

  it('RPC 抛异常 → failed 且节点保留', async () => {
    call.mockRejectedValueOnce(new Error('ws closed'))

    const res = await deleteEntity(makeData(), backendNode)

    expect(res.outcome).toBe('failed')
    expect(res.error).toContain('ws closed')
    expect(res.data.nodes.map(n => n.id)).toContain('bk-ent_1')
  })

  it('deleted 但落盘失败（success=false）→ 如实报 failed，不谎称删除成功', async () => {
    call.mockResolvedValueOnce({ success: false, deleted: true, error: 'save failed: disk full' })

    const res = await deleteEntity(makeData(), backendNode)

    expect(res.outcome).toBe('failed')
    expect(res.error).toContain('disk full')
    expect(res.data.nodes.map(n => n.id)).not.toContain('bk-ent_1')
  })

  it('后端已无此实体 → not_found 并清掉陈旧节点', async () => {
    call.mockResolvedValueOnce({ success: false, deleted: false, entity_id: 'ent_1', error: 'not found' })

    const res = await deleteEntity(makeData(), backendNode)

    expect(res.outcome).toBe('not_found')
    expect(res.data.nodes.map(n => n.id)).not.toContain('bk-ent_1')
  })

  it('本地派生节点（非 bk-）→ 不碰后端，仅本地移除', async () => {
    const res = await deleteEntity(makeData(), localNode)

    expect(call).not.toHaveBeenCalled()
    expect(res.outcome).toBe('local_only')
    expect(res.data.nodes.map(n => n.id)).not.toContain('n-tag_foo')
  })

  it('后端未连接 → 降级为仅本地移除并如实标注 unavailable', async () => {
    mockGetWsClient.mockReturnValue({ status: 'disconnected', call })

    const res = await deleteEntity(makeData(), backendNode)

    expect(call).not.toHaveBeenCalled()
    expect(res.outcome).toBe('unavailable')
    expect(res.data.nodes.map(n => n.id)).not.toContain('bk-ent_1')
  })
})
