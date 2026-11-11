// 用户插件动态加载器（服务端半身）
//
// 引擎启动时扫描 DSH_USER_PLUGINS 目录下的 .js 文件并加载。
// 约定：每个文件导出 name + apply(ctx, config)（可选 inject / Config）。
// 一个坏插件只记日志，不影响引擎启动。
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

export const name = 'siyi-user-plugins'

/**
 * 必须声明 inject：cordis 的 ctx 是 Proxy，未在 inject 里列出的服务
 * 一读就抛 cannot get property "xxx" without inject。
 * 用户插件最常用的是 ctx.tools，所以至少要声明它。
 */
export const inject = ['tools', 'commands', 'sessions', 'credentials', 'systemPrompt']

function log(level, msg) {
  try { process.stderr.write(`[siyi-user-plugins] ${level}: ${msg}\n`) } catch {}
}

export function apply(ctx) {
  // 用户插件放在 /sdcard/思忆集/plugins，那里没有 node_modules，
  // 无法直接 import '@deepseek-ai/dsh-tools'。这里从引擎侧解析后
  // 挂到 ctx 上，用户插件用 ctx.defineTool(...) 即可。
  let defineTool
  try {
    const req = createRequire(import.meta.url)
    defineTool = req('@deepseek-ai/dsh-tools').defineTool
  } catch (e) {
    log('warn', '无法加载 dsh-tools，用户插件将不能注册工具: ' + e.message)
  }
  if (defineTool) {
    try { ctx.provide('defineTool', defineTool) } catch {}
  }

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
        // 第二个参数除 config 外再挂 defineTool，避开 cordis 的 inject 限制：
        // 用户插件自己 provide 不了服务，但可以直接拿到这个函数。
        if (defineTool) cfg.defineTool = defineTool
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
