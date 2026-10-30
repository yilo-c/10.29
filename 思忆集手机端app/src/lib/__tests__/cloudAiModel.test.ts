/**
 * cloudAiClient 的模型解析行为测试。
 *
 * 背景：此前模型名完全由 API 地址硬编码推断（含 deepseek → deepseek-v4-flash），
 * 用户既看不到也不能改。服务商若不接受该模型名，只会返回 400，
 * 界面显示「AI 服务不可用」，无从判断原因。
 * 现在开放「模型名」配置，本文件锁定其优先级语义。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async () => ({ value: null }),
    set: async () => {},
    remove: async () => {},
  },
}))

let fakeModel = ''

vi.mock('../tokenManager', () => ({
  tokenManager: {
    get cloudModel() { return fakeModel },
    get cloudApiKey() { return 'sk-test' },
    get cloudApiBaseUrl() { return 'https://api.deepseek.com' },
  },
}))

describe('cloudAiClient.resolveModel', () => {
  beforeEach(() => {
    fakeModel = ''
  })

  it('用户未配置时，按地址推断（保持旧行为）', async () => {
    const { resolveModel } = await import('../cloudAiClient')
    expect(resolveModel('https://api.deepseek.com')).toBe('deepseek-chat')
    expect(resolveModel('https://api.openai.com')).toBe('gpt-4o-mini')
    expect(resolveModel('https://api.moonshot.cn')).toBe('moonshot-v1-8k')
  })

  it('用户显式配置时，以配置为准（覆盖推断结果）', async () => {
    fakeModel = 'deepseek-reasoner'
    const { resolveModel } = await import('../cloudAiClient')
    expect(resolveModel('https://api.deepseek.com')).toBe('deepseek-reasoner')
    // 即使地址与模型看起来不搭，也尊重用户选择——用户可能用中转网关
    expect(resolveModel('https://my-gateway.example.com')).toBe('deepseek-reasoner')
  })

  it('配置为纯空白时视为未配置，回落到推断', async () => {
    fakeModel = '   '
    const { resolveModel } = await import('../cloudAiClient')
    expect(resolveModel('https://api.deepseek.com')).toBe('deepseek-chat')
  })

  it('未知地址回落为 deepseek-chat，而不是不可核实的 deepseek-v4-flash', async () => {
    const { resolveModel } = await import('../cloudAiClient')
    expect(resolveModel('https://unknown-provider.example.com')).toBe('deepseek-chat')
  })
})
