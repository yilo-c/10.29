// 验证运行中 siyi-vault.js 接入索引后功能正常
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-pi-'))
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
// data: URL 无法解析相对/绝对模块路径（实测报 ERR_INVALID_URL）。
const probeFile = path.join(TMP, 'probe.mjs')
fs.writeFileSync(probeFile, src + '\nexport { handleList, handleStats, handleFolders, handleTasks, handleSave, handleDelete, getIndexer }\n', 'utf8')
const mod = await import('file://' + probeFile)

let pass = 0, fail = 0
const lines = []
async function tt(name, fn) {
  try { await fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error(`${label}: expected ${y}, got ${x}`)
}

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

await tt('索引器能创建', async () => {
  const idx = mod.getIndexer()
  eq(idx !== null, true, 'created')
})

await tt('空库列表为空', async () => {
  const r = mod.handleList(new URL('http://x/'))
  eq(r.total, 0, 'total')
})

let created
await tt('新建笔记成功', async () => {
  created = await mod.handleSave(fakeReq({ title: '索引测试', body: '内容', tags: ['t'] }))
  eq(created.ok, true, 'ok')
})

await tt('新建后列表立即可见（索引已失效）', async () => {
  const r = mod.handleList(new URL('http://x/'))
  eq(r.total, 1, 'total')
  eq(r.items[0].id, created.id, 'id')
})

await tt('统计正确', async () => {
  const st = mod.handleStats()
  eq(st.total, 1, 'total')
})

await tt('搜索能命中', async () => {
  const r = mod.handleList(new URL('http://x/?q=' + encodeURIComponent('索引')))
  eq(r.total, 1, 'found')
})

await tt('更新后列表反映新标题', async () => {
  await mod.handleSave(fakeReq({ id: created.id, title: '已更新标题' }))
  const r = mod.handleList(new URL('http://x/'))
  eq(r.items[0].title, '已更新标题', 'title')
})

await tt('删除后列表为空', async () => {
  mod.handleDelete(new URL('http://x/?id=' + created.id))
  const r = mod.handleList(new URL('http://x/'))
  eq(r.total, 0, 'total')
})

await tt('文件夹统计不崩', async () => {
  const r = mod.handleFolders()
  eq(r.ok, true, 'ok')
})

await tt('待办列表不崩', async () => {
  const r = mod.handleTasks(new URL('http://x/'))
  eq(r.ok, true, 'ok')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
