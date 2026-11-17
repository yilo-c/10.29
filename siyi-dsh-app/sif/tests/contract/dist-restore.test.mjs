// CI 固化包：校验 + 部署 + 恢复 的完整闭环测试
//
// 为什么必须测这个：
//   固化包的全部价值在于「覆盖安装后能恢复」。
//   如果恢复流程本身有 bug，固化等于没有。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'

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

const SIF = '/storage/emulated/0/思忆集/app/sif'
const DIST = path.join(SIF, 'dist')
const MANIFEST = path.join(DIST, 'manifest.json')

function sha256(p) {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex')
}

tt('manifest 存在且结构完整', () => {
  eq(fs.existsSync(MANIFEST), true, 'exists')
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  eq(m.schema, 'siyi.dist/v1', 'schema')
  eq(Array.isArray(m.files), true, 'files-array')
  eq(m.files.length > 0, true, 'nonempty')
})

tt('manifest 每个文件都有 sha256 和 bytes', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  for (const e of m.files) {
    eq(typeof e.sha256, 'string', e.path + '.sha256')
    eq(e.sha256.length, 64, e.path + '.sha256-len')
    eq(typeof e.bytes, 'number', e.path + '.bytes')
  }
})

tt('manifest 记录的 hash 与 dist 实际文件一致', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  for (const e of m.files) {
    const p = path.join(DIST, e.path)
    eq(fs.existsSync(p), true, e.path + '.exists')
    eq(sha256(p), e.sha256, e.path + '.hash')
  }
})

tt('checksums.txt 与 manifest 一致', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  const cs = fs.readFileSync(path.join(DIST, 'checksums.txt'), 'utf8').trim().split('\n')
  eq(cs.length, m.files.length, 'line-count')
  for (const e of m.files) {
    const hit = cs.find(l => l.endsWith('  ' + e.path))
    eq(hit !== undefined, true, e.path + '.present')
    eq(hit.startsWith(e.sha256), true, e.path + '.hash-match')
  }
})

tt('关键补丁标记都在固化包里', () => {
  const vault = fs.readFileSync(path.join(DIST, 'plugins/siyi-vault.js'), 'utf8')
  for (const mark of ['SIYI-PATHFIX-20261118', 'SIYI-TITLEFIX-20261118',
                      'SIYI-HTTPHARDEN-20261118', 'SIYI-INDEX-20261118']) {
    eq(vault.includes(mark), true, mark)
  }
})

tt('sif-core 模块齐全', () => {
  for (const f of ['vault/service.mjs', 'vault/indexer.mjs', 'vault/atomic.mjs',
                   'vault/paths.mjs', 'vault/capabilities.mjs',
                   'bridge/codec.mjs', 'bridge/journal.mjs', 'bridge/server.mjs']) {
    eq(fs.existsSync(path.join(DIST, 'core', f)), true, f)
  }
})

tt('固化包不含用户数据', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  for (const e of m.files) {
    eq(e.path.includes('vault/2026'), false, e.path + ' 不应含笔记数据')
    eq(e.path.includes('.siyi/'), false, e.path + ' 不应含应用元数据')
  }
})

tt('固化包不含密钥或凭据', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  for (const e of m.files) {
    const content = fs.readFileSync(path.join(DIST, e.path), 'utf8')
    eq(/gateway\.key/.test(content) && content.includes('-----BEGIN'), false, e.path + ' 疑似含私钥')
    eq(content.includes('sk-') && content.includes('apiKey:'), false, e.path + ' 疑似含明文密钥')
  }
})

tt('apply-dist --check 在当前状态返回 0', () => {
  const r = spawnSync(process.execPath, [path.join(SIF, 'scripts/apply-dist.mjs'), '--check'],
    { encoding: 'utf8' })
  eq(r.status, 0, 'exit-code')
  eq(r.stdout.includes('一致 13'), true, 'all-consistent')
})

tt('manifest 记录了部署目标路径', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  eq(typeof m.deployTargets, 'object', 'deployTargets')
  eq(typeof m.deployTargets.plugins, 'string', 'plugins')
  eq(typeof m.deployTargets.core, 'string', 'core')
})

tt('manifest 记录来源设备版本', () => {
  const m = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'))
  eq(m.sourceDevice.package, 'com.siyi.dshapp', 'package')
  eq(typeof m.sourceDevice.versionCode, 'number', 'versionCode')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
