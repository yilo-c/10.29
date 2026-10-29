import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test, expect, wsFrames, waitForWsFrames, type WsFrame } from './fixtures'

/**
 * Chat AI mock（补核心链路效果断言，2026-10-16）
 *
 * 既有 chat.spec.ts 的「发消息 → 流式 delta 到达 → 完成态」只在**降级分支**被验证：
 * harness 用全新临时数据目录、默认不配任何 provider，所以 chat.send 走「管家无产出
 * → simple_stream 在全部 provider 重试失败后抛 AIServiceError → V1-3 分型错误」，
 * 全程 0 个真实 delta，**从未验证「真 provider 可用 → 真流式 → 真 AI 回复落库」这条
 * 用户最核心的链路**。本 spec 补上它。
 *
 * 做法：
 *   1. beforeAll 在测试进程里起一个 OpenAI 兼容的 SSE mock（loopback 127.0.0.1:随机端口）。
 *      harness 后端与 Playwright 同处一个 sandbox 命名空间，后端对它发的 POST 走本地回环，
 *      无需真实外网。mock 记录被调用次数与收到的 prompt，作为「mock 真被用上」的硬证据。
 *   2. beforeEach 经 provider.add 把 mock 注册成 primary provider（enabled + api_key +
 *      is_local=false → _build_stage_provider_names 判定为 primary），后端随即
 *      _recreate_ai_client 把它装进 FallbackOpenAIClient.primary；再轮询 provider.list
 *      确认已生效。
 *   3. 发消息 → 断言：mock 被后端实际调用（hits>0）→ 协议层收到 N 条 delta → 前端
 *      store.streamedText 与 delta 拼接严格相等 → 会话落一条 assistant 消息且正文即拼接结果
 *      → isStreaming 复位。全链路效果断言，与降级分支互补。
 *
 * 不直接改动既有 chat.spec.ts（避免扰动已绿用例）：所需的 WS 取证/store 读取 helper 在此
 * 重新实现一份（与 chat.spec.ts 同源，保持口径一致）。
 */

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

interface StreamFrame {
  type: string
  data: string
  at: number
}
interface RequestRef {
  id: number
  at: number
}
interface ChatUiState {
  conversationId: string | null
  session: {
    isStreaming: boolean
    streamedText: string
    error: string | null
    events: Array<{ type: string; data: string }>
  } | null
  messages: Array<{ role: string; content: string }>
}

function streamFrames(frames: WsFrame[], requestId: number, since = 0): StreamFrame[] {
  const out: StreamFrame[] = []
  for (const f of frames) {
    if (f.dir !== 'in' || f.at < since || f.frame.id !== requestId) continue
    const result = f.frame.result as Record<string, unknown> | undefined
    if (!result || typeof result.type !== 'string') continue
    out.push({ type: result.type, data: typeof result.data === 'string' ? result.data : '', at: f.at })
  }
  return out
}
function requests(frames: WsFrame[], method: string): RequestRef[] {
  const out: RequestRef[] = []
  for (const f of frames) {
    if (f.dir !== 'out' || f.frame.method !== method) continue
    const id = f.frame.id
    if (typeof id === 'number') out.push({ id, at: f.at })
  }
  return out
}
async function readChatState(page: import('@playwright/test').Page): Promise<ChatUiState> {
  return page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: {
        getState: () => {
          activeConversationId: string | null
          chatSessions: Record<string, { isStreaming: boolean; streamedText: string; error: string | null; events: Array<{ type: string; data: string }> }>
          conversations: Array<{ id: string; messages: Array<{ role: string; content: string }> }>
        }
      }
    }).__useStore
    if (!store) throw new Error('__useStore 不可用（需 dev 模式）')
    const s = store.getState()
    const id = s.activeConversationId
    const conv = id ? s.conversations.find((c) => c.id === id) : undefined
    return {
      conversationId: id,
      session: id ? (s.chatSessions[id] ?? null) : null,
      messages: conv?.messages ?? [],
    }
  }) as Promise<ChatUiState>
}
async function ensureChatConnected(page: import('@playwright/test').Page): Promise<void> {
  await page.getByTestId('nav-sidebar').getByRole('button', { name: '小问' }).click()
  await expect(page.getByTestId('chat-workbench')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('后端已连接')).toBeVisible({ timeout: 30_000 })
}
async function sendMessage(page: import('@playwright/test').Page, text: string): Promise<RequestRef> {
  await page.getByLabel('聊天输入框').fill(text)
  await page.getByRole('button', { name: '发送消息' }).first().click()
  const frames = await waitForWsFrames(page, (fs) => requests(fs, 'chat.send').length > 0, 15_000, 'chat.send 请求帧')
  return requests(frames, 'chat.send')[0]
}
/**
 * 在页面里另开一条 WS 直连 harness 后端做一次性 RPC。
 *
 * 每条 rpc 调用都开一条**全新** WS 连接（不同 conn_id）：
 *  - client_version='1.2.0' 声明为新客户端，使 R4 破坏性方法（provider.add）走
 *    pending 审批信封而非 legacy 的 -32010；
 *  - 审批时（sandbox.approve）使用另一条连接，避开「同一连接自批」拦截
 *    （_sandbox.py:196：requester==conn_id 且 not trusted 一律拒绝）。
 */
const CLIENT_VERSION = '1.2.0'
async function rpc(page: import('@playwright/test').Page, method: string, params: Record<string, unknown>, rid: number): Promise<unknown> {
  const url = process.env.SIYI_E2E_WS_URL as string
  return page.evaluate(
    async ({ u, method, params, rid, cv }) =>
      (await new Promise<unknown>((resolve, reject) => {
        const ws = new WebSocket(u)
        const timer = setTimeout(() => {
          try { ws.close() } catch { /* ignore */ }
          reject(new Error(`rpc ${method} 超时`))
        }, 20_000)
        ws.onopen = () => ws.send(JSON.stringify({ jsonrpc: '2.0', id: rid, method, params, client_version: cv }))
        ws.onmessage = (ev) => {
          try {
            const m = JSON.parse(String(ev.data)) as Record<string, unknown>
            if (m && m.id === rid) {
              clearTimeout(timer)
              try { ws.close() } catch { /* ignore */ }
              resolve(m.result ?? m.error)
            }
          } catch { /* 非 JSON 帧忽略 */ }
        }
        ws.onerror = () => {
          clearTimeout(timer)
          reject(new Error(`rpc ${method} 连接失败`))
        }
      })) as unknown,
    { u: url, method, params, rid, cv: CLIENT_VERSION },
  ) as Promise<unknown>
}

/** mock 探针可观测状态（测试进程内共享，后端每调用一次累加）。 */
interface MockState {
  count: number
  prompts: string[]
  lastBody: string
}
function startMockServer(state: MockState): http.Server {
  const REPLY = ['你好，', '我是', '思忆集', '的', '模拟', '助手。']
  return http.createServer((req, res) => {
    if (req.method === 'POST' && (req.url ?? '').includes('/chat/completions')) {
      let body = ''
      req.on('data', (c) => (body += c))
      req.on('end', () => {
        state.count += 1
        state.lastBody = body
        let stream = true
        try {
          const p = JSON.parse(body) as { stream?: boolean; messages?: Array<{ role: string; content: string }> }
          stream = Boolean(p.stream)
          state.prompts.push((p.messages ?? []).map((m) => `${m.role}:${m.content}`).join(' | '))
        } catch { /* 解析失败也记一次命中 */ }
        if (!stream) {
          // 非流式调用（Judge / notes summary 等内部链路也会经 primary 走到本 mock）：
          // 返回单条 JSON 补全，避免 "Expecting value" 解析失败告警。
          const reply = {
            id: 'mock-cmpl',
            object: 'chat.completion',
            choices: [{ index: 0, message: { role: 'assistant', content: MOCK_REPLY }, finish_reason: 'stop' }],
            usage: { prompt_tokens: 1, completion_tokens: MOCK_REPLY.length, total_tokens: MOCK_REPLY.length + 1 },
          }
          res.writeHead(200, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify(reply))
          return
        }
        res.writeHead(200, {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        })
        let i = 0
        const emit = (): void => {
          if (i < REPLY.length) {
            const chunk = { choices: [{ index: 0, delta: { content: REPLY[i] }, finish_reason: null }] }
            res.write(`data: ${JSON.stringify(chunk)}\n\n`)
            i += 1
            setTimeout(emit, 30)
            return
          }
          const stop = { choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }
          res.write(`data: ${JSON.stringify(stop)}\n\n`)
          res.write('data: [DONE]\n\n')
          res.end()
        }
        emit()
      })
      return
    }
    res.writeHead(404)
    res.end()
  })
}

const MOCK_REPLY = '你好，我是思忆集的模拟助手。'
const PROVIDER_NAME = 'e2e-mock'

test.describe('Chat AI mock（注入 mock provider → 真流式 + 真回复）', () => {
  test.describe.configure({ timeout: 180_000 })
  const mock: MockState = { count: 0, prompts: [], lastBody: '' }
  let server: http.Server | null = null
  let mockPort = 0

  test.beforeAll(async () => {
    if (!haveBackend()) {
      test.skip(true, '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
      return
    }
    // 本用例需放开 loopback 出网才能命中本地 mock provider；harness 默认 SIYI_TEST_OFFLINE=1
    // 会拦截所有出网（含 127.0.0.1）请求。故仅在显式 SIYI_E2E_TEST_OFFLINE=0 时运行，
    // 默认离线套件中安全 skip（避免误红）。
    if (process.env.SIYI_E2E_TEST_OFFLINE !== '0') {
      test.skip(true, '本用例需 SIYI_E2E_TEST_OFFLINE=0 放开 loopback 出网（默认离线隔离会拦截 mock 调用，CI 默认离线跳过）')
      return
    }
    server = startMockServer(mock)
    await new Promise<void>((resolve) => server!.listen(0, '127.0.0.1', () => resolve()))
    mockPort = (server!.address() as AddressInfo).port
    console.log(`[chat-ai-mock] mock SSE server on 127.0.0.1:${mockPort}`)
  })

  test.afterAll(async () => {
    if (server) await new Promise<void>((r) => server!.close(() => r()))
    server = null
  })

  test.beforeEach(async ({ page }) => {
    await ensureChatConnected(page)
    const base = `http://127.0.0.1:${mockPort}/v1`
    const added = (await rpc(page, 'provider.add', {
      name: PROVIDER_NAME,
      api_key: 'sk-e2e-mock',
      api_base: base,
      model: 'mock-model',
      is_provider: true,
      enabled: true,
      is_local: false,
      is_fallback: false,
      description: 'E2E mock provider',
    }, 980_002)) as Record<string, unknown> | null
    // provider.add 是 R4 破坏性方法：声明了 client_version 的连接收到 pending 信封，
    // 需经 sandbox.approve（用另一条连接调用，避开同一连接自批拦截）才会真正写入。
    if (added && added.pending === true && added.pending_id) {
      const approved = (await rpc(page, 'sandbox.approve', { pending_id: added.pending_id as string }, 980_003)) as Record<string, unknown> | null
      expect(approved?.success, `sandbox.approve 必须成功（返回=${JSON.stringify(approved)}）`).toBe(true)
    } else {
      expect(added?.success, `provider.add 必须成功或返回 pending（返回=${JSON.stringify(added)}）`).toBe(true)
    }
    // 轮询 provider.list 确认重建后的 ai_client 已纳入本 mock（_recreate_ai_client 是 to_thread 异步）
    await expect
      .poll(
        async () => {
          const list = (await rpc(page, 'provider.list', {}, 980_004)) as Array<Record<string, unknown>>
          return Array.isArray(list) ? list.some((p) => p.name === PROVIDER_NAME) : false
        },
        { message: 'provider.list 应出现 e2e-mock', timeout: 20_000 },
      )
      .toBe(true)
  })

  test('发消息 → mock provider 被调用 → 真实 delta 流式 → 真 assistant 回复落库', async ({ page }) => {
    const before = mock.count
    const prompt = `E2E-AI-MOCK-${Date.now().toString(36)} 请用一句话介绍你自己`
    const req = await sendMessage(page, prompt)

    // 用户气泡即时上屏（optimistic 渲染）
    await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 10_000 })

    // 1) 首个流式帧（delta 或终帧）到达
    await waitForWsFrames(
      page,
      (fs) => streamFrames(fs, req.id).some((x) => x.type === 'chunk' || x.type === 'done' || x.type === 'error'),
      150_000,
      'chat.send 首个流式帧',
    )

    // 2) 终止帧收尾
    const settled = await waitForWsFrames(
      page,
      (fs) => streamFrames(fs, req.id).some((x) => x.type === 'done' || x.type === 'error'),
      120_000,
      'chat.send 终止帧',
    )
    const seen = streamFrames(settled, req.id)
    const deltas = seen.filter((x) => x.type === 'chunk' && x.data !== '')
    const terminal = seen.filter((x) => x.type === 'done' || x.type === 'error').at(-1) as StreamFrame
    const concat = deltas.map((x) => x.data).join('')
    expect(terminal, '终止帧必然存在').toBeTruthy()

    // 3) 核心链路证据：后端真的调了我们的 mock（不是降级分支），且 mock 收到了本条 prompt
    expect(mock.count, '后端必须实际调用 mock provider（hits 增加）').toBeGreaterThan(before)
    const hitThis = mock.prompts.slice(before).join(' || ')
    expect(hitThis, 'mock 应收到含本轮 prompt 的请求').toContain(prompt)

    if (deltas.length > 0) {
      // 4) 协议层每条 delta 被前端如实记账（无丢字、无重复）
      await expect
        .poll(async () => (await readChatState(page)).session?.streamedText ?? '', {
          message: 'store 记账应与收到的 delta 拼接一致',
          timeout: 20_000,
        })
        .toBe(concat)
      // 5) 会话落一条 assistant 消息，正文即累计 delta（效果断言）
      await expect
        .poll(async () => (await readChatState(page)).messages.filter((m) => m.role === 'assistant').at(-1)?.content ?? '', {
          message: 'AI 气泡正文应等于累计 delta',
          timeout: 20_000,
        })
        .toBe(concat)
      test.info().annotations.push({
        type: 'ai-mock-streaming',
        description: `mock 命中 ${mock.count - before} 次；收到 ${deltas.length} 条 delta，共 ${concat.length} 字`,
      })
    } else {
      // 走到这里说明 mock 被调了但仍无 delta —— 视为链路异常，明确失败
      throw new Error('mock provider 被调用却未收到任何 delta chunk（链路异常）')
    }

    // 6) 前端收口：不再 streaming、composer 回到可发送态
    await expect
      .poll(async () => (await readChatState(page)).session?.isStreaming ?? false, {
        message: '终止帧到达后前端应退出 streaming',
        timeout: 20_000,
      })
      .toBe(false)
    await expect(page.getByRole('button', { name: '发送消息' }).first()).toBeVisible({ timeout: 20_000 })
  })
})
