import { test, expect, wsFrames, waitForWsFrames, type WsFrame } from './fixtures'

test.describe('Chat module', () => {
  // 5s 的 expect 预算在本机与后端 pytest 并跑时会被饿死（boot + 模块切换 > 5s），
  // 出现「按钮其实存在、只是渲染慢」的假失败——放宽到 20s，不降断言强度。
  test.describe.configure({ timeout: 90_000 })
  const UI_TIMEOUT = 20_000

  test.beforeEach(async ({ page }) => {
    // Navigate to chat module (小问)
    // U0（2026-09-19）实测：冷启动重负载窗口下首次点击可能落在导航 handler 就绪前，
    // 模块不切换 → chat-workbench 永不可见（本轮 8.2m 套件 chat renders 连续两试失败，
    // 快照停在笔记模块）。二次点击重试一次；仍失败才判红。
    const navChat = page.getByTestId('nav-sidebar').getByRole('button', { name: '小问' })
    const workbench = page.getByTestId('chat-workbench')
    await navChat.click()
    await expect(workbench).toBeVisible({ timeout: UI_TIMEOUT }).catch(async () => {
      await navChat.click()
      await expect(workbench).toBeVisible({ timeout: UI_TIMEOUT })
    })
  })

  test('chat module renders', async ({ page }) => {
    // The chat area should have an input or content area
    const inputArea = page.locator('textarea, input[type="text"], [contenteditable]').first()
    await expect(inputArea).toBeVisible({ timeout: UI_TIMEOUT })
  })

  test('send button exists', async ({ page }) => {
    // 现行 ChatModule 发送按钮以 aria-label「发送消息」呈现（输入为空时 disabled
    // 属预期，仍可见）；旧选择器 i.fa-arrow-up 已随 UI 重构失效（C-12 实测对齐）。
    const sendBtn = page.getByRole('button', { name: '发送消息' }).first()
    await expect(sendBtn).toBeVisible({ timeout: UI_TIMEOUT })
  })
})

/* ── M1 出口断言补齐：发消息 → 流式到达 → 完成态 / 取消（2026-09-12 V1-3）──────
 *
 * 取证通道：fixtures.ts 的 WS 帧采集（协议层）+ dev 暴露的 __useStore（前端记账）。
 * 两层都要成立才判「链路通」，且不把断言挂在被前端静默丢弃的帧上。
 *
 * 离线降级（别把 CI 挂死在外部网络上）：harness 用全新临时数据目录，默认不配任何
 * provider。实测（venv3.11 + SIYI_TEST_OFFLINE=1，2026-09-12 探针）该轮链路是：
 *   chat.send → 管家路径无产出 → simple_stream 后台线程在所有提供方重试后抛
 *   AIServiceError → V1-3 分型：先 1 条 type=notice（JSON 双字段 {event:"chat_error",
 *   error_kind, friendly, technical}），再 1 条 type=error（data 恰为 friendly 纯文本）。
 *   **全程 0 个 type=chunk delta**，终态是 error（不是 done），约 15s 收尾。
 * 因此 provider 可用时断「delta 逐条到达且与前端记账一致」，不可用时断「收到分型
 * 错误（notice+error 双字段自洽）」——两者都算链路通。
 *
 * ⚠ 本文件只覆盖「无 provider → 降级」分支。真正「provider 可用 → 真流式 → 真 AI 回复
 * 落库」的核心效果断言在 **chat-ai-mock.spec.ts**（注入 loopback mock provider，需
 * SIYI_E2E_TEST_OFFLINE=0 放开出网；默认离线套件中 skip）。
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

/** 该请求 id 收到的流式 chunk 帧（since 用于只看「取消之后」的窗口）。 */
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

/** 页面发出的某个 RPC 的全部请求帧，按采集顺序（append-only 下标即时序）。 */
function requests(frames: WsFrame[], method: string): RequestRef[] {
  const out: RequestRef[] = []
  for (const f of frames) {
    if (f.dir !== 'out' || f.frame.method !== method) continue
    const id = f.frame.id
    if (typeof id === 'number') out.push({ id, at: f.at })
  }
  return out
}

/** 某个请求的非流式响应 result（流式 chunk 帧的 result 带 type，这里排除）。 */
function responseResult(frames: WsFrame[], requestId: number): Record<string, unknown> | null {
  for (const f of frames) {
    if (f.dir !== 'in' || f.frame.id !== requestId || !('result' in f.frame)) continue
    const result = f.frame.result as Record<string, unknown> | null
    if (!result || typeof result !== 'object' || typeof result.type === 'string') continue
    return result
  }
  return null
}

/**
 * 在页面里另开一条 WS 直连 harness 后端探测在途 chat 流注册表（同 notes.spec 的
 * 「后端侧复核」手法）。id 用 987xxx 独立段，避免与应用自增 id 撞进同一条采集帧。
 */
async function probeActiveChatStreams(page: import('@playwright/test').Page): Promise<Record<string, unknown>> {
  const url = process.env.SIYI_E2E_WS_URL as string
  return page.evaluate(
    async ({ u, rid }) =>
      (await new Promise((resolve, reject) => {
        const ws = new WebSocket(u)
        const timer = setTimeout(() => {
          ws.close()
          reject(new Error('chat.cancel 探测超时'))
        }, 8000)
        ws.onopen = () => ws.send(JSON.stringify({ jsonrpc: '2.0', id: rid, method: 'chat.cancel', params: { reason: 'e2e-probe' } }))
        ws.onmessage = (ev) => {
          try {
            const m = JSON.parse(String(ev.data)) as Record<string, unknown>
            if (m && m.id === rid) {
              clearTimeout(timer)
              ws.close()
              resolve((m.result ?? {}) as Record<string, unknown>)
            }
          } catch {
            /* 非 JSON 帧忽略 */
          }
        }
        ws.onerror = () => {
          clearTimeout(timer)
          reject(new Error('chat.cancel 探测连接失败'))
        }
      })) as Record<string, unknown>,
    { u: url, rid: 987_001 },
  ) as Promise<Record<string, unknown>>
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

/** 进小问并等前端与 harness 后端真实握手完成——连不上就没有链路可验。 */
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

test.describe('Chat 流式与取消（真后端）', () => {
  // 预算放在 describe 上：config 里的 30s 默认值连 beforeEach（fixtures 要 goto +
  // 等 boot 完成 + 握手）都不够，慢机器上会把用例误判成失败；且离线兜底链要跑满
  // provider 重试（实测约 15s），余量必须给足而不是靠 retry 蒙。
  test.describe.configure({ timeout: 180_000 })

  test.beforeEach(async ({ page }) => {
    test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
    await ensureChatConnected(page)
  })

  test('发消息 → 流式 delta 到达 → 完成态（provider 不可用时降级为分型错误）', async ({ page }) => {
    const prompt = `E2E流式探针-${Date.now().toString(36)} 请用一句话介绍你自己，不要调用工具`
    const req = await sendMessage(page, prompt)

    // 用户气泡即时上屏（前端 optimistic 渲染）
    await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 10_000 })

    // 1) 流式到达：delta chunk 或分型错误，二者其一即证明这一轮真跑到了后端
    await waitForWsFrames(
      page,
      (fs) => streamFrames(fs, req.id).some((x) => (x.type === 'chunk' && x.data !== '') || x.type === 'error'),
      150_000,
      'chat.send 首个流式帧（delta 或分型错误）',
    )

    // 2) 完成态：该请求以终止帧收尾（done|error）
    const settled = await waitForWsFrames(
      page,
      (fs) => streamFrames(fs, req.id).some((x) => x.type === 'done' || x.type === 'error'),
      120_000,
      'chat.send 终止帧',
    )
    const seen = streamFrames(settled, req.id)
    const deltas = seen.filter((x) => x.type === 'chunk' && x.data !== '')
    const terminal = seen.filter((x) => x.type === 'done' || x.type === 'error').at(-1) as StreamFrame
    const errorFrame = seen.find((x) => x.type === 'error')
    const noticeFrame = seen.find((x) => x.type === 'notice')
    const concat = deltas.map((x) => x.data).join('')
    expect(terminal, '终止帧必然存在（上一步已等到）').toBeTruthy()

    if (deltas.length > 0) {
      // 真流式分支：协议层每条 delta 都被前端如实记账（无丢字、无重复）。
      // 前端 for-await 消费者比 socket 慢一拍，故用 poll 而不是直读一次。
      await expect
        .poll(async () => (await readChatState(page)).session?.streamedText ?? '', {
          message: 'store 记账应与收到的 delta 拼接一致',
          timeout: 20_000,
        })
        .toBe(concat)
      test.info().annotations.push({
        type: 'streaming',
        description: `收到 ${deltas.length} 条 delta，共 ${concat.length} 字，终帧 ${terminal.type}`,
      })
    } else {
      // 离线降级分支：V1-3 错误分型双字段必须自洽
      expect(errorFrame, '无 delta 时必须以分型错误收尾').toBeTruthy()
      expect(noticeFrame, '分型错误必须伴随 notice 双字段帧').toBeTruthy()
      const graded = JSON.parse(noticeFrame!.data) as Record<string, unknown>
      expect(graded.event).toBe('chat_error')
      expect(graded.error_kind, '分型必须给出 error_kind').toBeTruthy()
      expect(graded.technical, '技术细节字段不得缺席').toBeTruthy()
      // 双字段契约：type=error 的 data 恰为用户友好文案（不含技术细节）
      expect(errorFrame!.data, 'error 帧应等于 notice.friendly').toBe(graded.friendly)
      test.info().annotations.push({
        type: 'degraded',
        description: `provider 不可用 → 降级断言分型错误 error_kind=${String(graded.error_kind)}`,
      })
    }

    // 3) 前端收口：不再 streaming、会话落一条 AI 消息、composer 回到可发送态
    await expect
      .poll(async () => (await readChatState(page)).session?.isStreaming ?? false, {
        message: '终止帧到达后前端应退出 streaming',
        timeout: 20_000,
      })
      .toBe(false)

    const done = await readChatState(page)
    const assistant = done.messages.filter((m) => m.role === 'assistant')
    expect(assistant.length, '完成态必须落一条 AI 消息（正文或占位）').toBeGreaterThan(0)
    if (deltas.length > 0) {
      await expect
        .poll(async () => (await readChatState(page)).messages.filter((m) => m.role === 'assistant').at(-1)?.content ?? '', {
          message: 'AI 气泡正文应等于累计 delta',
          timeout: 20_000,
        })
        .toBe(concat)
    } else {
      // 降级不静默：D4 错误卡（含「重试上一条」入口）必须在
      await expect(page.getByRole('alert').first()).toBeVisible({ timeout: 10_000 })
    }
    await expect(page.getByRole('button', { name: '发送消息' }).first()).toBeVisible({ timeout: 20_000 })
  })

  test('发送 → 立即点停止 → 后端确认取消且此后不再下发 delta（V1-3 真取消）', async ({ page }) => {
    const prompt = `E2E取消探针-${Date.now().toString(36)} 请写一篇 800 字的秋天散文，逐段输出`
    const req = await sendMessage(page, prompt)

    // 前端进入「生成中」→ 停止按钮可达
    const stopBtn = page.getByRole('button', { name: '停止生成' })
    await expect(stopBtn).toBeVisible({ timeout: 15_000 })

    // 立刻点停止。此刻后端那轮流仍在途（离线兜底链约 15s 才收尾），正是 V1-3
    // 要验的窗口：取消必须真传播到后端，而不是只关前端水龙头。
    // 用「点击前的请求条数」定位新产生的 chat.cancel——sendChatStream 开头会先
    // 发一条 chat.cancel（stopChatStream 幂等清场），不能按时间戳混为一条。
    const cancelCountBefore = requests(await wsFrames(page), 'chat.cancel').length
    await stopBtn.click()

    // 1) 前端把取消意图发到后端（stopChatStream 里的 call('chat.cancel', {})）
    const withCancel = await waitForWsFrames(
      page,
      (fs) => requests(fs, 'chat.cancel').length > cancelCountBefore,
      10_000,
      'chat.cancel 请求帧',
    )
    const cancel = requests(withCancel, 'chat.cancel')[cancelCountBefore]

    // 2) 后端确认：取消命中在途流并把状态落为 cancelled
    const acked = await waitForWsFrames(
      page,
      (fs) => responseResult(fs, cancel.id) !== null,
      15_000,
      'chat.cancel 响应',
    )
    const ack = responseResult(acked, cancel.id) as Record<string, unknown>
    expect(ack.cancelled, 'chat.cancel 应答必须确认取消').toBe(true)
    const streams = (ack.canceled_streams ?? []) as Array<Record<string, unknown>>
    expect(Array.isArray(streams), 'canceled_streams 必须是数组').toBe(true)
    if (streams.length > 0) {
      expect(streams.every((s) => s.cancelled === true), '被取消流必须标记 cancelled').toBe(true)
      expect(streams.every((s) => s.status === 'cancelled'), '被取消流状态必须落 cancelled').toBe(true)
    } else {
      // 该轮在取消到达前已自行收尾（provider 秒回/秒挂时可能）——如实记一笔，
      // 不判失败：本用例的硬判据是「取消之后不再有 delta」，见下。
      test.info().annotations.push({ type: 'note', description: 'chat.cancel 到达时后端已无在途流（该轮先一步收尾）' })
    }

    // 3) 出口判据：取消之后该请求不再收到任何 delta（观察窗一直覆盖到该流收尾）。
    //    等的是「该请求的终止帧」（不限取消时刻之后）——provider 秒回时该轮可能
    //    在点击到达后端前已收尾，此时窗口自然收缩，但硬判据不变。
    const settled = await waitForWsFrames(
      page,
      (fs) => streamFrames(fs, req.id).some((x) => x.type === 'done' || x.type === 'error'),
      150_000,
      '该 chat.send 流的终止帧',
    )
    const all = streamFrames(settled, req.id)
    const terminal = all.filter((x) => x.type === 'done' || x.type === 'error').at(-1) as StreamFrame
    const lateDeltas = all.filter((x) => x.type === 'chunk' && x.data !== '' && x.at >= cancel.at)
    expect(lateDeltas, `取消之后仍收到 ${lateDeltas.length} 条 delta —— 后端未真取消`).toHaveLength(0)
    const beforeCancelDeltas = all.filter((x) => x.type === 'chunk' && x.data !== '' && x.at < cancel.at)
    test.info().annotations.push({
      type: 'cancel',
      description:
        `取消前 delta=${beforeCancelDeltas.length}；取消后 → 终帧 ${terminal.type} 期间 delta=0` +
        `（观察 ${Math.max(0, terminal.at - cancel.at)}ms）`,
    })

    // 3.5) 取消不留僵尸登记：被取消的那轮流必须从后端在途注册表里注销
    //     （register/unregister 配对失效 = 注册表泄漏，后续 cancel 会误伤新流）。
    const cancelledIds = streams.map((s) => String(s.stream_id ?? '')).filter(Boolean)
    if (cancelledIds.length > 0) {
      await expect
        .poll(
          async () => {
            const probe = await probeActiveChatStreams(page)
            const active = ((probe.still_active ?? []) as Array<Record<string, unknown>>).map((s) => String(s.stream_id ?? ''))
            return active.filter((sid) => cancelledIds.includes(sid)).length
          },
          { message: '被取消的 chat 流必须从后端在途注册表注销', timeout: 25_000 },
        )
        .toBe(0)
    }

    // 4) 取消如实反映到会话：AI 位置有内容（有产出留部分正文，无产出落「（已停止）」），
    //    不静默吞掉这一轮。
    await expect
      .poll(async () => {
        const s = await readChatState(page)
        const bubble = s.messages.filter((m) => m.role === 'assistant').at(-1)?.content ?? ''
        const produced = s.session?.streamedText ?? ''
        if (!bubble) return false
        return produced ? bubble === produced : bubble === '（已停止）'
      }, {
        message: '取消后应落一条 AI 消息（部分正文或（已停止）占位）',
        timeout: 90_000,
      })
      .toBe(true)
    // 4.5) M2.2 bug① 复位断言（原为「刻意不断言」）：stopChatStream 旧实现先
    //    delete(_controllers[id]) 再 abort，sendChatStream 的 finally 守卫不成立 →
    //    chatSessionEnd 不被调用 → isStreaming 永真、composer 永久卡在停止态。
    //    修复后：点停止即复位会话态，后端以 done 干净收尾时同样复位。
    await expect
      .poll(async () => (await readChatState(page)).session?.isStreaming ?? false, {
        message: '取消后 isStreaming 必须复位（bug① 卡死回归）',
        timeout: 30_000,
      })
      .toBe(false)
    await expect(page.getByLabel('聊天输入框')).toBeEnabled({ timeout: 10_000 })

    // 4.6) bug③：本轮 chat.send 与 chat.cancel 都要带 session_id 定向
    //      —— 后端按 session_id 登记在途流（_chat_memory.py:309），不带参数的
    //      cancel 等于「取消全部在途流」，停 A 会话会误伤 B 会话。
    const finalFrames = await wsFrames(page)
    const convId = (await readChatState(page)).conversationId
    const sendFrame = finalFrames.find((f) => f.dir === 'out' && f.frame.method === 'chat.send' && f.frame.id === req.id)
    expect(sendFrame, '必须采集到本轮 chat.send 请求帧').toBeTruthy()
    expect(((sendFrame!.frame.params ?? {}) as Record<string, unknown>).session_id, 'chat.send 必须带 session_id').toBe(convId)
    const cancelFrame = finalFrames.find((f) => f.dir === 'out' && f.frame.method === 'chat.cancel' && f.frame.id === cancel.id)
    expect(cancelFrame, '必须采集到本轮 chat.cancel 请求帧').toBeTruthy()
    expect(String(((cancelFrame!.frame.params ?? {}) as Record<string, unknown>).session_id ?? ''), 'chat.cancel 必须带 session_id').toBe(convId ?? '')

    // 4.7) 第0步：前端每一帧都声明 client_version（后端 legacy_client → R4 显式 -32010）。
    //      排除 harness 自建的探测连接（probeActiveChatStreams 用 987xxx id 段、
    //      刻意不带 client_version，那是探针不是应用流量）。
    const outFrames = finalFrames.filter(
      (f) => f.dir === 'out' && typeof f.frame.method === 'string' && !(typeof f.frame.id === 'number' && f.frame.id >= 987_000),
    )
    expect(outFrames.length).toBeGreaterThan(0)
    for (const f of outFrames) {
      expect(typeof f.frame.client_version, `帧 ${String(f.frame.method)} 未声明 client_version`).toBe('string')
      expect(String(f.frame.client_version).length).toBeGreaterThan(0)
    }
  })
})
