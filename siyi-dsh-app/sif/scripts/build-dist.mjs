// 生成 CI 固化包清单
//
// 目的：把 /sdcard 上的补丁与插件变成可审计、可校验、可复现的发布单元。
// 覆盖安装 APK 会清空 /sdcard 上的临时改动，只有固化进仓库/CI 才能存活。
//
// 产物：
//   dist/manifest.json   文件清单 + sha256 + 补丁标记 + 版本
//   dist/checksums.txt   人类可读校验清单

import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const SIF = '/storage/emulated/0/思忆集/app/sif'
const DIST = path.join(SIF, 'dist')

const MARKERS = [
  'SIYI-PATHFIX-20261118',
  'SIYI-TITLEFIX-20261118',
  'SIYI-HTTPHARDEN-20261118',
  'SIYI-INDEX-20261118',
]

function sha256(p) {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex')
}

function walk(dir, base = DIST) {
  const out = []
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) { out.push(...walk(p, base)); continue }
    out.push(path.relative(base, p))
  }
  return out
}

// 只有 plugins/ 与 core/ 下的文件是「可部署单元」，才进 manifest。
// 文档类（ci-embed.md 等）留在 dist 目录供人阅读，但不参与部署，
// 否则 apply-dist 会因找不到目标目录而崩溃（实测踩到）。
const DEPLOYABLE_TOPS = new Set(['plugins', 'core'])
const files = walk(DIST)
  .filter(f => DEPLOYABLE_TOPS.has(f.split('/')[0]))
  .sort()

const entries = files.map(rel => {
  const p = path.join(DIST, rel)
  const content = fs.readFileSync(p, 'utf8')
  const st = fs.statSync(p)
  return {
    path: rel,
    bytes: st.size,
    sha256: sha256(p),
    markers: MARKERS.filter(m => content.includes(m)),
  }
})

const manifest = {
  schema: 'siyi.dist/v1',
  name: 'siyi-runtime-bundle',
  version: '1.0.0',
  generatedAt: new Date().toISOString(),
  sourceDevice: {
    package: 'com.siyi.dshapp',
    versionCode: 2026112001,
  },
  // 部署目标：运行引擎会从这里读插件
  deployTargets: {
    plugins: '/storage/emulated/0/思忆集/plugins',
    core: '/storage/emulated/0/思忆集/app/sif/core',
  },
  files: entries,
  totals: {
    files: entries.length,
    bytes: entries.reduce((s, e) => s + e.bytes, 0),
    patched: entries.filter(e => e.markers.length > 0).length,
  },
}

fs.writeFileSync(path.join(DIST, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n', 'utf8')

const checksums = entries.map(e => `${e.sha256}  ${e.path}`).join('\n') + '\n'
fs.writeFileSync(path.join(DIST, 'checksums.txt'), checksums, 'utf8')

console.log('manifest 已生成')
console.log('  文件数:', manifest.totals.files)
console.log('  总字节:', manifest.totals.bytes)
console.log('  含补丁标记:', manifest.totals.patched)
console.log('')
for (const e of entries) {
  const mk = e.markers.length ? '  [' + e.markers.length + ' 补丁]' : ''
  console.log(`  ${e.path}  ${e.bytes}B${mk}`)
}
