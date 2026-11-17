// Schema 版本迁移（L1 数据层的唯一缺口）
//
// ── 为什么必须有 ──────────────────────────────────────────────
// frontmatter 的 `schema` 字段是数据契约版本号。当前是 1。
// 如果未来要改字段结构（比如加 revision 向量、改 folder 语义），
// 没有迁移机制会发生两件事：
//   1. 旧笔记被新代码按新结构解读，静默丢字段或误读
//   2. 新笔记被旧代码读，遇到不认识的字段可能崩溃或覆盖
//
// ── 设计原则 ──────────────────────────────────────────────────
//   1. 迁移是显式动作，不隐式触发（用户可控）
//   2. 每个版本一个纯函数 migrate(v) -> v+1
//   3. 迁移前必须备份，失败可回滚
//   4. 迁移幂等：已迁移的跳过
//   5. 版本不认识时拒绝写入，不猜
//
// ── 版本历史 ──────────────────────────────────────────────────
//   v1（当前）：schema/id/kind/title/aliases/tags/sensitivity/
//               created/updated/device/source/status/source_id/folder/due
//   未来 v2 示例：加 revision 向量、加 sync 元数据

import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

export const CURRENT_SCHEMA = 1

/** 已知的迁移链。key 是源版本，值是升到下一版的纯函数。 */
const MIGRATIONS = new Map([
  // 示例（尚未启用）：
  // [1, (meta) => ({ ...meta, schema: 2, revisionVector: { device: meta.device || 'unknown', seq: 1 } })],
])

export class MigrationError extends Error {
  constructor(code, message, details) {
    super(message)
    this.name = 'MigrationError'
    this.code = code
    this.details = details
  }
}

/** 读 frontmatter 的 schema 版本；缺失或非法返回 null。 */
export function readSchemaVersion(meta) {
  const v = meta && meta.schema
  if (v === undefined || v === null) return null
  const n = Number(v)
  if (!Number.isInteger(n) || n < 1) return null
  return n
}

/**
 * 计算从 from 到 CURRENT_SCHEMA 需要走的步骤。
 * 返回 { steps: [from, from+1, ...], ok: bool, reason?: string }
 */
export function planMigration(from) {
  if (from === null) {
    return { steps: [], ok: false, reason: 'MISSING_SCHEMA', detail: '笔记缺少 schema 字段' }
  }
  if (from === CURRENT_SCHEMA) {
    return { steps: [], ok: true, reason: 'UP_TO_DATE' }
  }
  if (from > CURRENT_SCHEMA) {
    return {
      steps: [],
      ok: false,
      reason: 'NEWER_THAN_SUPPORTED',
      detail: `笔记 schema ${from} 高于代码支持的 ${CURRENT_SCHEMA}，拒绝降级解读`,
    }
  }
  const steps = []
  let cur = from
  while (cur < CURRENT_SCHEMA) {
    if (!MIGRATIONS.has(cur)) {
      return {
        steps: [],
        ok: false,
        reason: 'NO_MIGRATION_PATH',
        detail: `缺少 v${cur} → v${cur + 1} 的迁移函数`,
      }
    }
    steps.push(cur)
    cur++
  }
  return { steps, ok: true, reason: 'MIGRATABLE' }
}

/** 对单个 meta 执行迁移，返回新 meta（不修改入参）。 */
export function migrateMeta(meta) {
  const from = readSchemaVersion(meta)
  const plan = planMigration(from)
  if (!plan.ok) {
    if (plan.reason === 'UP_TO_DATE') return { meta, changed: false, from, to: from }
    throw new MigrationError(plan.reason, plan.detail || plan.reason, { from, current: CURRENT_SCHEMA })
  }
  // steps 为空说明本来就是最新版本（planMigration 对同版本返回 ok:true + 空 steps）。
  // 这里必须显式判空，否则会落到末尾返回 changed:true，
  // 让「无需迁移」被误报成「已迁移」（实测踩到）。
  if (plan.steps.length === 0) {
    return { meta, changed: false, from, to: from }
  }

  let cur = { ...meta }
  let v = from
  for (const step of plan.steps) {
    cur = MIGRATIONS.get(step)(cur)
    v++
    if (readSchemaVersion(cur) !== v) {
      throw new MigrationError('MIGRATION_BUG', `v${step} 迁移函数未正确设置 schema`, { got: cur.schema, want: v })
    }
  }
  return { meta: cur, changed: true, from, to: v }
}

/**
 * 扫描 vault，统计各版本分布，返回迁移计划摘要。
 * 只读，不改文件。
 */
export function surveyVault({ vaultRoot, parseNote }) {
  const out = { total: 0, byVersion: {}, missing: 0, newer: 0, migratable: 0, blocked: [] }
  const walk = (dir) => {
    let entries = []
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = path.join(dir, e.name)
      // 排除 .siyi（回收站、元数据）与其它点目录：它们不是正式笔记
      if (e.isDirectory()) { if (!e.name.startsWith('.')) walk(p); continue }
      if (!e.name.endsWith('.md')) continue
      // 只统计 ULID 命名的正式笔记；README.md 之类不算
      if (!/^[0-9A-HJKMNP-TV-Z]{26}\.md$/.test(e.name)) continue
      let text
      try { text = fs.readFileSync(p, 'utf8') } catch { continue }
      let meta
      try { meta = parseNote(text).meta } catch { continue }
      out.total++
      const v = readSchemaVersion(meta)
      if (v === null) { out.missing++; out.byVersion['(缺失)'] = (out.byVersion['(缺失)'] || 0) + 1; continue }
      out.byVersion[v] = (out.byVersion[v] || 0) + 1
      const plan = planMigration(v)
      if (plan.reason === 'UP_TO_DATE') continue
      if (plan.ok) out.migratable++
      else {
        if (plan.reason === 'NEWER_THAN_SUPPORTED') out.newer++
        out.blocked.push({ path: path.relative(vaultRoot, p), reason: plan.reason, detail: plan.detail })
      }
    }
  }
  walk(vaultRoot)
  return out
}

/**
 * 补齐缺失的 schema 字段（v1 的规范化动作）。
 *
 * 为什么这不算「迁移」而是「规范化」：
 *   现有 57 篇笔记文件里其实都有 schema: 1，只是 list() 没返回。
 *   真正缺字段的笔记按 v1 补齐即可，不需要版本跳跃。
 *
 * @returns 修改的文件列表
 */
export function normalizeMissingSchema({ vaultRoot, parseNote, buildNote, dryRun = true }) {
  const changed = []
  const walk = (dir) => {
    let entries = []
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const p = path.join(dir, e.name)
      if (e.isDirectory()) { if (!e.name.startsWith('.')) walk(p); continue }
      if (!e.name.endsWith('.md')) continue
      if (!/^[0-9A-HJKMNP-TV-Z]{26}\.md$/.test(e.name)) continue
      let text
      try { text = fs.readFileSync(p, 'utf8') } catch { continue }
      let parsed
      try { parsed = parseNote(text) } catch { continue }
      if (readSchemaVersion(parsed.meta) !== null) continue
      const next = { ...parsed.meta, schema: CURRENT_SCHEMA }
      const out = buildNote(next, parsed.body)
      changed.push(path.relative(vaultRoot, p))
      if (!dryRun) {
        const tmp = p + '.tmp-' + process.pid
        fs.writeFileSync(tmp, out, 'utf8')
        fs.renameSync(tmp, p)
      }
    }
  }
  walk(vaultRoot)
  return changed
}
