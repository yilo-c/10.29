import { describe, it, expect, vi, beforeEach } from 'vitest'

const mockCall = vi.fn()
const mockClient = {
  status: 'connected',
  call: mockCall,
  on: vi.fn(() => () => {}),
  onStatusChange: vi.fn(() => () => {}),
}

vi.mock('../../utils/ai', () => ({
  getWsClient: () => mockClient,
}))

import {
  PATTERN_MENU_ACTIONS,
  actionsForDisplay,
  executePatternAction,
  isCapturableText,
} from '../patternActions'
import { BROWSER_EVENT_TYPES } from '../useBackendEvents'

describe('PATTERN_MENU_ACTIONS（与后端 pattern_actions.py 注册表镜像）', () => {
  it('覆盖 15 个内置动作 id', () => {
    expect(PATTERN_MENU_ACTIONS.map(a => a.id)).toEqual([
      'continue', 'explain', 'translate', 'translate_en', 'summarize', 'polish',
      'save_note', 'fetch_url', 'word_count', 'keywords', 'json_format',
      'base64_encode', 'base64_decode', 'date_convert', 'code_format',
    ])
  })

  it('actionsForDisplay 主菜单 6 项 + 更多 9 项，无重复', () => {
    const { primary, more } = actionsForDisplay()
    expect(primary).toHaveLength(6)
    expect(more).toHaveLength(9)
    const ids = new Set([...primary, ...more].map(a => a.id))
    expect(ids.size).toBe(15)
  })
})

describe('BROWSER_EVENT_TYPES（与后端 emit 的五事件名一致）', () => {
  it('恰为 5 个 browser.* 事件', () => {
    expect(BROWSER_EVENT_TYPES).toEqual([
      'browser.captured',
      'browser.summarized',
      'browser.note_created',
      'browser.task_created',
      'browser.reading_saved',
    ])
  })
})

describe('isCapturableText（⑥ 空 payload 防御口径）', () => {
  it('trim 后为空判 false', () => {
    expect(isCapturableText('')).toBe(false)
    expect(isCapturableText('   \n ')).toBe(false)
    expect(isCapturableText(null)).toBe(false)
    expect(isCapturableText(undefined)).toBe(false)
    expect(isCapturableText('词')).toBe(true)
  })
})

describe('executePatternAction', () => {
  beforeEach(() => {
    mockCall.mockReset()
    mockClient.status = 'connected'
  })

  it('未知 action 不发 RPC', async () => {
    const r = await executePatternAction('nope', 'abc')
    expect(r.ok).toBe(false)
    expect(r.error).toContain('未知操作')
    expect(mockCall).not.toHaveBeenCalled()
  })

  it('空文本不发 RPC（空收件防御）', async () => {
    const r = await executePatternAction('translate', '   ')
    expect(r.ok).toBe(false)
    expect(r.error).toContain('为空')
    expect(mockCall).not.toHaveBeenCalled()
  })

  it('未连接时直接失败提示', async () => {
    mockClient.status = 'disconnected'
    const r = await executePatternAction('translate', 'hello')
    expect(r.ok).toBe(false)
    expect(r.error).toContain('后端未连接')
    expect(mockCall).not.toHaveBeenCalled()
  })

  it('成功路径：pattern.execute(action_id, text) 带超时参数', async () => {
    mockCall.mockResolvedValue({ result: '你好' })
    const r = await executePatternAction('translate', ' hello ')
    expect(r.ok).toBe(true)
    expect(r.result).toBe('你好')
    expect(mockCall).toHaveBeenCalledWith(
      'pattern.execute',
      { action_id: 'translate', text: 'hello' },
      15_000,
    )
  })

  it('后端业务错误透传行内提示', async () => {
    mockCall.mockResolvedValue({ result: '', error: '缺少 action_id 或 text' })
    const r = await executePatternAction('explain', 'x')
    expect(r.ok).toBe(false)
    expect(r.error).toBe('缺少 action_id 或 text')
  })

  it('RPC 挂起时前端超时兜底', async () => {
    mockCall.mockReturnValue(new Promise(() => {}))
    const r = await executePatternAction('summarize', 'long text', 30)
    expect(r.ok).toBe(false)
    expect(r.timedOut).toBe(true)
    expect(r.error).toContain('超时')
  })
})
