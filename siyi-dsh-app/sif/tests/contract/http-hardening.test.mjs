// T10 HTTP 安全：Host/Origin/Content-Type/限流 四道闸门
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-hh-'))
const VAULT = path.join(TMP, 'vault')
fs.mkdirSync(path.join(VAULT, '2026', '2026-11'), { recursive: true })

let src = fs.readFileSync(SRC, 'utf8')
src = src.replace(/const VAULT = '[^']*'/, `const VAULT = ${JSON.stringify(VAULT)}`)
const cut = src.indexOf('// ── 路由分发')
if (cut > 0) src = src.slice(0, cut)
src = src.replace(/export function apply[\s\S]*$/, '')
src = src.replace(/export const name = 'siyi-vault'/, '')
src = src.replace(/export const inject = \['tools'\]/, '')

// 写到真实临时文件再 import：插件里有绝对路径 import，
// data: URL 无法解析模块路径（实测 ERR_UNSUPPORTED_RESOLVE_REQUEST）。
const probeFile = path.join(TMP, 'probe.mjs')
fs.writeFileSync(probeFile, src + '\n' + "".trim() + ('\nexport { checkRequest, isLoopbackHost, originAllowed, LOOPBACK_HOSTS, WRITE_ACTIONS }'), 'utf8')
const mod = await import('file://' + probeFile)

let pass = 0, fail = 0
const lines = []
function tt(name, fn) {
  try { fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error(`${label}: expected ${y}, got ${x}`)
}

/** 构造最小 req 桩 */
function req(headers) {
  return { headers: headers || {}, method: 'GET' }
}

// ── Host 闸门 ─────────────────────────────────────────────
tt('回环 Host 识别：127.0.0.1:3180', () => eq(mod.isLoopbackHost('127.0.0.1:3180'), true, 'ok'))
tt('回环 Host 识别：localhost:3180', () => eq(mod.isLoopbackHost('localhost:3180'), true, 'ok'))
tt('回环 Host 识别：[::1]:3180', () => eq(mod.isLoopbackHost('[::1]:3180'), true, 'ok'))
tt('非回环 Host 拒绝：evil.com', () => eq(mod.isLoopbackHost('evil.com:3180'), false, 'reject'))
tt('非回环 Host 拒绝：192.168.1.5', () => eq(mod.isLoopbackHost('192.168.1.5:3180'), false, 'reject'))
tt('非回环 Host 拒绝：空', () => eq(mod.isLoopbackHost(''), false, 'reject'))

tt('Host 非回环时请求被拒绝', () => {
  const d = mod.checkRequest(req({ host: 'evil.com:3180' }), 'list')
  eq(d !== null, true, 'denied')
  eq(d.code, 403, 'status')
  eq(d.body.code, 'FORBIDDEN_HOST', 'code')
})

// ── Origin 闸门 ───────────────────────────────────────────
tt('无 Origin 视为非浏览器客户端（放行）', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180' }), 'list')
  eq(d, null, 'allowed')
})

tt('同源 Origin 放行', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', origin: 'http://127.0.0.1:3180' }), 'list')
  eq(d, null, 'allowed')
})

tt('跨站 Origin 拒绝', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', origin: 'https://evil.example.com' }), 'list')
  eq(d !== null, true, 'denied')
  eq(d.body.code, 'FORBIDDEN_ORIGIN', 'code')
})

tt('跨站 Origin 写操作也拒绝', () => {
  const d = mod.checkRequest(req({
    host: '127.0.0.1:3180',
    origin: 'https://evil.example.com',
    'content-type': 'application/json',
  }), 'save')
  eq(d.body.code, 'FORBIDDEN_ORIGIN', 'code')
})

// ── Content-Type 闸门（CSRF）─────────────────────────────
tt('写操作 text/plain 被拒（表单 CSRF）', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'text/plain' }), 'save')
  eq(d !== null, true, 'denied')
  eq(d.body.code, 'UNSUPPORTED_MEDIA_TYPE', 'code')
})

tt('写操作 x-www-form-urlencoded 被拒', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'application/x-www-form-urlencoded' }), 'save')
  eq(d.body.code, 'UNSUPPORTED_MEDIA_TYPE', 'code')
})

tt('写操作 multipart 被拒', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'multipart/form-data' }), 'delete')
  eq(d.body.code, 'UNSUPPORTED_MEDIA_TYPE', 'code')
})

tt('写操作 application/json 放行', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'application/json' }), 'save')
  eq(d, null, 'allowed')
})

tt('upload 允许图片类型', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'image/png' }), 'upload')
  eq(d, null, 'allowed')
})

tt('upload 拒绝表单类型', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180', 'content-type': 'multipart/form-data' }), 'upload')
  eq(d.body.code, 'UNSUPPORTED_MEDIA_TYPE', 'code')
})

// ── 读操作不受 Content-Type 限制 ─────────────────────────
tt('读操作不限 Content-Type', () => {
  const d = mod.checkRequest(req({ host: '127.0.0.1:3180' }), 'list')
  eq(d, null, 'allowed')
})

// ── 限流 ─────────────────────────────────────────────────
tt('写操作超过每分钟上限被限流', () => {
  // 前 60 次通过
  for (let i = 0; i < 60; i++) {
    const d = mod.checkRequest(req({ host: '127.0.0.1:3199', 'content-type': 'application/json' }), 'save')
    if (d) throw new Error('第 ' + (i + 1) + ' 次就被拒: ' + d.body.code)
  }
  // 第 61 次拒绝
  const d = mod.checkRequest(req({ host: '127.0.0.1:3199', 'content-type': 'application/json' }), 'save')
  eq(d !== null, true, 'denied')
  eq(d.body.code, 'RATE_LIMITED', 'code')
})

tt('限流按 host+action 隔离', () => {
  // 换个 host 不受上一个影响
  const d = mod.checkRequest(req({ host: 'localhost:3199', 'content-type': 'application/json' }), 'save')
  eq(d, null, 'allowed')
})

// ── 闸门覆盖所有写操作 ───────────────────────────────────
tt('所有写操作都在 WRITE_ACTIONS 里', () => {
  for (const a of ['save', 'delete', 'upload', 'folder-create', 'chat']) {
    eq(mod.WRITE_ACTIONS.has(a), true, a)
  }
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
