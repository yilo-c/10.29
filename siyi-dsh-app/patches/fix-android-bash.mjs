// 修正 DSH 在非 android platform 下把 shell 写死成 "bash" 的问题。
//
// 背景：官方 DSH 用定制 libdshnode.so 启动，process.platform === "android"，
// 这两个插件因此会走 /system/bin/sh 分支。思忆集用普通 node 启动，
// platform 是 "linux"，于是去 spawn bash —— Android 没有 bash，
// 所有 shell 工具都报 "spawn bash EACCES"。
//
// 修复：把 argv 里的 "bash" 换成 DSH_BASH_SHELL 环境变量（App 侧已设为 /system/bin/sh）。
// 幂等：已修过的文件会跳过。
import fs from 'node:fs'
import path from 'node:path'

const profile = process.argv[2]
if (!profile) {
  console.error('用法: node fix-android-bash.mjs <profiles/web 目录>')
  process.exit(1)
}

const SHELL_EXPR = '(process.env.DSH_BASH_SHELL || (process.platform === "android" ? "/system/bin/sh" : "bash"))'
const targets = [
  'node_modules/@deepseek-ai/dsh-bash-local/lib/index.js',
  'node_modules/@deepseek-ai/dsh-bash-sandbox/lib/index.js',
]

let changed = 0
for (const rel of targets) {
  const file = path.join(profile, rel)
  if (!fs.existsSync(file)) {
    console.log(`skip (missing): ${rel}`)
    continue
  }
  let src = fs.readFileSync(file, 'utf8')
  if (src.includes('DSH_BASH_SHELL')) {
    console.log(`skip (already patched): ${rel}`)
    continue
  }
  const before = src
  // 两种写法都覆盖：[ "bash", ... ] 与 confine([ "bash", ... ])
  src = src.replace(/(\[\s*)"bash"(\s*,\s*"-c")/g, `$1${SHELL_EXPR}$2`)
  if (src === before) {
    console.log(`no match: ${rel}`)
    continue
  }
  fs.writeFileSync(file, src)
  changed += 1
  console.log(`patched: ${rel}`)
}
console.log(`done, ${changed} file(s) changed`)
