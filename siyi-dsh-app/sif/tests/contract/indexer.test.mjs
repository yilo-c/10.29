// T09 索引一致性：增量正确、脏数据隔离、对账、写入后即时可见
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createVaultService, parseNote, extractLinks } from '../../core/vault/service.mjs'
import { createIndexer } from '../../core/vault/indexer.mjs'

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
const sleep = ms => new Promise(r => setTimeout(r, ms))

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-idx-'))
function freshVault() {
  return createVaultService({ root: fs.mkdtempSync(path.join(TMP, 'v-')) })
}
const ULID = '01M7KJWVWBB7W10TQKVFCGSWXS'
const ULID2 = '01M7KJWVWBB7W10TQKVFCGSWXT'

function writeRaw(vault, id, fm, body = '') {
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  const dir = path.join(vault.root, y, ym)
  fs.mkdirSync(dir, { recursive: true })
  const p = path.join(dir, id + '.md')
  fs.writeFileSync(p, '---\n' + fm.join('\n') + '\n---\n' + body, 'utf8')
  return p
}

// ── 基础 ──────────────────────────────────────────────────
tt('索引能扫到所有笔记', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['id: ' + ULID, 'title: A', 'kind: note'])
  writeRaw(v, ULID2, ['id: ' + ULID2, 'title: B', 'kind: task'])
  eq(v.list({}).total, 2, 'total')
})

tt('索引按 mtime 增量，未变不重读', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['id: ' + ULID, 'title: A', 'kind: note'])
  v.list({})
  const before = v.indexer.stats()
  v.list({})
  v.list({})
  const after = v.indexer.stats()
  // incrementalHits 应增长，说明走了缓存
  eq(after.incrementalHits > before.incrementalHits, true, 'cache-hits')
})

tt('外部修改文件后能感知变化', async () => {
  // 同步测试无法 await，用内部同步检查
  const v = freshVault()
  const p = writeRaw(v, ULID, ['id: ' + ULID, 'title: 原始', 'kind: note'])
  eq(v.list({}).items[0].title, '原始', 'before')
  // 改文件并推后 mtime
  const future = new Date(Date.now() + 5000)
  fs.writeFileSync(p, '---\nid: ' + ULID + '\ntitle: 已改\nkind: note\n---\n', 'utf8')
  fs.utimesSync(p, future, future)
  eq(v.list({}).items[0].title, '已改', 'after')
})

tt('删除文件后索引不再返回它', () => {
  const v = freshVault()
  const p = writeRaw(v, ULID, ['id: ' + ULID, 'title: A', 'kind: note'])
  writeRaw(v, ULID2, ['id: ' + ULID2, 'title: B', 'kind: note'])
  eq(v.list({}).total, 2, 'before')
  fs.unlinkSync(p)
  // 触发重新扫描
  const idx = v.indexer
  idx.scan({ force: true, minIntervalMs: 0 })
  eq(v.list({}).total, 1, 'after')
})

// ── 脏数据隔离 ────────────────────────────────────────────
tt('解析失败的文件不拖垮全库', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['id: ' + ULID, 'title: 正常', 'kind: note'])
  // 写一个坏 frontmatter
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  fs.writeFileSync(path.join(v.root, y, ym, ULID2 + '.md'),
    '---\n这行没有冒号\n---\n', 'utf8')
  const r = v.list({})
  eq(r.total, 1, 'only-good')
  const st = v.stats()
  eq(st.index.failures >= 1, true, 'failure-recorded')
})

tt('索引状态暴露 failure 详情', () => {
  const v = freshVault()
  writeRaw(v, ULID, ['id: ' + ULID, 'title: 正常', 'kind: note'])
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  fs.writeFileSync(path.join(v.root, y, ym, ULID2 + '.md'), '---\n坏行\n---\n', 'utf8')
  v.list({})
  const st = v.stats()
  eq(st.index.stale, true, 'stale')
  eq(Array.isArray(st.index.failureDetails), true, 'details')
})

tt('修复坏文件后 failure 消失', () => {
  const v = freshVault()
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  fs.mkdirSync(path.join(v.root, y, ym), { recursive: true })
  const p = path.join(v.root, y, ym, ULID + '.md')
  fs.writeFileSync(p, '---\n坏行\n---\n', 'utf8')
  v.indexer.scan({ force: true, minIntervalMs: 0 })
  eq(v.stats().index.failures, 1, 'has-failure')
  // 修好。外部直接改文件时索引不知道，真实场景由 watcher 或显式 markDirty 通知；
  // 这里模拟那个通知。
  const future = new Date(Date.now() + 5000)
  fs.writeFileSync(p, '---\nid: ' + ULID + '\ntitle: 修好了\nkind: note\n---\n', 'utf8')
  fs.utimesSync(p, future, future)
  v.indexer.markDirty()
  v.list({})
  eq(v.stats().index.failures, 0, 'fixed')
})

// ── 写入后即时可见 ────────────────────────────────────────
tt('create 后立即可查到（不等扫描）', () => {
  const v = freshVault()
  v.list({})
  const c = v.create({ title: '新建的' })
  const r = v.list({})
  eq(r.total, 1, 'visible')
  eq(r.items[0].id, c.id, 'id')
})

tt('update 后立即反映新标题', () => {
  const v = freshVault()
  const c = v.create({ title: '原标题' })
  v.list({})
  v.update(c.id, { title: '新标题' }, { expectedRevision: c.revision })
  eq(v.list({}).items[0].title, '新标题', 'updated')
})

tt('trash 后立即消失', () => {
  const v = freshVault()
  const c = v.create({ title: '待删' })
  eq(v.list({}).total, 1, 'before')
  v.trash(c.id, { expectedRevision: c.revision })
  eq(v.list({}).total, 0, 'after')
})

// ── 对账 ──────────────────────────────────────────────────
tt('reconcile 重建索引并返回状态', () => {
  const v = freshVault()
  v.create({ title: 'A' })
  v.create({ title: 'B' })
  const r = v.reconcile()
  eq(r.notes, 2, 'notes')
  eq(typeof r.index, 'object', 'index-stats')
})

tt('reconcile 能清理崩溃遗留临时文件', () => {
  const v = freshVault()
  v.create({ title: 'A' })
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  const dir = path.join(v.root, y, ym)
  const tmp = path.join(dir, '.x.md.999.abcdef.tmp')
  fs.writeFileSync(tmp, 'junk')
  const old = new Date(Date.now() - 7200_000)
  fs.utimesSync(tmp, old, old)
  const r = v.reconcile()
  eq(r.swept >= 1, true, 'swept')
  eq(fs.existsSync(tmp), false, 'removed')
})

// ── 真实 vault ────────────────────────────────────────────
tt('真实 vault 索引全量正确', () => {
  const v = createVaultService({ root: '/storage/emulated/0/思忆集/vault' })
  v.list({ limit: 1 })
  const st = v.stats()
  eq(st.total > 50, true, 'count')
  eq(st.index.failures, 0, 'no-failures')
  eq(typeof st.index.lastScanMs, 'number', 'scan-time')
})

tt('真实 vault 索引无类型问题', () => {
  const v = createVaultService({ root: '/storage/emulated/0/思忆集/vault' })
  for (const it of v.list({ limit: 1000 }).items) {
    eq(typeof it.title, 'string', it.id + '.title')
    eq(typeof it.kind, 'string', it.id + '.kind')
  }
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
