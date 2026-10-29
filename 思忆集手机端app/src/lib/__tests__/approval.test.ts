/**
 * 审批逻辑测试
 *
 * 验证 C 项的核心保证：
 *   1. R3 工具在用户拒绝时不执行 —— 这是防误付款/误删的最后一道闸
 *   2. describeToolCall 能把参数翻译成人能看懂的话
 *   3. 拒绝原因会回灌给 AI
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

let script: Array<{
  content?: string
  toolCalls?: Array<{ id: string; type: 'function'; function: { name: string; arguments: string } }>
}> = []
let callIndex = 0
/** 记录回灌给模型的消息，用于验证拒绝原因确实传回去了 */
const sentMessages: unknown[] = []

vi.mock('../cloudAiClient', () => ({
  usingCloudAi: () => true,
  chatWithTools: vi.fn(async (args: { messages: unknown[] }) => {
    sentMessages.push(JSON.parse(JSON.stringify(args.messages)))
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

import { runAgent } from '../agentLoop'
import { ToolRegistry, ExecutionResult, type AiTool } from '../toolRegistry'
import { describeToolCall } from '../../components/ui/ApprovalDialog'

function makeTool(
  id: string,
  exec: (p: Record<string, unknown>) => Promise<{ success: boolean; data?: string; error?: string }>,
  risk: AiTool['riskLevel'],
): AiTool {
  return { id, description: id, parameters: null, tags: [id], riskLevel: risk, execute: exec }
}

describe('审批 / R3 工具拦截', () => {
  beforeEach(() => {
    script = []
    callIndex = 0
    sentMessages.length = 0
  })

  it('用户拒绝时 tap 绝对不执行', async () => {
    const reg = new ToolRegistry()
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.register(makeTool('phone.tap', tap as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":540,"y":1200}' } }] },
      { content: '好的，取消操作' },
    ]

    const r = await runAgent('点支付按钮', reg, {
      requestApproval: async () => ({ approved: false, reason: '用户拒绝' }),
    })

    expect(tap).not.toHaveBeenCalled()
    expect(r.trace.some(t => t.type === 'rejected')).toBe(true)
    // 拒绝原因必须回灌，AI 才知道被拒
    const lastMsgs = sentMessages[sentMessages.length - 1] as Array<{ role: string; content: string }>
    const toolMsg = lastMsgs.find(m => m.role === 'tool')
    expect(toolMsg?.content).toContain('拒绝')
  })

  it('用户允许时才执行', async () => {
    const reg = new ToolRegistry()
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.register(makeTool('phone.tap', tap as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":10,"y":20}' } }] },
      { content: '已点击' },
    ]

    await runAgent('点一下', reg, { requestApproval: async () => ({ approved: true }) })
    expect(tap).toHaveBeenCalledWith({ x: 10, y: 20 })
  })

  it('R0 只读工具不需要审批', async () => {
    const reg = new ToolRegistry()
    const read = vi.fn(async () => ExecutionResult.success('屏幕内容'))
    reg.register(makeTool('phone.read_screen', read as never, 'R0_INFO'))
    const approval = vi.fn(async () => ({ approved: false }))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__read_screen', arguments: '{}' } }] },
      { content: '读完了' },
    ]

    await runAgent('看看屏幕', reg, { requestApproval: approval })
    expect(approval).not.toHaveBeenCalled()
    expect(read).toHaveBeenCalled()
  })

  it('审批回调抛异常时视为拒绝（fail-safe）', async () => {
    const reg = new ToolRegistry()
    const tap = vi.fn(async () => ExecutionResult.success('已点击'))
    reg.register(makeTool('phone.tap', tap as never, 'R3_MODIFY'))

    script = [
      { toolCalls: [{ id: 'c1', type: 'function', function: { name: 'phone__tap', arguments: '{"x":1,"y":2}' } }] },
      { content: '无法确认，已停止' },
    ]

    await runAgent('点一下', reg, {
      requestApproval: async () => { throw new Error('弹窗崩溃') },
    })
    expect(tap).not.toHaveBeenCalled()
  })
})

describe('审批 / describeToolCall', () => {
  it('tap 翻译成人话', () => {
    const d = describeToolCall('phone.tap', { x: 540, y: 1200 })
    expect(d.label).toBe('点击屏幕')
    expect(d.summary).toContain('540')
    expect(d.details).toHaveLength(2)
  })

  it('swipe 展示起点终点时长', () => {
    const d = describeToolCall('phone.swipe', { x1: 100, y1: 200, x2: 100, y2: 800, duration: 400 })
    expect(d.label).toBe('滑动屏幕')
    expect(d.details.map(x => x.label)).toContain('起点')
    expect(d.details.map(x => x.label)).toContain('时长')
  })

  it('未知工具也能兜底展示参数', () => {
    const d = describeToolCall('some.unknown', { foo: 'bar' })
    expect(d.summary).toContain('some.unknown')
    expect(d.details.some(x => x.label === 'foo')).toBe(true)
  })
})
