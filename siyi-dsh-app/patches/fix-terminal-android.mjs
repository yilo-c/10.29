// 让 dsh-subprocess-local 的终端在 Android 上可用。
//
// 官方 createProcessInspector 只认 linux / darwin / win32：
//   throw new Error(`subprocess-local: terminal inspection is unsupported on platform ${platform}`)
// Android 上 process.platform === "android"，直接抛错，终端面板显示
// 「终端错误：subprocess-local: terminal inspection is unsupported on platform android」。
//
// 但 Android 内核就是 Linux，/proc/[pid]/stat 的 pid / comm / state / starttime
// 字段齐全（实测可用），LinuxProcessInspector 完全可以复用。
import fs from 'node:fs'
import path from 'node:path'

const root = process.argv[2]
if (!root) {
  console.error('用法: node fix-terminal-android.mjs <engine 解包目录>')
  process.exit(1)
}

function findFiles(dir, depth = 0, out = []) {
  if (depth > 8) return out
  let entries
  try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    if (!e.isDirectory()) {
      if (e.name.endsWith('.js')) out.push(path.join(dir, e.name))
      continue
    }
    if (e.name === '.git' || e.name === 'assets') continue
    findFiles(path.join(dir, e.name), depth + 1, out)
  }
  return out
}

// 只扫 subprocess-local 包
const pkgDir = findFiles(root).length && (() => {
  const all = findFiles(root)
  const hit = all.find(f => f.includes('dsh-subprocess-local'))
  return hit ? hit.slice(0, hit.indexOf('dsh-subprocess-local') + 'dsh-subprocess-local'.length) : null
})()

if (!pkgDir) {
  console.log('no dsh-subprocess-local found under ' + root)
  process.exit(0)
}

const files = findFiles(pkgDir)
let changed = 0
for (const f of files) {
  let s = fs.readFileSync(f, 'utf8')
  if (!s.includes('terminal inspection is unsupported')) continue
  if (s.includes('platform === "android"')) {
    console.log('skip (already patched): ' + path.relative(root, f))
    continue
  }
  const old = 'if (platform === "linux") return new LinuxProcessInspector(arch, internals);'
  if (!s.includes(old)) {
    console.log('no match: ' + path.relative(root, f))
    continue
  }
  const neu = [
    '// 思忆集补丁：Android 内核即 Linux，/proc/[pid]/stat 字段齐全，',
    '// LinuxProcessInspector 可直接复用。官方漏了 android 分支。',
    'if (platform === "linux" || platform === "android") return new LinuxProcessInspector(arch, internals);',
  ].join('\n\t')
  s = s.replace(old, neu)
  fs.writeFileSync(f, s)
  changed += 1
  console.log('patched: ' + path.relative(root, f))
}
console.log(`done, ${changed} file(s) changed`)
