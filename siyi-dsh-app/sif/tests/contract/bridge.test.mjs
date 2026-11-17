// T02/T04/T05 桥接合同测试：握手、调用、取消、幂等、断线终态
import { createBridgeServer } from '../../core/bridge/server.mjs'
import { createJournal, STATE } from '../../core/bridge/journal.mjs'
import { LineDecoder, encodeFrame } from '../../core/bridge/codec.mjs'
import { Duplex, PassThrough } from 'node:stream'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

let pass = 0, fail = 0
const lines = []
const tests = []
function t(name, fn) { tests.push({ name, fn }) }
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error(`${label}: expected ${y}, got ${x}`)
}

/** 建立一对互相连通的双向流，模拟 socket 两端。 */
function makePair() {
  const a2b = new PassThrough()
  const b2a = new PassThrough()
  const clientSide = Duplex.from({ readable: b2a, writable: a2b })
  const serverSide = Duplex.from({ readable: a2b, writable: b2a })
  return { clientSide, serverSide }
}

function makeCaps(overrides = {}) {
  const base = (name, risk, execute, extra = {}) => ({
    descriptor: {
      specVersion: 'siyi.capability/v1', version: '1.0.0',
      description: name + ' 测试能力',
      inputSchema: { type: 'object' },
      risk, permissions: [], idempotent: risk !== 'read',
      supportsCancel: true, timeoutMs: 2000, concurrency: 'parallel',
      ...extra,
    },
    execute,
  })
  return {
    'test.echo': base('echo', 'read', async args => ({ echo: args })),
    'test.save': base('save', 'write', async args => ({ saved: args.v })),
    'test.slow': base('slow', 'read', async (args, { signal }) => {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, args.ms ?? 500)
        signal.addEventListener('abort', () => { clearTimeout(timer); reject(Object.assign(new Error('aborted'), { code: 'CANCELLED' })) }, { once: true })
      })
      return { done: true }
    }),
    'test.timeout': base('timeout', 'write', async () => {
      await new Promise(r => setTimeout(r, 300))
      return { late: true }
    }, { timeoutMs: 50 }),
    'test.fail': base('fail', 'read', async () => {
      throw Object.assign(new Error('故意失败'), { code: 'UNAVAILABLE', details: { why: 'test' } })
    }),
    ...overrides,
  }
}

/** 起一个服务端并返回客户端辅助函数。 */
async function start({ caps = makeCaps(), journalFile } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-bridge-'))
  const journal = createJournal({ file: journalFile || path.join(dir, 'ops.ndjson') })
  const { clientSide, serverSide } = makePair()
  // 测试用 Duplex 在 destroy 时会发 AbortError，挂空监听避免未处理异常
  clientSide.on('error', () => {})
  serverSide.on('error', () => {})
  const server = createBridgeServer({ caps, journal, stream: serverSide, platform: 'test' })
  const dec = new LineDecoder()
  const inbox = []
  const waiters = []
  clientSide.on('data', chunk => {
    for (const line of dec.push(chunk)) {
      const msg = JSON.parse(line)
      inbox.push(msg)
      for (let i = waiters.length - 1; i >= 0; i--) {
        if (waiters[i].match(msg)) { waiters.splice(i, 1)[0].resolve(msg) }
      }
    }
  })
  let nextId = 1
  function send(method, params) {
    const id = nextId++
    clientSide.write(encodeFrame({ jsonrpc: '2.0', id, method, params }))
    return id
  }
  function waitFor(predicate, timeoutMs = 3000) {
    return new Promise((resolve, reject) => {
      const hit = inbox.find(predicate)
      if (hit) return resolve(hit)
      const timer = setTimeout(() => reject(new Error('waitFor timeout')), timeoutMs)
      waiters.push({ match: predicate, resolve: m => { clearTimeout(timer); resolve(m) } })
    })
  }
  const waitId = id => waitFor(m => m.id === id)
  return { server, journal, clientSide, serverSide, send, waitId, waitFor, inbox, dir }
}

// ── 握手 ──────────────────────────────────────────────────
t('hello 返回协议版本、epoch 和能力清单', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('bridge/hello', { protocolVersion: 'siyi.bridge/v1' }))
  eq(r.result.protocolVersion, 'siyi.bridge/v1', 'version')
  eq(typeof r.result.epoch, 'string', 'epoch')
  eq(r.result.capabilities.length, 5, 'cap-count')
  eq(r.result.features.includes('operation/status'), true, 'features')
  server.close()
})

t('hello 拒绝不支持的协议版本', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('bridge/hello', { protocolVersion: 'siyi.bridge/v0' }))
  eq(r.error.message.includes('不支持的协议版本'), true, 'reject')
  server.close()
})

t('hello 缺 protocolVersion 报 INVALID_ARGUMENT', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('bridge/hello', {}))
  eq(r.error.message.includes('需要 protocolVersion'), true, 'reject')
  server.close()
})

// ── 清单 ──────────────────────────────────────────────────
t('capability/list 返回全部工具与 epoch', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('capability/list', {}))
  eq(r.result.tools.length, 5, 'count')
  eq(typeof r.result.epoch, 'string', 'epoch')
  eq(r.result.tools.map(t => t.name).sort().join(','), 'test.echo,test.fail,test.save,test.slow,test.timeout', 'names')
  server.close()
})

// ── 调用 ──────────────────────────────────────────────────
t('read 调用返回结果', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('capability/invoke', {
    operationId: 'op-1', attemptId: 'att-1', callId: 'c-1', tool: 'test.echo', args: { x: 42 },
  }))
  eq(r.result.ok, true, 'ok')
  eq(r.result.result.echo.x, 42, 'value')
  server.close()
})

t('未知能力返回 NOT_FOUND', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('capability/invoke', {
    operationId: 'op-x', attemptId: 'a', callId: 'c', tool: 'nope.nope', args: {},
  }))
  eq(r.error.message.includes('未知能力'), true, 'notfound')
  server.close()
})

t('能力失败带 code 与 details', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('capability/invoke', {
    operationId: 'op-f', attemptId: 'a', callId: 'c', tool: 'test.fail', args: {},
  }))
  eq(r.result.ok, false, 'ok')
  eq(r.result.error.code, 'UNAVAILABLE', 'code')
  eq(r.result.error.retryable, true, 'retryable')
  eq(r.result.error.details.why, 'test', 'details')
  server.close()
})

// ── 取消 ──────────────────────────────────────────────────
t('取消在途调用，调用方收到 CANCELLED', async () => {
  const { send, waitId, server } = await start()
  const invokeId = send('capability/invoke', {
    operationId: 'op-c', attemptId: 'a', callId: 'call-cancel', tool: 'test.slow', args: { ms: 2000 },
  })
  await new Promise(r => setTimeout(r, 50))
  const cancelId = send('capability/cancel', { callId: 'call-cancel' })
  const c = await waitId(cancelId)
  eq(c.result.outcome, 'cancel_requested', 'cancel-ack')
  const inv = await waitId(invokeId)
  eq(inv.result.ok, false, 'invoke-failed')
  eq(inv.result.error.code, 'CANCELLED', 'code')
  server.close()
})

t('取消未知 callId 返回 not_found_or_finished', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('capability/cancel', { callId: 'never-existed' }))
  eq(r.result.outcome, 'not_found_or_finished', 'outcome')
  server.close()
})

// ── 幂等 ──────────────────────────────────────────────────
t('同一写操作重放返回原结果不重复执行', async () => {
  let runs = 0
  const caps = makeCaps({
    'test.count': {
      descriptor: {
        specVersion: 'siyi.capability/v1', version: '1.0.0', description: '计数写',
        inputSchema: { type: 'object' }, risk: 'write', permissions: [],
        idempotent: true, supportsCancel: true, timeoutMs: 2000, concurrency: 'serialized',
      },
      execute: async args => { runs++; return { runs, v: args.v } },
    },
  })
  const { send, waitId, server } = await start({ caps })
  const p1 = send('capability/invoke', { operationId: 'op-i1', attemptId: 'a1', callId: 'c1', tool: 'test.count', args: { v: 'same' } })
  const r1 = await waitId(p1)
  const p2 = send('capability/invoke', { operationId: 'op-i2', attemptId: 'a2', callId: 'c2', tool: 'test.count', args: { v: 'same' } })
  const r2 = await waitId(p2)
  eq(r1.result.ok, true, 'first')
  eq(r2.result.replayed, true, 'replayed')
  eq(runs, 1, 'executed-once')
  eq(r2.result.result.v, 'same', 'same-result')
  server.close()
})

t('不同参数不算同一操作', async () => {
  let runs = 0
  const caps = makeCaps({
    'test.count2': {
      descriptor: {
        specVersion: 'siyi.capability/v1', version: '1.0.0', description: '计数写2',
        inputSchema: { type: 'object' }, risk: 'write', permissions: [],
        idempotent: true, supportsCancel: true, timeoutMs: 2000, concurrency: 'serialized',
      },
      execute: async args => { runs++; return { runs, v: args.v } },
    },
  })
  const { send, waitId, server } = await start({ caps })
  await waitId(send('capability/invoke', { operationId: 'op-j1', attemptId: 'a', callId: 'c1', tool: 'test.count2', args: { v: 'a' } }))
  await waitId(send('capability/invoke', { operationId: 'op-j2', attemptId: 'a', callId: 'c2', tool: 'test.count2', args: { v: 'b' } }))
  eq(runs, 2, 'both-ran')
  server.close()
})

// ── 超时与结果未知 ────────────────────────────────────────
t('写操作超时标 ambiguous 并记 outcome_unknown', async () => {
  const { send, waitId, server, journal } = await start()
  const r = await waitId(send('capability/invoke', {
    operationId: 'op-to', attemptId: 'a', callId: 'c', tool: 'test.timeout', args: {},
  }))
  eq(r.result.ok, false, 'ok')
  eq(r.result.error.code, 'TIMEOUT', 'code')
  eq(r.result.error.ambiguous, true, 'ambiguous')
  const rec = journal.status('op-to')
  eq(rec.state, STATE.OUTCOME_UNKNOWN, 'journal-state')
  server.close()
})

t('operation/status 可查询终态与结果', async () => {
  const { send, waitId, server } = await start()
  await waitId(send('capability/invoke', {
    operationId: 'op-st', attemptId: 'a', callId: 'c', tool: 'test.save', args: { v: 'persisted' },
  }))
  const r = await waitId(send('operation/status', { operationId: 'op-st' }))
  eq(r.result.ok, true, 'ok')
  eq(r.result.operation.state, 'completed', 'state')
  eq(r.result.operation.result.saved, 'persisted', 'result')
  eq(r.result.terminal, true, 'terminal')
  server.close()
})

t('operation/status 未知 id 返回 NOT_FOUND', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('operation/status', { operationId: 'never' }))
  eq(r.result.ok, false, 'ok')
  eq(r.result.error.code, 'NOT_FOUND', 'code')
  server.close()
})

// ── 断线与重启对账 ────────────────────────────────────────
t('断线时在途写操作记为 outcome_unknown', async () => {
  const { send, server, journal, clientSide } = await start()
  send('capability/invoke', {
    operationId: 'op-drop', attemptId: 'a', callId: 'c-drop', tool: 'test.timeout', args: {},
  })
  await new Promise(r => setTimeout(r, 10))
  clientSide.destroy()
  await new Promise(r => setTimeout(r, 60))
  const rec = journal.status('op-drop')
  eq(rec && rec.state, STATE.OUTCOME_UNKNOWN, 'unknown')
  server.close()
})

t('重启后 journal 仍能对账已完成操作', async () => {
  const first = await start()
  await first.waitId(first.send('capability/invoke', {
    operationId: 'op-persist', attemptId: 'a', callId: 'c', tool: 'test.save', args: { v: 'keep' },
  }))
  first.server.close()
  const jf = path.join(first.dir, 'ops.ndjson')
  const { createJournal: cj } = await import('../../core/bridge/journal.mjs')
  const reopened = cj({ file: jf })
  const rec = reopened.status('op-persist')
  eq(rec.state, 'completed', 'state')
  eq(rec.result.saved, 'keep', 'result')
})

// ── 非法输入 ──────────────────────────────────────────────
t('未知方法返回 NOT_FOUND', async () => {
  const { send, waitId, server } = await start()
  const r = await waitId(send('nope/method', {}))
  eq(r.error.message.includes('未知方法'), true, 'unknown-method')
  server.close()
})

// ── 执行 ──────────────────────────────────────────────────
for (const { name, fn } of tests) {
  try { await fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
if (fail > 0) process.exit(1)
