// T06 路径与沙箱 + T07 Markdown 并发与原子性
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { createVaultService, VaultError, parseNote, buildNote } from '../../core/vault/service.mjs'
import { assertInside, assertUlid, newUlid, isUlid } from '../../core/vault/paths.mjs'
import { writeFileAtomic, contentHash, sweepTempFiles } from '../../core/vault/atomic.mjs'

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
function throwsCode(fn, code, label) {
  try { fn() } catch (e) {
    if (e.code !== code) throw new Error(`${label}: expected ${code}, got ${e.code} (${e.message})`)
    return e
  }
  throw new Error(`${label}: expected ${code}, got no throw`)
}

// 隔离测试 vault，不碰正式数据
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-vault-'))
const ROOT = path.join(TMP, 'vault')
fs.mkdirSync(ROOT, { recursive: true })

function freshVault() {
  const sub = fs.mkdtempSync(path.join(TMP, 'v-'))
  return createVaultService({ root: sub })
}

// ── T06 路径与沙箱 ─────────────────────────────────────────
t('ULID 校验拒绝 ../', () => throwsCode(() => assertUlid('../../etc/passwd'), 'INVALID_ARGUMENT', 'dotdot'))
t('ULID 校验拒绝绝对路径', () => throwsCode(() => assertUlid('/etc/passwd'), 'INVALID_ARGUMENT', 'abs'))
t('ULID 校验拒绝反斜杠', () => throwsCode(() => assertUlid('..\\..\\win'), 'INVALID_ARGUMENT', 'backslash'))
t('ULID 校验拒绝 NUL', () => throwsCode(() => assertUlid('01M7NQXA5C3T702MJ24X791C2T\u0000'), 'INVALID_ARGUMENT', 'nul'))
t('ULID 校验拒绝短串', () => throwsCode(() => assertUlid('abc'), 'INVALID_ARGUMENT', 'short'))
t('ULID 校验接受合法值', () => { const id = newUlid(); eq(isUlid(id), true, 'valid'); eq(assertUlid(id), id, 'return') })

t('assertInside 拒绝词法越界', () => {
  throwsCode(() => assertInside(ROOT, '../outside.md'), 'INVALID_ARGUMENT', 'lexical')
})

t('assertInside 接受根内路径', () => {
  const p = assertInside(ROOT, '2026/2026-11/x.md')
  eq(p.startsWith(fs.realpathSync(ROOT)), true, 'inside')
})

t('符号链接目录逃逸被拒绝', () => {
  const v = freshVault()
  const outside = fs.mkdtempSync(path.join(TMP, 'outside-'))
  const link = path.join(v.root, 'evil')
  try { fs.symlinkSync(outside, link, 'dir') } catch { return }  // 平台不支持则跳过
  throwsCode(() => assertInside(v.root, 'evil/x.md'), 'INVALID_ARGUMENT', 'symlink')
})

t('新建拒绝外部 id（原漏洞点）', () => {
  const v = freshVault()
  throwsCode(() => v.create({ id: '../../../tmp/evil', title: 'x' }), 'INVALID_ARGUMENT', 'external-id')
})

t('get 拒绝非法 id', () => {
  const v = freshVault()
  throwsCode(() => v.get('../../etc/passwd'), 'INVALID_ARGUMENT', 'get-dotdot')
})

t('更新拒绝非法 id', () => {
  const v = freshVault()
  throwsCode(() => v.update('../x', {}, { expectedRevision: '0'.repeat(64) }), 'INVALID_ARGUMENT', 'update-dotdot')
})

// ── T07 Markdown round-trip ────────────────────────────────
t('含冒号的标题 round-trip 不变义', () => {
  const v = freshVault()
  const title = '时间: 12:30 记录'
  const c = v.create({ title })
  eq(v.get(c.id).meta.title, title, 'title')
})

t('含井号的标题 round-trip', () => {
  const v = freshVault()
  const title = 'C# 与 F#'
  const c = v.create({ title })
  eq(v.get(c.id).meta.title, title, 'title')
})

t('含引号的标题 round-trip', () => {
  const v = freshVault()
  const title = '他说"你好"'
  const c = v.create({ title })
  eq(v.get(c.id).meta.title, title, 'title')
})

t('含换行的标题不破坏 frontmatter', () => {
  const v = freshVault()
  const title = '第一行\n第二行'
  const c = v.create({ title })
  eq(v.get(c.id).meta.title, title, 'title')
})

t('未知字段被保留', () => {
  const meta = { schema: 2, id: newUlid(), title: 'x', customField: 'keep-me' }
  const text = buildNote(meta, 'body')
  const back = parseNote(text).meta
  eq(back.customField, 'keep-me', 'custom')
})

t('数组字段 round-trip', () => {
  const v = freshVault()
  const c = v.create({ title: 't', tags: ['a', 'b c', 'd:e'] })
  eq(v.get(c.id).meta.tags, ['a', 'b c', 'd:e'], 'tags')
})

t('正文中的 --- 不被误判为 frontmatter 结束', () => {
  const v = freshVault()
  const c = v.create({ title: 't', body: '前文\n\n---\n\n后文' })
  eq(v.get(c.id).body, '前文\n\n---\n\n后文', 'body')
})

// ── T07 revision 并发 ─────────────────────────────────────
t('无 expectedRevision 拒绝更新', () => {
  const v = freshVault()
  const c = v.create({ title: 'a' })
  throwsCode(() => v.update(c.id, { title: 'b' }), 'INVALID_ARGUMENT', 'no-rev')
})

t('过期 revision 返回 CONFLICT', () => {
  const v = freshVault()
  const c = v.create({ title: 'a' })
  v.update(c.id, { title: 'b' }, { expectedRevision: c.revision })
  throwsCode(() => v.update(c.id, { title: 'c' }, { expectedRevision: c.revision }), 'CONFLICT', 'stale')
})

t('两端同 revision 后写必冲突', () => {
  const v = freshVault()
  const c = v.create({ title: 'base' })
  const rev = c.revision
  v.update(c.id, { title: 'deviceA' }, { expectedRevision: rev })
  const err = throwsCode(() => v.update(c.id, { title: 'deviceB' }, { expectedRevision: rev }), 'CONFLICT', 'race')
  eq(typeof err.details.actual, 'string', 'details')
})

t('CONFLICT 不修改文件', () => {
  const v = freshVault()
  const c = v.create({ title: 'base' })
  v.update(c.id, { title: 'first' }, { expectedRevision: c.revision })
  const before = v.get(c.id)
  try { v.update(c.id, { title: 'second' }, { expectedRevision: c.revision }) } catch {}
  const after = v.get(c.id)
  eq(after.meta.title, before.meta.title, 'unchanged')
  eq(after.revision, before.revision, 'rev-unchanged')
})

// ── 原子写 ────────────────────────────────────────────────
t('原子写不留临时文件', () => {
  const dir = fs.mkdtempSync(path.join(TMP, 'aw-'))
  const p = path.join(dir, 'x.md')
  writeFileAtomic(p, 'hello')
  const leftovers = fs.readdirSync(dir).filter(n => n.endsWith('.tmp'))
  eq(leftovers, [], 'no-tmp')
})

t('原子写内容与 hash 一致', () => {
  const dir = fs.mkdtempSync(path.join(TMP, 'aw2-'))
  const p = path.join(dir, 'y.md')
  const text = '思忆集 atomic 测试\n第二行'
  const r = writeFileAtomic(p, text)
  eq(fs.readFileSync(p, 'utf8'), text, 'content')
  eq(r.sha256, contentHash(text), 'hash')
})

t('崩溃遗留临时文件可被清理', () => {
  const dir = fs.mkdtempSync(path.join(TMP, 'sweep-'))
  const stale = path.join(dir, '.x.md.123.abcdef.tmp')
  fs.writeFileSync(stale, 'junk')
  const old = Date.now() - 7200_000
  fs.utimesSync(stale, new Date(old), new Date(old))
  const n = sweepTempFiles(dir, 3600_000)
  eq(n, 1, 'swept')
  eq(fs.existsSync(stale), false, 'removed')
})

// ── 回收站 ────────────────────────────────────────────────
t('删除进回收站而非真删', () => {
  const v = freshVault()
  const c = v.create({ title: 'trash me' })
  const r = v.trash(c.id, { expectedRevision: c.revision })
  eq(typeof r.trashed, 'string', 'receipt')
  throwsCode(() => v.get(c.id), 'NOT_FOUND', 'gone')
  eq(fs.existsSync(path.join(v.root, '.siyi', 'trash')), true, 'trash-dir')
})

t('删除时 revision 不符拒绝', () => {
  const v = freshVault()
  const c = v.create({ title: 'x' })
  throwsCode(() => v.trash(c.id, { expectedRevision: '0'.repeat(64) }), 'CONFLICT', 'trash-conflict')
})

// ── 列表与坏文件隔离 ──────────────────────────────────────
t('坏 frontmatter 不拖垮全库', () => {
  const v = freshVault()
  v.create({ title: 'good' })
  const d = new Date()
  const y = String(d.getFullYear()), ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  const dir = path.join(v.root, y, ym)
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, newUlid() + '.md'), '---\nbad line without colon\n---\nbody')
  const r = v.list()
  eq(r.total, 1, 'only-good')
})

t('secret 笔记默认不出现在列表', () => {
  const v = freshVault()
  v.create({ title: 'public' })
  v.create({ title: 'hidden', sensitivity: 'secret' })
  eq(v.list().total, 1, 'default')
  eq(v.list({ includeSensitive: true }).total, 2, 'include')
})

t('文件夹过滤与 __none__', () => {
  const v = freshVault()
  v.create({ title: 'a', folder: '知识' })
  v.create({ title: 'b' })
  eq(v.list({ folder: '知识' }).total, 1, 'folder')
  eq(v.list({ folder: '__none__' }).total, 1, 'none')
})

t('reconcile 返回笔记数', () => {
  const v = freshVault()
  v.create({ title: 'x' })
  const r = v.reconcile()
  eq(r.notes, 1, 'notes')
})

// ── 输出 ──────────────────────────────────────────────────
console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
console.log('fixture:', TMP)
if (fail > 0) process.exit(1)
