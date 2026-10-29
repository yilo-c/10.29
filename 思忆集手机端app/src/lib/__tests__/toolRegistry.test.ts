import { describe, it, expect, beforeEach } from 'vitest'
import { ToolRegistry, ExecutionResult, type AiTool } from '../toolRegistry'

function makeTool(overrides: Partial<AiTool> = {}): AiTool {
  return {
    id: 'test-tool',
    description: 'A test tool',
    parameters: null,
    tags: ['test', 'example'],
    riskLevel: 'R0_INFO',
    execute: async () => ExecutionResult.success('done'),
    ...overrides,
  }
}

describe('ToolRegistry', () => {
  let registry: ToolRegistry

  beforeEach(() => {
    registry = new ToolRegistry()
  })

  it('starts empty', () => {
    expect(registry.size).toBe(0)
    expect(registry.getAllTools()).toEqual([])
  })

  it('registers a single tool', () => {
    registry.register(makeTool())
    expect(registry.size).toBe(1)
  })

  it('unregisters a tool by id', () => {
    registry.register(makeTool({ id: 'tool-1' }))
    registry.register(makeTool({ id: 'tool-2' }))
    expect(registry.size).toBe(2)
    registry.unregister('tool-1')
    expect(registry.size).toBe(1)
    expect(registry.getTool('tool-2')).toBeDefined()
  })

  it('retrieves a tool by id', () => {
    const tool = makeTool({ id: 'my-tool' })
    registry.register(tool)
    expect(registry.getTool('my-tool')).toBe(tool)
    expect(registry.getTool('nonexistent')).toBeUndefined()
  })

  it('registerAll adds multiple tools', () => {
    registry.registerAll([
      makeTool({ id: 'tool-a' }),
      makeTool({ id: 'tool-b' }),
    ])
    expect(registry.size).toBe(2)
  })

  it('matchTools returns tools sorted by tag match score', () => {
    registry.registerAll([
      makeTool({ id: 'notes-tool', tags: ['note', '笔记'] }),
      makeTool({ id: 'task-tool', tags: ['task', '待办'] }),
      makeTool({ id: 'general', tags: ['utility'] }),
    ])

    const matched = registry.matchTools('帮我创建一个笔记')
    expect(matched).toHaveLength(1)
    expect(matched[0].id).toBe('notes-tool')
  })

  it('matchTools matches multiple tags and sorts by score', () => {
    registry.registerAll([
      makeTool({ id: 'both', tags: ['note', 'task'] }),
      makeTool({ id: 'notes-only', tags: ['note'] }),
    ])

    const matched = registry.matchTools('create a new note and task')
    expect(matched).toHaveLength(2)
    expect(matched[0].id).toBe('both')
    expect(matched[1].id).toBe('notes-only')
  })

  it('returns empty array when no tools match', () => {
    registry.register(makeTool({ tags: ['unrelated'] }))
    expect(registry.matchTools('something else')).toEqual([])
  })

  it('dispatch executes the correct tool', async () => {
    const tool = makeTool({
      id: 'my-tool',
      execute: async () => ExecutionResult.success('executed!'),
    })
    registry.register(tool)
    const result = await registry.dispatch('my-tool', {})
    expect(result.success).toBe(true)
    expect(result.data).toBe('executed!')
  })

  it('dispatch returns error for unknown tool', async () => {
    const result = await registry.dispatch('nonexistent', {})
    expect(result.success).toBe(false)
    expect(result.error).toContain('not found')
  })

  it('toFunctionCallSpec generates correct specs', () => {
    registry.register(makeTool({
      id: 'note-creator',
      description: 'Creates a note',
      parameters: { type: 'object', properties: { title: { type: 'string' } } },
    }))

    const specs = registry.toFunctionCallSpec()
    expect(specs).toHaveLength(1)
    expect(specs[0].name).toBe('note-creator')
    expect(specs[0].description).toBe('Creates a note')
    expect(specs[0].parameters).toEqual({ type: 'object', properties: { title: { type: 'string' } } })
  })

  it('getAllTools returns a copy (not reference)', () => {
    registry.register(makeTool({ id: 't1' }))
    const all = registry.getAllTools()
    expect(all).toHaveLength(1)
    // Modifying returned array should not affect registry
    all.pop()
    expect(registry.size).toBe(1)
  })
})
