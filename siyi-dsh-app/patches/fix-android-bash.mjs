// 修正 DSH 在非 android platform 下把 shell 写死成 "bash" 的问题。
//
// 背景：官方 DSH 用定制 libdshnode.so 启动，process.platform === "android"，
// 这两个插件因此会走 /system/bin/sh 分支。思忆集用普通 node 启动，
// platform 是 "linux"，于是去 spawn bash —— Android 没有 bash，
// 所有 shell 工具都报 "spawn bash EACCES"。
//
// 修复：把 argv 里的 "bash" 换成 DSH_BASH_SHELL 环境变量（App 侧已设为 /system/bin/sh）。
// 源码里该字面量可能是单行 `[ "bash", "-c", cmd ]`，也可能是多行缩进形式，
// 所以用「前后文锚定 + 只换引号内内容」的方式，两种排版都能命中。
// 幂等：已修过的文件会跳过。
import fs from 'node:fs'
import path from 'node:path'

const profile = process.argv[2]
if (!profile) {
  console.error('用法: node fix-android-bash.mjs <profiles/web 目录>')
  process.exit(1)
}

const SHELL_EXPR = '(process.env.DSH_BASH_SHELL || (process.platform === "android" ? "/system/bin/sh" : "bash"))'

// 命中「紧接在 -c 之前的那个 "bash" 字面量」，不关心换行与缩进
const PATTERN = /(["'`])bash\1(\s*,\s*["'`]-c["'`])/g

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
  src = src.replace(PATTERN, (_m, _q, tail) => SHELL_EXPR + tail)
  if (src === before) {
    console.log(`no match: ${rel}`)
    continue
  }
  fs.writeFileSync(file, src)
  changed += 1
  console.log(`patched: ${rel}`)
}
console.log(`done, ${changed} file(s) changed`)
