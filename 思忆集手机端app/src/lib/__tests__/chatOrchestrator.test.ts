/**
 * ChatOrchestrator 测试 —— 验证隐私闸门与降级链是真实生效的。
 *
 * 这些用例针对的是本次修复的核心缺陷：
 * 原实现里 `assistantRuntime` 无人调用，导致「隐私分级」在真实链路中完全失效，
 * 用户开启云 AI 后，日记/密码类内容会被直接上传。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ChatOrchestrator } from '../chatOrchestrator'
import { ToolRegistry, ExecutionResult, type AiTool } from '../toolRegistry'
import { OfflineAiFallback } from '../offlineFallback'
import { PrivacyLevel } from '../aiRouter'

// vitest 默认 node 环境无 localStorage —— 与 contentSync.test.ts 同款内存实现
function mockLocalStorage() {
  let store: Record<string, string> = {}
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value },
      removeItem: (key: string) => { delete store[key] },
      clear: () => { store = {} },
      get length() { return Object.keys(store).length },
      key: (i: number) => Object.keys(store)[i] ?? null,
    },
    configurable: true,
  })
}

mockLocalStorage()

function makeTool(id: string, tags: string[], result = 'ok'): AiTool {
  return {
    id,
    description: id,
    parameters: null,
    tags,
    riskLevel: 'R1_LIGHT',
    execute: async () => ExecutionResult.success(result),
  }
}

function makeCtx(overrides: Partial<Parameters<ChatOrchestrator['run']>[1]> = {}) {
  const sendCloud = vi.fn(async (_text: string, onChunk: (c: string) => void) => {
    onChunk('cloud-reply')
    return 'cloud-reply'
  })
  const sendDesktop = vi.fn(async (_text: string, onChunk: (c: string) => void) => {
    onChunk('desktop-reply')
    return 'desktop-reply'
  })
  return {
    ctx: {
      isDesktopConnected: false,
      cloudConfigured: true,
      userPref: { cloudAiEnabled: true, promptBeforeCloud: false, preferDesktopAi: true },
      onChunk: () => {},
      sendCloud,
      sendDesktop,
      ...overrides,
    },
    sendCloud,
    sendDesktop,
  }
}

describe('ChatOrchestrator — 隐私闸门（本次核心修复）', () => {
  let orchestrator: ChatOrchestrator

  beforeEach(() => {
    localStorage.clear()
    orchestrator = new ChatOrchestrator(new ToolRegistry(), new OfflineAiFallback())
  })

  it('把含「密码」的内容判为仅本机', () => {
    const r = orchestrator.inspect('帮我记住我的密码是 abc123')
    expect(r.privacy).toBe(PrivacyLevel.LOCAL_ONLY)
    expect(r.sensitive).toBe(true)
  })

  it('普通内容不会被误判为敏感', () => {
    const r = orchestrator.inspect('今天天气怎么样')
    expect(r.sensitive).toBe(false)
  })

  it('敏感内容即使云端可用也不发出去', async () => {
    const { ctx, sendCloud, sendDesktop } = makeCtx()
    const res = await orchestrator.run({ text: '这是我的日记：今天很难过' }, ctx)

    expect(res.channel).toBe('blocked')
    expect(sendCloud).not.toHaveBeenCalled()
    expect(sendDesktop).not.toHaveBeenCalled()
    expect(res.error).toContain('敏感')
  })

  it('隐私等级可用 override 强制指定', async () => {
    const { ctx, sendCloud } = makeCtx()
    const res = await orchestrator.run(
      { text: '普通问题', privacyOverride: PrivacyLevel.LOCAL_ONLY },
      ctx,
    )
    expect(res.channel).toBe('blocked')
    expect(sendCloud).not.toHaveBeenCalled()
  })
})

describe('ChatOrchestrator — 工具优先', () => {
  it('命中本地工具时不走网络', async () => {
    const reg = new ToolRegistry()
    reg.register(makeTool('notes.create', ['记下来'], '笔记已创建'))
    const { ctx, sendCloud, sendDesktop } = makeCtx()
    const orch = new ChatOrchestrator(reg, new OfflineAiFallback())

    const res = await orch.run({ text: '帮我把这段记下来' }, ctx)

    expect(res.channel).toBe('tool')
    expect(res.text).toBe('笔记已创建')
    expect(sendCloud).not.toHaveBeenCalled()
    expect(sendDesktop).not.toHaveBeenCalled()
  })
})

describe('ChatOrchestrator — 通道降级', () => {
  let orchestrator: ChatOrchestrator
  beforeEach(() => {
    localStorage.clear()
    orchestrator = new ChatOrchestrator(new ToolRegistry(), new OfflineAiFallback())
  })

  it('桌面未连接时走云', async () => {
    const { ctx, sendCloud } = makeCtx()
    const res = await orchestrator.run({ text: '解释一下量子纠缠' }, ctx)
    expect(res.channel).toBe('cloud')
    expect(sendCloud).toHaveBeenCalled()
  })

  it('桌面已连接且优先桌面时走桌面', async () => {
    const { ctx, sendDesktop, sendCloud } = makeCtx({ isDesktopConnected: true })
    const res = await orchestrator.run({ text: '解释一下量子纠缠' }, ctx)
    expect(res.channel).toBe('desktop')
    expect(sendDesktop).toHaveBeenCalled()
    expect(sendCloud).not.toHaveBeenCalled()
  })

  it('桌面失败时不丢请求，会降级或排队', async () => {
    const failingDesktop = vi.fn(async () => { throw new Error('ws closed') })
    const { ctx } = makeCtx({ isDesktopConnected: true, sendDesktop: failingDesktop })
    const res = await orchestrator.run({ text: '解释一下量子纠缠' }, ctx)
    // 要么走云成功，要么排队，但绝不能是「无结果且不排队」
    expect(['cloud', 'queued']).toContain(res.channel)
  })

  it('全部通道不可用时排队而不是静默失败', async () => {
    const { ctx } = makeCtx({
      cloudConfigured: false,
      isDesktopConnected: false,
      sendCloud: vi.fn(async () => { throw new Error('no key') }),
      sendDesktop: vi.fn(async () => { throw new Error('offline') }),
    })
    const res = await orchestrator.run({ text: '解释一下量子纠缠' }, ctx)
    expect(res.channel).toBe('queued')
    expect(res.queued).toBe(true)
  })
})

describe('ChatOrchestrator — 缓存', () => {
  it('AI 成功后会写入缓存，网络断开时能命中', async () => {
    localStorage.clear()
    const orch = new ChatOrchestrator(new ToolRegistry(), new OfflineAiFallback())

    const first = makeCtx()
    await orch.run({ text: '什么是递归' }, first.ctx)

    // 新实例模拟重启，缓存应在 localStorage 里
    const orch2 = new ChatOrchestrator(new ToolRegistry(), new OfflineAiFallback())
    const second = makeCtx({
      cloudConfigured: false,
      sendCloud: vi.fn(async () => { throw new Error('offline') }),
    })
    const res = await orch2.run({ text: '什么是递归' }, second.ctx)

    expect(res.channel).toBe('local_cache')
    expect(res.fromCache).toBe(true)
    expect(res.text).toBe('cloud-reply')
  })
})
