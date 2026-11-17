// F 组 · 额度池观察与策略（siyi-quota）
//
// 目标：把 C6 的「出错才降级」补成「按历史健康度主动排序候选渠道」。
// 本插件只观察 + 写策略：不拦截请求、不重试、不改进程里的默认模型选择。
//
// ── 观察点（为什么是这两个）────────────────────────────────────────
//   失败：agent/request-error（waterfall）
//     payload.provider 是失败请求选中的渠道，payload.failure.code 是归一化错误码。
//   成功：llm/stream（waterfall）外面包一层，迭代正常结束才算一次成功
//     options.provider / options.model 是 agent/request 瀑布最终选定的真实路由，
//     C6 降级之后实际用的渠道也能正确归属；next() 拿到的是真实流。
//     为什么不用 agent/assistant-stream：它的 frame 只有 start/chunk/end 和
//     turn/step，没有 provider/model。一旦发生重试或降级就无法归属渠道，
//     成功率会被记到错误的 provider 上；llm/stream 的 options 才带真实路由。
//
// ── 作用域坑 ──────────────────────────────────────────────────────
// cordis 的 dispatch 会按 agent 作用域过滤监听器：
//   filter(hook => hook.global || !filter || filter.call(thisArg, hook.ctx))
// 用户插件挂在根上下文，{global:true} 必须写，否则一个 agent 事件都收不到。
//
// ── 顺序坑（为什么还要 prepend）──────────────────────────────────
// 用户插件按文件名排序加载：siyi-fallback.js 排在 siyi-quota.js 前面，先注册。
// 它的 agent/request-error 在连续失败到阈值时直接返回 {kind:'retry'} 且不调
// next()。我们若排在它下游，恰好第 3 次失败会被它截走、收不到——漏记的正是
// 最关键的那次失败。所以两个监听器都用 {prepend:true} 挂到链最外层：先记数，
// 再照常 return next() 交给下游，fallback 的 retry 语义完全不变。
//
// ── 滑动窗口口径 ──────────────────────────────────────────────────
// 每个 provider 保留最近 20 次尝试（windowMaxSamples），统计前再剔除超过
// 30 分钟的样本（windowMaxAgeMs），两者同时生效：先按时间窗过滤，再只取最近
// 20 条。样本数 >= 3（minSamples）才下健康结论：
//   成功率 < 60%（healthyRate）        → unhealthy
//   连续失败 >= 3（consecutiveFailureLimit）→ unhealthy（刚坏的渠道不必攒样本）
//   其余                                → healthy
//   样本不足且没有连续失败              → unknown（证据不足，保持原相对顺序）
// 客户端侧错误（用户取消、上下文超限、请求非法等）不算渠道不健康，见
// IGNORED_FAILURE_CODES，直接跳过不采样（否则用户按一次停止就会把渠道判死）。
//
// ── 策略写回口径 ──────────────────────────────────────────────────
// 只重写 app/quota-policy.json 的 candidates 数组：healthy > unknown >
// unhealthy；同档按近期成功率降序，再按原顺序（稳定，不来回抖）。
// 不健康的不剔除（剔除会让 C6 的降级链变短甚至无路可走），只排到最后。
// 文件里其他字段（consecutiveFailures、人工备注等）原样保留；插件自己的元数据
// 写进 siyiQuota 命名空间。写盘一律「同目录临时文件 + fsync + rename」，
// 避免写一半断电留下坏 JSON；rename 不可用时退化为直接写并清理临时文件。
// 文件解析失败时只记日志、绝不覆盖，避免毁掉用户正在手改的内容。
//
// ── 生效时机 ──────────────────────────────────────────────────────
// 插件由引擎启动时扫描 /sdcard/思忆集/plugins 加载，改完要等下次 App 重启
// 才生效；引擎运行中不会热加载（重启会打断用户会话，所以这里不重启）。

import fs from 'node:fs'
import path from 'node:path'

export const name = 'siyi-quota'
// 与 siyi-fallback / siyi-vault 一致：必须声明 inject，否则 cordis 的 ctx
// 代理读到未注入服务会抛 cannot get property。
export const inject = ['tools']

const APP_DIR = '/sdcard/思忆集/app'
const STATS_PATH = path.join(APP_DIR, 'quota-stats.json')
const POLICY_PATH = path.join(APP_DIR, 'quota-policy.json')
const ROUTE_PREFIX = '/siyi-quota'

// 没读到 quota-policy.json 时的候选（必须与 siyi-fallback.js 的 BUILTIN 一致，
// 否则插件新建的策略文件会改变 fallback 的降级链）。
// 2026-11-17：本机网关（local 8427 / trae-ide 8317 / pi-ai free）已整体移除，
// 备用档改为 dsh-get-it-for-free 渠道包账号池里的 deepseek-v4.1-flash。
export const BUILTIN_CANDIDATES = Object.freeze([
  Object.freeze({ provider: 'deepseek-account', model: 'deepseek-flash' }),
  Object.freeze({ provider: 'get-it-for-free', model: 'deepseek-v4.1-flash' }),
])

export const DEFAULT_CONFIG = Object.freeze({
  windowMaxSamples: 20,
  windowMaxAgeMs: 30 * 60 * 1000,
  minSamples: 3,
  healthyRate: 0.6,
  consecutiveFailureLimit: 3,
})

// 这些 code 反映的是客户端/请求本身的问题，不是渠道健康度，不采样。
export const IGNORED_FAILURE_CODES = Object.freeze([
  'ABORTED',
  'ABORT',
  'CANCELLED',
  'CANCELED',
  'ABORTED_BEFORE_DISPATCH',
  'INVALID_REQUEST',
  'CONTEXT_WINDOW_EXCEEDED',
  'IMAGE_OFFLOAD_REQUIRED',
])
const IGNORED_FAILURE = new Set(IGNORED_FAILURE_CODES)

const HEALTH_RANK = Object.freeze({ healthy: 0, unknown: 1, unhealthy: 2 })

function errText(e) {
  return String((e && e.message) || e)
}

function iso(ms) {
  return ms ? new Date(ms).toISOString() : null
}

function positiveInt(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback
}

function clamp01(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 && n <= 1 ? n : fallback
}

function defaultLog(msg) {
  try { process.stderr.write('[siyi-quota] ' + msg + '\n') } catch {}
}

/**
 * 按健康度给候选排序（纯函数，测试可直接用）。
 * 判定顺序：healthy(0) < unknown(1) < unhealthy(2)；同档成功率高的在前；
 * 再同档保持原顺序（稳定排序，避免无意义的顺序抖动）。
 * @param candidates 形如 [{ provider, model, ...其他字段 }]
 * @param lookup 形如 { provider: { health, successRate } } 或 (provider) => 同上
 */
export function rankCandidates(candidates, lookup) {
  const get = typeof lookup === 'function'
    ? lookup
    : (p) => (lookup && lookup[p]) || { health: 'unknown', successRate: null }
  return candidates
    .map((c, i) => ({ c, i }))
    .sort((a, b) => {
      const ha = get(a.c.provider) || { health: 'unknown', successRate: null }
      const hb = get(b.c.provider) || { health: 'unknown', successRate: null }
      const da = HEALTH_RANK[ha.health] === undefined ? 1 : HEALTH_RANK[ha.health]
      const db = HEALTH_RANK[hb.health] === undefined ? 1 : HEALTH_RANK[hb.health]
      if (da !== db) return da - db
      const ra = ha.successRate === null || ha.successRate === undefined ? -1 : ha.successRate
      const rb = hb.successRate === null || hb.successRate === undefined ? -1 : hb.successRate
      if (rb !== ra) return rb - ra
      return a.i - b.i
    })
    .map((x) => x.c)
}

/** 同目录临时文件 + fsync + rename 的原子 JSON 写；失败退化为直接写。 */
function writeJsonAtomic(filePath, obj) {
  const text = JSON.stringify(obj, null, 2) + '\n'
  const dir = path.dirname(filePath)
  const base = path.basename(filePath)
  const tmp = path.join(dir, '.' + base + '.' + process.pid + '.tmp')
  let wroteTmp = false
  try {
    const fd = fs.openSync(tmp, 'w')
    try {
      fs.writeFileSync(fd, text, 'utf8')
      try { fs.fsyncSync(fd) } catch { /* FUSE 上 fsync 可能不支持，忽略 */ }
    } finally {
      try { fs.closeSync(fd) } catch {}
    }
    wroteTmp = true
    fs.renameSync(tmp, filePath)
    return { ok: true, atomic: true }
  } catch (e) {
    if (wroteTmp) { try { fs.unlinkSync(tmp) } catch {} }
    try {
      fs.writeFileSync(filePath, text, 'utf8')
      return { ok: true, atomic: false, error: '临时文件+rename 失败，已退化为直接写: ' + errText(e) }
    } catch (e2) {
      return { ok: false, atomic: false, error: errText(e2) }
    }
  }
}

/**
 * 建一个统计核心。所有方法都不抛异常（内部兜住），测试可注入 now / 路径。
 */
export function createQuotaCore(options = {}) {
  const now = typeof options.now === 'function' ? options.now : () => Date.now()
  const log = typeof options.log === 'function' ? options.log : defaultLog
  const rawCfg = options.config || {}
  const cfg = {
    windowMaxSamples: positiveInt(rawCfg.windowMaxSamples, DEFAULT_CONFIG.windowMaxSamples),
    windowMaxAgeMs: positiveInt(rawCfg.windowMaxAgeMs, DEFAULT_CONFIG.windowMaxAgeMs),
    minSamples: positiveInt(rawCfg.minSamples, DEFAULT_CONFIG.minSamples),
    healthyRate: clamp01(rawCfg.healthyRate, DEFAULT_CONFIG.healthyRate),
    consecutiveFailureLimit: positiveInt(rawCfg.consecutiveFailureLimit, DEFAULT_CONFIG.consecutiveFailureLimit),
  }
  const statsPath = options.statsPath || STATS_PATH
  const policyPath = options.policyPath || POLICY_PATH
  const builtin = Array.isArray(options.builtin) && options.builtin.length
    ? options.builtin
    : BUILTIN_CANDIDATES

  const providers = new Map()
  const policyState = { lastWriteAt: 0, lastWriteOk: null, lastError: null }
  const loggedOnce = new Set()
  const logOnce = (key, msg) => {
    if (loggedOnce.has(key)) return
    loggedOnce.add(key)
    log(msg)
  }

  function bucket(provider) {
    let st = providers.get(provider)
    if (!st) {
      st = {
        provider,
        samples: [],          // 按时间正序，最多 windowMaxSamples 条
        consecutiveFailures: 0,
        lastSuccessAt: 0,
        lastFailureAt: 0,
        lastErrorCode: null,
        successTotal: 0,
        failureTotal: 0,
        codeTotal: {},
        models: new Set(),
      }
      providers.set(provider, st)
    }
    return st
  }

  /** 按时间窗与条数上限裁剪内存里的样本。 */
  function trim(st, t) {
    const cutoff = t - cfg.windowMaxAgeMs
    while (st.samples.length && st.samples[0].t < cutoff) st.samples.shift()
    while (st.samples.length > cfg.windowMaxSamples) st.samples.shift()
  }

  function recordSuccess(provider, model, latencyMs) {
    try {
      const p = String(provider || '').trim()
      if (!p) return
      const t = now()
      const st = bucket(p)
      if (model) st.models.add(String(model))
      st.samples.push({ t, ok: true, latencyMs: Number.isFinite(latencyMs) ? latencyMs : null })
      trim(st, t)
      st.consecutiveFailures = 0
      st.successTotal += 1
      st.lastSuccessAt = t
    } catch (e) {
      log('记录成功异常（已忽略）: ' + errText(e))
    }
    flush()
  }

  function recordFailure(provider, model, code) {
    try {
      const p = String(provider || '').trim()
      if (!p) return
      const c = String(code || 'UNKNOWN')
      const t = now()
      const st = bucket(p)
      if (model) st.models.add(String(model))
      st.samples.push({ t, ok: false, code: c })
      trim(st, t)
      st.consecutiveFailures += 1
      st.failureTotal += 1
      st.lastFailureAt = t
      st.lastErrorCode = c
      st.codeTotal[c] = (st.codeTotal[c] || 0) + 1
    } catch (e) {
      log('记录失败异常（已忽略）: ' + errText(e))
    }
    flush()
  }

  /** 单 provider 的健康快照（只读、不抛）。 */
  function healthOf(provider) {
    const p = String(provider || '').trim()
    const st = providers.get(p)
    if (!st) {
      return {
        provider: p,
        health: 'unknown',
        samples: 0,
        successes: 0,
        failures: 0,
        successRate: null,
        consecutiveFailures: 0,
        codes: {},
        codesTotal: {},
        models: [],
        lastSuccessAt: null,
        lastFailureAt: null,
        lastErrorCode: null,
        lastUpdatedAt: null,
        attemptsTotal: 0,
        successTotal: 0,
        failureTotal: 0,
        avgLatencyMs: null,
        lastLatencyMs: null,
      }
    }
    const t = now()
    const cutoff = t - cfg.windowMaxAgeMs
    const samples = st.samples.filter((s) => s.t >= cutoff)
    const n = samples.length
    let successes = 0
    const codes = {}
    let latencySum = 0
    let latencyCount = 0
    let lastLatencyMs = null
    for (const s of samples) {
      if (s.ok) {
        successes += 1
        if (Number.isFinite(s.latencyMs)) {
          latencySum += s.latencyMs
          latencyCount += 1
          lastLatencyMs = s.latencyMs
        }
      } else {
        codes[s.code || 'UNKNOWN'] = (codes[s.code || 'UNKNOWN'] || 0) + 1
      }
    }
    const failures = n - successes
    const successRate = n ? successes / n : null
    // 连续失败只在时间窗内有效：静默超过 30 分钟就不拿旧 streak 判死渠道。
    const streakActive = st.lastFailureAt > 0 && (t - st.lastFailureAt) <= cfg.windowMaxAgeMs
    const consecutiveFailures = streakActive ? st.consecutiveFailures : 0

    let health = 'unknown'
    if (consecutiveFailures >= cfg.consecutiveFailureLimit) health = 'unhealthy'
    else if (n >= cfg.minSamples) health = successRate < cfg.healthyRate ? 'unhealthy' : 'healthy'

    return {
      provider: p,
      health,
      samples: n,
      successes,
      failures,
      successRate,
      consecutiveFailures,
      codes,
      codesTotal: { ...st.codeTotal },
      models: Array.from(st.models),
      lastSuccessAt: iso(st.lastSuccessAt),
      lastFailureAt: iso(st.lastFailureAt),
      lastErrorCode: st.lastErrorCode,
      lastUpdatedAt: iso(Math.max(st.lastSuccessAt, st.lastFailureAt)),
      attemptsTotal: st.successTotal + st.failureTotal,
      successTotal: st.successTotal,
      failureTotal: st.failureTotal,
      avgLatencyMs: latencyCount ? Math.round(latencySum / latencyCount) : null,
      lastLatencyMs,
    }
  }

  function readPolicy() {
    try {
      if (!fs.existsSync(policyPath)) return { exists: false, ok: false, data: null, error: null }
      const text = fs.readFileSync(policyPath, 'utf8')
      return { exists: true, ok: true, data: JSON.parse(text), error: null }
    } catch (e) {
      return { exists: true, ok: false, data: null, error: errText(e) }
    }
  }

  /**
   * 按健康度重写 candidates，保留其他字段。
   * 返回 { ok, skipped, candidates, path, error }；任何异常都不外抛。
   */
  function rewritePolicy() {
    try {
      const read = readPolicy()
      if (read.exists && !read.ok) {
        policyState.lastWriteAt = now()
        policyState.lastWriteOk = false
        policyState.lastError = 'JSON 解析失败: ' + read.error
        logOnce('policy-invalid:' + read.error, 'quota-policy.json 不是合法 JSON，跳过重排（不覆盖手写内容）: ' + read.error)
        return { ok: false, skipped: true, reason: 'policy-invalid', candidates: null, error: policyState.lastError }
      }
      if (read.exists && (read.data === null || typeof read.data !== 'object' || Array.isArray(read.data))) {
        policyState.lastWriteAt = now()
        policyState.lastWriteOk = false
        policyState.lastError = '顶层不是对象'
        logOnce('policy-shape', 'quota-policy.json 顶层不是对象，跳过重排（不覆盖手写内容）')
        return { ok: false, skipped: true, reason: 'policy-shape', candidates: null, error: policyState.lastError }
      }
      const original = read.exists ? read.data : {}
      const source = (Array.isArray(original.candidates) && original.candidates.length)
        ? original.candidates
        : builtin
      for (const c of source) {
        if (!c || typeof c !== 'object' || typeof c.provider !== 'string' || !c.provider
          || typeof c.model !== 'string' || !c.model) {
          policyState.lastWriteAt = now()
          policyState.lastWriteOk = false
          policyState.lastError = 'candidates 含非法项'
          logOnce('policy-candidate-shape', 'quota-policy.json 的 candidates 里有不符合 {provider,model} 的项，跳过重排以免破坏手写内容')
          return { ok: false, skipped: true, reason: 'candidate-shape', candidates: null, error: policyState.lastError }
        }
      }

      const lookup = {}
      for (const c of source) lookup[c.provider] = healthOf(c.provider)
      const ranked = rankCandidates(source, lookup)

      const next = { ...original }
      if (!read.exists) {
        // 首次生成时给出与 siyi-fallback 默认值一致的阈值，方便用户知道去哪改。
        if (next.version === undefined) next.version = 1
        if (next.consecutiveFailures === undefined) next.consecutiveFailures = 3
      }
      next.candidates = ranked
      const prevSi = (original.siyiQuota && typeof original.siyiQuota === 'object' && !Array.isArray(original.siyiQuota))
        ? original.siyiQuota
        : {}
      next.siyiQuota = {
        ...prevSi,
        updatedAt: new Date(now()).toISOString(),
        note: 'candidates 顺序由 siyi-quota 按渠道健康度自动维护；其余字段原样保留，可手改。',
        ranking: ranked.map((c) => {
          const h = lookup[c.provider]
          return {
            provider: c.provider,
            model: c.model,
            health: h.health,
            samples: h.samples,
            successRate: h.successRate,
            consecutiveFailures: h.consecutiveFailures,
          }
        }),
      }
      // 用户可能把候选项写成字符串之类，上面已经跳过；这里只写合法结构。
      const res = writeJsonAtomic(policyPath, next)
      policyState.lastWriteAt = now()
      policyState.lastWriteOk = res.ok
      policyState.lastError = res.error || null
      if (!res.ok) logOnce('policy-write-fail:' + res.error, '写 quota-policy.json 失败: ' + res.error)
      else if (res.error) logOnce('policy-write-degraded', 'quota-policy.json 写入降级（非原子）: ' + res.error)
      return { ok: res.ok, skipped: false, candidates: ranked, path: policyPath, error: res.error || null, atomic: res.atomic }
    } catch (e) {
      policyState.lastWriteAt = now()
      policyState.lastWriteOk = false
      policyState.lastError = errText(e)
      log('重写策略异常（已忽略）: ' + errText(e))
      return { ok: false, skipped: true, reason: 'exception', candidates: null, error: errText(e) }
    }
  }

  /** 生成 quota-stats.json 的内容（人可读，含每个 provider 的样本数/成功率/连续失败/最后更新时间）。 */
  function buildSnapshot(policyResult) {
    try {
      const t = now()
      const read = readPolicy()
      let source = null
      if (policyResult && Array.isArray(policyResult.candidates)) source = policyResult.candidates
      else if (read.ok && read.data && Array.isArray(read.data.candidates) && read.data.candidates.length) source = read.data.candidates
      else source = builtin

      const candidateProviders = []
      for (const c of source) if (c && typeof c.provider === 'string') candidateProviders.push(c.provider)
      const names = Array.from(new Set([...providers.keys(), ...candidateProviders]))
      const healthMap = new Map()
      for (const n of names) healthMap.set(n, healthOf(n))

      const order = HEALTH_RANK
      const idx = new Map(names.map((n, i) => [n, i]))
      const ranking = names.slice().sort((a, b) => {
        const ha = healthMap.get(a)
        const hb = healthMap.get(b)
        const da = order[ha.health] === undefined ? 1 : order[ha.health]
        const db = order[hb.health] === undefined ? 1 : order[hb.health]
        if (da !== db) return da - db
        const ra = ha.successRate === null ? -1 : ha.successRate
        const rb = hb.successRate === null ? -1 : hb.successRate
        if (rb !== ra) return rb - ra
        return idx.get(a) - idx.get(b)
      })

      const outProviders = {}
      for (const n of ranking) outProviders[n] = healthMap.get(n)

      return {
        schema: 1,
        generatedAt: new Date(t).toISOString(),
        algorithm: {
          window: '统计前剔除超过 windowMaxAgeMs 的样本，再只保留最近 windowMaxSamples 次尝试',
          windowMaxSamples: cfg.windowMaxSamples,
          windowMaxAgeMinutes: Math.round(cfg.windowMaxAgeMs / 60000),
          minSamples: cfg.minSamples,
          healthyRate: cfg.healthyRate,
          consecutiveFailureLimit: cfg.consecutiveFailureLimit,
          healthRule: '成功率 < healthyRate 或连续失败 >= consecutiveFailureLimit 判 unhealthy；样本不足判 unknown',
          ignoredCodes: IGNORED_FAILURE_CODES.slice(),
          policyRule: 'candidates 排序：healthy > unknown > unhealthy；同档按近期成功率降序，再按原顺序；不剔除只排后',
        },
        providers: outProviders,
        ranking,
        policy: {
          path: policyPath,
          candidates: source.map((c) => ({ provider: c && c.provider, model: c && c.model })),
          lastRewriteAt: iso(policyState.lastWriteAt),
          lastRewriteOk: policyState.lastWriteOk,
          lastError: policyState.lastError,
        },
      }
    } catch (e) {
      log('生成统计快照异常（已忽略）: ' + errText(e))
      return { schema: 1, generatedAt: new Date(now()).toISOString(), error: errText(e), providers: {}, ranking: [] }
    }
  }

  /** 统计一变就落盘：先重写策略，再写统计（统计里带上本次策略写回结果）。 */
  function flush() {
    let policyResult = null
    try { policyResult = rewritePolicy() } catch (e) { log('重写策略异常（已忽略）: ' + errText(e)) }
    const snapshot = buildSnapshot(policyResult)
    let statsResult
    try {
      statsResult = writeJsonAtomic(statsPath, snapshot)
      if (!statsResult.ok) logOnce('stats-write-fail:' + statsResult.error, '写 quota-stats.json 失败: ' + statsResult.error)
      else if (statsResult.error) logOnce('stats-write-degraded', 'quota-stats.json 写入降级（非原子）: ' + statsResult.error)
    } catch (e) {
      statsResult = { ok: false, atomic: false, error: errText(e) }
      log('写 quota-stats.json 异常（已忽略）: ' + errText(e))
    }
    return { policy: policyResult, stats: statsResult, snapshot }
  }

  return {
    cfg,
    recordSuccess,
    recordFailure,
    flush,
    healthOf,
    snapshot: () => buildSnapshot(null),
    readPolicy,
    rewritePolicy,
    paths: { statsPath, policyPath },
  }
}

// ── 插件入口 ────────────────────────────────────────────────────────────

export function apply(ctx, config = {}) {
  const log = defaultLog
  const safe = (label, fn) => {
    try { fn() } catch (e) { log(label + ' 异常（已忽略，不影响对话）: ' + errText(e)) }
  }

  let core
  try {
    core = createQuotaCore({
      statsPath: config && config.statsPath,
      policyPath: config && config.policyPath,
      config: config && config.quota,
      log,
    })
  } catch (e) {
    log('初始化失败，插件不生效: ' + errText(e))
    return
  }

  // 1) 失败观察：全局 + 插到最前（见文件头「顺序坑」）。
  ctx.on('agent/request-error', (payload, next) => {
    safe('agent/request-error 监听器', () => {
      const provider = String((payload && payload.provider) || '').trim()
      const code = String((payload && payload.failure && payload.failure.code) || 'UNKNOWN')
      if (!provider) { log('失败事件缺少 provider，跳过统计 code=' + code); return }
      if (IGNORED_FAILURE.has(code)) { log('忽略非渠道健康失败 code=' + code + ' provider=' + provider); return }
      core.recordFailure(provider, '', code)
    })
    return next()
  }, { global: true, prepend: true })

  // 2) 成功观察：llm/stream 包一层，迭代正常结束算成功，记首个 chunk 延迟。
  ctx.on('llm/stream', (options, next) => {
    const provider = String((options && options.provider) || '').trim()
    const model = String((options && options.model) || '').trim()
    const inner = next()
    return (async function* observed() {
      let stream = inner
      // 极少数监听器可能返回 Promise<AsyncIterable>，兼容一下。
      if (stream && typeof stream.then === 'function') stream = await stream
      const startedAt = Date.now()
      let firstChunkAt = 0
      let completed = false
      try {
        for await (const chunk of stream) {
          if (!firstChunkAt) firstChunkAt = Date.now()
          yield chunk
        }
        completed = true
      } finally {
        if (completed && provider) {
          safe('记录成功', () => {
            core.recordSuccess(provider, model, firstChunkAt ? firstChunkAt - startedAt : null)
          })
        }
      }
    })()
  }, { global: true, prepend: true })

  // 3) 启动基线：即使还没请求也把 stats 落盘，方便排查；策略文件缺失时按内置候选生成。
  safe('启动基线写入', () => {
    const r = core.flush()
    log('启动基线写入完成（stats ' + (r.stats && r.stats.ok ? 'ok' : 'fail') +
      ' / policy ' + (r.policy && r.policy.ok ? 'ok' : (r.policy && r.policy.skipped ? 'skipped' : 'fail')) + '）')
  })

  // 4) 只读排查接口：/siyi-quota/status 返回 quota-stats.json，/siyi-quota/policy 返回策略原文。
  safe('挂载 HTTP 排查接口', () => {
    const ws = ctx.get('webServer', false)
    if (!ws || typeof ws.register !== 'function') {
      log('未找到 webServer 服务，HTTP 排查接口未挂载（统计与策略照常工作）')
      return
    }
    const sendJson = (res, code, obj) => {
      try {
        const body = JSON.stringify(obj)
        res.writeHead(code, {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-store',
        })
        res.end(body)
      } catch { /* 客户端断开之类，忽略 */ }
    }
    const disposer = ws.register({
      kind: 'prefix',
      path: ROUTE_PREFIX,
      handler: async (req, res) => {
        let url
        try {
          url = new URL(req.url, 'http://127.0.0.1')
        } catch {
          return sendJson(res, 400, { ok: false, error: 'bad url' })
        }
        if (req.method !== 'GET' && req.method !== 'HEAD') {
          return sendJson(res, 405, { ok: false, error: 'GET only' })
        }
        const action = url.pathname
          .slice(ROUTE_PREFIX.length)
          .replace(/^\/+/, '')
          .replace(/\/+$/, '') || 'status'
        try {
          if (action === 'status') {
            // 优先读盘上的 quota-stats.json，保证看到的是落盘真相。
            let stats = null
            let source = 'quota-stats.json'
            try {
              stats = JSON.parse(fs.readFileSync(core.paths.statsPath, 'utf8'))
            } catch {
              stats = core.snapshot()
              source = 'memory'
            }
            return sendJson(res, 200, { ok: true, source, stats })
          }
          if (action === 'policy') {
            const read = core.readPolicy()
            if (!read.exists) return sendJson(res, 200, { ok: false, exists: false, path: core.paths.policyPath })
            if (!read.ok) return sendJson(res, 500, { ok: false, exists: true, error: read.error, path: core.paths.policyPath })
            return sendJson(res, 200, { ok: true, exists: true, path: core.paths.policyPath, policy: read.data })
          }
          return sendJson(res, 404, { ok: false, error: 'unknown action: ' + action + '（可用 status / policy）' })
        } catch (e) {
          log('HTTP ' + action + ' 失败: ' + errText(e))
          return sendJson(res, 500, { ok: false, error: errText(e) })
        }
      },
    })
    ctx.effect(() => () => {
      try { disposer() } catch {}
    }, 'siyi-quota: dispose route')
    log('HTTP 排查接口已挂载 ' + ROUTE_PREFIX + '/status')
  })

  log('已挂载：窗口最近 ' + core.cfg.windowMaxSamples + ' 次 / ' +
    Math.round(core.cfg.windowMaxAgeMs / 60000) + ' 分钟，健康阈值成功率 >= ' +
    (core.cfg.healthyRate * 100).toFixed(0) + '%，连续失败 >= ' +
    core.cfg.consecutiveFailureLimit + ' 判不健康；策略文件 ' + POLICY_PATH)
}
