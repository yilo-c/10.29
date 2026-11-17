// SIF 路径解析（可迁移架构的核心件之一）
//
// ── 要解决的问题 ──────────────────────────────────────────────
// 之前插件用绝对路径 import 核心模块：
//   import { createVaultService } from '/storage/emulated/0/思忆集/app/sif/core/vault/service.mjs'
// 这带来三个迁移障碍：
//   1. 换设备：路径前缀变了（Android / Windows / Linux 各不相同）
//   2. 装 APK：/sdcard 下的 sif/ 目录不存在，插件加载直接失败
//   3. 换技术栈：路径写死在源码里，无法由宿主注入
//
// ── 设计原则 ──────────────────────────────────────────────────
// 路径是「运行时环境」而不是「源码常量」。按优先级解析：
//   1. 显式环境变量（测试、CI、跨平台宿主用）
//   2. 已知平台默认值（Android / Linux / Windows）
//   3. 从当前模块位置反推（回退）
//
// 任何模块都不应该再出现硬编码的 /storage/emulated/0 前缀。

import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))   // .../sif/core
const SIF_ROOT_DEFAULT = path.dirname(HERE)                 // .../sif

/** 候选根目录，按优先级排列。 */
function candidateRoots() {
  const out = []
  if (process.env.SIYI_SIF_ROOT) out.push(process.env.SIYI_SIF_ROOT)
  // Android 默认
  out.push('/storage/emulated/0/思忆集/app/sif')
  // 从模块位置反推（装进 APK 后走这条）
  out.push(SIF_ROOT_DEFAULT)
  return out
}

/** 候选数据根，按优先级排列。 */
function candidateDataRoots() {
  const out = []
  if (process.env.SIYI_DATA_ROOT) out.push(process.env.SIYI_DATA_ROOT)
  if (process.env.SIYI_VAULT_ROOT) {
    // 显式给了 vault 就用它的父目录作为数据根
    out.push(path.dirname(process.env.SIYI_VAULT_ROOT))
  }
  out.push('/storage/emulated/0/思忆集')
  out.push(path.join(SIF_ROOT_DEFAULT, '..'))
  return out
}

function firstExisting(list, probe) {
  for (const p of list) {
    try {
      if (fs.existsSync(probe ? path.join(p, probe) : p)) return p
    } catch {}
  }
  return null
}

/**
 * 解析 sif 根目录。
 *
 * 优先级：显式环境变量 > 存在性探测 > 模块位置反推。
 * 显式指定必须无条件生效——否则测试隔离、跨设备迁移都会被
 * 「目录不存在」否决掉（实测踩到：SIYI_SIF_ROOT=/tmp/fake 被跳过）。
 */
export function resolveSifRoot() {
  if (process.env.SIYI_SIF_ROOT) return process.env.SIYI_SIF_ROOT
  return firstExisting(candidateRoots(), 'core') || SIF_ROOT_DEFAULT
}

/** 解析数据根。显式环境变量无条件优先，理由同 resolveSifRoot。 */
export function resolveDataRoot() {
  if (process.env.SIYI_DATA_ROOT) return process.env.SIYI_DATA_ROOT
  if (process.env.SIYI_VAULT_ROOT) return path.dirname(process.env.SIYI_VAULT_ROOT)
  return firstExisting(candidateDataRoots(), 'vault') || path.dirname(SIF_ROOT_DEFAULT)
}

/** vault 目录。 */
export function resolveVaultRoot() {
  if (process.env.SIYI_VAULT_ROOT) return process.env.SIYI_VAULT_ROOT
  return path.join(resolveDataRoot(), 'vault')
}

/** 用户插件目录。 */
export function resolvePluginsDir() {
  if (process.env.SIYI_PLUGINS_DIR) return process.env.SIYI_PLUGINS_DIR
  return path.join(resolveDataRoot(), 'plugins')
}

/** 补丁目录。 */
export function resolvePatchesDir() {
  if (process.env.SIYI_PATCHES_DIR) return process.env.SIYI_PATCHES_DIR
  return path.join(resolveDataRoot(), 'patches')
}

/**
 * 解析核心模块路径。
 *
 * 为什么需要这个函数：用户插件在 /sdcard 下，没有 node_modules 解析路径，
 * 必须用绝对路径或 file:// URL import。但绝对路径又不能写死。
 * 所以插件先用这个函数算出路径，再动态 import。
 *
 * @param rel 相对 sif 根的路径，如 'core/vault/service.mjs'
 * @returns 绝对路径
 */
export function resolveCoreModule(rel) {
  return path.join(resolveSifRoot(), rel)
}

/** 同上，返回 file:// URL，供 import() 使用。 */
export function coreModuleUrl(rel) {
  const p = resolveCoreModule(rel)
  // Windows 路径需要转换
  return 'file://' + (process.platform === 'win32' ? '/' + p.replace(/\\/g, '/') : p)
}

/** 一次性导出环境快照，便于诊断与写进 current-state。 */
export function describePaths() {
  return {
    platform: process.platform,
    sifRoot: resolveSifRoot(),
    dataRoot: resolveDataRoot(),
    vaultRoot: resolveVaultRoot(),
    pluginsDir: resolvePluginsDir(),
    patchesDir: resolvePatchesDir(),
    envOverrides: {
      SIYI_SIF_ROOT: process.env.SIYI_SIF_ROOT || null,
      SIYI_DATA_ROOT: process.env.SIYI_DATA_ROOT || null,
      SIYI_VAULT_ROOT: process.env.SIYI_VAULT_ROOT || null,
      SIYI_PLUGINS_DIR: process.env.SIYI_PLUGINS_DIR || null,
    },
  }
}
