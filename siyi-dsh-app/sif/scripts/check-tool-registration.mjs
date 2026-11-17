// 检查运行中引擎是否真的把 vault_* 工具暴露给模型
//
// 方法：解压最新会话日志，找最新的 request/header 事件，
// 读其中的工具定义列表。这比看插件日志更硬——日志说注册了，
// 不代表工具真的进了模型可见的工具清单。

import fs from 'node:fs'
import zlib from 'node:zlib'
import { execSync } from 'node:child_process'

const root = '/data/data/com.siyi.dshapp/files/dsh-home/sessions/--storage-emulated-0-~601D~5FC6~96C6-app--'

function newestSession() {
  let newest = null
  for (const sess of fs.readdirSync(root)) {
    const p = root + '/' + sess
    let st
    try { st = fs.statSync(p) } catch { continue }
    if (!st.isDirectory()) continue
    const f = p + '/session.v4.jsonl.zstd'
    try {
      const fst = fs.statSync(f)
      if (!newest || fst.mtimeMs > newest.mtime) newest = { sess, path: f, mtime: fst.mtimeMs }
    } catch {}
  }
  return newest
}

function decompress(path) {
  // 优先用系统 zstd；没有则用 node zlib 试 gzip 兜底
  try {
    return execSync(`zstd -d -c "${path}"`, { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 })
  } catch {
    const buf = fs.readFileSync(path)
    try { return zlib.gunzipSync(buf).toString('utf8') } catch {}
    try { return zlib.brotliDecompressSync(buf).toString('utf8') } catch {}
    return null
  }
}

const s = newestSession()
if (!s) { console.log('未找到会话'); process.exit(1) }
console.log('会话:', s.sess)
console.log('时间:', new Date(s.mtime).toISOString())

const text = decompress(s.path)
if (!text) { console.log('无法解压（无 zstd 命令）'); process.exit(2) }

const lines = text.split('\n').filter(Boolean)
console.log('事件数:', lines.length)

// 从后往前找 request/header
let header = null
for (let i = lines.length - 1; i >= 0; i--) {
  let ev
  try { ev = JSON.parse(lines[i]) } catch { continue }
  if (ev && ev.type === 'request/header') { header = ev; break }
}
if (!header) { console.log('未找到 request/header'); process.exit(3) }

const tools = header.data?.tools || header.data?.toolDefs || []
const names = tools.map(t => t.name || t.function?.name).filter(Boolean)
console.log('')
console.log('模型可见工具数:', names.length)
const vaultTools = names.filter(n => n.startsWith('vault_'))
console.log('vault_* 工具:', vaultTools.length ? vaultTools.join(', ') : '（无）')

if (vaultTools.length === 6) {
  console.log('')
  console.log('RESULT: PASS — 6 个 vault 工具已进入模型可见清单')
  process.exit(0)
} else {
  console.log('')
  console.log(`RESULT: FAIL — 期望 6 个，实际 ${vaultTools.length} 个`)
  process.exit(1)
}
