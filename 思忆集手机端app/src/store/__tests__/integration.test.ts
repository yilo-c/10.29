/**
 * 功能联动（cross-feature integration）测试
 *
 * 为什么需要这一套：
 *   已有 211 个单测是「每个模块自己算得对」，但没人验证过
 *   **模块之间有没有真的接上**。走查发现三处断点：
 *     ① buildAIContext() 只喂待办 + 黄历，不喂笔记
 *       → AI 回答「根据我的笔记」时看不到笔记内容
 *     ② 语音工具在回调里直接 addFloatingNote，绕过 AI 路由
 *       → 说一段话只存一条原始笔记，不会整理、不抽取待办
 *     ③ 任务抽取路径（tryAutoCreateTask）直连 client.sendMessage(extractPrompt)，
 *       **不经过 contextualizeMessage**，因此那条链路完全不带用户上下文
 *
 * 断言方式（重要）：
 *   不测内部实现，而是 **mock globalThis.fetch，截获真实发往
 *   /agent/ask 的请求体**，断言 message 字段里到底带了什么。
 *   buildAIContext 是模块私有函数调不到，但它最终会进 HTTP body，
 *   所以这是黑盒断言，实现怎么重构都不会失真。
 *
 * ⚠️ 为什么不能 mock DesktopAiClient / CloudAiClient：
 *   实测那样做时 ChatOrchestrator 会把 sendDesktop 的返回值当失败，
 *   走 OfflineAiFallback 返回「⏳ AI 服务暂时不可用，已排队」，
 *   请求根本没发出去，测试会以「prompt 为空」的方式假失败。
 *   必须让它真的走 fetch，才测得到上下文。
 *
 * ⚠️ 本文件里标了「已知断点」的用例当前预期失败。
 *    它们就是缺陷清单本身，修复后应转绿；
 *    为让它变绿而放宽断言，等于把缺陷当正常。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

/* ── 截获真实 HTTP 请求体 ── */
let capturedBodies: string[] = []

/* ── 可控的 shared store（数据真源在 @desk-notes/shared 的 useStore）── */
type FakeNote = { id: string; title: string; content: string; type: string }
type FakeTodo = { id: string; text: string; done: boolean }

let fakeNotes: FakeNote[] = []
let fakeTodos: FakeTodo[] = []

vi.mock('@desk-notes/shared', () => ({
  useStore: {
    getState: () => ({
      todos: fakeTodos,
      floatingNotes: fakeNotes,
      conversations: [],
      aiProfiles: [],
      activeProfileId: null,
      addTodo: (text: string) => {
        fakeTodos.push({ id: `t-${fakeTodos.length}`, text, done: false })
      },
      addFloatingNote: (n: FakeNote) => {
        fakeNotes.push(n)
      },
    }),
  },
  getHuangli: () => null,
}))

vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async () => ({ value: null }),
    set: async () => {},
    remove: async () => {},
  },
}))

/*
 * 桌面连接状态：mobileStore 现在用 rpcClient.isDesktopConnected() 判断，
 * 而它要求 WS 真的 connected。单测环境没有 WS，
 * 故这里显式声明「桌面在线」，让路由走桌面分支（本文件测的是上下文拼装，
 * 不测传输层）。同时把 callRpcStream 桩掉，避免真去建连接。
 */
/**
 * 捕获发往桌面的 chat.send 参数。
 *
 * 为什么不能再断言 fetch：桌面通道已从「不存在的 /agent/ask HTTP 接口」
 * 改为 WS JSON-RPC 的 chat.send（见 desktopAiClient 顶部说明）。
 * 这一层要验的是「上下文有没有随消息发出去」，所以捕获 RPC 参数即可，
 * 与传输方式无关。
 */
const desktopCalls: Array<{ method: string; params: Record<string, unknown> }> = []

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

/**
 * 预置「已配对」状态 + mock fetch。
 * 不预置配对，ChatOrchestrator 会直接走离线兜底，请求不会发出。
 */
function setupEnv() {
  const store: Record<string, string> = {
    siyi_is_paired: 'true',
    siyi_base_url: 'http://127.0.0.1:18765',
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

/** 取出所有请求体里 message 字段拼起来，就是「AI 实际看到的上下文」 */
/** 汇总「AI 实际看到的内容」：云端 fetch body + 桌面 chat.send 参数 */
function aiVisibleText(): string {
  const parts: string[] = []
  for (const body of capturedBodies) {
    try {
      const obj = JSON.parse(body) as { message?: string; prompt?: string }
      if (obj.message) parts.push(obj.message)
      if (obj.prompt) parts.push(obj.prompt)
    } catch {
      parts.push(body)
    }
  }
  for (const c of desktopCalls) {
    const m = c.params.message
    if (typeof m === 'string') parts.push(m)
  }
  return parts.join('\n')
}

/** 是否真的发出了 AI 请求（云端或桌面任一路） */
function requestCount(): number {
  return capturedBodies.length + desktopCalls.length
}

describe('功能联动：AI 上下文是否带上用户数据', () => {
  beforeEach(() => {
    capturedBodies = []
    desktopCalls.length = 0
    fakeNotes = []
    fakeTodos = []
    setupEnv()
    vi.resetModules()
  })

  it('① 待办会进入 AI 上下文（已实现的能力，作为对照基线，必须通过）', async () => {
    fakeTodos = [{ id: 't1', text: '交电费', done: false }]

    const { useMobileStore } = await import('../mobileStore')
    // 提问要绕开两个前置分叉，否则走不到 contextualizeMessage：
    //   - tryAutoCreateTask：动词/「我要/帮我」开头会被它截走
    //   - looksLikeMultiStepTask：含「打开/点/看看/然后」会走 agent 循环
    await useMobileStore.getState().sendMessage('这条记录说的是什么')

    expect(requestCount(), '必须真的发出了请求（否则断言无意义）').toBeGreaterThan(0)
    expect(aiVisibleText()).toContain('交电费')
  })

  it('② 笔记内容会进入 AI 上下文', async () => {
    fakeNotes = [
      { id: 'n1', title: '周会记录', content: '下周三前提交嵌入式课程设计报告', type: 'text' },
    ]

    const { useMobileStore } = await import('../mobileStore')
    // 注意：措辞里不能出现「笔记/记下来/提醒」等词。
    // ChatOrchestrator 第 1 步会先跑工具关键词匹配，命中 notes.create 就直接
    // 返回「笔记已创建」，压根不会请求 AI —— 那样就测不到上下文拼装了。
    await useMobileStore.getState().sendMessage('我手上还有什么没做完的事')

    expect(requestCount()).toBeGreaterThan(0)
    expect(
      aiVisibleText(),
      '用户问「根据我的笔记」时，笔记内容必须进入上下文。' +
      '当前 buildAIContext() 只拼了 todos + 黄历，从未读 floatingNotes。'
    ).toContain('嵌入式课程设计报告')
  })
})

describe('功能联动：语音与 AI 链路', () => {
  beforeEach(() => {
    capturedBodies = []
    desktopCalls.length = 0
    fakeNotes = []
    fakeTodos = []
    setupEnv()
    vi.resetModules()
  })

  it('③ 语音识别出的文本应进入 AI 链路', async () => {
    /*
     * 测法（别改成自己 new createVoiceTool）：
     *   语音→AI 的接线在 mobileStore 注册的 createVoiceTool 回调里。
     *   测试自行构造 createVoiceTool(测试回调) 只能测到工具空壳，
     *   永远验不到真实集成 —— 这是之前那条用例假阴性的原因。
     *   这里通过 getToolRegistry() 取到**真正注册**的那个工具来执行。
     */
    const RECOGNIZED = '明天上午十点去银行办卡，顺便买牛奶'

    let startCalled = false
    class FakeSpeechRecognition {
      lang = ''
      continuous = false
      interimResults = false
      onresult: ((e: unknown) => void) | null = null
      start() {
        startCalled = true
        setTimeout(() => {
          this.onresult?.({ results: [[{ transcript: RECOGNIZED }]] })
        }, 0)
      }
    }
    ;(globalThis as unknown as { window: unknown }).window = {
      SpeechRecognition: FakeSpeechRecognition,
      screen: { width: 412, height: 915 },
      devicePixelRatio: 1,
    }

    const { getToolRegistry } = await import('../mobileStore')
    const reg = getToolRegistry()
    const voiceTool = reg.getTool('quick_capture.voice')
    expect(voiceTool, '语音工具应当已注册').toBeTruthy()

    // 执行真实注册的语音工具
    const result = await voiceTool!.execute({})
    expect(result.success, '语音工具执行应当成功').toBe(true)
    expect(startCalled, '应当真的调用了 recognition.start()').toBe(true)

    // onresult 是 setTimeout(0) 触发的，等一轮宏任务
    await new Promise(r => setTimeout(r, 30))

    // 语音文本必须进了 AI 链路
    expect(requestCount(), '语音识别后应当发出 AI 请求').toBeGreaterThan(0)
    expect(aiVisibleText(), '语音文本应当出现在发给 AI 的内容里').toContain('去银行办卡')
  })
})

describe('功能联动：任务抽取路径的上下文', () => {
  beforeEach(() => {
    capturedBodies = []
    desktopCalls.length = 0
    fakeNotes = []
    fakeTodos = []
    setupEnv()
    vi.resetModules()
  })

  it('④ 自然语言建任务时也应当带上用户上下文', async () => {
    fakeTodos = [{ id: 't0', text: '复习傅里叶变换', done: false }]

    const { useMobileStore } = await import('../mobileStore')
    // 这句以「提醒我」开头，会命中 tryAutoCreateTask 的规则分支
    await useMobileStore.getState().sendMessage('提醒我明天交电费')

    expect(requestCount(), '抽取任务时也发了请求').toBeGreaterThan(0)

    // 抽取用的是独立 prompt（extractPrompt），当前不经 contextualizeMessage，
    // 所以已有待办「复习傅里叶变换」不会出现在这次请求里。
    // 这意味着 AI 抽任务时看不到用户已有安排，可能建出重复或冲突的任务。
    expect(
      aiVisibleText(),
      '任务抽取请求应当带上已有待办，避免建出重复/冲突任务。' +
      '当前 tryAutoCreateTask 直接 client.sendMessage(extractPrompt)，绕过了 contextualizeMessage。'
    ).toContain('复习傅里叶变换')
  })
})
