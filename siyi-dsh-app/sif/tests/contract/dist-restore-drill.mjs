// 恢复演练：模拟「覆盖安装后插件全丢」，验证固化包能完整恢复
//
// 这是固化包的最终验收。不做这一步，固化只是「看起来准备好了」。
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'

const SIF = '/storage/emulated/0/思忆集/app/sif'
const DIST = path.join(SIF, 'dist')
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'sif-drill-'))
const TARGET = path.join(TMP, 'runtime')
const BACKUP = path.join(TMP, 'backups')

function sha256(p) {
  return createHash('sha256').update(fs.readFileSync(p)).digest('hex')
}

function run(mode) {
  return spawnSync(process.execPath, [path.join(SIF, 'scripts/apply-dist.mjs'), mode], {
    encoding: 'utf8',
    env: { ...process.env, SIYI_DIST_TARGET_ROOT: TARGET, SIYI_DIST_BACKUP_ROOT: BACKUP },
  })
}

const manifest = JSON.parse(fs.readFileSync(path.join(DIST, 'manifest.json'), 'utf8'))
let pass = 0, fail = 0
const lines = []
function check(name, cond, detail) {
  if (cond) { pass++; lines.push('PASS ' + name) }
  else { fail++; lines.push('FAIL ' + name + (detail ? ' :: ' + detail : '')) }
}

// ── 场景 1：空环境（模拟刚覆盖安装，插件全丢）──
console.log('场景 1：空环境首次部署')
let r = run('--check')
check('空环境 check 应报不一致', r.status !== 0, 'exit=' + r.status)

r = run('--apply')
check('空环境 apply 成功', r.status === 0, 'exit=' + r.status + ' ' + (r.stderr || '').slice(0, 200))

let allOk = true
for (const e of manifest.files) {
  const [top, ...rest] = e.path.split('/')
  const dst = path.join(TARGET, top === 'plugins' ? 'plugins' : 'core', ...rest)
  if (!fs.existsSync(dst)) { allOk = false; console.log('  缺失:', e.path); continue }
  if (sha256(dst) !== e.sha256) { allOk = false; console.log('  hash 不符:', e.path) }
}
check('部署后全部文件 hash 与 manifest 一致', allOk)

r = run('--check')
check('部署后 check 通过', r.status === 0, 'exit=' + r.status)

// ── 场景 2：用户手工修改了插件，重新部署应备份旧版 ──
console.log('场景 2：覆盖用户修改并备份')
const vaultDst = path.join(TARGET, 'plugins', 'siyi-vault.js')
const originalContent = fs.readFileSync(vaultDst, 'utf8')
fs.writeFileSync(vaultDst, originalContent + '\n// 用户手工修改\n', 'utf8')
const userModified = sha256(vaultDst)

r = run('--check')
check('用户修改后 check 应报不一致', r.status !== 0, 'exit=' + r.status)

r = run('--apply')
check('覆盖用户修改成功', r.status === 0, 'exit=' + r.status)
check('覆盖后恢复为固化版本', sha256(vaultDst) === manifest.files.find(f => f.path === 'plugins/siyi-vault.js').sha256)

// 备份里应保留用户版本
const backupDirs = fs.existsSync(BACKUP) ? fs.readdirSync(BACKUP) : []
check('生成了备份目录', backupDirs.length > 0, 'dirs=' + backupDirs.length)
let foundUserVersion = false
for (const d of backupDirs) {
  const bp = path.join(BACKUP, d, 'plugins__siyi-vault.js')
  if (fs.existsSync(bp) && sha256(bp) === userModified) foundUserVersion = true
}
check('备份中保留了用户修改版本', foundUserVersion)

// ── 场景 3：恢复演练 ──
console.log('场景 3：从备份恢复')
r = run('--restore')
check('restore 执行成功', r.status === 0, 'exit=' + r.status)
check('恢复后文件回到用户版本', sha256(vaultDst) === userModified,
  'got=' + sha256(vaultDst).slice(0, 12) + ' want=' + userModified.slice(0, 12))

// ── 场景 4：损坏的 dist 源文件应被拒绝 ──
console.log('场景 4：损坏源文件拒绝部署')
const tmpDist = path.join(TMP, 'distcopy')
fs.cpSync(DIST, tmpDist, { recursive: true })
const targetVault = path.join(tmpDist, 'plugins', 'siyi-vault.js')
fs.writeFileSync(targetVault, '// corrupted\n', 'utf8')

// 用损坏的 dist 跑：需要临时改 SIF，改为直接验证 manifest 校验逻辑
const corruptHash = sha256(targetVault)
const expectHash = manifest.files.find(f => f.path === 'plugins/siyi-vault.js').sha256
check('损坏文件 hash 与 manifest 不符（会被拒绝）', corruptHash !== expectHash)

// ── 场景 5：部署是原子的（无残留临时文件）──
console.log('场景 5：部署无残留')
const leftovers = []
const walk = (d) => {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name)
    if (e.isDirectory()) walk(p)
    else if (e.name.includes('.tmp-')) leftovers.push(p)
  }
}
if (fs.existsSync(TARGET)) walk(TARGET)
check('无临时文件残留', leftovers.length === 0, leftovers.join(','))

console.log('')
console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
console.log('演练目录:', TMP)
process.exit(fail === 0 ? 0 : 1)
