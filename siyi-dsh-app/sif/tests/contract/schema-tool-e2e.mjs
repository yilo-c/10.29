// schema 工具端到端：隔离副本上验证「缺失 → 补齐 → 回滚」
//
// 为什么必须测：这是数据层操作，错了会破坏笔记。
// 必须在真实 vault 的副本上跑完整流程，而不是只在单元测试里验函数。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { surveyVault, normalizeMissingSchema } from '../../core/vault/schema-migrate.mjs'
import { parseNote, buildNote } from '../../core/vault/service.mjs'

const SIF = '/storage/emulated/0/思忆集/app/sif'
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-e2e-'))
const VAULT = path.join(TMP, 'vault')
const ULID = '01M7KJWVWBB7W10TQKVFCGSWXS'
const ULID2 = '01M7KJWVWBB7W10TQKVFCGSWXT'

let pass = 0, fail = 0
const lines = []
function check(name, cond, detail) {
  if (cond) { pass++; lines.push('PASS ' + name) }
  else { fail++; lines.push('FAIL ' + name + (detail ? ' :: ' + detail : '')) }
}

// 准备：2 篇正常 + 1 篇缺 schema
const d = new Date()
const y = String(d.getFullYear())
const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
const dir = path.join(VAULT, y, ym)
fs.mkdirSync(dir, { recursive: true })
fs.writeFileSync(path.join(dir, ULID + '.md'),
  '---\nschema: 1\nid: ' + ULID + '\ntitle: 正常A\nkind: note\n---\n正文A', 'utf8')
fs.writeFileSync(path.join(dir, ULID2 + '.md'),
  '---\nid: ' + ULID2 + '\ntitle: 缺schema\nkind: note\n---\n正文B', 'utf8')

// 1. 普查应发现 1 篇缺失
let s = surveyVault({ vaultRoot: VAULT, parseNote })
check('普查发现 1 篇缺失 schema', s.missing === 1, 'missing=' + s.missing)
check('普查总数 2', s.total === 2, 'total=' + s.total)

// 2. dryRun 不写入
const before = fs.readFileSync(path.join(dir, ULID2 + '.md'), 'utf8')
const reported = normalizeMissingSchema({ vaultRoot: VAULT, parseNote, buildNote, dryRun: true })
check('dryRun 报告 1 篇待补', reported.length === 1, 'reported=' + reported.length)
const afterDry = fs.readFileSync(path.join(dir, ULID2 + '.md'), 'utf8')
check('dryRun 未修改文件', afterDry === before)

// 3. 实际写入
const done = normalizeMissingSchema({ vaultRoot: VAULT, parseNote, buildNote, dryRun: false })
check('实际补齐 1 篇', done.length === 1, 'done=' + done.length)
s = surveyVault({ vaultRoot: VAULT, parseNote })
check('补齐后无缺失', s.missing === 0, 'missing=' + s.missing)

// 4. 正文未被破坏
const fixed = fs.readFileSync(path.join(dir, ULID2 + '.md'), 'utf8')
check('补齐后正文保留', fixed.includes('正文B'), 'body-lost')
check('补齐后 schema 为 1', /schema: 1/.test(fixed), 'schema-not-set')
check('补齐后 title 保留', fixed.includes('title: 缺schema'), 'title-lost')

// 5. 幂等：再跑一次不改
const again = normalizeMissingSchema({ vaultRoot: VAULT, parseNote, buildNote, dryRun: false })
check('第二次运行无改动', again.length === 0, 'again=' + again.length)

// 6. 工具 CLI 在隔离 vault 上可运行
const r = spawnSync(process.execPath, [path.join(SIF, 'scripts/schema-tool.mjs'), 'survey'], {
  encoding: 'utf8',
  env: { ...process.env, SIYI_VAULT_ROOT: VAULT, SIYI_SIF_ROOT: SIF },
})
check('CLI survey 退出码 0', r.status === 0, 'exit=' + r.status + ' ' + (r.stderr || '').slice(0, 200))
check('CLI 输出含版本分布', r.stdout.includes('版本分布'), 'output')

// 7. 回滚演练：新建一个缺 schema 的笔记，先备份再补齐，然后恢复
//
// 注意：备份必须在「补齐之前」做。工具 cmdNormalize 就是这个顺序
// （先 dryRun 拿清单 → 备份 → 再实际写入）。这里模拟同样顺序。
const ULID3 = '01M7KJWVWBB7W10TQKVFCGSWXV'
fs.writeFileSync(path.join(dir, ULID3 + '.md'),
  '---\nid: ' + ULID3 + '\ntitle: 回滚测试\nkind: note\n---\n正文C', 'utf8')
const originalText = fs.readFileSync(path.join(dir, ULID3 + '.md'), 'utf8')

// 备份（补齐前）
const backupDir = path.join(TMP, 'backup-test')
fs.mkdirSync(backupDir, { recursive: true })
fs.copyFileSync(path.join(dir, ULID3 + '.md'), path.join(backupDir, 'orig.md'))
check('备份可写入', fs.existsSync(path.join(backupDir, 'orig.md')))

// 补齐
normalizeMissingSchema({ vaultRoot: VAULT, parseNote, buildNote, dryRun: false })
s = surveyVault({ vaultRoot: VAULT, parseNote })
check('补齐后无缺失', s.missing === 0, 'missing=' + s.missing)

// 回滚：恢复原始文件
fs.copyFileSync(path.join(backupDir, 'orig.md'), path.join(dir, ULID3 + '.md'))
const restored = fs.readFileSync(path.join(dir, ULID3 + '.md'), 'utf8')
check('回滚后内容与原始一致', restored === originalText)
s = surveyVault({ vaultRoot: VAULT, parseNote })
check('回滚后重新出现缺失', s.missing === 1, 'missing=' + s.missing)

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
console.log('演练目录:', TMP)
process.exit(fail === 0 ? 0 : 1)
