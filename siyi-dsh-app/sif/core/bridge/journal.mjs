// Operation Journal —— 写操作幂等与结果对账（对应规范 §5.4 / §7.3）
//
// 为什么必须有：写请求超时后断线，调用方无法知道「日历建了没」「笔记写了没」。
// 现有实现超时后直接删 pending，晚到结果被静默丢弃，只能靠猜。
//
// 本模块提供：
//   1. operationId 幂等：同一逻辑操作重复提交，返回第一次的结果，不重复执行
//   2. 状态机：accepted → running → completed / failed / cancelled / outcome_unknown
//   3. 跨进程重启持久化（NDJSON append-only + 定期压缩）
//   4. 结果未知时禁止自动重放，由调用方用 status() 对账

import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'

export const STATE = Object.freeze({
  ACCEPTED: 'accepted',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
  OUTCOME_UNKNOWN: 'outcome_unknown',
})

const TERMINAL = new Set([STATE.COMPLETED, STATE.FAILED, STATE.CANCELLED, STATE.OUTCOME_UNKNOWN])

export function isTerminal(state) {
  return TERMINAL.has(state)
}

/** 幂等键：同一调用意图必须产生同一 key。 */
export function idempotencyKeyOf({ tool, args, sessionId = '' }) {
  const canonical = JSON.stringify({ tool, args: args ?? {}, sessionId })
  return crypto.createHash('sha256').update(canonical).digest('hex')
}

export function createJournal({ file, now = () => new Date(), maxEntriesInMemory = 5000 } = {}) {
  if (!file) throw new Error('journal file 必填')
  const dir = path.dirname(file)
  fs.mkdirSync(dir, { recursive: true })

  /** operationId -> record */
  const ops = new Map()
  /** idempotencyKey -> operationId（只记终态，避免占位抢占） */
  const byKey = new Map()

  function load() {
    let text = ''
    try { text = fs.readFileSync(file, 'utf8') } catch { return }
    for (const line of text.split('\n')) {
      if (!line.trim()) continue
      let rec
      try { rec = JSON.parse(line) } catch { continue }
      if (!rec || typeof rec.operationId !== 'string') continue
      const prev = ops.get(rec.operationId)
      // 后写覆盖先写；终态不可被非终态覆盖
      if (prev && isTerminal(prev.state) && !isTerminal(rec.state)) continue
      ops.set(rec.operationId, rec)
      if (rec.idempotencyKey && isTerminal(rec.state)) byKey.set(rec.idempotencyKey, rec.operationId)
    }
    compactIfNeeded()
  }

  function append(rec) {
    const line = JSON.stringify(rec) + '\n'
    try {
      const fd = fs.openSync(file, 'a', 0o600)
      try { fs.writeSync(fd, line) } finally { fs.closeSync(fd) }
    } catch {}
  }

  function compactIfNeeded() {
    if (ops.size <= maxEntriesInMemory) return
    const terminal = [...ops.values()].filter(r => isTerminal(r.state))
    terminal.sort((a, b) => String(a.updatedAt).localeCompare(String(b.updatedAt)))
    const drop = terminal.slice(0, Math.max(0, terminal.length - Math.floor(maxEntriesInMemory * 0.8)))
    for (const r of drop) {
      ops.delete(r.operationId)
      if (r.idempotencyKey) byKey.delete(r.idempotencyKey)
    }
  }

  function put(rec) {
    const next = { ...rec, updatedAt: now().toISOString() }
    ops.set(next.operationId, next)
    if (next.idempotencyKey && isTerminal(next.state)) byKey.set(next.idempotencyKey, next.operationId)
    append(next)
    return next
  }

  /**
   * 开始一个操作。若同 idempotencyKey 已有终态，直接返回既有结果（不重复执行）。
   * 返回 { existing: true, record } 或 { existing: false, record }。
   */
  function begin({ operationId, idempotencyKey, tool, args, sessionId = '' }) {
    if (idempotencyKey) {
      const prevId = byKey.get(idempotencyKey)
      if (prevId) {
        const prev = ops.get(prevId)
        if (prev && isTerminal(prev.state)) return { existing: true, record: prev }
      }
    }
    const rec = put({
      operationId,
      idempotencyKey: idempotencyKey || null,
      tool,
      args: args ?? null,
      sessionId,
      state: STATE.ACCEPTED,
      createdAt: now().toISOString(),
      attempts: [],
      result: null,
      error: null,
    })
    return { existing: false, record: rec }
  }

  function markRunning(operationId, attemptId) {
    const rec = ops.get(operationId)
    if (!rec) return null
    const attempts = [...(rec.attempts || []), { attemptId, startedAt: now().toISOString() }]
    return put({ ...rec, state: STATE.RUNNING, attempts })
  }

  function complete(operationId, result, attemptId) {
    const rec = ops.get(operationId)
    if (!rec) return null
    if (isTerminal(rec.state)) return rec   // 终态幂等
    const attempts = (rec.attempts || []).map(a =>
      a.attemptId === attemptId ? { ...a, endedAt: now().toISOString(), outcome: 'completed' } : a)
    return put({ ...rec, state: STATE.COMPLETED, result, attempts, error: null })
  }

  function fail(operationId, error, { ambiguous = false, attemptId } = {}) {
    const rec = ops.get(operationId)
    if (!rec) return null
    if (isTerminal(rec.state)) return rec
    const state = ambiguous ? STATE.OUTCOME_UNKNOWN : STATE.FAILED
    const attempts = (rec.attempts || []).map(a =>
      a.attemptId === attemptId ? { ...a, endedAt: now().toISOString(), outcome: state } : a)
    return put({ ...rec, state, error: error ?? null, attempts })
  }

  function cancel(operationId) {
    const rec = ops.get(operationId)
    if (!rec) return null
    if (isTerminal(rec.state)) return rec
    return put({ ...rec, state: STATE.CANCELLED })
  }

  function status(operationId) {
    return ops.get(operationId) || null
  }

  function findByKey(idempotencyKey) {
    const id = byKey.get(idempotencyKey)
    return id ? ops.get(id) : null
  }

  function stats() {
    const byState = {}
    for (const r of ops.values()) byState[r.state] = (byState[r.state] || 0) + 1
    return { total: ops.size, byState, file }
  }

  load()
  return { begin, markRunning, complete, fail, cancel, status, findByKey, stats, STATE }
}
