// 用户插件动态加载器（服务端半身）
//
// 引擎启动时扫描 DSH_USER_PLUGINS 目录下的 .js 文件并加载。
// 约定：每个文件导出 name + apply(ctx, config)（可选 inject / Config）。
// 一个坏插件只记日志，不影响引擎启动。
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

export const name = 'siyi-user-plugins'
export const inject = []

function log(level, msg) {
  try { process.stderr.write(`[siyi-user-plugins] ${level}: ${msg}\n`) } catch {}
}

export function apply(ctx) {
  const dir = process.env.DSH_USER_PLUGINS
  if (!dir) { log('info', 'DSH_USER_PLUGINS 未设置，跳过'); return }
  let files
  try {
    files = fs.readdirSync(dir).filter(f => f.endsWith('.js')).sort()
  } catch (e) {
    log('info', '目录不可读，跳过: ' + e.message)
    return
  }
  log('info', '扫描 ' + dir + ' -> ' + files.length + ' 个 .js')

  const disposers = []
  for (const f of files) {
    const file = path.join(dir, f)
    // 同步 import 不可用，这里用 createRequire 走 CJS 兼容；
    // ESM 用户插件通过下面的动态 import().then 异步挂载。
    const url = pathToFileURL(file).href + '?t=' + Date.now()
    import(url).then(mod => {
      const pName = mod.name || f.replace(/\.js$/, '')
      if (typeof mod.apply !== 'function') {
        log('warn', f + ' 没有 apply()，跳过')
        return
      }
      try {
        // 直接用 ctx：ctx.isolate(name) 是「隔离某个具名服务」，
        // 不是创建通用子上下文，误用会拿不到 ctx.tools 等内建能力。
        const cfg = typeof mod.Config === 'function' ? mod.Config({}) : {}
        const d = mod.apply(ctx, cfg)
        if (typeof d === 'function') disposers.push(d)
        log('info', '已加载 ' + f + ' (' + pName + ')')
      } catch (e) {
        log('warn', 'apply ' + f + ' 失败: ' + e.message)
      }
    }).catch(e => log('warn', '加载 ' + f + ' 失败: ' + e.message))
  }

  ctx.effect(() => () => {
    for (const d of disposers) { try { d() } catch {} }
    log('info', '用户插件已卸载')
  }, 'siyi-user-plugins: dispose')
}
