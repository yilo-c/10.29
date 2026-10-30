/**
 * 云端对话「复读」回归测试。
 *
 * 缺陷（2026-10-30 用户实测发现）：
 *   配置云端 AI 后，用户发「你好」，AI 气泡里显示的却是「你好」——
 *   即 AI 复读用户原话，不给任何回答。
 *
 * 根因（mobileStore 的 sendCloud 回调）：
 *     await cloudAi.sendMessageStreaming(...)
 *     return msg        ← 返回的是用户原话，不是 AI 回复
 *   ChatOrchestrator 把 sendCloud 的返回值当作 reply 写进气泡，
 *   于是界面显示用户自己说的话。
 *   讽刺的是 CloudAiClient.sendMessageStreaming 本身返回的是
 *   { reply: fullReply, ... }，正确的值就在手边，只是被丢掉了。
 *
 * 本文件锁定：气泡里必须是模型回复的内容，绝不能是用户输入。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

let capturedBodies: string[] = []

vi.mock('@desk-notes/shared', () => ({
  useStore: {
    getState: () => ({
      todos: [],
      floatingNotes: [],
      conversations: [],
      aiProfiles: [],
      activeProfileId: null,
      addTodo: () => {},
      addFloatingNote: () => {},
    }),
  },
  getHuangli: () => null,
}))

/**
 * 伪造 Capacitor Preferences，让 secureStore 在 hydrate 时能取到 API Key。
 *
 * 为什么必须这样：cloudApiKey 是敏感键，存 secureStore（Preferences → 内存缓存），
 * 不走 localStorage。若 mock 返回 null，tokenManager.cloudApiKey 恒为空，
 * cloudConfigured 永假，请求根本发不出去，就测不到「复读」这个真问题
 * （只会得到「缺少 API Key」的假失败）。
 */
vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async ({ key }: { key: string }) => ({
      value: key === 'siyi_cloud_api_key' ? 'sk-test-cloud-key-for-e2e' : null,
    }),
    set: async () => {},
    remove: async () => {},
  },
}))

/**
 * 预置「云 AI 已配置好」的状态。
 * 注意 promptBeforeCloud 必须为 false，否则路由会直接排队，
 * 根本走不到云端调用，测不到复读。
 */
function setupCloudConfigured() {
  const store: Record<string, string> = {
    siyi_cloud_ai_enabled: 'true',
    siyi_cloud_api_base_url: 'https://api.deepseek.com',
    siyi_cloud_model: 'deepseek-chat',
    siyi_prefer_desktop_ai: 'false',
    siyi_prompt_before_cloud: 'false',
    siyi_is_paired: 'false',
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v },
      removeItem: (k: string) => { delete store[k] },
      clear: () => Object.keys(store).forEach(k => { delete store[k] }),
      get length() { return Object.keys(store).length },
      key: (i: number) => Object.keys(store)[i] ?? null,
    },
    configurable: true,
  })

  // 按 OpenAI 兼容的 SSE 格式返回，内容是模型真实回复
  vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
    capturedBodies.push(String(init?.body ?? ''))
    const sse =
      'data: {"choices":[{"delta":{"content":"我是"}}]}\n\n' +
      'data: {"choices":[{"delta":{"content":"思忆助手"}}]}\n\n' +
      'data: {"choices":[{"delta":{"content":"，有什么可以帮你？"}}]}\n\n' +
      'data: [DONE]\n\n'
    return new Response(sse, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  })
}

describe('云端对话：不得复读用户输入', () => {
  beforeEach(async () => {
    capturedBodies = []
    setupCloudConfigured()
    vi.resetModules()
    // 敏感键（API Key）要先水合进内存缓存，否则读不到
    const { initSecureStore, _resetForTests } = await import('../../lib/secureStore')
    _resetForTests()
    await initSecureStore()
  })

  it('气泡里必须是模型回复，而不是用户原话', async () => {
    const { useMobileStore } = await import('../mobileStore')

    // 用一句不会命中工具关键词、不触发任务抽取的中性问句
    const USER_TEXT = '这句话是什么意思'
    await useMobileStore.getState().sendMessage(USER_TEXT)

    const msgs = useMobileStore.getState().chat.messages
    const assistantMsgs = msgs.filter(m => m.role === 'assistant')
    expect(assistantMsgs.length, '应当有一条助手回复').toBeGreaterThan(0)

    const lastAssistant = assistantMsgs[assistantMsgs.length - 1].content

    // 核心断言：助手内容必须来自模型，不能等于用户输入
    expect(
      lastAssistant,
      `助手气泡复读了用户输入「${USER_TEXT}」。` +
      'sendCloud 必须返回 CloudAiClient 的 reply，而不是 msg。'
    ).not.toBe(USER_TEXT)

    // 正向断言：确实是模型返回的那段内容
    expect(lastAssistant).toContain('思忆助手')
  })

  it('真的发出了云端请求（否则上面的断言没有意义）', async () => {
    const { useMobileStore } = await import('../mobileStore')
    await useMobileStore.getState().sendMessage('这句话是什么意思')

    expect(capturedBodies.length, '应当向云端发出请求').toBeGreaterThan(0)
    // 请求体里应当使用用户配置的模型，而不是写死的默认值
    const body = JSON.parse(capturedBodies[0]) as { model?: string }
    expect(body.model).toBe('deepseek-chat')
  })
})
