// 修正 DSH 在非 android platform 下把 shell 写死成 "bash" 的问题。
//
// 背景：官方 DSH 用定制 libdshnode.so 启动，process.platform === "android"，
// 这两个插件因此走 /system/bin/sh 分支。思忆集用普通 node 启动，platform 是 "linux"，
// 于是去 spawn bash —— Android 没有 bash，所有 shell 工具报 "spawn bash EACCES"。
//
// 修复：把 argv 里紧邻 "-c" 的那个 "bash" 字面量换成 DSH_BASH_SHELL 表达式。
//
// 两个坑：
// 1. 源码排版可能是单行也可能多行缩进，正则要能同时命中；
// 2. pnpm 用 hoisted 之外的布局时，包会落在 node_modules/.pnpm/<pkg>@<ver>/node_modules/ 下，
//    顶层 node_modules/@deepseek-ai/ 里看不到。所以这里递归查找所有匹配目录，全部打补丁。
import fs from 'node:fs'
import path from 'node:path'

const profile = process.argv[2]
if (!profile) {
  console.error('用法: node fix-android-bash.mjs <profiles/web 目录>')
  process.exit(1)
}

const SHELL_EXPR = '(process.env.DSH_BASH_SHELL || (process.platform === "android" ? "/system/bin/sh" : "bash"))'
// 命中「紧接在 -c 之前的 bash 字面量」，不关心换行与缩进
const PATTERN = /(["'`])bash\1(\s*,\s*["'`]-c["'`])/g
const PKG_DIRS = ['dsh-bash-local', 'dsh-bash-sandbox']
const REL = path.join('lib', 'index.js')

/** 递归找出所有名为 dsh-bash-local / dsh-bash-sandbox 的包目录 */
function findPackageDirs(root, depth = 0, out = []) {
  if (depth > 6) return out
  let entries
  try { entries = fs.readdirSync(root, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    if (!e.isDirectory()) continue
    const full = path.join(root, e.name)
    if (PKG_DIRS.includes(e.name)) {
      const target = path.join(full, REL)
      if (fs.existsSync(target)) out.push(target)
      continue
    }
    if (e.name === '.git' || e.name === 'dist' || e.name === 'assets') continue
    findPackageDirs(full, depth + 1, out)
  }
  return out
}

const files = [...new Set(findPackageDirs(profile))]
if (files.length === 0) {
  console.log('no target package found under', profile)
  process.exit(0)
}

let changed = 0
for (const file of files) {
  const rel = path.relative(profile, file)
  let src = fs.readFileSync(file, 'utf8')
  if (src.includes('DSH_BASH_SHELL')) {
    console.log(`skip (already patched): ${rel}`)
    continue
  }
  const before = src
  src = src.replace(PATTERN, (_m, _q, tail) => SHELL_EXPR + tail)
  if (src === before) {
    console.log(`no match: ${rel}`)
    continue
  }
  fs.writeFileSync(file, src)
  changed += 1
  console.log(`patched: ${rel}`)
}
console.log(`done, ${changed} file(s) changed (scanned ${files.length})`)
