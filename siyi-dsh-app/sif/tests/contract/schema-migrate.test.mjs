// L1 数据层：schema 版本迁移框架
//
// 为什么必须测：
//   这是数据契约的版本闸门。迁移逻辑错了会静默破坏笔记。
//   必须验证：正常迁移、缺失补齐、拒绝降级、拒绝无路径、幂等。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import {
  CURRENT_SCHEMA, readSchemaVersion, planMigration, migrateMeta,
  surveyVault, normalizeMissingSchema, MigrationError,
} from '../../core/vault/schema-migrate.mjs'
import { parseNote, buildNote, createVaultService } from '../../core/vault/service.mjs'

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
function throwsCode(fn, code, label) {
  try { fn() } catch (e) {
    if (e.code !== code) throw new Error(`${label}: expected ${code}, got ${e.code}`)
    return e
  }
  throw new Error(`${label}: expected ${code}, got no throw`)
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-mig-'))
const ULID = '01M7KJWVWBB7W10TQKVFCGSWXS'

function makeVault(notes) {
  const root = fs.mkdtempSync(path.join(TMP, 'v-'))
  const d = new Date()
  const y = String(d.getFullYear())
  const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
  const dir = path.join(root, y, ym)
  fs.mkdirSync(dir, { recursive: true })
  for (const n of notes) {
    fs.writeFileSync(path.join(dir, n.id + '.md'),
      '---\n' + n.fm.join('\n') + '\n---\n' + (n.body || ''), 'utf8')
  }
  return root
}

// ── 版本读取 ─────────────────────────────────────────────
tt('读取合法版本', () => {
  eq(readSchemaVersion({ schema: 1 }), 1, 'v1')
  eq(readSchemaVersion({ schema: '2' }), 2, 'string-coerce')
})

tt('缺失或非法版本返回 null', () => {
  eq(readSchemaVersion({}), null, 'missing')
  eq(readSchemaVersion({ schema: null }), null, 'null')
  eq(readSchemaVersion({ schema: 'abc' }), null, 'nan')
  eq(readSchemaVersion({ schema: 0 }), null, 'zero')
  eq(readSchemaVersion({ schema: -1 }), null, 'negative')
  eq(readSchemaVersion({ schema: 1.5 }), null, 'float')
})

// ── 迁移计划 ─────────────────────────────────────────────
tt('当前版本无需迁移', () => {
  const p = planMigration(CURRENT_SCHEMA)
  eq(p.ok, true, 'ok')
  eq(p.reason, 'UP_TO_DATE', 'reason')
  eq(p.steps, [], 'steps')
})

tt('缺失 schema 不可自动迁移', () => {
  const p = planMigration(null)
  eq(p.ok, false, 'ok')
  eq(p.reason, 'MISSING_SCHEMA', 'reason')
})

tt('高于当前版本拒绝降级解读', () => {
  const p = planMigration(CURRENT_SCHEMA + 1)
  eq(p.ok, false, 'ok')
  eq(p.reason, 'NEWER_THAN_SUPPORTED', 'reason')
  eq(p.detail.includes('拒绝降级'), true, 'detail')
})

tt('低版本但无迁移函数时明确拒绝', () => {
  // 当前 MIGRATIONS 为空，从 0 开始会被 null 挡掉；
  // 这里验证「有低版本但缺函数」的路径
  const p = planMigration(CURRENT_SCHEMA - 1)
  if (CURRENT_SCHEMA - 1 < 1) {
    // CURRENT_SCHEMA 是 1 时，0 不是合法版本
    eq(p.ok, false, 'ok')
  } else {
    eq(p.reason, 'NO_MIGRATION_PATH', 'reason')
  }
})

// ── 迁移执行 ─────────────────────────────────────────────
tt('已是最新版本时 migrateMeta 不改动', () => {
  const meta = { schema: 1, id: ULID, title: 'x' }
  const r = migrateMeta(meta)
  eq(r.changed, false, 'changed')
  eq(r.meta.title, 'x', 'title')
})

tt('缺失 schema 时 migrateMeta 抛错', () => {
  throwsCode(() => migrateMeta({ id: ULID }), 'MISSING_SCHEMA', 'missing')
})

tt('高版本 migrateMeta 抛错', () => {
  throwsCode(() => migrateMeta({ schema: 99 }), 'NEWER_THAN_SUPPORTED', 'newer')
})

// ── 普查 ─────────────────────────────────────────────────
tt('普查统计版本分布', () => {
  const root = makeVault([
    { id: ULID, fm: ['schema: 1', 'id: ' + ULID, 'title: A', 'kind: note'] },
    { id: '01M7KJWVWBB7W10TQKVFCGSWXT', fm: ['schema: 1', 'id: 01M7KJWVWBB7W10TQKVFCGSWXT', 'title: B', 'kind: note'] },
  ])
  const s = surveyVault({ vaultRoot: root, parseNote })
  eq(s.total, 2, 'total')
  eq(s.byVersion['1'], 2, 'v1-count')
  eq(s.missing, 0, 'missing')
})

tt('普查发现缺失 schema 的笔记', () => {
  const root = makeVault([
    { id: ULID, fm: ['id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  const s = surveyVault({ vaultRoot: root, parseNote })
  eq(s.missing, 1, 'missing')
})

tt('普查排除 .siyi 目录（回收站不是正式笔记）', () => {
  const root = makeVault([
    { id: ULID, fm: ['schema: 1', 'id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  // 放一篇到回收站
  const trash = path.join(root, '.siyi', 'trash')
  fs.mkdirSync(trash, { recursive: true })
  fs.writeFileSync(path.join(trash, '2026-11-01T00-00-00Z__' + ULID + '.md'),
    '---\nschema: 1\nid: ' + ULID + '\ntitle: trashed\n---\n', 'utf8')
  const s = surveyVault({ vaultRoot: root, parseNote })
  eq(s.total, 1, 'trash-excluded')
})

tt('普查排除非 ULID 命名的 md（如 README）', () => {
  const root = makeVault([
    { id: ULID, fm: ['schema: 1', 'id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  fs.writeFileSync(path.join(root, 'README.md'), '# readme\n', 'utf8')
  const s = surveyVault({ vaultRoot: root, parseNote })
  eq(s.total, 1, 'readme-excluded')
})

// ── 规范化 ───────────────────────────────────────────────
tt('dryRun 只报告不写入', () => {
  const root = makeVault([
    { id: ULID, fm: ['id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  const before = fs.readFileSync(
    path.join(root, String(new Date().getFullYear()),
      String(new Date().getFullYear()) + '-' + String(new Date().getMonth() + 1).padStart(2, '0'),
      ULID + '.md'), 'utf8')
  const changed = normalizeMissingSchema({ vaultRoot: root, parseNote, buildNote, dryRun: true })
  eq(changed.length, 1, 'reported')
  const after = fs.readFileSync(
    path.join(root, String(new Date().getFullYear()),
      String(new Date().getFullYear()) + '-' + String(new Date().getMonth() + 1).padStart(2, '0'),
      ULID + '.md'), 'utf8')
  eq(after, before, 'unchanged')
})

tt('实际写入会补上 schema 字段', () => {
  const root = makeVault([
    { id: ULID, fm: ['id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  normalizeMissingSchema({ vaultRoot: root, parseNote, buildNote, dryRun: false })
  const s = surveyVault({ vaultRoot: root, parseNote })
  eq(s.missing, 0, 'no-missing')
  eq(s.byVersion['1'], 1, 'v1')
})

tt('规范化幂等：已补齐的不再改', () => {
  const root = makeVault([
    { id: ULID, fm: ['schema: 1', 'id: ' + ULID, 'title: A', 'kind: note'] },
  ])
  const changed = normalizeMissingSchema({ vaultRoot: root, parseNote, buildNote, dryRun: false })
  eq(changed.length, 0, 'no-change')
})

// ── 真实 vault ───────────────────────────────────────────
tt('真实 vault 全部 schema v1，无缺失', () => {
  const s = surveyVault({ vaultRoot: '/storage/emulated/0/思忆集/vault', parseNote })
  eq(s.total > 50, true, 'count')
  eq(s.missing, 0, 'no-missing')
  eq(s.newer, 0, 'no-newer')
  eq(s.blocked.length, 0, 'no-blocked')
})

tt('真实 vault 的 list 能读到 schema', () => {
  const v = createVaultService({ root: '/storage/emulated/0/思忆集/vault' })
  const r = v.list({ limit: 1000, includeSensitive: true })
  for (const i of r.items) {
    if (i.schema === null) throw new Error(i.id + ' schema 为 null')
  }
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
