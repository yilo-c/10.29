import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 路由可达性机器断言（2026-09-16 新增，防复发门禁）。
 *
 * 由来：2026-09-09「导航收敛」把工作台等模块**从侧边栏隐藏**，但提交 17ecc90 顺手把
 * `ContentRouter` 的 `case 'dashboard'` 整段删了。侧边栏少一项看不出来，代价是：
 *   - store 启动默认值 `activeModule: 'dashboard'`（uiSlice.ts）落到 `default` 分支
 *     渲染成 NewChatView —— 启动页静默错位；
 *   - 快捷键 4（MainWindow moduleMap）、快速搜索「工作台」（QuickSearchModal nav 列表）、
 *     主动建议面板（SuggestionPanel）共 4 处调用点全部失效，且**没有任何测试会红**。
 * 单元测试全 mock、E2E 的 dashboard 用例只断言「有圆角卡片」→ 假绿。所以这里补一条
 * **静态断言**：凡是能把 `activeModule` 设成某个模块 id 的入口，ContentRouter 必须
 * 有对应 case。隐藏导航 ≠ 删路由，两者必须解耦。
 *
 * 覆盖的 5 类入口（漏一类就等于漏一条静默失效路径）：
 *   1. store 启动默认值（uiSlice.ts）
 *   2. `setActiveModule('<字面量>')`
 *   3. NavSidebar 的 SECTIONS 项
 *   4. NavSidebar 的 MERGED_DEFAULTS 合并目标（点父项落到子模块）
 *   5. QuickSearchModal 的 nav 列表 + MainWindow 的 Ctrl+数字 快捷键表
 *
 * 为什么用静态源码扫描而不是渲染测试：ContentRouter 挂了一堆 React.lazy + ErrorBoundary，
 * 渲染测试要 mock 二十来个模块才能跑，成本远高于收益，且新增模块时测试自身也要改。
 * 源码扫描零 mock，新增模块只要忘了加 case 立刻红。
 *
 * 注意 `setActiveModule(x as 'dashboard' | ...)` 这类**类型断言调用**扫不到字面量，
 * 所以第 3/5 类入口必须单独覆盖 —— 只扫字面量会漏掉 dashboard（它正是这么被调用的）。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const DESKTOP_SRC = path.join(PKG, 'src')
const SHARED_SRC = path.resolve(PKG, '..', 'shared', 'src')
const MW = path.join(DESKTOP_SRC, 'components', 'MainWindow')
const ROUTER = path.join(MW, 'ContentRouter.tsx')
const NAV_SIDEBAR = path.join(MW, 'NavSidebar.tsx')
const QUICK_SEARCH = path.join(DESKTOP_SRC, 'components', 'Search', 'QuickSearchModal.tsx')

const read = (p: string) => fs.readFileSync(p, 'utf8')
const rel = (p: string) => path.relative(PKG, p).replace(/\\/g, '/')

/** 允许「有入口但 ContentRouter 故意没有 case」的模块（每条必须写理由）。 */
const ALLOWLIST = new Map<string, string>([])

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (['node_modules', '__tests__', 'dist'].includes(entry.name)) continue
      walk(full, out)
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

/** ContentRouter 里 switch (activeModule) 的 case 标签集合。 */
function routedModules(): Set<string> {
  const set = new Set<string>()
  for (const m of read(ROUTER).matchAll(/^\s*case\s+'([^']+)'\s*:/gm)) set.add(m[1])
  return set
}

/** 模块 id → 出现该 id 的入口来源描述（人可读，失败信息里直接给）。 */
function navigationSources(): Map<string, Set<string>> {
  const add = (map: Map<string, Set<string>>, mod: string, where: string) => {
    if (!mod || mod.includes(' ')) return
    if (!map.has(mod)) map.set(mod, new Set())
    map.get(mod)!.add(where)
  }
  const sources = new Map<string, Set<string>>()

  // 1) store 启动默认值
  const uiSlice = read(path.join(SHARED_SRC, 'store', 'slices', 'uiSlice.ts'))
  const boot = uiSlice.match(/^\s*activeModule:\s*'([^']+)'/m)
  if (boot) add(sources, boot[1], 'store 启动默认值 uiSlice.ts')

  // 2) setActiveModule('<字面量>')
  for (const file of walk(DESKTOP_SRC)) {
    for (const m of read(file).matchAll(/setActiveModule\(\s*'([^']+)'\s*\)/g)) {
      add(sources, m[1], `setActiveModule('${m[1]}') @ ${rel(file)}`)
    }
  }

  // 3) NavSidebar SECTIONS 项
  const nav = read(NAV_SIDEBAR)
  const sectionsBody = nav.slice(nav.indexOf('const SECTIONS'), nav.indexOf('const MERGED_DEFAULTS'))
  for (const m of sectionsBody.matchAll(/items:\s*\[([^\]]*)\]/g)) {
    for (const item of m[1].split(',').map((s) => s.trim().replace(/^'|'$/g, '')).filter(Boolean)) {
      add(sources, item, 'NavSidebar SECTIONS 侧边栏项')
    }
  }

  // 4) NavSidebar MERGED_DEFAULTS 合并目标
  const mergedBody = nav.slice(nav.indexOf('const MERGED_DEFAULTS'))
  const mergedBlock = mergedBody.slice(0, mergedBody.indexOf('}'))
  for (const m of mergedBlock.matchAll(/:\s*'([^']+)'/g)) {
    add(sources, m[1], 'NavSidebar MERGED_DEFAULTS 合并目标')
  }

  // 5a) QuickSearchModal nav 列表（用 labelKey: 'search.nav*' 与 action 列表区分开）
  for (const m of read(QUICK_SEARCH).matchAll(/\{\s*key:\s*'([^']+)',\s*labelKey:\s*'search\.nav[^']*'/g)) {
    add(sources, m[1], 'QuickSearchModal 导航项')
  }
  // 5b) MainWindow Ctrl+数字 快捷键表
  const mwSrc = read(path.join(MW, 'MainWindow.tsx'))
  const mapBody = mwSrc.slice(mwSrc.indexOf('moduleMap: Record<string, ActiveModule>'))
  const mapBlock = mapBody.slice(0, mapBody.indexOf('}'))
  for (const m of mapBlock.matchAll(/:\s*'([^']+)'/g)) {
    add(sources, m[1], 'MainWindow Ctrl+数字 快捷键表')
  }

  return sources
}

function missingRoutes(): string[] {
  const routed = routedModules()
  const out: string[] = []
  for (const [mod, where] of navigationSources()) {
    if (routed.has(mod) || ALLOWLIST.has(mod)) continue
    out.push(`  '${mod}' ← ${[...where].join('; ')}`)
  }
  return out
}

describe('路由可达性：隐藏导航不得删路由', () => {
  it('ContentRouter 的 switch 有兜底 default 分支', () => {
    expect(/^\s*default\s*:/m.test(read(ROUTER))).toBe(true)
  })

  it('每个可设 activeModule 的入口都有对应 case（17ecc90 事故判据）', () => {
    const missing = missingRoutes()
    expect(
      missing,
      '以下模块存在导航入口，但 ContentRouter 里没有 case —— 点了只会渲染 default 兜底模块（静默失效）：\n' +
        `${missing.join('\n')}\n` +
        '隐藏导航入口 ≠ 删除路由；若确实要下线该模块，请连入口一起删并更新本测试白名单。',
    ).toEqual([])
  })

  it('E2E 侧栏计数断言与 NavSidebar 源码结构一致', () => {
    const nav = read(NAV_SIDEBAR)
    // 注意用 'const MERGED_DEFAULTS'：文件头注释里先出现过一次裸词 'MERGED_DEFAULTS'，
    // 用裸词做 indexOf 会把切片起点放到终点之后，切片成空串。
    const sectionsBody = nav.slice(nav.indexOf('const SECTIONS'), nav.indexOf('const MERGED_DEFAULTS'))
    const sectionItems = [...sectionsBody.matchAll(/items:\s*\[([^\]]*)\]/g)]
      .flatMap((m) => m[1].split(',').map((s) => s.trim()).filter(Boolean))
    // footer 目前固定 1 个「设置」按钮（NavSidebar 的 nav-footer 区块）
    const FOOTER_ITEMS = 1
    const expected = sectionItems.length + FOOTER_ITEMS

    const asserted = read(path.join(PKG, 'e2e', 'smoke.spec.ts')).match(/toHaveCount\((\d+)\)/)
    expect(asserted, 'smoke.spec.ts 里找不到 toHaveCount 断言').not.toBeNull()

    expect(
      Number(asserted?.[1]),
      `smoke.spec.ts 断言侧栏 ${asserted?.[1]} 个 nav-item，但 NavSidebar 源码是 ` +
        `${sectionItems.length} 个分组项 + ${FOOTER_ITEMS} 个 footer = ${expected} 个。` +
        '改导航结构请同步 e2e/smoke.spec.ts 的 toHaveCount。',
    ).toBe(expected)
  })
})
