// Test if PowerShell Copy-Item works when spawned from Node.js
const { execSync } = require('child_process')
const fs = require('fs')
const os = require('os')
const path = require('path')

const tmpFile = path.join(os.tmpdir(), 'test-node-ps.tmp')
fs.writeFileSync(tmpFile, 'test content')

const dstFile = path.join('C:\\Users\\Administrator\\AppData\\Roaming\\desk-notes\\siyi-notes\\notes', 'test-node-ps.md')

const psCmd = `powershell -NoProfile -Command "Copy-Item -LiteralPath '${tmpFile}' -Destination '${dstFile}' -Force"`
console.log('PS CMD:', psCmd)

try {
  const out = execSync(psCmd, { encoding: 'utf-8', stdio: 'pipe' })
  console.log('PS OUTPUT:', out)
  console.log('DST EXISTS:', fs.existsSync(dstFile))
  if (fs.existsSync(dstFile)) {
    console.log('CONTENT:', fs.readFileSync(dstFile, 'utf-8'))
    try { fs.unlinkSync(dstFile) } catch (e) { console.log('unlink failed:', e.code) }
  }
} catch (e) {
  console.log('PS FAILED:', e.message)
  console.log('STDERR:', e.stderr ? e.stderr.toString() : 'none')
  console.log('STDOUT:', e.stdout ? e.stdout.toString() : 'none')
  console.log('STATUS:', e.status)
}

try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
