// Bridge Server —— 能力宿主侧实现（对应规范 §5）
//
// 职责：接收 capability/list、capability/invoke、capability/cancel、
// operation/status、bridge/hello，把调用路由到已注册的能力实现。
//
// 与现有 Android plugin-device 的差别（这正是要修的地方）：
//   1. 带 hello 握手和协议版本协商
//   2. 带 epoch，工具清单变化时旧调用有明确终态
//   3. 带 operationId/attemptId，写操作走 journal 幂等
//   4. 超时后标 outcome_unknown 而不是静默丢弃
//   5. 断线时取消所有在途调用

import crypto from 'node:crypto'
import { LineDecoder, encodeFrame, decodeFrame, writeWithBackpressure, FrameTooLargeError, CodecError } from './codec.mjs'
import { createJournal, idempotencyKeyOf, STATE, isTerminal } from './journal.mjs'

export const PROTOCOL_VERSION = 'siyi.bridge/v1'

const AMBIGUOUS_CODES = new Set(['TIMEOUT', 'TRANSPORT', 'CANCELLED', 'RESULT_UNKNOWN'])

function newId(prefix = '') {
  const b = crypto.randomBytes(10).toString('hex')
  return prefix + Date.now().toString(36) + '-' + b
}

/**
 * 创建一个 bridge server。
 * @param caps     能力实现表：{ 'vault.get': { descriptor, execute } }
 * @param journal  operation journal 实例
 * @param stream   双向流（socket / pipe / 测试用 duplex）
 */
export function createBridgeServer({ caps, journal, stream, log = () => {}, platform = 'unknown', maxFrameBytes } = {}) {
  if (!stream) throw new Error('stream 必填')
  const decoder = new LineDecoder(maxFrameBytes ? { maxFrameBytes } : undefined)
  const epoch = 'host-' + crypto.randomBytes(4).toString('hex')
  const inflight = new Map()   // callId -> { controller, operationId, attemptId, tool }
  let closed = false

  const jr = journal || createJournal({ file: '/tmp/sif-journal.ndjson' })

  function send(message) {
    if (closed) return Promise.resolve()
    let buf
    try { buf = encodeFrame(message) } catch (e) {
      log('warn', 'encode failed: ' + e.message)
      return Promise.resolve()
    }
    return writeWithBackpressure(stream, buf).catch(e => log('warn', 'write failed: ' + e.message))
  }

  function reply(id, result) { return send({ jsonrpc: '2.0', id, result }) }
  function replyError(id, code, message, data) {
    return send({ jsonrpc: '2.0', id, error: { code, message, data } })
  }

  function listDescriptors() {
    return Object.entries(caps).map(([name, impl]) => ({
      ...impl.descriptor,
      name,
    }))
  }

  async function handleHello(id, params) {
    const clientVersion = params?.protocolVersion
    if (typeof clientVersion !== 'string') return replyError(id, 'INVALID_ARGUMENT', 'bridge/hello 需要 protocolVersion')
    if (!clientVersion.startsWith('siyi.bridge/v1')) {
      return replyError(id, 'INVALID_ARGUMENT', '不支持的协议版本: ' + clientVersion, { supported: [PROTOCOL_VERSION] })
    }
    return reply(id, {
      protocolVersion: PROTOCOL_VERSION,
      platform,
      epoch,
      features: ['capability/list', 'capability/invoke', 'capability/cancel', 'operation/status', 'capability/changed'],
      maxFrameBytes: maxFrameBytes ?? 1024 * 1024,
      capabilities: listDescriptors(),
    })
  }

  async function handleInvoke(id, params) {
    const { operationId, attemptId, callId, tool, args } = params || {}
    if (typeof tool !== 'string' || !caps[tool]) {
      return replyError(id, 'NOT_FOUND', '未知能力: ' + String(tool), { epoch })
    }
    const impl = caps[tool]
    const opId = typeof operationId === 'string' && operationId ? operationId : newId('op-')
    const attId = typeof attemptId === 'string' && attemptId ? attemptId : newId('att-')
    const call = typeof callId === 'string' && callId ? callId : newId('call-')

    // 幂等：写操作且提供了 key 语义（tool+args+sessionId）
    const isWrite = impl.descriptor.risk !== 'read'
    const idemKey = isWrite ? idempotencyKeyOf({ tool, args, sessionId: params?.sessionId || '' }) : null

    if (isWrite) {
      const begun = jr.begin({ operationId: opId, idempotencyKey: idemKey, tool, args, sessionId: params?.sessionId || '' })
      if (begun.existing) {
        const rec = begun.record
        return reply(id, {
          ok: rec.state === STATE.COMPLETED,
          replayed: true,
          operationId: rec.operationId,
          state: rec.state,
          result: rec.result,
          error: rec.error,
        })
      }
      jr.markRunning(opId, attId)
    }

    const controller = new AbortController()
    inflight.set(call, { controller, operationId: opId, attemptId: attId, tool, isWrite })

    const timeoutMs = impl.descriptor.timeoutMs ?? 30000
    // 超时必须由服务端强制生效：仅 abort signal 不够，因为能力实现可能不监听它。
    // 用 Promise.race 让 deadline 先到就先返回 TIMEOUT，晚到结果只记审计。
    let timer = null
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const err = Object.assign(new Error('capability ' + tool + ' timed out after ' + timeoutMs + 'ms'), { code: 'TIMEOUT' })
        try { controller.abort(err) } catch {}
        reject(err)
      }, timeoutMs)
    })

    try {
      const value = await Promise.race([
        impl.execute(args ?? {}, { signal: controller.signal, operationId: opId, attemptId: attId, callId: call }),
        deadline,
      ])
      clearTimeout(timer)
      inflight.delete(call)
      if (isWrite) jr.complete(opId, value, attId)
      return reply(id, { ok: true, operationId: opId, attemptId: attId, result: value })
    } catch (e) {
      if (timer) clearTimeout(timer)
      inflight.delete(call)
      const code = e && e.code ? String(e.code) : 'INTERNAL'
      const ambiguous = AMBIGUOUS_CODES.has(code) && isWrite
      if (isWrite) jr.fail(opId, { code, message: String(e && e.message || e) }, { ambiguous, attemptId: attId })
      return reply(id, {
        ok: false,
        operationId: opId,
        attemptId: attId,
        error: {
          code,
          message: String(e && e.message || e),
          retryable: code === 'UNAVAILABLE' || code === 'TIMEOUT' || code === 'TRANSPORT' || code === 'RATE_LIMITED',
          ambiguous,
          details: e && e.details ? e.details : undefined,
        },
      })
    }
  }

  async function handleCancel(id, params) {
    const callId = params?.callId
    const target = inflight.get(callId)
    if (!target) {
      // 可能已完成或未知
      return reply(id, { ok: true, callId, outcome: 'not_found_or_finished' })
    }
    target.controller.abort(Object.assign(new Error('cancelled by peer'), { code: 'CANCELLED' }))
    if (target.isWrite) jr.cancel(target.operationId)
    inflight.delete(callId)
    return reply(id, { ok: true, callId, outcome: 'cancel_requested', operationId: target.operationId })
  }

  async function handleOperationStatus(id, params) {
    const opId = params?.operationId
    if (typeof opId !== 'string') return replyError(id, 'INVALID_ARGUMENT', 'operationId 必填')
    const rec = jr.status(opId)
    if (!rec) return reply(id, { ok: false, error: { code: 'NOT_FOUND', message: '未知 operationId' } })
    return reply(id, { ok: true, operation: rec, terminal: isTerminal(rec.state) })
  }

  async function dispatch(line) {
    let msg
    try { msg = decodeFrame(line) } catch (e) {
      return send({ jsonrpc: '2.0', error: { code: -32700, message: 'parse error: ' + e.message } })
    }
    const id = msg.id
    const method = msg.method
    const params = msg.params || {}

    if (typeof method !== 'string') {
      if (id !== undefined) return replyError(id, 'INVALID_ARGUMENT', '缺少 method')
      return
    }
    if (id === undefined || id === null) {
      // 通知：目前只接受 capability/changed 之类，无响应
      return
    }
    switch (method) {
      case 'bridge/hello': return handleHello(id, params)
      case 'capability/list': return reply(id, { epoch, tools: listDescriptors() })
      case 'capability/invoke': return handleInvoke(id, params)
      case 'capability/cancel': return handleCancel(id, params)
      case 'operation/status': return handleOperationStatus(id, params)
      default: return replyError(id, 'NOT_FOUND', '未知方法: ' + method)
    }
  }

  function onData(chunk) {
    let frames
    try { frames = decoder.push(chunk) } catch (e) {
      log('warn', 'framing error: ' + e.message)
      close(e)
      return
    }
    for (const line of frames) dispatch(line).catch(e => log('warn', 'dispatch failed: ' + e.message))
  }

  function close(reason) {
    if (closed) return
    closed = true
    for (const [, t] of inflight) {
      try { t.controller.abort(Object.assign(new Error('bridge closed'), { code: 'TRANSPORT' })) } catch {}
      if (t.isWrite) jr.fail(t.operationId, { code: 'TRANSPORT', message: 'bridge closed' }, { ambiguous: true, attemptId: t.attemptId })
    }
    inflight.clear()
    if (reason) log('info', 'closed: ' + (reason.message || reason))
  }

  stream.on('data', onData)
  stream.on('error', e => close(e))
  stream.on('close', () => close())
  stream.on('end', () => close())

  return {
    epoch,
    close,
    inflightCount: () => inflight.size,
    descriptorList: listDescriptors,
  }
}
