// 思忆集 · PC DSH 桥（SIYI-PCBRIDGE-20261128）
//
// 通过 Tailscale 把当前手机 DSH 与 PC 上的 DSH 实例连起来：
//   - 数据共享：调 PC 的 session/list、schedule/list、pluginInventory/list 等
//   - 手机操控 PC DSH：调 PC 的 Remote 方法
//   - PC 操控电脑：PC 端 DSH 自带 bash/fs 工具（本插件负责把指令送过去）
//
// 协议（已实测）：
//   - 认证：GET /?token=xxx 换 cookie（dsh-auth-*）；WS/HTTP 都必须带 cookie
//   - unary 方法：POST /api/{namespace}/{method}
//       body = {type:'client-request',rpcId,method,payload:{args:{...}}}
//       返回 {type:'server-response',rpcId,result:{ok:true,value}|{ok:false,error}}
//   - stream 方法：WS /api/remote.mux，send {type:'open',streamId,endpoint,payload:{args:{}}}
//
// 配置：/storage/emulated/0/思忆集/app/sif/config/pc-dsh-connection.json
//   {base, token} —— token 由 PC DSH 启动时打印
//
// 边界：这是外部连接，token 是敏感凭证；本插件只在本机回环 HTTP 暴露接口，
//       沿用 siyi-vault.js 的 Host/Origin 闸门思路。

import fs from 'node:fs'
import { randomUUID } from 'node:crypto'

export const name = 'siyi-pc-bridge'
export const inject = []

const CONF = '/storage/emulated/0/思忆集/app/sif/config/pc-dsh-connection.json'
const LOG = (m) => { try { process.stderr.write('[siyi-pc-bridge] ' + m + '\n') } catch {} }

function loadConf() {
  try { return JSON.parse(fs.readFileSync(CONF, 'utf8')) } catch { return null }
}

let cookieCache = null
let cookieAt = 0
async function getCookie(conf) {
  if (cookieCache && Date.now() - cookieAt < 20 * 60 * 1000) return cookieCache
  const r = await fetch(`${conf.base}/?token=${conf.token}`, {
    redirect: 'manual', signal: AbortSignal.timeout(15000),
  })
  const cookies = r.headers.getSetCookie().map(x => x.split(';')[0])
  if (!cookies.length) throw new Error('token 换取 cookie 失败（HTTP ' + r.status + '）')
  cookieCache = cookies.join('; ')
  cookieAt = Date.now()
  return cookieCache
}

/** unary RPC：POST /api/{endpoint} */
async function callRpc(conf, endpoint, args) {
  const cookie = await getCookie(conf)
  const rpcId = randomUUID()
  const body = JSON.stringify({
    type: 'client-request', rpcId, method: endpoint, payload: { args: args || {} },
  })
  const r = await fetch(`${conf.base}/api/${endpoint}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', cookie },
    body,
    signal: AbortSignal.timeout(20000),
  })
  if (!r.ok) throw new Error(`RPC ${endpoint} HTTP ${r.status}`)
  const j = await r.json()
  const result = j.result
  if (!result) throw new Error('RPC 响应缺 result')
  if (result.ok === false) throw new Error(`${result.error?.code || 'error'}: ${result.error?.message || ''}`)
  return result.value
}

/** stream RPC：WS /api/remote.mux，收集首个 item/end */
function streamRpc(conf, endpoint, args, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const cookie = cookieCache
    if (!cookie) return reject(new Error('需先获取 cookie'))
    let ws
    try { ws = new WebSocket(`${conf.base.replace('http', 'ws')}/api/remote.mux`, { headers: { cookie } }) }
    catch (e) { return reject(e) }
    const streamId = randomUUID()
    const items = []
    const timer = setTimeout(() => { try { ws.close() } catch {}; resolve({ items, timeout: true }) }, timeoutMs)
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'open', streamId, endpoint, payload: { args: args || {} } }))
    }
    ws.onmessage = (e) => {
      let m
      try { m = JSON.parse(String(e.data)) } catch { return }
      if (m.streamId !== streamId) return
      if (m.type === 'item') items.push(m.value)
      else if (m.type === 'end') {
        clearTimeout(timer); try { ws.close() } catch {}
        resolve({ items, done: true })
      } else if (m.type === 'error') {
        clearTimeout(timer); try { ws.close() } catch {}
        reject(new Error(m.error?.code + ': ' + m.error?.message))
      }
    }
    ws.onerror = (e) => { clearTimeout(timer); reject(new Error('WS error: ' + (e.message || ''))) }
  })
}

function sendJson(res, code, obj) {
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' })
  res.end(JSON.stringify(obj))
}
function isLoopback(host) {
  const h = String(host || '').split(':')[0].toLowerCase()
  return h === '127.0.0.1' || h === 'localhost' || h === '[::1]' || h === '::1'
}

export function apply(ctx) {
  const conf = loadConf()
  if (!conf || !conf.base || !conf.token) { LOG("未找到连接配置，插件停用"); return }
  const ws = ctx.get("webServer", false)
  if (!ws || typeof ws.register !== "function") { LOG("webServer 不可用，插件停用"); return }
  const PREFIX = "/siyi-pc"

  const handler = async (req, res) => {
    let url
    try { url = new URL(req.url, "http://127.0.0.1") }
    catch { return sendJson(res, 400, { ok: false, error: "bad url" }) }
    if (!isLoopback(req.headers.host)) return sendJson(res, 403, { ok: false, error: "forbidden host" })
    const action = url.pathname.slice(PREFIX.length).replace(/^\//, "")
    try {
      if (action === "status") {
        await getCookie(conf)
        return sendJson(res, 200, { ok: true, base: conf.base, connected: true, cookieAgeMs: Date.now() - cookieAt })
      }
      if (action === "rpc" || action === "stream") {
        const endpoint = url.searchParams.get("endpoint") || ""
        if (!/^[A-Za-z0-9_$.-]+\/[A-Za-z0-9_$.-]+$/.test(endpoint)) throw new Error("endpoint 格式非法")
        let raw = ""
        for await (const c of req) raw += c
        const args = raw ? JSON.parse(raw) : {}
        if (action === "rpc") return sendJson(res, 200, { ok: true, endpoint, value: await callRpc(conf, endpoint, args) })
        await getCookie(conf)
        return sendJson(res, 200, { ok: true, endpoint, ...(await streamRpc(conf, endpoint, args)) })
      }
      if (action === "sessions") return sendJson(res, 200, { ok: true, value: await callRpc(conf, "session/list", { _request: {} }) })
      if (action === "schedules") return sendJson(res, 200, { ok: true, value: await callRpc(conf, "schedule/list", { request: {} }) })
      if (action === "plugins") return sendJson(res, 200, { ok: true, value: await callRpc(conf, "pluginInventory/list", {}) })
      // SIYI-PCMODEL-20261128: PC model catalog + switching
      if (action === "models") {
        const value = await callRpc(conf, "session/modelCatalog", {})
        return sendJson(res, 200, { ok: true, value })
      }
      if (action === "select-model") {
        let raw = ""
        for await (const c of req) raw += c
        const body = raw ? JSON.parse(raw) : {}
        const request = { sessionId: String(body.sessionId || ""), provider: String(body.provider || ""), model: String(body.model || "") }
        if (body.reasoningEffort) request.reasoningEffort = String(body.reasoningEffort)
        const value = await callRpc(conf, "session/selectModel", { request })
        return sendJson(res, 200, { ok: true, value })
      }
      // SIYI-PCCREATE-20261128: create a new session on the PC side
      if (action === "create") {
        let raw = ""
        for await (const c of req) raw += c
        const body = raw ? JSON.parse(raw) : {}
        const args = { request: {} }
        if (body.cwd) args.request.cwd = String(body.cwd)
        const value = await callRpc(conf, "session/create", args)
        return sendJson(res, 200, { ok: true, value })
      }
      // SIYI-PCHIST-20261128：读 PC 某会话的历史消息（自动探测 throughSeq）
      if (action === "history") {
        const sessionId = url.searchParams.get("sessionId") || ""
        if (!/^session-[\w-]+$/.test(sessionId)) throw new Error("sessionId 非法")
        const maxMessages = Math.min(Number(url.searchParams.get("limit")) || 30, 100)
        let throughSeq = 99999999, lastErr = ""
        for (let i = 0; i < 12; i++) {
          try {
            const v = await callRpc(conf, "session/page", { request: { address: { kind: "session", sessionId }, throughSeq, maxMessages } })
            return sendJson(res, 200, { ok: true, sessionId, throughSeq, value: v })
          } catch (e) {
            const m = String(e.message || "")
            const hit = m.match(/exceeds (\d+)/)
            if (!hit) { lastErr = m; break }
            throughSeq = Number(hit[1])
          }
        }
        throw new Error(lastErr || "读取历史失败")
      }
      // SIYI-PCCONTROL-20261128：让 PC DSH 执行一条指令（PC 端 DSH 自带工具操控电脑）
      if (action === "command") {
        let raw = ""
        for await (const c of req) raw += c
        const body = raw ? JSON.parse(raw) : {}
        const text = String(body.text || body.command || "").trim()
        if (!text) throw new Error("缺少 text/command")
        const sessionId = body.sessionId || "session-8d76b0d9-9089-4a62-a4ec-cb8f56c72954"
        const request = {
          sessionId,
          requestId: "pcbridge-" + randomUUID(),
          mode: "queue",
          content: [{ type: "text", text }],
          ...(body.clientTimeZone ? { clientTimeZone: body.clientTimeZone } : {}),
        }
        const value = await callRpc(conf, "session/prompt", { request })
        return sendJson(res, 200, { ok: true, accepted: value?.accepted === true, sessionId, value })
      }
      return sendJson(res, 404, { ok: false, error: "unknown action: " + action })
    } catch (e) { return sendJson(res, 200, { ok: false, error: e.message }) }
  }

  ws.register({ kind: "prefix", path: PREFIX, handler })
  LOG("已挂载 /siyi-pc/* (status/rpc/stream/sessions/schedules/plugins)")
}
