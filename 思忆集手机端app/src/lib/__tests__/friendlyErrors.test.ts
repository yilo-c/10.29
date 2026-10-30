/**
 * 错误提示友好化测试。
 *
 * 为什么值得测：用户报障时最需要的是「我该改什么」。
 * 之前 429 直接抛 `Cloud AI 429: {...}`，用户既不知道是限流、
 * 也不知道该等还是该换 Key。这些映射是产品行为，必须锁住。
 */
import { describe, it, expect, vi } from 'vitest'

vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async () => ({ value: null }),
    set: async () => {},
    remove: async () => {},
  },
}))

describe('cloudAiClient.friendlyHttpError', () => {
  it('401 → 指向 API Key（而不是泛泛的"认证失败"）', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    const msg = friendlyHttpError(401, '{"error":"invalid_api_key"}')
    expect(msg).toContain('API Key')
    // 必须给出可执行动作：去哪里改
    expect(msg).toContain('设置')
  })

  it('402 → 余额不足', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    expect(friendlyHttpError(402, '')).toContain('余额')
  })

  it('404 → 提示检查地址与模型名（两个都会导致 404）', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    const msg = friendlyHttpError(404, 'model not found')
    expect(msg).toContain('模型名')
    expect(msg).toContain('API 地址')
  })

  it('429 → 限流，提示稍等（而不是让用户改配置）', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    const msg = friendlyHttpError(429, 'rate limited')
    expect(msg).toContain('限流')
    expect(msg).not.toContain('API Key')
  })

  it('5xx → 服务商问题，提示稍后重试', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    for (const code of [500, 502, 503, 504]) {
      expect(friendlyHttpError(code, '')).toContain('稍后重试')
    }
  })

  it('未覆盖的状态码保留原始信息，不吞掉细节', async () => {
    const { friendlyHttpError } = await import('../cloudAiClient')
    const msg = friendlyHttpError(418, 'I am a teapot')
    expect(msg).toContain('418')
    expect(msg).toContain('teapot')
  })
})

describe('desktopAiClient 的桌面端错误语义', () => {
  /*
   * 桌面端通道已从 HTTP /agent/ask 改为 WS JSON-RPC chat.send
   * （原接口在真实后端上不存在，见 desktopAiClient 顶部注释）。
   * 所以这里不再 mock fetch，而是控制 rpcClient 的行为。
   */
  it('未连接桌面端时，提示去配对/检查后端，而不是提 API Key', async () => {
    vi.resetModules()
    vi.doMock('../rpcClient', () => ({
      isDesktopConnected: () => false,
      configureWs: () => {},
      configureWsAuthToken: () => {},
      callRpcStream: async function* () { /* 不应被调用 */ },
    }))
    const mod = await import('../desktopAiClient')
    const c = new mod.DesktopAiClient()
    c.configure('http://127.0.0.1:18765', 'tok')
    let msg = ''
    try {
      await c.sendMessageStreaming('hi', () => {})
    } catch (e) {
      msg = (e as Error).message
    }
    expect(msg).toBeTruthy()
    // 桌面端语境：要么让他查后端，要么让他重新配对；绝不能提 API Key
    expect(msg).toMatch(/桌面端|配对/)
    expect(msg).not.toContain('API Key')
  })

  it('桌面端返回空内容时给出明确提示，而不是静默空串', async () => {
    vi.resetModules()
    vi.doMock('../rpcClient', () => ({
      isDesktopConnected: () => true,
      configureWs: () => {},
      configureWsAuthToken: () => {},
      callRpcStream: async function* () { /* 一个 chunk 都不发 */ },
    }))
    const mod = await import('../desktopAiClient')
    const c = new mod.DesktopAiClient()
    c.configure('http://127.0.0.1:18765', 'tok')
    await expect(c.sendMessageStreaming('hi', () => {})).rejects.toThrow(/未返回内容/)
  })
})
