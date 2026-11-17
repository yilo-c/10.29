import fs from 'node:fs'
const TARGET = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
let src = fs.readFileSync(TARGET, 'utf8')

const startMark = '/** 断言 target 在 VAULT 内；目标不存在时校验最近已存在祖先。 */'
const endMark = '  return resolved\n}\n'
const i = src.indexOf(startMark)
if (i < 0) { console.error('START not found'); process.exit(1) }
const j = src.indexOf(endMark, i)
if (j < 0) { console.error('END not found'); process.exit(1) }
const blockEnd = j + endMark.length

const newBlock = `/**
 * 断言 target 在 VAULT 内，返回规范化后的绝对路径。
 *
 * 为什么不能简单比较 realpath 前缀：Android 上 /data/user/0 是指向
 * /data/data 的符号链接。调用方传进来的路径是 /data/user/0/...（未规范化），
 * 而 realpath(VAULT) 返回 /data/data/...，直接比较必然误判为越界（实测踩到）。
 *
 * 正确做法：取「最近的已存在祖先」做 realpath，再把尚未存在的尾段拼回去。
 * 这样既处理了根路径符号链接，也能在文件尚未创建时判断归属。
 */
function assertInsideVault(target) {
  const realRoot = fs.realpathSync(VAULT)
  const abs = path.resolve(target)

  let cur = abs
  const tail = []
  while (!fs.existsSync(cur)) {
    const parent = path.dirname(cur)
    if (parent === cur) break
    tail.unshift(path.basename(cur))
    cur = parent
  }
  const realAncestor = fs.realpathSync(cur)
  const canonical = tail.length ? path.join(realAncestor, ...tail) : realAncestor

  if (canonical !== realRoot && !canonical.startsWith(realRoot + path.sep)) {
    const e = new Error('路径越界')
    e.code = 'INVALID_ARGUMENT'
    throw e
  }
  return canonical
}
`
src = src.slice(0, i) + newBlock + src.slice(blockEnd)
fs.writeFileSync(TARGET, src, 'utf8')
console.log('patched assertInsideVault')
