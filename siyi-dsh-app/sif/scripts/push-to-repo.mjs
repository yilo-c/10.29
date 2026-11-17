// 通过 GitHub API 推送 sif/ 目录到仓库（无需本地 git）
//
// 为什么用 API：手机上无法可靠运行 git CLI，交接文档已说明用 API 模式。
//
// 流程：GET ref → GET commit 拿 base_tree → 逐个 POST blob
//      → POST tree（带 base_tree）→ POST commit → PATCH ref
//
// 安全：token 只从环境变量读，不落盘、不进日志。

import fs from 'node:fs'
import path from 'node:path'

const T = process.env.GH_TOKEN
if (!T) { console.error('缺少 GH_TOKEN'); process.exit(1) }

const OWNER = 'yilo-c'
const REPO = '10.29'
const BRANCH = 'dsh-app'
const REPO_PREFIX = 'siyi-dsh-app/sif'   // 推到仓库里的位置

const LOCAL = '/storage/emulated/0/思忆集/app/sif'

// 排除：备份、fixtures、临时证据、node_modules
const EXCLUDE_DIRS = new Set(['backups', 'fixtures', 'node_modules', 'tmp', 'evidence'])
const EXCLUDE_FILES = new Set(['.DS_Store'])
const MAX_SIZE = 5 * 1024 * 1024

async function api(p, opts = {}) {
  const r = await fetch('https://api.github.com' + p, {
    ...opts,
    headers: {
      Authorization: 'Bearer ' + T,
      'User-Agent': 'siyi-agent',
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  })
  const text = await r.text()
  let body = null
  try { body = text ? JSON.parse(text) : null } catch { body = { raw: text.slice(0, 300) } }
  return { status: r.status, body }
}

function collect(dir, base = LOCAL) {
  const out = []
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith('.') && e.name !== '.gitignore') continue
    if (EXCLUDE_DIRS.has(e.name)) continue
    if (EXCLUDE_FILES.has(e.name)) continue
    const p = path.join(dir, e.name)
    if (e.isDirectory()) { out.push(...collect(p, base)); continue }
    if (!/\.(mjs|js|json|md|txt|yml|yaml|sh)$/.test(e.name)) continue
    const st = fs.statSync(p)
    if (st.size > MAX_SIZE) { console.log('  SKIP 过大:', path.relative(base, p)); continue }
    out.push({ abs: p, rel: path.relative(base, p).split(path.sep).join('/'), size: st.size })
  }
  return out
}

const files = collect(LOCAL).sort((a, b) => a.rel.localeCompare(b.rel))
console.log('待推送文件:', files.length)
let total = 0
for (const f of files) total += f.size
console.log('总字节:', total)
console.log('')

// 1. 拿分支 HEAD
const ref = await api(`/repos/${OWNER}/${REPO}/git/ref/heads/${BRANCH}`)
if (ref.status !== 200) { console.error('读取分支失败:', ref.status, ref.body?.message); process.exit(1) }
const baseSha = ref.body.object.sha
console.log('分支 HEAD:', baseSha.slice(0, 12))

const commit = await api(`/repos/${OWNER}/${REPO}/git/commits/${baseSha}`)
if (commit.status !== 200) { console.error('读取 commit 失败:', commit.status); process.exit(1) }
const baseTree = commit.body.tree.sha
console.log('base_tree:', baseTree.slice(0, 12))
console.log('')

// 2. 逐个上传 blob
console.log('上传 blob...')
const treeEntries = []
let uploaded = 0
for (const f of files) {
  const content = fs.readFileSync(f.abs)
  const blob = await api(`/repos/${OWNER}/${REPO}/git/blobs`, {
    method: 'POST',
    body: JSON.stringify({ content: content.toString('base64'), encoding: 'base64' }),
  })
  if (blob.status !== 201) {
    console.error('  FAIL', f.rel, blob.status, blob.body?.message)
    process.exit(1)
  }
  treeEntries.push({
    path: REPO_PREFIX + '/' + f.rel,
    mode: '100644',
    type: 'blob',
    sha: blob.body.sha,
  })
  uploaded++
  if (uploaded % 10 === 0) console.log('  已上传', uploaded, '/', files.length)
}
console.log('  blob 完成:', uploaded)
console.log('')

// 3. 建 tree
console.log('创建 tree...')
const tree = await api(`/repos/${OWNER}/${REPO}/git/trees`, {
  method: 'POST',
  body: JSON.stringify({ base_tree: baseTree, tree: treeEntries }),
})
if (tree.status !== 201) { console.error('创建 tree 失败:', tree.status, tree.body?.message); process.exit(1) }
console.log('  tree:', tree.body.sha.slice(0, 12))

// 4. 建 commit
const msg = `feat(sif): 固化思忆集工具整合框架核心与插件

本轮内容：
- SIF 协议契约（capability/bridge/error schema）
- VaultService 核心（路径约束、原子写、revision 冲突）
- 增量索引（56 篇 list 43.7ms → 0.05ms）
- 桥接编解码与 operation 幂等账本
- DSH 适配器与 6 个 vault 工具
- HTTP 安全闸门（回环/同源/CSRF/限流）
- CI 固化包与恢复演练
- 170 项合同测试

不含用户数据、凭据与密钥。`

const newCommit = await api(`/repos/${OWNER}/${REPO}/git/commits`, {
  method: 'POST',
  body: JSON.stringify({ message: msg, tree: tree.body.sha, parents: [baseSha] }),
})
if (newCommit.status !== 201) { console.error('创建 commit 失败:', newCommit.status, newCommit.body?.message); process.exit(1) }
console.log('  commit:', newCommit.body.sha.slice(0, 12))

// 5. 更新分支
console.log('更新分支...')
const upd = await api(`/repos/${OWNER}/${REPO}/git/refs/heads/${BRANCH}`, {
  method: 'PATCH',
  body: JSON.stringify({ sha: newCommit.body.sha, force: false }),
})
if (upd.status !== 200) { console.error('更新分支失败:', upd.status, upd.body?.message); process.exit(1) }

console.log('')
console.log('推送成功')
console.log('  commit:', newCommit.body.sha)
console.log('  文件数:', files.length)
console.log('  路径前缀:', REPO_PREFIX)
