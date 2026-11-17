// 盘点所有需要固化进 CI 的改动
import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const PLUGINS = '/storage/emulated/0/思忆集/plugins'
const SIF = '/storage/emulated/0/思忆集/app/sif'

const MARKERS = [
  'SIYI-PATHFIX-20261118',
  'SIYI-TITLEFIX-20261118',
  'SIYI-HTTPHARDEN-20261118',
  'SIYI-INDEX-20261118',
]

function hash(p) {
  try {
    return createHash('sha256').update(fs.readFileSync(p)).digest('hex').slice(0, 16)
  } catch { return null }
}

console.log('=== 运行目录插件盘点 ===')
const pluginFiles = fs.readdirSync(PLUGINS).filter(f => f.endsWith('.js')).sort()
for (const f of pluginFiles) {
  const p = path.join(PLUGINS, f)
  const st = fs.statSync(p)
  const content = fs.readFileSync(p, 'utf8')
  const found = MARKERS.filter(m => content.includes(m))
  console.log(`${f}`)
  console.log(`  size: ${st.size}  hash: ${hash(p)}  modified: ${new Date(st.mtimeMs).toISOString()}`)
  if (found.length) console.log(`  补丁标记: ${found.join(', ')}`)
}

console.log('')
console.log('=== sif 核心模块 ===')
const walk = (d, depth = 0) => {
  if (depth > 3) return
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.name === 'fixtures' || e.name.startsWith('.')) continue
    const p = path.join(d, e.name)
    if (e.isDirectory()) { walk(p, depth + 1); continue }
    if (!e.name.endsWith('.mjs')) continue
    const rel = path.relative(SIF, p)
    console.log(`  ${rel}  ${fs.statSync(p).size}B  ${hash(p)}`)
  }
}
walk(path.join(SIF, 'core'))
walk(path.join(SIF, 'plugins'))
