#!/usr/bin/env node
// 部署固化包到运行目录（带校验与备份）
//
// 用法：
//   node scripts/apply-dist.mjs --check   只校验不写入
//   node scripts/apply-dist.mjs --apply   校验后写入（自动备份被覆盖文件）
//   node scripts/apply-dist.mjs --restore 从最近备份恢复
//
// 为什么需要这个脚本：
//   覆盖安装 APK 会清空 /sdcard 上的插件改动。
//   恢复时要能一条命令把固化包重新铺回去，且每一步都可校验。

import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const SIF = '/storage/emulated/0/思忆集/app/sif'
const DIST = path.join(SIF, 'dist')
const BACKUP_ROOT = process.env.SIYI_DIST_BACKUP_ROOT || path.join(SIF, 'backups', 'dist')

// 目标路径。SIYI_DIST_TARGET_ROOT 用于隔离测试（模拟覆盖安装后的恢复演练）。
const TARGET_ROOT = process.env.SIYI_DIST_TARGET_ROOT || null
const TARGETS = TARGET_ROOT
  ? {
      'plugins': path.join(TARGET_ROOT, 'plugins'),
      'core': path.join(TARGET_ROOT, 'core'),
    }
  : {
      'plugins': '/storage/emulated/0/思忆集/plugins',
      'core': path.join(SIF, 'core'),
    }

const mode = process.argv[2] || '--check'

function sha256(p) {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex')
}

function loadManifest() {
  const p = path.join(DIST, 'manifest.json')
  if (!fs.existsSync(p)) throw new Error('manifest.json 不存在，先跑 build-dist.mjs')
  return JSON.parse(fs.readFileSync(p, 'utf8'))
}

/** 目标路径：plugins/* -> plugins 目录；core/* -> core 目录 */
function targetPathFor(rel) {
  const [top, ...rest] = rel.split('/')
  const base = TARGETS[top]
  if (!base) throw new Error('未知顶层目录: ' + top)
  return path.join(base, ...rest)
}

function cmdCheck() {
  const m = loadManifest()
  let ok = 0, missing = 0, mismatch = 0
  console.log('=== 校验固化包 vs 运行目录 ===')
  for (const e of m.files) {
    const src = path.join(DIST, e.path)
    if (!fs.existsSync(src)) { console.log('  MISSING  ' + e.path); missing++; continue }
    const srcHash = sha256(src)
    if (srcHash !== e.sha256) { console.log('  CORRUPT  ' + e.path + ' (源文件 hash 不符)'); mismatch++; continue }

    const dst = targetPathFor(e.path)
    if (!fs.existsSync(dst)) { console.log('  未部署  ' + e.path); missing++; continue }
    const dstHash = sha256(dst)
    if (dstHash !== e.sha256) {
      console.log('  不一致  ' + e.path + '  dist=' + e.sha256.slice(0, 12) + '  runtime=' + dstHash.slice(0, 12))
      mismatch++
    } else ok++
  }
  console.log('')
  console.log(`一致 ${ok} / 未部署或缺失 ${missing} / 不一致 ${mismatch}`)
  process.exit(mismatch > 0 || missing > 0 ? 1 : 0)
}

function cmdApply() {
  const m = loadManifest()
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const backupDir = path.join(BACKUP_ROOT, stamp)
  fs.mkdirSync(backupDir, { recursive: true })

  let applied = 0, backedUp = 0, failed = 0
  console.log('=== 部署固化包 ===')
  console.log('备份目录:', backupDir)
  console.log('')

  for (const e of m.files) {
    const src = path.join(DIST, e.path)
    const dst = targetPathFor(e.path)

    // 校验源
    if (!fs.existsSync(src)) { console.log('  SKIP  ' + e.path + ' (源不存在)'); failed++; continue }
    if (sha256(src) !== e.sha256) { console.log('  SKIP  ' + e.path + ' (源 hash 不符)'); failed++; continue }

    // 备份目标
    if (fs.existsSync(dst)) {
      const relBackup = e.path.replace(/\//g, '__')
      const bp = path.join(backupDir, relBackup)
      try {
        fs.copyFileSync(dst, bp)
        backedUp++
      } catch (err) {
        console.log('  WARN  ' + e.path + ' 备份失败: ' + err.message)
      }
    }

    // 写入：同目录临时文件 + rename
    try {
      fs.mkdirSync(path.dirname(dst), { recursive: true })
      const tmp = dst + '.tmp-' + process.pid
      fs.copyFileSync(src, tmp)
      fs.renameSync(tmp, dst)
      if (sha256(dst) !== e.sha256) throw new Error('写入后 hash 不符')
      applied++
      console.log('  OK    ' + e.path)
    } catch (err) {
      try { fs.unlinkSync(dst + '.tmp-' + process.pid) } catch {}
      console.log('  FAIL  ' + e.path + ' :: ' + err.message)
      failed++
    }
  }

  fs.writeFileSync(path.join(backupDir, 'backup-manifest.json'),
    JSON.stringify({ stamp, files: m.files.map(e => e.path) }, null, 2) + '\n', 'utf8')

  console.log('')
  console.log(`已部署 ${applied} / 已备份 ${backedUp} / 失败 ${failed}`)
  console.log('回滚: node scripts/apply-dist.mjs --restore')
  process.exit(failed > 0 ? 1 : 0)
}

function cmdRestore() {
  if (!fs.existsSync(BACKUP_ROOT)) { console.log('无备份目录'); process.exit(1) }
  const dirs = fs.readdirSync(BACKUP_ROOT).filter(d => {
    try { return fs.statSync(path.join(BACKUP_ROOT, d)).isDirectory() } catch { return false }
  }).sort()
  if (dirs.length === 0) { console.log('无可用备份'); process.exit(1) }
  const latest = dirs[dirs.length - 1]
  const backupDir = path.join(BACKUP_ROOT, latest)
  console.log('=== 从备份恢复 ===')
  console.log('备份:', latest)
  console.log('')

  const mf = path.join(backupDir, 'backup-manifest.json')
  if (!fs.existsSync(mf)) { console.log('备份清单缺失'); process.exit(1) }
  const meta = JSON.parse(fs.readFileSync(mf, 'utf8'))

  let restored = 0
  for (const rel of meta.files) {
    const relBackup = rel.replace(/\//g, '__')
    const bp = path.join(backupDir, relBackup)
    if (!fs.existsSync(bp)) { console.log('  SKIP  ' + rel + ' (无备份文件)'); continue }
    const dst = targetPathFor(rel)
    try {
      const tmp = dst + '.tmp-' + process.pid
      fs.copyFileSync(bp, tmp)
      fs.renameSync(tmp, dst)
      restored++
      console.log('  OK    ' + rel)
    } catch (err) {
      console.log('  FAIL  ' + rel + ' :: ' + err.message)
    }
  }
  console.log('')
  console.log(`已恢复 ${restored} 个文件`)
}

switch (mode) {
  case '--check': cmdCheck(); break
  case '--apply': cmdApply(); break
  case '--restore': cmdRestore(); break
  default:
    console.log('用法: node scripts/apply-dist.mjs [--check|--apply|--restore]')
    process.exit(1)
}
