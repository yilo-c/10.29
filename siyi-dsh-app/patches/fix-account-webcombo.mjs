// 让 @dsh-android/plugin-account 兼容 Web 组合（dsh-web-app + dsh-host-webserver）。
//
// 背景：官方 plugin-account 无条件 ctx.provide('webServer', loopback)，
// 前提是 SDK 组合（dsh-base + dsh-sdk-app + bundle-android）里没有别的 webServer。
// 思忆集是 Web 组合，dsh-web-app 会挂 @deepseek-ai/dsh-host-webserver，
// 它已经 provide 了 webServer，于是 plugin-account 抛：
//   service "webServer" has been registered at <WebServer>
// 引擎启动失败，登录页白屏。
//
// 修法：检测到 webServer 已存在时改为复用它 —— 把 /oauth/callback 与
// /oauth/complete 两个路由注册到宿主机上，不再自建 loopback 监听。
// 两者接口兼容：register(route) / port / origin / listen()。
//
// 本脚本在 CI 的 profile 构建阶段执行，作用于 node_modules 里的包副本，
// 因此重装 APK 不会丢失。
import fs from 'node:fs'
import path from 'node:path'

const profile = process.argv[2]
if (!profile) {
  console.error('用法: node fix-account-webcombo.mjs <profiles/web 目录>')
  process.exit(1)
}

const PKG = '@dsh-android/plugin-account'

/** 递归找出 plugin-account 包目录（pnpm 可能装在 .pnpm 下） */
function findPkg(root, depth = 0, out = []) {
  if (depth > 6) return out
  let entries
  try { entries = fs.readdirSync(root, { withFileTypes: true }) } catch { return out }
  for (const e of entries) {
    if (!e.isDirectory()) continue
    const full = path.join(root, e.name)
    if (e.name === 'plugin-account' && fs.existsSync(path.join(full, 'index.js'))) {
      out.push(full)
      continue
    }
    if (e.name === '.git' || e.name === 'dist' || e.name === 'assets') continue
    findPkg(full, depth + 1, out)
  }
  return out
}

const dirs = [...new Set(findPkg(profile))]
if (dirs.length === 0) {
  console.log('no ' + PKG + ' found under ' + profile)
  process.exit(0)
}

let changed = 0
for (const dir of dirs) {
  const idx = path.join(dir, 'index.js')
  const lb = path.join(dir, 'loopback.js')
  let s = fs.readFileSync(idx, 'utf8')

  if (s.includes('existingWebServer')) {
    console.log('skip (already patched): ' + path.relative(profile, idx))
    continue
  }

  // ── 1. index.js：provide 改为「有则复用」 ──
  const oldProvide = `  ctx.provide('webServer', loopback)
  ctx.effect(() => () => loopback.close(), 'android-account: loopback server')`
  const newProvide = `  // 思忆集补丁：Web 组合里 dsh-host-webserver 已 provide webServer，
  // 再 provide 会抛 "service webServer has been registered"。有则复用。
  let existingWebServer
  try { existingWebServer = ctx.get('webServer') } catch { existingWebServer = undefined }
  if (existingWebServer === undefined) {
    ctx.provide('webServer', loopback)
    ctx.effect(() => () => loopback.close(), 'android-account: loopback server')
  } else {
    log('info', 'webServer 已存在（Web 组合），把回调路由挂到宿主机上')
    ctx.effect(() => {
      const disposers = []
      for (const p of ['/oauth/callback', '/oauth/complete']) {
        disposers.push(existingWebServer.register({
          kind: 'exact',
          path: p,
          handler: (req, res) => loopback.handle(req, res),
        }))
      }
      return () => {
        for (const d of disposers) { try { d?.() } catch {} }
        try { loopback.close() } catch {}
      }
    }, 'android-account: reuse host webserver')
  }

  // sign-in/start 必须用宿主的 origin 作为 redirect_uri。
  // 官方实现调 loopback.listen()，会起一个随机端口的服务器，但回调路由
  // 挂在了宿主机上，浏览器回到随机端口会 404，loginOrigin 校验失败 → protocol。
  //
  // ⚠ 宿主服务上**没有** origin 这个属性：dsh-host-webserver 只暴露 getOrigin()。
  // 早先这里读 existingWebServer.origin，拿到 undefined，redirect_uri 变成
  // "undefined/oauth/callback"，服务端直接按 protocol 错误打回，
  // 表现就是「登录转一圈回来白屏 + sign-in failed { errorCode: 'protocol' }」。
  // 未绑定端口时 getOrigin() 会抛错，退回自建 loopback 保底。
  const signInOrigin = existingWebServer === undefined
    ? undefined
    : () => {
        try {
          return typeof existingWebServer.getOrigin === 'function'
            ? existingWebServer.getOrigin()
            : existingWebServer.origin
        } catch {
          return undefined
        }
      }`

  if (!s.includes(oldProvide)) {
    console.log('no match (provide): ' + path.relative(profile, idx))
  } else {
    s = s.replace(oldProvide, newProvide)
    fs.writeFileSync(idx, s)
    changed += 1
    console.log('patched index.js: ' + path.relative(profile, idx))
  }

  // ── 3. index.js：sign-in/start 用宿主 origin ──
  if (s.includes('const origin = await loopback.listen()')) {
    // signInOrigin() 取不到时必须真的退回自建 loopback，
    // 否则 origin 是 undefined，redirect_uri 拼出来就是 "undefined/oauth/callback"。
    s = s.replace('const origin = await loopback.listen()',
      'const origin = (signInOrigin && signInOrigin()) || await loopback.listen()')
    fs.writeFileSync(idx, s)
    changed += 1
    console.log('patched sign-in/start origin: ' + path.relative(profile, idx))
  }

  // ── 2. loopback.js：暴露 handle ──
  if (fs.existsSync(lb)) {
    let t = fs.readFileSync(lb, 'utf8')
    if (!t.includes('handle(req, res) { return handle(req, res) }')) {
      const oldRet = `  return {
    register(r) {`
      const newRet = `  return {
    // 思忆集补丁：Web 组合下由宿主机转发请求，需要直接调用内部 handle。
    handle(req, res) { return handle(req, res) },
    register(r) {`
      if (t.includes(oldRet)) {
        t = t.replace(oldRet, newRet)
        fs.writeFileSync(lb, t)
        changed += 1
        console.log('patched loopback.js: ' + path.relative(profile, lb))
      } else {
        console.log('no match (loopback return): ' + path.relative(profile, lb))
      }
    } else {
      console.log('skip (loopback already patched): ' + path.relative(profile, lb))
    }
  }
}

console.log(`done, ${changed} file(s) changed (scanned ${dirs.length})`)
