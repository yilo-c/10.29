// 修复 Android 上 plugin-manager 无法调用 pnpm：execa("pnpm") → execa(process.execPath, [pnpm.mjs, ...])
// 用法: node fix-pnpm-android.mjs <engine解包目录>
import fs from 'node:fs'
import path from 'node:path'
const root = process.argv[2]
if (!root || !fs.existsSync(root)) { console.error('用法: node fix-pnpm-android.mjs <engine解包目录>'); process.exit(1) }
const file = path.join(root, 'node_modules/@deepseek-ai/dsh-plugin-manager/lib/index.js')
if (!fs.existsSync(file)) { console.error('找不到 dsh-plugin-manager/lib/index.js'); process.exit(1) }
let s = fs.readFileSync(file, 'utf8')
const MARK = 'SIYI-PNPM-ANDROID-20261124'
if (s.includes(MARK)) { console.log('已打过补丁'); process.exit(0) }
const importMatch = s.match(/^import .*$/m)
if (!importMatch) { console.error('找不到 import 语句'); process.exit(1) }
const constDecl = "\n// " + MARK + "：Android 上 pnpm 只有 pnpm.mjs，PATH 无可执行 pnpm；用 process.execPath 直接跑\nconst PNPM_JS = new URL('../../../pnpm/bin/pnpm.mjs', import.meta.url).pathname;\n"
s = s.replace(importMatch[0], importMatch[0] + constDecl)
// 统一替换所有 execa(options.command ?? "pnpm", [ 形态（主调用+只读调用）
const n = s.split('execa(options.command ?? "pnpm", [').length - 1
s = s.split('execa(options.command ?? "pnpm", [').join('execa(process.execPath, [PNPM_JS, ')
fs.writeFileSync(file, s, 'utf8')
console.log('已替换 execa 调用数:', n)
if (n < 4) { console.error('替换数过少，补丁可能不完整'); process.exit(1) }
