// HTTP 安全改造（幂等）
//
// 实测确认的攻击面（改造前）：
//   1. 无任何认证即可读写全部笔记（curl 直接 200）
//   2. 任意 Origin 的网页可以跨站读取（无 Origin 校验）
//   3. 任意网页可用 text/plain 表单 POST 写入（CSRF）
//
// 改造策略：不引入新依赖、不改引擎，只在插件路由层加四道闸门。
//   1. 只接受回环来源（Host 必须是 127.0.0.1 / localhost）
//   2. Origin 存在时必须与 Host 同源（浏览器发起的跨站请求一律拒绝）
//   3. 写操作要求 Content-Type: application/json（挡掉表单 CSRF）
//   4. 写操作限流（同一来源每分钟上限）

import fs from 'node:fs'
const TARGET = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const MARK = 'SIYI-HTTPHARDEN-20261118'

let s = fs.readFileSync(TARGET, 'utf8')
if (s.includes(MARK)) { console.log('SKIP: 已加固'); process.exit(0) }

// ── 1. 在 sendJson 之前插入安全闸门 ─────────────────────────
const anchor = 'function sendJson(res, code, obj) {'
const guard = `// ── HTTP 安全闸门（${MARK}）──────────────────────────────────────
//
// 为什么必须有：改造前实测 curl 无认证就能读写全部笔记，
// 任意网页也能通过 CSRF 写入。这是本机回环服务最容易忽略的攻击面。
//
// 四道闸门，都不依赖引擎改动：
//   1. Host 必须是回环地址（挡 DNS rebinding）
//   2. Origin 存在时必须同源（挡浏览器跨站）
//   3. 写操作必须是 application/json（挡表单 CSRF）
//   4. 写操作限流（挡批量破坏）
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])
const WRITE_ACTIONS = new Set(['save', 'delete', 'upload', 'folder-create', 'chat'])
const RATE_LIMIT_PER_MIN = 60

const writeHits = new Map()

/** 提取 host（不含端口）并判断是否回环。 */
function isLoopbackHost(hostHeader) {
  if (!hostHeader) return false
  const h = String(hostHeader).trim().toLowerCase()
  // 去掉端口；IPv6 形如 [::1]:3180
  let host = h
  if (h.startsWith('[')) {
    const end = h.indexOf(']')
    if (end > 0) host = h.slice(0, end + 1)
  } else {
    const colon = h.lastIndexOf(':')
    if (colon > 0 && h.indexOf(':') === colon) host = h.slice(0, colon)
  }
  return LOOPBACK_HOSTS.has(host)
}

/** Origin 与 Host 同源校验。Origin 缺失时按非浏览器客户端处理（仍需过其他闸门）。 */
function originAllowed(req) {
  const origin = req.headers['origin']
  if (!origin) return true   // curl / node 客户端不带 Origin
  try {
    const o = new URL(String(origin))
    const hostHeader = String(req.headers['host'] || '')
    const oHost = o.host.toLowerCase()
    const hHost = hostHeader.toLowerCase()
    if (oHost === hHost) return true
    // 端口可能省略：比较 hostname
    const oName = o.hostname.toLowerCase()
    let hName = hHost
    if (hName.startsWith('[')) { const e = hName.indexOf(']'); if (e > 0) hName = hName.slice(0, e + 1) }
    else { const c = hName.lastIndexOf(':'); if (c > 0) hName = hName.slice(0, c) }
    return oName === hName
  } catch {
    return false
  }
}

/** 写操作限流：同一 host+action 每分钟上限。 */
function rateLimited(req, action) {
  const key = String(req.headers['host'] || '') + '|' + action
  const now = Date.now()
  const win = writeHits.get(key) || []
  const fresh = win.filter(t => now - t < 60000)
  if (fresh.length >= RATE_LIMIT_PER_MIN) { writeHits.set(key, fresh); return true }
  fresh.push(now)
  writeHits.set(key, fresh)
  return false
}

/**
 * 执行全部闸门。通过返回 null，拒绝返回 { code, body }。
 */
function checkRequest(req, action) {
  // 闸门 1：Host 必须是回环
  if (!isLoopbackHost(req.headers['host'])) {
    return { code: 403, body: { ok: false, error: 'Host 不是回环地址，拒绝访问', code: 'FORBIDDEN_HOST' } }
  }
  // 闸门 2：Origin 同源
  if (!originAllowed(req)) {
    return { code: 403, body: { ok: false, error: '跨站请求被拒绝', code: 'FORBIDDEN_ORIGIN' } }
  }
  // 闸门 3+4 只针对写操作
  if (WRITE_ACTIONS.has(action)) {
    const ct = String(req.headers['content-type'] || '').toLowerCase()
    // chat 允许 application/json；upload 允许图片 MIME
    if (action === 'upload') {
      const okCt = ct.includes('application/json') || ct.startsWith('image/') || ct.startsWith('application/octet-stream')
      if (!okCt) return { code: 415, body: { ok: false, error: 'upload 需要图片或 JSON 类型', code: 'UNSUPPORTED_MEDIA_TYPE' } }
    } else if (!ct.includes('application/json')) {
      // 表单提交（text/plain / x-www-form-urlencoded / multipart）是 CSRF 的典型载体
      return { code: 415, body: { ok: false, error: '写操作必须是 application/json', code: 'UNSUPPORTED_MEDIA_TYPE' } }
    }
    if (rateLimited(req, action)) {
      return { code: 429, body: { ok: false, error: '写入过于频繁，请稍后再试', code: 'RATE_LIMITED' } }
    }
  }
  return null
}

function sendJson(res, code, obj) {`

if (!s.includes(anchor)) { console.error('sendJson ANCHOR NOT FOUND'); process.exit(1) }
s = s.replace(anchor, guard)

// ── 2. 在 handler 里调用闸门 ───────────────────────────────
const oldHandler = `      const rest = url.pathname.slice(PREFIX.length).replace(/^\\//, '')
      const action = rest.replace(/^api\\//, '') || 'stats'
      try {`

const newHandler = `      const rest = url.pathname.slice(PREFIX.length).replace(/^\\//, '')
      const action = rest.replace(/^api\\//, '') || 'stats'

      // ${MARK}：所有请求先过安全闸门
      const denied = checkRequest(req, action)
      if (denied) {
        log('安全闸门拒绝 ' + action + ': ' + denied.body.code)
        return sendJson(res, denied.code, denied.body)
      }

      try {`

if (!s.includes(oldHandler)) { console.error('HANDLER BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldHandler, newHandler)

// ── 3. 安全响应头 ─────────────────────────────────────────
const oldSend = `function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  res.end(body)
}`

const newSend = `function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    // ${MARK}：敏感响应禁止被缓存、禁止被 iframe 嵌入、禁止 MIME 嗅探
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
  })
  res.end(body)
}`

if (!s.includes(oldSend)) { console.error('sendJson BODY NOT FOUND'); process.exit(1) }
s = s.replace(oldSend, newSend)

fs.writeFileSync(TARGET, s, 'utf8')
console.log('PATCHED: ' + TARGET)
