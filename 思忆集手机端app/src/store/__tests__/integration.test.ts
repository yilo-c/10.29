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
  return parts.join('\n')
}

describe('功能联动：AI 上下文是否带上用户数据', () => {
  beforeEach(() => {
    capturedBodies = []
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

    expect(capturedBodies.length, '必须真的发出了请求（否则断言无意义）').toBeGreaterThan(0)
    expect(aiVisibleText()).toContain('交电费')
  })

  it.fails('② 笔记内容会进入 AI 上下文（已知断点：修好后此用例会转为「意外通过」并报警）', async () => {
    fakeNotes = [
      { id: 'n1', title: '周会记录', content: '下周三前提交嵌入式课程设计报告', type: 'text' },
    ]

    const { useMobileStore } = await import('../mobileStore')
    // 注意：措辞里不能出现「笔记/记下来/提醒」等词。
    // ChatOrchestrator 第 1 步会先跑工具关键词匹配，命中 notes.create 就直接
    // 返回「笔记已创建」，压根不会请求 AI —— 那样就测不到上下文拼装了。
    await useMobileStore.getState().sendMessage('我手上还有什么没做完的事')

    expect(capturedBodies.length).toBeGreaterThan(0)
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
    fakeNotes = []
    fakeTodos = []
    setupEnv()
    vi.resetModules()
  })

  it.fails('③ 语音识别出的文本应进入 AI 链路（已知断点：修好后此用例会转为「意外通过」并报警）', async () => {
    const { createVoiceTool } = await import('../../lib/ai-tools/voiceTool')

    let launched = false
    let recognized: string | null = null

    const tool = createVoiceTool(() => {
      launched = true
      recognized = '明天上午十点去银行办卡，顺便买牛奶'
    })

    await tool.execute({})

    expect(launched, '语音工具应当启动录制').toBe(true)
    expect(recognized, '语音应当产出文本').toContain('去银行办卡')

    // 关键：语音文本有没有进入 AI（用于整理成笔记 / 抽任务）
    expect(
      aiVisibleText(),
      '语音文本应进入 AI 链路做整理或任务抽取。' +
      '当前 createVoiceTool 只在回调里 addFloatingNote，完全绕过 AI。'
    ).toContain('去银行办卡')
  })
})

describe('功能联动：任务抽取路径的上下文', () => {
  beforeEach(() => {
    capturedBodies = []
    fakeNotes = []
    fakeTodos = []
    setupEnv()
    vi.resetModules()
  })

  it.fails('④ 自然语言建任务时也应当带上用户上下文（已知断点：修好后此用例会转为「意外通过」并报警）', async () => {
    fakeTodos = [{ id: 't0', text: '复习傅里叶变换', done: false }]

    const { useMobileStore } = await import('../mobileStore')
    // 这句以「提醒我」开头，会命中 tryAutoCreateTask 的规则分支
    await useMobileStore.getState().sendMessage('提醒我明天交电费')

    expect(capturedBodies.length, '抽取任务时也发了请求').toBeGreaterThan(0)

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
