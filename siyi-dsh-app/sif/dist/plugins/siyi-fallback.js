// C6 · 连续失败自动降级兜底
//
// 解决的问题：默认模型所在渠道断流或额度耗尽时，一条对话会卡到重试上限
// 然后整轮失败，用户只能手动去设置页换模型。
//
// ── 用哪两个官方钩子，为什么 ──────────────────────────────────────
// 这一版和最初设想的写法不同，原因是查了 api-catalog 的真实契约：
//
//   agent/request-error (waterfall)
//     签名 (payload, next)，payload 含 failure/retryPolicy。
//     返回 { kind: 'retry' } 表示「我接管了，重发这次请求」。
//     但它在请求失败之后触发，此时改配置已经来不及 —— 这里只做计数和判定。
//
//   agent/request (waterfall)
//     签名 (payload, next)，next() 给出「本来会用」的配置，
//     返回替换值即可切换 provider/model。
//     这是改模型的唯一正确位置（文档原文：Replace the frozen call
//     configuration）。
//
// 所以流程是：
//   请求失败且错误码可切换 → 计数，到阈值就标记「下次要降级」并返回 retry
//   → agent 重发 → agent/request 看到标记 → 返回降级后的 provider/model
//
// ── 只在内存里切，不落盘 ─────────────────────────────────────────
// 不改 profile-patch.yml，也不写配置。下次冷启动自动回到用户设的模型，
// 不会悄悄改掉用户的选择。用户想固定就用 quota-policy.json 配。

import fs from 'node:fs'

export const name = 'siyi-fallback'
export const inject = ['tools']

const POL = '/sdcard/思忆集/app/quota-policy.json'
const LOG = '/sdcard/思忆集/app/fallback.log'

// 这些错误码说明「换一个渠道有可能好」。
//
// ⚠ 2026-11-17 修正：上下文超限原本被归为「换了也没用」，其实反了。
// 上下文超限的正解就是换一个窗口更大的模型 —— 同一个 40 万 token 的会话，
// 在 deepseek-flash 上能跑，在窗口小的渠道就 400。把它排除在可切换之外，
// 用户只会看到「本轮运行失败」，而本来是可以自动救回来的。
//
// 注意 CONTEXT_WINDOW_EXCEEDED 是 DSH 自己的错误码（dsh-llm 里定义），
// 它只在上游措辞能匹配「max context length」「too long for this model」
// 这类模式时才会产出。如果上游只回一句笼统的 "Invalid request parameters"，
// 就匹配不上、退化成 INVALID_REQUEST —— 那种情况这里无从判断，
// 只能靠下面的日志把现场记下来。
const SWITCHABLE = new Set([
  'TRANSPORT', 'TIMEOUT', 'QUOTA', 'RATE_LIMIT', 'RATE_LIMITED',
  'PROVIDER_UNREACHABLE', 'OVERLOADED', 'SERVER_ERROR', 'MESSAGES_IDLE',
  // 上下文超限：换窗口更大的模型是对的解法
  'CONTEXT_WINDOW_EXCEEDED', 'CONTEXT_LENGTH_EXCEEDED', 'CONTEXT_OVERFLOW',
  // 输入超长/过大（部分渠道用这个码表示塞不下）
  'REQUEST_TOO_LARGE', 'PROMPT_TOO_LONG',
])

// 2026-11-17：本机网关（local/trae-ide/pi-ai free，端口 8427/8317）连同插件整体移除，
// 备用档改走 dsh-get-it-for-free 的渠道包账号池（WorkBuddy / Buddy 等，
// 模型 id 用渠道包上游的裸 id，provider 固定为 get-it-for-free）。
const BUILTIN = [
  { provider: 'deepseek-account', model: 'deepseek-flash' },
  { provider: 'get-it-for-free', model: 'deepseek-v4.1-flash' },
]

const log = (m) => {
  try { process.stderr.write('[siyi-fallback] ' + m + '\n') } catch {}
  try { fs.appendFileSync(LOG, '[' + new Date().toISOString() + '] ' + m + '\n') } catch {}
}

function loadPolicy() {
  try {
    if (!fs.existsSync(POL)) return null
    return JSON.parse(fs.readFileSync(POL, 'utf8'))
  } catch (e) {
    log('策略文件解析失败，用内置顺序: ' + e.message)
    return null
  }
}

function candidates() {
  const pol = loadPolicy()
  const list = (Array.isArray(pol?.candidates) && pol.candidates.length) ? pol.candidates : BUILTIN
  return list.filter(c => c && c.provider && c.model)
}

export function apply(ctx) {
  const pol = loadPolicy()
  const threshold = (() => {
    const n = Number(pol?.consecutiveFailures)
    return Number.isFinite(n) && n > 0 ? n : 3
  })()

  // 每个 agent 单独维护：失败计数 + 待降级目标
  const fails = new Map()
  const pending = new Map()   // agentKey -> {provider, model}
  const used = new Map()      // agentKey -> 已降级次数

  const agentKey = (payload) => {
    const a = payload && payload.agent
    return String((a && (a.id || a.sessionId)) || 'default')
  }

  // ── 1. 失败计数 ────────────────────────────────────────────────
  // global: true 是必须的。cordis 的 dispatch 会按 agent 作用域过滤：
  //   filter((hook) => hook.global || !filter || filter.call(thisArg, hook.ctx))
  // 用户插件挂在根上下文，不加 global 就收不到任何 agent 事件。
  ctx.on('agent/request-error', async (payload, next) => {
    const failure = payload && payload.failure
    const code = String((failure && failure.code) || '')
    const provider = String((payload && payload.provider) || '')

    if (!SWITCHABLE.has(code)) {
      // 把现场一起记下来：渠道、错误码、第几轮、消息首段。
      // 很多渠道（例如 WorkBuddy）对「参数不合规」和「上下文塞不下」
      // 都回同一句 Invalid request parameters，不记规模就没法判断是哪一类。
      let turns = '?'
      try {
        const sess = payload && payload.agent && payload.agent.session
        if (sess && typeof sess.turn === 'number') turns = String(sess.turn)
        else if (payload && typeof payload.turn === 'number') turns = String(payload.turn)
      } catch (_) {}
      log('不可切换 code=' + code + ' provider=' + provider + ' turn=' + turns +
          ' msg=' + String((failure && failure.message) || '').slice(0, 160))
      return next()
    }

    const k = agentKey(payload)
    const n = (fails.get(k) || 0) + 1
    fails.set(k, n)
    log('失败 ' + n + '/' + threshold + ' code=' + code + ' provider=' + provider +
        ' msg=' + String((failure && failure.message) || '').slice(0, 100))

    if (n < threshold) return next()

    // 到阈值：决定下一次用哪个
    const list = candidates()
    const u = used.get(k) || 0
    if (u >= list.length - 1) {
      log('候选已用尽（' + u + '/' + (list.length - 1) + '），不再降级')
      return next()
    }
    const target = list[u + 1]
    if (!target) return next()

    used.set(k, u + 1)
    fails.delete(k)
    pending.set(k, target)
    log('★ 标记降级 → ' + target.provider + '/' + target.model + '，重发本次请求')
    return { kind: 'retry' }
  }, { global: true })

  // ── 2. 重发时替换配置 ──────────────────────────────────────────
  ctx.on('agent/request', async (payload, next) => {
    const base = await next()
    const k = agentKey(payload)
    const target = pending.get(k)
    if (!target) return base
    pending.delete(k)
    log('应用降级配置: ' + target.provider + '/' + target.model)
    return { ...base, provider: target.provider, model: target.model }
  }, { global: true })

  // ── 3. 请求成功就清计数，避免历史失败累积误触发 ────────────────
  ctx.on('agent/assistant-stream', (payload) => {
    fails.delete(agentKey(payload))
  }, { global: true })

  ctx.effect(() => () => {
    fails.clear(); pending.clear(); used.clear()
  }, 'siyi-fallback: dispose')

  log('已挂载（阈值 ' + threshold + '，候选 ' +
      candidates().map(c => c.provider + '/' + c.model).join(' → ') + '）')
}
