/**
 * MOB-KG（2026-09-21）· 手机端知识图谱桥 → 后端 RPC 契约测试。
 *
 * 存在的理由：这批桥函数历史上按**文档**而非代码写参数，后端真实签名变了
 * 也没人发现（`knowledge.graph` 不接收参数却被传 query；`knowledge.traverse`
 * 要 start_id/max_hops 却传 entity_id/depth；`knowledge.unified_query` 要
 * text 却传 query → 被 `**kwargs` 静默吞掉）。此处把 method + 参数名锁死。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockCallRpc } = vi.hoisted(() => ({ mockCallRpc: vi.fn() }))

vi.mock('../rpcClient', () => ({ callRpc: mockCallRpc }))

import {
  searchKnowledgeGraph,
  fetchKnowledgeGraph,
  traverseKnowledge,
  unifiedKnowledgeQuery,
} from '../bridges/aiBridge'
import {
  searchKnowledge,
  knowledgeRelations,
  knowledgeUnifiedQuery,
} from '../bridges/learningBridge'

describe('知识图谱桥：method 与参数名必须等于后端 handler 形参', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockCallRpc.mockResolvedValue([])
  })

  it('检索走 knowledge.search（knowledge.graph 不支持过滤）', async () => {
    await searchKnowledgeGraph('量子', 'concept')

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.search', { query: '量子', entity_type: 'concept' })
  })

  it('aiBridge.searchKnowledgeGraph 不得再误用 knowledge.graph', async () => {
    await searchKnowledgeGraph('量子')

    const method = mockCallRpc.mock.calls[0][0]
    expect(method).not.toBe('knowledge.graph')
  })

  it('全图走 knowledge.graph 且不塞参数', async () => {
    mockCallRpc.mockResolvedValueOnce({ entities: [], relations: [] })
    await fetchKnowledgeGraph()

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.graph', {})
  })

  it('遍历用 start_id / max_hops（旧参数名会让后端返回空图）', async () => {
    mockCallRpc.mockResolvedValueOnce({ entities: [], relations: [] })
    await traverseKnowledge('ent_1', 3)

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.traverse', { start_id: 'ent_1', max_hops: 3 })
  })

  it('统一查询用 text（learningBridge 曾传 query → 被 **kwargs 吞掉）', async () => {
    await knowledgeUnifiedQuery('张三')

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.unified_query', { text: '张三', limit: 15 })
  })

  it('统一查询（aiBridge 侧）同参数名，两条桥不得分叉', async () => {
    await unifiedKnowledgeQuery('张三', 5)

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.unified_query', { text: '张三', limit: 5 })
  })

  it('关系查询用 entity_id', async () => {
    mockCallRpc.mockResolvedValueOnce([])
    await knowledgeRelations('ent_1')

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.relations', { entity_id: 'ent_1' })
  })

  it('learningBridge.searchKnowledge 参数名与 schema 一致', async () => {
    await searchKnowledge('量子', 'concept')

    expect(mockCallRpc).toHaveBeenCalledWith('knowledge.search', { query: '量子', entity_type: 'concept' })
  })
})
