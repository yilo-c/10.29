#!/usr/bin/env node
// Schema 版本工具（数据契约闸门）
//
// 用法：
//   node scripts/schema-tool.mjs survey           普查版本分布（只读）
//   node scripts/schema-tool.mjs normalize --dry  补齐缺失 schema（演练）
//   node scripts/schema-tool.mjs normalize        补齐缺失 schema（写入，自动备份）
//   node scripts/schema-tool.mjs migrate --dry    执行版本迁移（演练）
//   node scripts/schema-tool.mjs migrate          执行版本迁移（写入，自动备份）
//
// 为什么要有这个工具：
//   frontmatter 的 schema 是数据契约版本。改结构时必须能：
//   1. 先普查有多少篇、什么版本
//   2. 演练一遍看会改哪些
//   3. 备份后执行
//   4. 失败能回滚
//   没有这个工具，未来改字段只能靠手改文件，风险不可控。

import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'
import {
  CURRENT_SCHEMA, surveyVault, normalizeMissingSchema, planMigration,
  migrateMeta, readSchemaVersion, MigrationError,
} from '../core/vault/schema-migrate.mjs'
import { parseNote, buildNote } from '../core/vault/service.mjs'
import { resolveVaultRoot, resolveSifRoot } from '../core/paths.mjs'

const VAULT = resolveVaultRoot()
const SIF = resolveSifRoot()
const BACKUP_ROOT = path.join(SIF, 'backups', 'schema')

const [cmd, ...flags] = process.argv.slice(2)
const dryRun = flags.includes('--dry')

function sha256(buf) {
  return createHash('sha256').update(buf).digest('hex')
}

function cmdSurvey() {
  console.log('=== schema 版本普查 ===')
  console.log('vault:', VAULT)
  console.log('代码支持的当前版本:', CURRENT_SCHEMA)
  console.log('')
  const s = surveyVault({ vaultRoot: VAULT, parseNote })
  console.log('正式笔记总数:', s.total)
  console.log('版本分布:', JSON.stringify(s.byVersion, null, 2))
  console.log('缺失 schema:', s.missing)
  console.log('高于支持版本:', s.newer)
  console.log('可迁移:', s.migratable)
  if (s.blocked.length) {
    console.log('阻塞项:')
    for (const b of s.blocked) console.log('  ', b.path, '->', b.reason, b.detail)
  }
  const healthy = s.missing === 0 && s.newer === 0 && s.blocked.length === 0
  console.log('')
  console.log(healthy ? '状态: 健康，无需操作' : '状态: 需要处理')
  process.exit(healthy ? 0 : 1)
}

function cmdNormalize() {
  console.log('=== 补齐缺失 schema ===')
  console.log('模式:', dryRun ? '演练（不写入）' : '实际写入')
  console.log('')
  const changed = normalizeMissingSchema({
    vaultRoot: VAULT, parseNote, buildNote, dryRun: true,
  })
  if (changed.length === 0) { console.log('没有需要补齐的笔记'); process.exit(0) }
  console.log('需要补齐:', changed.length, '篇')
  for (const f of changed.slice(0, 20)) console.log('  ', f)
  if (changed.length > 20) console.log('  ...还有', changed.length - 20, '篇')
  if (dryRun) { console.log(''); console.log('演练结束，未修改任何文件'); process.exit(0) }

  // 备份
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const backupDir = path.join(BACKUP_ROOT, 'normalize-' + stamp)
  fs.mkdirSync(backupDir, { recursive: true })
  console.log('')
  console.log('备份到:', backupDir)
  for (const rel of changed) {
    const src = path.join(VAULT, rel)
    const dst = path.join(backupDir, rel.replace(/\//g, '__'))
    try { fs.copyFileSync(src, dst) } catch (e) { console.log('  备份失败:', rel, e.message) }
  }

  const done = normalizeMissingSchema({ vaultRoot: VAULT, parseNote, buildNote, dryRun: false })
  console.log('')
  console.log('已补齐:', done.length, '篇')
  console.log('回滚: 从', backupDir, '复制回 vault')
}

function cmdMigrate() {
  console.log('=== 执行版本迁移 ===')
  console.log('目标版本:', CURRENT_SCHEMA)
  console.log('模式:', dryRun ? '演练（不写入）' : '实际写入')
  console.log('')
  const s = surveyVault({ vaultRoot: VAULT, parseNote })
  if (s.migratable === 0 && s.blocked.length === 0) {
    console.log('所有笔记已是最新版本，无需迁移')
    process.exit(0)
  }
  if (s.blocked.length > 0) {
    console.log('存在阻塞项，拒绝迁移：')
    for (const b of s.blocked) console.log('  ', b.path, b.reason)
    process.exit(1)
  }
  console.log('可迁移:', s.migratable, '篇')
  console.log('')
  console.log('当前代码没有注册任何迁移函数（MIGRATIONS 为空）。')
  console.log('未来新增版本时，在 core/vault/schema-migrate.mjs 的 MIGRATIONS 里加一条：')
  console.log('  [1, (meta) => ({ ...meta, schema: 2, 新字段: 默认值 })]')
}

switch (cmd) {
  case 'survey': cmdSurvey(); break
  case 'normalize': cmdNormalize(); break
  case 'migrate': cmdMigrate(); break
  default:
    console.log('用法: node scripts/schema-tool.mjs [survey|normalize|migrate] [--dry]')
    process.exit(1)
}
