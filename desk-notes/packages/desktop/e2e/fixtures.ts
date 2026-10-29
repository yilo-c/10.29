import { test as base, type Page } from '@playwright/test'

/**
 * C-12 真后端 harness：globalSetup 起隔离 headless 后端后把 WS 地址放进
 * process.env.SIYI_E2E_WS_URL。这里在每个 page 的应用脚本执行前注入
 * window.__SIYI_WS_URL__（ws-client 的最高优先级通道），让前端连到 harness
 * 后端而不是（可能不存在的）默认 18765 实例。browser-only 模式（CI 跳过
 * 后端）不设该 env，前端保持原生默认解析，不注入。
 *
 * 2026-10-16（click-sweep v1 实测根因）：隔离实例每次全新启动都会弹
 * SetupWizard 新手引导（localStorage['siyi-onboarding-done'] 缺失，
 * fixed inset-0 z-[99998] 全屏遮罩）——sweep 的点击全被它拦截
 （clickFailed/stuck 误报一片）。此处预设该键 = 等价于"已完成引导的
 用户"，不是伪造 boot 状态（bootPhase 仍自然等待真实 ready）。
 */
async function skipOnboarding(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      window.localStorage.setItem('siyi-onboarding-done', '1')
    } catch {
      /* localStorage 不可用时忽略（boot 流程自有兜底） */
    }
  })
}

async function injectBackendWsUrl(page: Page): Promise<void> {
  const wsUrl = process.env.SIYI_E2E_WS_URL
  if (!wsUrl) return
  const token = process.env.SIYI_E2E_WS_TOKEN || ''
  await page.addInitScript(
    ([url, tk]) => {
      const w = window as unknown as Record<string, unknown>
      w.__SIYI_WS_URL__ = url
      if (tk) w.__SIYI_WS_AUTH_TOKEN__ = tk
    },
    [wsUrl, token],
  )
}

/**
 * Wait for the app to finish booting naturally.
 *
 * 只要 `init.ts` 的 boot 链真实完成到 `bootPhase === 'ready'` 才继续。
 * 不再强推 `bootPhase='ready'`（旧 workaround）——那会掩盖真实 boot 失败
 * （若 initFileStore 等真挂了，E2E 也应如实失败，而不是被伪造状态掩盖成绿）。
 */
async function waitForApp(page: Page, timeout = 15000) {
  await page.goto('/')
  await page.waitForSelector('#root', { timeout })

  const started = Date.now()
  while (Date.now() - started < timeout) {
    const state = await page.evaluate(() => {
      const store = (window as unknown as { __useStore?: { getState: () => { bootPhase: string; bootError?: string } } }).__useStore
      return store?.getState() ?? null
    })
    if (!state) {
      await page.waitForTimeout(200)
      continue
    }
    if (state.bootPhase === 'ready') {
      // 仅居中浮动面板，保证各模块布局稳定；不伪造 boot 状态
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { panel: { x: number; y: number } }; setState: (s: Record<string, unknown>) => void } }).__useStore
        if (!store) return
        const s = store.getState()
        const cx = Math.round((window.innerWidth - Math.min(window.innerWidth * 0.92, 900)) / 2)
        const cy = Math.round(Math.max(window.innerHeight * 0.06, 16))
        store.setState({ panel: { ...s.panel, x: cx, y: cy } })
      })
      await page.waitForTimeout(800) // allow React re-render + framer-motion animation
      return
    }
    if (state.bootPhase === 'failed') {
      throw new Error(`App boot failed: ${state.bootError ?? 'unknown error'}`)
    }
    await page.waitForTimeout(200)
  }
  throw new Error('Timed out waiting for app to boot (bootPhase stuck)')
}

/**
 * WS 帧采集（M1/V1-3 补测取证通道）
 *
 * 为什么需要：流式与取消的判据必须在**协议层**取证，而不是只看 UI 或 store——
 * 前端 `stopChatStream` 只 abort 本地 AbortController，之后到达的 chunk 会被
 * ws-client 静默丢弃（消费者已不再 await），所以「点停止后后端是否还在产 delta」
 * 这件事 store 里查不到，只有 socket 上真实收到的帧能证明。
 *
 * 做法：在应用脚本执行前把 `window.WebSocket` 换成一个**只做镜像记录**的子类，
 * 仅对 URL 含 `/ws` 的后端连接记录 JSON 帧（vite HMR 的 ws 连接被过滤掉）。
 * 不拦截、不改写、不延迟任何一帧——发送失败/解析失败一律原样透传，保证对既有
 * 22 条用例零行为影响（纯观察者）。
 */
export interface WsFrame {
  /** out = 页面发出，in = 后端下发 */
  dir: 'in' | 'out'
  /** page 侧 Date.now()，用于「取消时刻之后是否还有 delta」这类先后判断 */
  at: number
  frame: Record<string, unknown>
}

async function installWsFrameTap(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __e2eWsFrames?: WsFrame[] }
    const sink: WsFrame[] = []
    w.__e2eWsFrames = sink
    const Native = window.WebSocket
    if (!Native) return
    const record = (dir: WsFrame['dir'], raw: unknown): void => {
      if (typeof raw !== 'string') return
      try {
        sink.push({ dir, at: Date.now(), frame: JSON.parse(raw) as Record<string, unknown> })
      } catch {
        /* 非 JSON 文本帧（心跳等）不入采集 */
      }
    }
    class TappedWebSocket extends Native {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        if (!String(url).includes('/ws')) return
        this.addEventListener('message', (event: MessageEvent) => record('in', event.data))
        const nativeSend = this.send.bind(this)
        this.send = (data: string | ArrayBufferLike | ArrayBufferView): void => {
          record('out', data)
          nativeSend(data)
        }
      }
    }
    window.WebSocket = TappedWebSocket as unknown as typeof WebSocket
  })
}

/** 读取当前页面已采集到的 WS 帧快照（拷贝，调用方可安全持有）。 */
export async function wsFrames(page: Page): Promise<WsFrame[]> {
  return page.evaluate(
    () => ((window as unknown as { __e2eWsFrames?: WsFrame[] }).__e2eWsFrames ?? []).slice(),
  ) as Promise<WsFrame[]>
}

/**
 * 轮询采集帧直到 `done(frames)` 为真，超时抛错并附带已观测到的帧摘要。
 * 用轮询而非 waitForFunction 是因为判定逻辑（按请求 id 关联 chunk/notice/error、
 * 比较取消时刻先后）在 Node 侧表达更清晰，也让失败信息直接可读。
 */
export async function waitForWsFrames(
  page: Page,
  done: (frames: WsFrame[]) => boolean,
  timeoutMs = 20_000,
  label = 'ws frames',
): Promise<WsFrame[]> {
  const deadline = Date.now() + timeoutMs
  let frames: WsFrame[] = []
  for (;;) {
    frames = await wsFrames(page)
    if (done(frames)) return frames
    if (Date.now() > deadline) {
      throw new Error(
        `[e2e] 等待 ${label} 超时（${timeoutMs}ms）。已观测帧 ${frames.length} 条，尾部：` +
        frames.slice(-8).map((f) => `${f.dir}:${summarizeFrame(f.frame)}`).join(' | '),
      )
    }
    await page.waitForTimeout(200)
  }
}

/** 帧的一句话摘要（仅用于失败信息，不含正文避免刷屏）。 */
export function summarizeFrame(frame: Record<string, unknown>): string {
  const method = typeof frame.method === 'string' ? ` ${frame.method}` : ''
  const result = frame.result as Record<string, unknown> | undefined
  const chunkType = result && typeof result.type === 'string' ? ` ${result.type}` : ''
  const evt = typeof frame.event_type === 'string' ? ` event:${frame.event_type}` : ''
  return `#${String(frame.id ?? '-')}[${String(frame.dir ?? '')}${method}${chunkType}${evt}]`
}

// Extend base test with our fixture
export const test = base.extend({
  page: async ({ page }, use) => {
    await injectBackendWsUrl(page)
    await skipOnboarding(page)
    await installWsFrameTap(page)
    await waitForApp(page)
    await use(page)
  },
})

export { expect } from '@playwright/test'