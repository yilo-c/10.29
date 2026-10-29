import { describe, expect, it } from 'vitest'
import { normalizeAgentEvents, summarizePlan } from './agentEvents'

const event = (type: 'planner' | 'tool_progress' | 'tool_result' | 'subagent_start' | 'subagent_done' | 'multi_agent_summary', data: string) => ({ type, data })

describe('normalizeAgentEvents', () => {
  it('normalizes planner and tool events into typed execution states', () => {
    const result = normalizeAgentEvents([
      event('planner', '{"step":"读取资料","status":"running"}'),
      event('tool_result', '{"tool":"web.search","status":"success","output":"找到 3 条结果"}'),
    ])

    expect(result).toEqual([
      expect.objectContaining({ kind: 'plan', detail: '读取资料', status: 'running' }),
      expect.objectContaining({ kind: 'tool', title: '工具结果', detail: '找到 3 条结果', status: 'done' }),
    ])
  })

  it('marks structured failures as attention and exposes plan summaries', () => {
    const result = normalizeAgentEvents([
      event('planner', '{"step":"请求确认","status":"waiting"}'),
      event('tool_result', '{"tool":"filesystem","status":"error","error":"需要审批"}'),
    ])

    expect(result[1]).toEqual(expect.objectContaining({ status: 'attention', detail: '需要审批' }))
    expect(summarizePlan(result)).toEqual([{ title: '请求确认', status: 'running' }])
  })

  it('keeps legacy plain-text events readable and handles empty input', () => {
    expect(normalizeAgentEvents([event('subagent_done', '完成分析')])[0]).toEqual(expect.objectContaining({ detail: '完成分析', status: 'done' }))
    expect(normalizeAgentEvents([])).toEqual([])
  })
})
