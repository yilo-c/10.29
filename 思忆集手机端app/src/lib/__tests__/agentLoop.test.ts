/**
 * Agent 多步循环测试
 *
 * 核心验证点：模型能不能连续调多次工具再给答案。
 * 这是 A 项（多步任务）能否成立的根本。
 *
 * 用 mock 的 chatWithTools 脚本化模拟模型的决策序列，
 * 不需要真实 API Key 也能验证循环逻辑。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

/* 脚本化模型决策：每次调用 chatWithTools 返回预设的下一轮 */
let script: Array<{
  content?: string
  toolCalls?: Array<{ id: string; type: 'function'; function: { name: string; arguments: string } }>
}> = []
let callIndex = 0

vi.mock('../cloudAiClient', () => ({
  usingCloudAi: () => true,
  chatWithTools: vi.fn(async () => {
    const step = script[callIndex] ?? { content: '默认结束' }
    callIndex++
    return {
      content: step.content ?? '',
      toolCalls: step.toolCalls ?? [],
      raw: {
        role: 'assistant',
        content: step.content ?? null,
        ...(step.toolCalls?.length ? { tool_calls: step.toolCalls } : {}),
      },
    }
  }),
}))

import { runAgent, looksLikeMultiStepTask } from '../agentLoop'
import { ToolRegistry, ExecutionResult, type AiTool } from '../toolRegistry'

function makeTool(
  id: string,
  exec: (p: Record<string, unknown>) => Promise<{ success: boolean; data?: string; error?: string }>,
  risk: AiTool['riskLevel'] = 'R0_INFO',
): AiTool {
  return {
    id,
    description: `test tool ${id}`,
    parameters: null,
    tags: [id],
    riskLevel: risk,
    execute: exec,
  }
}

describe('agentLoop / 多步执行', () => {
  beforeEach(() => {
    script = []
    callIndex = 0
  })

  it('两步任务：读屏 → 点击 → 给答案', async () => {
    const reg = new ToolRegistry()
    const readScreen = vi.fn(async () => ExecutionResult.success('屏幕上有"设置"按钮'))
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.registerAll([
      makeTool('phone.read_screen', readScreen as never),
      makeTool('phone.tap', tap as never, 'R3_MODIFY'),
    ])

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__read_screen', arguments: '{}' } }] },
      { toolCalls: [{ id: 'c2', type: 'function', function: { name: 'phone__tap', arguments: '{"x":100,"y":200}' } }] },
      { content: '已完成：找到设置按钮并点击' },
    ]

    const r = await runAgent('看看屏幕上有什么，然后点设置', reg, { maxSteps: 5 })
    expect(r.completed).toBe(true)
    expect(r.text).toContain('已完成')
    expect(readScreen).toHaveBeenCalledTimes(1)
    expect(tap).toHaveBeenCalledWith({ x: 100, y: 200 })
    expect(r.usedTools).toContain('phone.read_screen')
    expect(r.usedTools).toContain('phone.tap')
  })

  it('模型直接回答时不多调工具', async () => {
    const reg = new ToolRegistry()
    const t = vi.fn(async () => ExecutionResult.success('x'))
    reg.register(makeTool('phone.read_screen', t as never))

    script = [{ content: '不需要操作手机' }]

    const r = await runAgent('今天几号', reg)
    expect(r.completed).toBe(true)
    expect(t).not.toHaveBeenCalled()
    expect(r.steps).toBe(1)
  })

  it('步数上限会中止死循环', async () => {
    const reg = new ToolRegistry()
    reg.register(makeTool('phone.read_screen', (async () => ExecutionResult.success('loop')) as never))

    // 模型永远要求再读一次
    script = Array.from({ length: 20 }, (_, i) => ({
      toolCalls: [{ id: `c${i}`, type: 'function' as const, function: { name: 'phone__read_screen', arguments: '{}' } }],
    }))

    const r = await runAgent('无限循环', reg, { maxSteps: 3 })
    expect(r.completed).toBe(false)
    expect(r.error).toBe('max steps reached')
    expect(r.steps).toBe(3)
  })

  it('工具执行失败时把错误回灌给模型，让它决定下一步', async () => {
    const reg = new ToolRegistry()
    reg.register(makeTool('phone.tap', (async () => ExecutionResult.failure('无障碍未开启')) as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":1,"y":2}' } }] },
      { content: '无障碍服务没开，请先去设置里开启' },
    ]

    const r = await runAgent('点一下', reg)
    expect(r.completed).toBe(true)
    expect(r.text).toContain('无障碍')
    const resultStep = r.trace.find(t => t.type === 'tool_result')
    expect(resultStep?.result).toContain('失败')
  })

  it('R3 工具在用户拒绝时不执行', async () => {
    const reg = new ToolRegistry()
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.register(makeTool('phone.tap', tap as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":5,"y":5}' } }] },
      { content: '已取消' },
    ]

    const r = await runAgent('点付款按钮', reg, {
      requestApproval: async () => ({ approved: false, reason: '危险操作' }),
    })
    expect(tap).not.toHaveBeenCalled()
    expect(r.trace.some(t => t.type === 'rejected')).toBe(true)
  })

  it('用户批准后 R3 工具才执行', async () => {
    const reg = new ToolRegistry()
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.register(makeTool('phone.tap', tap as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":5,"y":5}' } }] },
      { content: '完成' },
    ]

    const r = await runAgent('点一下', reg, {
      requestApproval: async () => ({ approved: true }),
    })
    expect(tap).toHaveBeenCalled()
    expect(r.completed).toBe(true)
  })

  it('未知工具名不会崩溃，错误回灌后继续', async () => {
    const reg = new ToolRegistry()
    reg.register(makeTool('phone.read_screen', (async () => ExecutionResult.success('ok')) as never))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'nonexistent__tool', arguments: '{}' } }] },
      { content: '改用别的方式' },
    ]

    const r = await runAgent('测试', reg)
    expect(r.completed).toBe(true)
    expect(r.trace.some(t => t.type === 'error')).toBe(true)
  })

  it('非法 JSON 参数被拦截', async () => {
    const reg = new ToolRegistry()
    const t = vi.fn(async () => ExecutionResult.success('ok'))
    reg.register(makeTool('phone.tap', t as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{bad json' } }] },
      { content: '参数错了' },
    ]

    const r = await runAgent('测试', reg)
    expect(t).not.toHaveBeenCalled()
    expect(r.trace.some(t => t.text?.includes('JSON'))).toBe(true)
  })

  it('maxRiskLevel 可屏蔽高风险工具', async () => {
    const reg = new ToolRegistry()
    reg.register(makeTool('phone.read_screen', (async () => ExecutionResult.success('ok')) as never))
    reg.register(makeTool('phone.tap', (async () => ExecutionResult.success('ok')) as never, 'R3_MODIFY'))

    script = [{ content: '只能读不能点' }]
    const r = await runAgent('测试', reg, { maxRiskLevel: 'R1_LIGHT' })
    // 进入循环说明工具有效，只是 tap 被过滤
    expect(r.completed).toBe(true)
  })
})

describe('agentLoop / 多步任务判定', () => {
  it.each([
    '看看屏幕上有什么，然后点第二个按钮',
    '打开微信，接着搜索张三',
    '先返回，再打开设置',
    '帮我找到那个文件并且分享',
    '第一步打开应用，第二步点击登录',
  ])('「%s」应判定为多步任务', (t) => {
    expect(looksLikeMultiStepTask(t)).toBe(true)
  })

  it.each([
    '今天天气怎么样',
    '帮我记一条笔记：明天买牛奶',
    '你是谁',
  ])('「%s」不应判定为多步任务', (t) => {
    expect(looksLikeMultiStepTask(t)).toBe(false)
  })
})

describe('agentLoop / strict 模式', () => {
  it('必填参数覆盖全部字段时才开 strict（tap）', async () => {
    const { chatWithTools } = await import('../cloudAiClient')
    const calls: unknown[] = []
    ;(chatWithTools as unknown as { mockImplementation: (f: unknown) => void }).mockImplementation(
      async (args: { tools: unknown[] }) => {
        calls.push(args.tools)
        callIndex++
        return { content: 'ok', toolCalls: [], raw: { role: 'assistant', content: 'ok' } }
      },
    )
    callIndex = 0
    const reg = new ToolRegistry()
    reg.register({
      id: 'phone.tap',
      description: 'tap',
      parameters: {
        type: 'object',
        properties: { x: { type: 'number' }, y: { type: 'number' } },
        required: ['x', 'y'],
      },
      tags: ['tap'],
      riskLevel: 'R3_MODIFY',
      execute: async () => ExecutionResult.success('ok'),
    })

    await runAgent('点', reg)
    const sent = (calls[0] as Array<{ function: { strict?: boolean } }>)
    expect(sent[0].function.strict).toBe(true)
  })

  it('有可选参数时不开 strict（list_apps）', async () => {
    const { chatWithTools } = await import('../cloudAiClient')
    const calls: unknown[] = []
    ;(chatWithTools as unknown as { mockImplementation: (f: unknown) => void }).mockImplementation(
      async (args: { tools: unknown[] }) => {
        calls.push(args.tools)
        return { content: 'ok', toolCalls: [], raw: { role: 'assistant', content: 'ok' } }
      },
    )
    const reg = new ToolRegistry()
    reg.register({
      id: 'phone.list_apps',
      description: 'list',
      parameters: {
        type: 'object',
        properties: { limit: { type: 'number' } },
        required: [],
      },
      tags: ['apps'],
      riskLevel: 'R0_INFO',
      execute: async () => ExecutionResult.success('ok'),
    })

    await runAgent('列表', reg)
    const sent = (calls[0] as Array<{ function: { strict?: boolean } }>)
    expect(sent[0].function.strict).toBeUndefined()
  })
})
