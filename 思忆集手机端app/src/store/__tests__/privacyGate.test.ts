/**
 * 隐私闸门测试 / Privacy gate tests
 *
 * 背景：sendMessage 有三条可能把内容发出去的分支
 *   ① tryAutoCreateTask（自然语言建任务，内部直连 AI）
 *   ② Agent 循环（多步任务，带全量上下文）
 *   ③ ChatOrchestrator（常规对话）
 * 原实现只有 ③ 内部做了 LOCAL_ONLY 判定，①② 在它之前执行 ——
 * 于是「帮我记下我的密码是 abc123」这类句子会绕开隐私闸门直接上云。
 *
 * 本文件锁定：LOCAL_ONLY 内容不得进入 ① 与 ②。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

const capturedBodies: string[] = []
const desktopCalls: Array<{ method: string; params: Record<string, unknown> }> = []

vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async () => ({ value: null }),
    set: async () => {},
    remove: async () => {},
  },
}))

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

vi.mock('../../lib/rpcClient', async () => {
  const actual = await vi.importActual<typeof import('../../lib/rpcClient')>('../../lib/rpcClient')
  return {
    ...actual,
    isDesktopConnected: () => true,
    configureWs: () => {},
    configureWsAuthToken: () => {},
    callRpcStream: async function* (method: string, params: Record<string, unknown>) {
      desktopCalls.push({ method, params: { ...params } })
      yield { type: 'delta', content: '好的' }
    },
    callRpc: async () => ({ success: true }),
  }
})

function setupEnv() {
  const store: Record<string, string> = {
    siyi_is_paired: 'true',
    siyi_base_url: 'http://127.0.0.1:18765',
    siyi_cloud_ai_enabled: 'true',
    siyi_cloud_api_base_url: 'https://api.deepseek.com',
    siyi_cloud_model: 'deepseek-chat',
    siyi_prefer_desktop_ai: 'false',
    siyi_prompt_before_cloud: 'false',
    siyi_cloud_api_key: 'sk-test',
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

  vi.stubGlobal('fetch', async (_url: string, init?: RequestInit) => {
    capturedBodies.push(String(init?.body ?? ''))
    return new Response('data: {"type":"final","text":"ok"}\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  })
}

/** AI 实际收到的全部文本（云端 fetch + 桌面 RPC） */
function sentText(): string {
  const parts: string[] = []
  for (const b of capturedBodies) {
    try {
      const o = JSON.parse(b) as { message?: string; prompt?: string }
      if (o.message) parts.push(o.message)
      if (o.prompt) parts.push(o.prompt)
    } catch { parts.push(b) }
  }
  for (const c of desktopCalls) {
    const m = c.params.message
    if (typeof m === 'string') parts.push(m)
  }
  return parts.join('\n')
}

describe('隐私闸门：LOCAL_ONLY 内容不得外发', () => {
  beforeEach(() => {
    capturedBodies.length = 0
    desktopCalls.length = 0
    setupEnv()
    vi.resetModules()
  })

  it('「帮我记下我的密码是 xxx」不得发往云端', async () => {
    /*
     * 命中 tryAutoCreateTask 的规则（「帮我」开头），
     * 且含敏感词「密码」→ 应判 LOCAL_ONLY → 该分支必须被跳过。
     */
    const { useMobileStore } = await import('../mobileStore')
    await useMobileStore.getState().sendMessage('帮我记下我的密码是 abc123')

    const sent = sentText()
    expect(sent, '密码不得出现在任何外发请求里').not.toContain('abc123')
  })

  it('含「身份证」的多步任务不得进入 Agent 分支外发', async () => {
    // 同时含敏感词与多步标记（「然后」）
    const { useMobileStore } = await import('../mobileStore')
    await useMobileStore.getState().sendMessage('我的身份证 110101199001011234 然后帮我看看')

    const sent = sentText()
    expect(sent).not.toContain('110101199001011234')
  })

  it('普通问题照常外发（隐私闸门不能把正常功能也拦死）', async () => {
    const { useMobileStore } = await import('../mobileStore')
    await useMobileStore.getState().sendMessage('今天天气怎么样')

    // 至少有一条通道真的发出了请求
    expect(capturedBodies.length + desktopCalls.length, '普通内容应正常外发').toBeGreaterThan(0)
  })
})
