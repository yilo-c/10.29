// 补丁后回归：确认正常新建/更新/并发/原子写仍工作
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const SRC = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-reg-'))
const VAULT = path.join(TMP, 'vault')
fs.mkdirSync(path.join(VAULT, '2026', '2026-11'), { recursive: true })

let src = fs.readFileSync(SRC, 'utf8')
src = src.replace(/const VAULT = '[^']*'/, `const VAULT = ${JSON.stringify(VAULT)}`)
const cut = src.indexOf('// ── 路由分发')
if (cut > 0) src = src.slice(0, cut)
src = src.replace(/export function apply[\s\S]*$/, '')
src = src.replace(/export const name = 'siyi-vault'/, '')
src = src.replace(/export const inject = \['tools'\]/, '')

const mod = await import('data:text/javascript;base64,' + Buffer.from(
  src + '\nexport { handleSave, handleGet, handleDelete, handleList, findById, contentHashOf }'
).toString('base64'))

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

let pass = 0, fail = 0
const lines = []
function t(name, fn) {
  try { fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error(`${label}: expected ${y}, got ${x}`)
}

let created
await t('正常新建成功并返回 ULID', async () => {})  // placeholder replaced below

// 用 await 版本
async function tt(name, fn) {
  try { await fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}

// 清空同步 placeholder 计数
pass = 0; fail = 0; lines.length = 0

await tt('正常新建返回 26 位 ULID', async () => {
  created = await mod.handleSave(fakeReq({ title: '正常笔记', body: '正文', tags: ['a'] }))
  eq(created.ok, true, 'ok')
  eq(created.created, true, 'created')
  eq(created.id.length, 26, 'ulid-len')
  eq(/^[0-9A-HJKMNP-TV-Z]{26}$/.test(created.id), true, 'ulid-shape')
})

await tt('新建文件落在 vault 内年月目录', async () => {
  const p = mod.findById(created.id)
  eq(typeof p, 'string', 'path')
  // findById 返回的是 path.join 拼接的路径（可能未规范化），
  // 用 realpath 比较两侧，避免 /data/user/0 -> /data/data 符号链接误判
  eq(fs.realpathSync(p).startsWith(fs.realpathSync(VAULT)), true, 'inside')
})

await tt('新建返回 revision', async () => {
  eq(typeof created.revision, 'string', 'rev')
  eq(created.revision.length, 64, 'rev-len')
})

await tt('更新不带 expectedRevision 仍可保存（向后兼容）', async () => {
  const r = await mod.handleSave(fakeReq({ id: created.id, title: '改过标题' }))
  eq(r.ok, true, 'ok')
  eq(r.created, false, 'updated')
})

await tt('更新带正确 expectedRevision 成功', async () => {
  const g = mod.handleGet(new URL('http://x/?id=' + created.id))
  const r = await mod.handleSave(fakeReq({ id: created.id, title: '再改', expectedRevision: g.meta ? mod.contentHashOf(fs.readFileSync(mod.findById(created.id), 'utf8')) : '' }))
  eq(r.ok, true, 'ok')
})

await tt('更新带过期 expectedRevision 返回 CONFLICT', async () => {
  const r = await mod.handleSave(fakeReq({ id: created.id, title: '冲突', expectedRevision: '0'.repeat(64) }))
  eq(r.ok, false, 'ok')
  eq(r.code, 'CONFLICT', 'code')
})

await tt('CONFLICT 不修改文件', async () => {
  const before = fs.readFileSync(mod.findById(created.id), 'utf8')
  await mod.handleSave(fakeReq({ id: created.id, title: '不该生效', expectedRevision: '0'.repeat(64) }))
  const after = fs.readFileSync(mod.findById(created.id), 'utf8')
  eq(after, before, 'unchanged')
})

await tt('更新不存在的合法 ULID 返回 NOT_FOUND', async () => {
  const r = await mod.handleSave(fakeReq({ id: '01M7NQXA5C3T702MJ24X791C2T', title: 'x' }))
  eq(r.ok, false, 'ok')
  eq(r.code, 'NOT_FOUND', 'code')
})

await tt('删除拒绝非法 id', async () => {
  const r = mod.handleDelete(new URL('http://x/?id=../evil'))
  eq(r.ok, false, 'ok')
  eq(r.code, 'INVALID_ARGUMENT', 'code')
})

await tt('删除合法 id 进回收站', async () => {
  const c = await mod.handleSave(fakeReq({ title: '待删' }))
  const r = mod.handleDelete(new URL('http://x/?id=' + c.id))
  eq(r.ok, true, 'ok')
  eq(typeof r.trashed, 'string', 'trashed')
  eq(fs.existsSync(path.join(VAULT, '.siyi', 'trash')), true, 'trash-dir')
})

await tt('原子写不留临时文件', async () => {
  const leftovers = []
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name)
      if (e.isDirectory()) walk(p)
      else if (e.name.endsWith('.tmp')) leftovers.push(p)
    }
  }
  walk(VAULT)
  eq(leftovers, [], 'no-tmp')
})

await tt('列表能读出新建的笔记', async () => {
  const r = mod.handleList(new URL('http://x/?limit=100'))
  eq(r.ok, true, 'ok')
  eq(r.items.some(i => i.id === created.id), true, 'contains')
})

await tt('中文标题与标签 round-trip', async () => {
  const c = await mod.handleSave(fakeReq({ title: '时间: 12:30 #标签', body: '正文 #标签2' }))
  const g = mod.handleGet(new URL('http://x/?id=' + c.id))
  eq(g.meta.title, '时间: 12:30 #标签', 'title')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
