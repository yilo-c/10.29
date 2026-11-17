// 验证运行中 siyi-vault.js 的 title 类型修复
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-vj-'))
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
fs.writeFileSync(probeFile, src + '\n' + "".trim() + ('\nexport { handleList, handleGet, parseMd }'), 'utf8')
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

const ULID = '01M7KJWVWBB7W10TQKVFCGSWXS'
function writeRaw(id, fm, body = '') {
  fs.writeFileSync(path.join(VAULT, '2026', '2026-11', id + '.md'),
    '---\n' + fm.join('\n') + '\n---\n' + body, 'utf8')
}

tt('parseMd 把 title: 1 解析为字符串', () => {
  const { meta } = mod.parseMd('---\nid: ' + ULID + '\ntitle: 1\n---\n1')
  eq(meta.title, '1', 'title')
  eq(typeof meta.title, 'string', 'type')
})

tt('搜索不因数字 title 崩溃', () => {
  writeRaw(ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []', 'status: open'], '1')
  const r = mod.handleList(new URL('http://x/?q=1'))
  eq(typeof r.total, 'number', 'total')
})

tt('搜索其他词不崩溃', () => {
  const r = mod.handleList(new URL('http://x/?q=知识'))
  eq(typeof r.total, 'number', 'total')
})

tt('列表 title 为字符串', () => {
  const r = mod.handleList(new URL('http://x/?limit=100'))
  for (const it of r.items) eq(typeof it.title, 'string', it.id)
})

tt('get 返回 title 为字符串', () => {
  const g = mod.handleGet(new URL('http://x/?id=' + ULID))
  eq(typeof g.meta.title, 'string', 'type')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
