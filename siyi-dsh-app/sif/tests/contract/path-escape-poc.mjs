// T06 回归：证明当前 siyi-vault.js 的 handleSave 存在路径逃逸
//
// 手法：把 VAULT 常量替换成临时目录，然后直接调用 handleSave 的逻辑。
// 不触碰正式 vault，也不启动引擎。

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = process.argv[2] || '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-poc-'))
const VAULT = path.join(TMP, 'vault')
fs.mkdirSync(path.join(VAULT, '2026', '2026-11'), { recursive: true })

// 读源码，替换 VAULT 常量，剥离 webServer 依赖，只取需要的函数
let src = fs.readFileSync(SRC, 'utf8')
src = src.replace(/const VAULT = '[^']*'/, `const VAULT = ${JSON.stringify(VAULT)}`)
src = src.replace(/const ASSETS = path\.join\(VAULT, 'assets'\)/, `const ASSETS = path.join(VAULT, 'assets')`)

// 只保留工具函数和 handleSave，去掉 apply 路由
const cut = src.indexOf('// ── 路由分发')
if (cut > 0) src = src.slice(0, cut)
src = src.replace(/export function apply[\s\S]*$/, '')
src = src.replace(/export const name = 'siyi-vault'/, '')
src = src.replace(/export const inject = \['tools'\]/, '')

const mod = await import('data:text/javascript;base64,' + Buffer.from(src + '\nexport { handleSave, VAULT, ulid, buildMd, parseMd }').toString('base64'))
  .catch(e => { console.error('LOAD FAIL:', e.message); process.exit(2) })

// 伪造 req：readBody 读 req 的 data 事件
function fakeReq(obj) {
  const buf = Buffer.from(JSON.stringify(obj), 'utf8')
  const listeners = {}
  const req = {
    on(ev, fn) { (listeners[ev] ||= []).push(fn); return req },
    resume() {},
    _flush() {
      for (const fn of listeners.data || []) fn(buf)
      for (const fn of listeners.end || []) fn()
    },
  }
  queueMicrotask(() => req._flush())
  return req
}

const marker = 'ESCAPED-' + Date.now()
const evilId = '../../' + marker

console.log('VAULT root :', VAULT)
console.log('TMP root   :', TMP)
console.log('payload id :', evilId)

let result
try {
  result = await mod.handleSave(fakeReq({ id: evilId, title: 'path escape poc', body: 'x' }))
} catch (e) {
  console.log('THREW     :', e.message)
}

console.log('handleSave :', JSON.stringify(result))

// 检查是否在 VAULT 外写出了文件
const escaped = []
const walk = (d, depth = 0) => {
  if (depth > 4) return
  let entries = []
  try { entries = fs.readdirSync(d, { withFileTypes: true }) } catch { return }
  for (const e of entries) {
    const p = path.join(d, e.name)
    if (e.isDirectory()) walk(p, depth + 1)
    else if (e.name.includes(marker)) escaped.push(p)
  }
}
walk(TMP)

console.log('')
if (escaped.length > 0) {
  console.log('RESULT: VULNERABLE — 文件写出到 vault 外：')
  for (const p of escaped) console.log('  ' + path.relative(TMP, p))
  process.exit(1)
} else {
  console.log('RESULT: SAFE — 没有文件逃出 vault')
  process.exit(0)
}
