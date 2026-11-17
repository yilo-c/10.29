// 验证穿越能否完全逃出 vault 本体
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-deep-'))
const VAULT = path.join(TMP, 'a', 'vault')
fs.mkdirSync(path.join(VAULT, '2026', '2026-11'), { recursive: true })

let src = fs.readFileSync(SRC, 'utf8')
src = src.replace(/const VAULT = '[^']*'/, `const VAULT = ${JSON.stringify(VAULT)}`)
const cut = src.indexOf('// ── 路由分发')
if (cut > 0) src = src.slice(0, cut)
src = src.replace(/export function apply[\s\S]*$/, '')
src = src.replace(/export const name = 'siyi-vault'/, '')
src = src.replace(/export const inject = \['tools'\]/, '')

const mod = await import('data:text/javascript;base64,' + Buffer.from(src + '\nexport { handleSave }').toString('base64'))

function fakeReq(obj) {
  const buf = Buffer.from(JSON.stringify(obj), 'utf8')
  const L = {}
  const req = {
    on(e, f) { (L[e] ||= []).push(f); return req },
    resume() {},
    _flush() { for (const f of L.data || []) f(buf); for (const f of L.end || []) f() },
  }
  queueMicrotask(() => req._flush())
  return req
}

const marker = 'DEEP-' + Date.now()
const evilId = '../../../../' + marker
console.log('VAULT  :', VAULT)
console.log('payload:', evilId)
const r = await mod.handleSave(fakeReq({ id: evilId, title: 'deep', body: 'x' }))
console.log('result :', JSON.stringify(r))

const found = []
const walk = (d, depth = 0) => {
  if (depth > 5) return
  let es = []
  try { es = fs.readdirSync(d, { withFileTypes: true }) } catch { return }
  for (const e of es) {
    const p = path.join(d, e.name)
    if (e.isDirectory()) walk(p, depth + 1)
    else if (e.name.includes(marker)) found.push(p)
  }
}
walk(TMP)
console.log('found  :', found.map(p => path.relative(TMP, p)))

const outside = found.filter(p => !p.startsWith(VAULT + path.sep))
console.log(outside.length ? 'CONFIRMED: 逃出 vault 本体' : 'only inside vault')
process.exit(outside.length ? 1 : 0)
