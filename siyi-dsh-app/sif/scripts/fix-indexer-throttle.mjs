// 修复：节流导致「刚修好的文件」仍报 failure
//
// 现象：test '修复坏文件后 failure 消失' 失败，expected 0 got 1。
// 根因：scan() 有 250ms 节流，测试在 250ms 内连续调用两次，
//       第二次被节流跳过，没有重读被修复的文件。
//
// 这暴露一个真实设计问题：
//   节流是为了避免高频请求反复扫描，但「外部修改后立刻查询」
//   是真实用户场景（编辑器保存后马上刷新），不该被节流挡住。
//
// 正确修法：
//   1. 保留节流（高频重复查询仍有收益）
//   2. 增加「脏标记」：外部写入或显式 invalidate 后，下次 scan 强制穿透节流
//   3. 判断文件是否变化仍靠 mtime/size，不是靠时间窗口

import fs from 'node:fs'
const p = 'core/vault/indexer.mjs'
let s = fs.readFileSync(p, 'utf8')

if (s.includes('dirtyFlag')) { console.log('SKIP'); process.exit(0) }

// 1. 加脏标记
const oldState = `  let lastFullScanAt = 0
  let lastFullScanMs = 0
  let scans = 0
  let incrementalHits = 0`
const newState = `  let lastFullScanAt = 0
  let lastFullScanMs = 0
  let scans = 0
  let incrementalHits = 0
  /** 脏标记：外部写入/失效后置位，下次 scan 强制穿透节流。 */
  let dirtyFlag = false`
if (!s.includes(oldState)) { console.error('STATE NOT FOUND'); process.exit(1) }
s = s.replace(oldState, newState)

// 2. scan 检查脏标记
const oldScan = `    const t0 = now()
    if (!force && t0 - lastFullScanAt < minIntervalMs) {
      incrementalHits++
      return { scanned: false, files: entries.size }
    }`
const newScan = `    const t0 = now()
    // 脏标记优先于节流：外部刚写过，必须重新扫，否则读到旧数据
    if (!force && !dirtyFlag && t0 - lastFullScanAt < minIntervalMs) {
      incrementalHits++
      return { scanned: false, files: entries.size }
    }
    dirtyFlag = false`
if (!s.includes(oldScan)) { console.error('SCAN NOT FOUND'); process.exit(1) }
s = s.replace(oldScan, newScan)

// 3. invalidate / put 置脏
const oldInv = `  function invalidate(filePath) {
    entries.delete(filePath)
    failures.delete(filePath)
  }`
const newInv = `  function invalidate(filePath) {
    entries.delete(filePath)
    failures.delete(filePath)
    dirtyFlag = true
  }`
if (!s.includes(oldInv)) { console.error('INVALIDATE NOT FOUND'); process.exit(1) }
s = s.replace(oldInv, newInv)

const oldPut = `    failures.delete(filePath)
  }

  return { scan, all, byId, rebuild, stats, invalidate, put, root: realRoot, entries, failures }`
const newPut = `    failures.delete(filePath)
    // put 是写入后的主动更新，索引已是最新，不需要置脏；
    // 但要让下一次 scan 不因节流而漏掉其它文件的真实变化
    dirtyFlag = true
  }

  /** 显式标记需要重新扫描（外部修改文件后调用）。 */
  function markDirty() {
    dirtyFlag = true
  }

  return { scan, all, byId, rebuild, stats, invalidate, put, markDirty, root: realRoot, entries, failures }`
if (!s.includes(oldPut)) { console.error('PUT NOT FOUND'); process.exit(1) }
s = s.replace(oldPut, newPut)

fs.writeFileSync(p, s, 'utf8')
console.log('PATCHED: dirtyFlag bypasses throttle')
