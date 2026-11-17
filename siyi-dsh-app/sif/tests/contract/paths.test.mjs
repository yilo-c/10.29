// 路径可迁移性测试（换设备 / 换技术栈的前提）
//
// 为什么必须测：
//   绝对路径写死会让「装 APK / 换设备 / 换平台」全部失败。
//   这里验证路径完全由环境与探测决定，不依赖任何硬编码前缀。
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

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

/** 在指定环境下跑一段探测脚本，返回 JSON。 */
function probe(env) {
  const script = `
    import { describePaths } from '${SIF}/core/paths.mjs'
    console.log(JSON.stringify(describePaths()))
  `
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8',
    env: { ...process.env, ...env },
  })
  if (r.status !== 0) throw new Error('探测失败: ' + (r.stderr || '').slice(0, 300))
  return JSON.parse(r.stdout.trim())
}

// ── 默认环境 ─────────────────────────────────────────────
tt('默认环境解析出 Android 路径', () => {
  const p = probe({ SIYI_SIF_ROOT: '', SIYI_VAULT_ROOT: '', SIYI_DATA_ROOT: '' })
  eq(p.sifRoot, SIF, 'sifRoot')
  eq(p.vaultRoot, '/storage/emulated/0/思忆集/vault', 'vaultRoot')
  eq(p.platform, process.platform, 'platform')
})

// ── 显式覆盖必须无条件生效 ───────────────────────────────
tt('SIYI_SIF_ROOT 覆盖生效（即使目录不存在）', () => {
  const p = probe({ SIYI_SIF_ROOT: '/tmp/does-not-exist-sif' })
  eq(p.sifRoot, '/tmp/does-not-exist-sif', 'sifRoot')
})

tt('SIYI_VAULT_ROOT 覆盖生效', () => {
  const p = probe({ SIYI_VAULT_ROOT: '/tmp/fake-vault' })
  eq(p.vaultRoot, '/tmp/fake-vault', 'vaultRoot')
})

tt('SIYI_DATA_ROOT 覆盖生效并推导 vault', () => {
  const p = probe({ SIYI_DATA_ROOT: '/tmp/fake-data' })
  eq(p.dataRoot, '/tmp/fake-data', 'dataRoot')
  eq(p.vaultRoot, '/tmp/fake-data/vault', 'vaultRoot')
})

tt('SIYI_PLUGINS_DIR 覆盖生效', () => {
  const p = probe({ SIYI_PLUGINS_DIR: '/tmp/fake-plugins' })
  eq(p.pluginsDir, '/tmp/fake-plugins', 'pluginsDir')
})

tt('多个覆盖同时生效且互不干扰', () => {
  const p = probe({
    SIYI_SIF_ROOT: '/tmp/a-sif',
    SIYI_DATA_ROOT: '/tmp/a-data',
    SIYI_PLUGINS_DIR: '/tmp/a-plugins',
  })
  eq(p.sifRoot, '/tmp/a-sif', 'sifRoot')
  eq(p.dataRoot, '/tmp/a-data', 'dataRoot')
  eq(p.vaultRoot, '/tmp/a-data/vault', 'vaultRoot')
  eq(p.pluginsDir, '/tmp/a-plugins', 'pluginsDir')
})

tt('envOverrides 如实记录', () => {
  const p = probe({ SIYI_SIF_ROOT: '/tmp/x' })
  eq(p.envOverrides.SIYI_SIF_ROOT, '/tmp/x', 'recorded')
  eq(p.envOverrides.SIYI_DATA_ROOT, null, 'others-null')
})

// ── 源码里不应再有硬编码 import ──────────────────────────
tt('sif 插件源码无绝对路径 import', () => {
  const src = fs.readFileSync(path.join(SIF, 'plugins/siyi-vault-tools.js'), 'utf8')
  eq(/^import\s+.*from\s+'\/storage/m.test(src), false, '静态绝对 import')
})

tt('运行插件无绝对路径静态 import', () => {
  for (const f of ['siyi-vault-tools.js', 'siyi-vault.js']) {
    const p = '/storage/emulated/0/思忆集/plugins/' + f
    const src = fs.readFileSync(p, 'utf8')
    eq(/^import\s+.*from\s+'\/storage/m.test(src), false, f + ' 静态绝对 import')
  }
})

tt('插件仍通过动态 import 加载核心模块', () => {
  const src = fs.readFileSync('/storage/emulated/0/思忆集/plugins/siyi-vault-tools.js', 'utf8')
  eq(src.includes('await import(SIF_ROOT'), true, 'dynamic-import')
  eq(src.includes('SIYI_SIF_ROOT'), true, 'env-aware')
})

// ── 跨平台路径拼接 ───────────────────────────────────────
tt('coreModuleUrl 产出 file:// URL', () => {
  const script = `
    import { coreModuleUrl } from '${SIF}/core/paths.mjs'
    console.log(coreModuleUrl('core/vault/service.mjs'))
  `
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', script], { encoding: 'utf8' })
  eq(r.status, 0, 'exit')
  eq(r.stdout.trim().startsWith('file://'), true, 'file-url')
})

// ── 换设备模拟：路径完全变但功能不变 ─────────────────────
tt('模拟换设备：新根目录下插件能解析核心模块', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-relocate-'))
  // 复制 core 到新位置
  fs.cpSync(path.join(SIF, 'core'), path.join(tmp, 'core'), { recursive: true })
  const script = `
    import { resolveSifRoot, resolveCoreModule } from '${tmp}/core/paths.mjs'
    import fs from 'node:fs'
    const root = resolveSifRoot()
    const svc = resolveCoreModule('core/vault/service.mjs')
    console.log(JSON.stringify({ root, svc, exists: fs.existsSync(svc) }))
  `
  const r = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8',
    env: { ...process.env, SIYI_SIF_ROOT: tmp },
  })
  eq(r.status, 0, 'exit: ' + (r.stderr || '').slice(0, 200))
  const out = JSON.parse(r.stdout.trim())
  eq(out.root, tmp, 'root')
  eq(out.exists, true, 'core-module-resolvable')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
