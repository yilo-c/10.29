import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import KnowledgeGraphModule from './KnowledgeGraphModule'
import { useStore } from '../../store/useStore'

// ── 子图渲染组件桩化：避免真实 SVG 力导向 / 思维导图在 jsdom 下的布局开销 ──
vi.mock('../Graph/ForceGraph', () => ({
  default: ({ nodes, edges }: { nodes: unknown[]; edges: unknown[] }) => (
    <div data-testid="force-graph" data-node-count={nodes.length} data-edge-count={edges.length} />
  ),
}))
vi.mock('../Graph/GraphMindMap', () => ({
  default: () => <div data-testid="mindmap" />,
}))

// ── graphService 是知识图谱的后端封装层，整体桩化，断言其被正确调用 ──
const graphService = vi.hoisted(() => ({
  getGraphData: vi.fn(),
  reloadGraphData: vi.fn(),
  searchNodes: vi.fn((data: { nodes: unknown[] }) => data.nodes),
  getNodeConnections: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  addNode: vi.fn(),
  removeNode: vi.fn(),
  addEdge: vi.fn(),
  searchBackendKnowledge: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  expandBackendNode: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  fetchKnowledgeCards: vi.fn().mockResolvedValue([]),
  fetchTopicMOCs: vi.fn().mockResolvedValue([]),
  triggerKnowledgeSynthesize: vi.fn(),
  getLocalSubgraph: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
  propertyEntries: vi.fn().mockReturnValue([]),
  findLocalNoteId: vi.fn().mockReturnValue(null),
}))
vi.mock('../Graph/graphService', () => graphService)

const SAMPLE_NODES = [
  { id: 'e1', label: '量子力学', type: 'concept' },
  { id: 'e2', label: '爱因斯坦', type: 'person' },
]
const SAMPLE_EDGES = [{ source: 'e1', target: 'e2', type: 'related_to' }]

beforeAll(() => {
  // jsdom 无 ResizeObserver，组件测量容器需要它（globalThis 而非 global：
  // 前者是标准全局对象，tsc 的 node 类型下可解析）
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
})

beforeEach(() => {
  vi.clearAllMocks()
  useStore.setState({
    locale: 'zh-CN',
    floatingNotes: [
      {
        id: 'note-1',
        type: 'text',
        title: '笔记一',
        content: '正文',
        x: 0,
        y: 0,
        width: 320,
        height: 240,
        zIndex: 1,
      },
    ],
  })
  graphService.getGraphData.mockResolvedValue({ nodes: SAMPLE_NODES, edges: SAMPLE_EDGES })
})

describe('KnowledgeGraphModule', () => {
  it('挂载时经 graphService 加载图谱并把节点渲染到 ForceGraph', async () => {
    render(<KnowledgeGraphModule />)

    const graph = await screen.findByTestId('force-graph')
    // 端到端接线：默认视图为 graph，且用本地笔记派生调用 getGraphData
    expect(graphService.getGraphData).toHaveBeenCalledTimes(1)
    expect(graphService.getGraphData.mock.calls[0][0]).toHaveLength(1)
    expect(graph.getAttribute('data-node-count')).toBe('2')
    expect(graph.getAttribute('data-edge-count')).toBe('1')
  })

  it('空图谱不崩溃，渲染 0 节点', async () => {
    graphService.getGraphData.mockResolvedValue({ nodes: [], edges: [] })
    render(<KnowledgeGraphModule />)

    const graph = await screen.findByTestId('force-graph')
    expect(graph.getAttribute('data-node-count')).toBe('0')
  })

  it('切换到思维导图视图渲染 GraphMindMap、卸载 ForceGraph', async () => {
    render(<KnowledgeGraphModule />)
    await screen.findByTestId('force-graph')

    fireEvent.click(screen.getByRole('button', { name: '思维导图' }))

    await waitFor(() => expect(screen.getByTestId('mindmap')).toBeTruthy())
    expect(screen.queryByTestId('force-graph')).toBeNull()
  })

  it('切换到知识卡片视图触发 cards / MOC 拉取', async () => {
    graphService.fetchKnowledgeCards.mockResolvedValue([{ id: 'c1', title: '卡片一' }])
    graphService.fetchTopicMOCs.mockResolvedValue([{ id: 'm1', topic: '主题一' }])
    render(<KnowledgeGraphModule />)
    await screen.findByTestId('force-graph')

    fireEvent.click(screen.getByRole('button', { name: '知识卡片' }))

    await waitFor(() => expect(graphService.fetchKnowledgeCards).toHaveBeenCalled())
    await waitFor(() => expect(graphService.fetchTopicMOCs).toHaveBeenCalled())
  })
})
