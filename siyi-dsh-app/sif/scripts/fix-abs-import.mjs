// 把插件的绝对路径 import 改为可迁移的动态解析（幂等）
//
// 为什么要改：
//   1. 装 APK 后 /sdcard/思忆集/app/sif/ 不存在，静态 import 直接失败
//   2. 换设备路径前缀不同
//   3. 换技术栈时无法由宿主注入路径
//
// 改法：
//   静态 import 改成模块加载期的一次性动态 import（顶层 await）。
//   用户插件是 ESM，顶层 await 可用；加载器用 import() 加载，能等待完成。
//   路径由 core/paths.mjs 解析，不再写死。

import fs from 'node:fs'
import path from 'node:path'

const MARK = 'SIYI-PORTABLE-20261118'

function patchFile(target, replacements) {
  let s = fs.readFileSync(target, 'utf8')
  if (s.includes(MARK)) { console.log('SKIP（已改）:', target); return false }
  let changed = false
  for (const { from, to } of replacements) {
    if (!s.includes(from)) continue
    s = s.replace(from, to)
    changed = true
  }
  if (!changed) { console.log('未匹配:', target); return false }
  fs.writeFileSync(target, s, 'utf8')
  console.log('已改:', target)
  return true
}

// ── 1. sif 源插件 ─────────────────────────────────────────
const SRC = '/storage/emulated/0/思忆集/app/sif/plugins/siyi-vault-tools.js'
patchFile(SRC, [{
  from: `import { createVaultService } from '/storage/emulated/0/思忆集/app/sif/core/vault/service.mjs'
import { vaultCapabilities } from '/storage/emulated/0/思忆集/app/sif/core/vault/capabilities.mjs'`,
  to: `// ${MARK}：路径不再写死。
// 用户插件在 /sdcard 下没有 node_modules 解析路径，必须用绝对路径 import；
// 但绝对路径写死会让「装 APK / 换设备 / 换平台」全部失败。
// 改为：先解析 sif 根目录，再动态 import 核心模块（顶层 await，ESM 支持）。
const SIF_ROOT = process.env.SIYI_SIF_ROOT
  || '/storage/emulated/0/思忆集/app/sif'
const { createVaultService } = await import(SIF_ROOT + '/core/vault/service.mjs')
const { vaultCapabilities } = await import(SIF_ROOT + '/core/vault/capabilities.mjs')`,
}])

// ── 2. 运行目录插件 ───────────────────────────────────────
const RUN = '/storage/emulated/0/思忆集/plugins/siyi-vault-tools.js'
patchFile(RUN, [{
  from: `import { createVaultService } from '/storage/emulated/0/思忆集/app/sif/core/vault/service.mjs'
import { vaultCapabilities } from '/storage/emulated/0/思忆集/app/sif/core/vault/capabilities.mjs'`,
  to: `// ${MARK}：路径不再写死（详见 sif/scripts/fix-abs-import.mjs）
const SIF_ROOT = process.env.SIYI_SIF_ROOT
  || '/storage/emulated/0/思忆集/app/sif'
const { createVaultService } = await import(SIF_ROOT + '/core/vault/service.mjs')
const { vaultCapabilities } = await import(SIF_ROOT + '/core/vault/capabilities.mjs')`,
}])

// ── 3. siyi-vault.js 的 indexer import ────────────────────
const VAULT_PLUGIN = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
patchFile(VAULT_PLUGIN, [{
  from: `import { createIndexer } from '/storage/emulated/0/思忆集/app/sif/core/vault/indexer.mjs'`,
  to: `// ${MARK}：路径不再写死
const SIF_ROOT = process.env.SIYI_SIF_ROOT
  || '/storage/emulated/0/思忆集/app/sif'
const { createIndexer } = await import(SIF_ROOT + '/core/vault/indexer.mjs')`,
}])

// ── 4. VAULT_ROOT 也改成可覆盖 ────────────────────────────
const t = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
let s = fs.readFileSync(t, 'utf8')
const oldVault = `const VAULT = '/storage/emulated/0/思忆集/vault'`
const newVault = `// ${MARK}：数据目录可被环境变量覆盖，便于换设备与测试隔离
const VAULT = process.env.SIYI_VAULT_ROOT || '/storage/emulated/0/思忆集/vault'`
if (s.includes(oldVault)) {
  s = s.replace(oldVault, newVault)
  fs.writeFileSync(t, s, 'utf8')
  console.log('已改 VAULT 根目录')
}

const t2 = SRC
let s2 = fs.readFileSync(t2, 'utf8')
const old2 = `const VAULT_ROOT = '/storage/emulated/0/思忆集/vault'`
const new2 = `// ${MARK}：数据目录可被环境变量覆盖
const VAULT_ROOT = process.env.SIYI_VAULT_ROOT || '/storage/emulated/0/思忆集/vault'`
if (s2.includes(old2)) {
  s2 = s2.replace(old2, new2)
  fs.writeFileSync(t2, s2, 'utf8')
  console.log('已改源插件 VAULT 根目录')
}
