// 回归：frontmatter 里 title 是纯数字时不能崩溃
//
// 这个 bug 是 E5 真实工具循环发现的：
// 86 项测试全用字符串标题，漏掉了真实数据里的 title: 1。
// 教训：测试要用真实数据的形态，不能只构造理想输入。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createVaultService, parseNote, buildNote } from '../../core/vault/service.mjs'

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

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-title-'))

function freshVault() {
  return createVaultService({ root: fs.mkdtempSync(path.join(TMP, 'v-')) })
}

/** 直接写一个 frontmatter，绕过 buildNote，模拟真实脏数据。 */
function writeRaw(vault, id, frontmatterLines, body = '') {
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  const dir = path.join(vault.root, y, ym)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, id + '.md'), '---\n' + frontmatterLines.join('\n') + '\n---\n' + body, 'utf8')
}

const ULID = '01M7KJWVWBB7W10TQKVFCGSWXS'

tt('title: 1 被解析为字符串而非数字', () => {
  const text = '---\nid: ' + ULID + '\ntitle: 1\nkind: task\n---\n1'
  const { meta } = parseNote(text)
  eq(meta.title, '1', 'title')
  eq(typeof meta.title, 'string', 'type')
})

tt('搜索不因数字 title 崩溃', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []', 'sensitivity: personal'], '1')
  const r = v.list({ q: '1' })
  eq(typeof r.total, 'number', 'total-is-number')
})

tt('搜索其他关键词时不因脏数据崩溃', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []'], '1')
  v.create({ title: '知识图谱' })
  const r = v.list({ q: '知识' })
  eq(r.total, 1, 'found-one')
})

tt('列表返回的 title 永远是字符串', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []'], '1')
  v.create({ title: '正常标题' })
  const r = v.list({})
  for (const it of r.items) {
    eq(typeof it.title, 'string', it.id + '.title')
  }
})

tt('get 返回的 meta.title 是字符串', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []'], '1')
  const g = v.get(ULID)
  eq(g.meta.title, '1', 'title')
  eq(typeof g.meta.title, 'string', 'type')
})

tt('数字型 kind 也归一为字符串', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: 1', 'title: 正常', 'tags: []'], '')
  const r = v.list({})
  eq(typeof r.items[0].kind, 'string', 'kind-type')
})

tt('数字型 tags 元素归一为字符串', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: note', 'title: 正常', 'tags: [1, 2]'], '')
  const r = v.list({})
  for (const t of r.items[0].tags) eq(typeof t, 'string', 'tag-type')
})

tt('stats 能处理脏数据', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['schema: 1', 'id: ' + ULID, 'kind: task', 'title: 1', 'tags: []', 'status: open'], '1')
  v.create({ title: '正常', kind: 'note' })
  const st = v.stats()
  eq(st.total, 2, 'total')
  eq(typeof st.byKind, 'object', 'byKind')
})

tt('真实 vault 全库搜索不崩溃', () => {
  const real = createVaultService({ root: '/storage/emulated/0/思忆集/vault' })
  const r = real.list({ q: '知识' })
  eq(typeof r.total, 'number', 'total')
  eq(r.total >= 1, true, 'has-results')
})

tt('真实 vault 全库列表无类型问题', () => {
  const real = createVaultService({ root: '/storage/emulated/0/思忆集/vault' })
  const r = real.list({ limit: 1000 })
  eq(r.total > 0, true, 'nonempty')
  for (const it of r.items) {
    eq(typeof it.title, 'string', it.id)
    eq(typeof it.kind, 'string', it.id)
  }
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
