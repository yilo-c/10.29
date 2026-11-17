import fs from 'node:fs'
const p = 'core/bridge/server.mjs'
let s = fs.readFileSync(p, 'utf8')

const oldBlock = `    const controller = new AbortController()
    inflight.set(call, { controller, operationId: opId, attemptId: attId, tool, isWrite })

    const timeoutMs = impl.descriptor.timeoutMs ?? 30000
    const timer = setTimeout(() => controller.abort(Object.assign(new Error('timeout'), { code: 'TIMEOUT' })), timeoutMs)

    try {
      const value = await impl.execute(args ?? {}, { signal: controller.signal, operationId: opId, attemptId: attId, callId: call })
      clearTimeout(timer)`

const newBlock = `    const controller = new AbortController()
    inflight.set(call, { controller, operationId: opId, attemptId: attId, tool, isWrite })

    const timeoutMs = impl.descriptor.timeoutMs ?? 30000
    // 超时必须由服务端强制生效：仅 abort signal 不够，因为能力实现可能不监听它。
    // 用 Promise.race 让 deadline 先到就先返回 TIMEOUT，晚到结果只记审计。
    let timer = null
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        const err = Object.assign(new Error('capability ' + tool + ' timed out after ' + timeoutMs + 'ms'), { code: 'TIMEOUT' })
        try { controller.abort(err) } catch {}
        reject(err)
      }, timeoutMs)
    })

    try {
      const value = await Promise.race([
        impl.execute(args ?? {}, { signal: controller.signal, operationId: opId, attemptId: attId, callId: call }),
        deadline,
      ])
      clearTimeout(timer)`

if (!s.includes(oldBlock)) { console.error('BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldBlock, newBlock)

// catch 里也要清理 timer
const oldCatch = `    } catch (e) {
      clearTimeout(timer)
      inflight.delete(call)
      const code = e && e.code ? String(e.code) : 'INTERNAL'`
const newCatch = `    } catch (e) {
      if (timer) clearTimeout(timer)
      inflight.delete(call)
      const code = e && e.code ? String(e.code) : 'INTERNAL'`
if (!s.includes(oldCatch)) { console.error('CATCH NOT FOUND'); process.exit(1) }
s = s.replace(oldCatch, newCatch)

fs.writeFileSync(p, s, 'utf8')
console.log('patched server deadline enforcement')
