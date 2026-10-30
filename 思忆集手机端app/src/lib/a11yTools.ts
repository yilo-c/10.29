import SiyiNative, { withNativeTimeout } from './native/siyiNative'
import { ExecutionResult, type AiTool } from './toolRegistry'

/**
 * 无障碍自动化工具集 / Accessibility automation tools
 *
 * 目标：把手机原生的「读屏 + 点击 + 滑动 + 启动应用」能力交给 AI 调度，
 * 让它能真的替用户操作手机，而不只是回答问题。
 *
 * 能力来源：SiyiAccessibilityService（用户需在系统设置里手动开启）。
 *
 * 风险分级（沿用 toolRegistry 的 R0-R3 体系）：
 *   - readScreen          → R0_INFO   只读，无副作用
 *   - listApps            → R0_INFO   只读
 *   - launchApp           → R1_LIGHT  切前台，不改数据
 *   - navBack/Home/Recents→ R1_LIGHT  导航按键
 *   - tap / swipe         → R3_MODIFY 可能触发任意操作（下单、删除、发送）
 *
 * 关于 tap/swipe 定为 R3：这类工具的能力边界等于「用户的手指」，
 * 点错地方后果不可预估（误付款、误删消息）。因此：
 *   1. 调用前必须让用户看到 AI 打算点哪
 *   2. 未开启无障碍时直接返回明确错误，不静默失败
 */

/* ── 内部工具函数 ── */

async function requireAccessibility(): Promise<string | null> {
  try {
    const st = await withNativeTimeout(SiyiNative.isAccessibilityRunning(), 3000, 'isAccessibilityRunning')
    if (!st?.running) {
      return '无障碍服务未开启。请到 设置 → 更多设置 → 无障碍 → 已下载的应用 里打开「思忆便签」，再重试。'
    }
    return null
  } catch {
    return '无法访问无障碍服务（可能未安装原生壳，或在浏览器预览环境）。'
  }
}

/** 把读屏文本截断到合理长度，避免塞爆上下文 */
function clampText(s: string, max = 3000): string {
  if (!s) return ''
  return s.length > max ? s.slice(0, max) + `\n…（已截断，原文 ${s.length} 字）` : s
}

/* ── 工具定义 ── */

export const a11yTools: AiTool[] = [
  {
    id: 'phone.read_screen',
    description: '读取当前屏幕上的所有文字（含按钮名称、列表项、输入框内容），并列出可点击元素及其屏幕坐标。用于了解用户当前在哪个界面、有哪些可操作元素，再用 phone.tap 或 phone.tap_text 操作。',
    parameters: null,
    tags: ['屏幕', '读取', '当前界面', '看看', '屏幕上有什么', '读屏', '识别界面'],
    riskLevel: 'R0_INFO',
    execute: async () => {
      const err = await requireAccessibility()
      if (err) return ExecutionResult.failure(err)
      try {
        // 优先用结构化读屏：一次拿到文字 + 可点击元素坐标，
        // 避免「先纯文本、再猜坐标」的二次往返（猜坐标正是误点的主因）。
        const structured = await withNativeTimeout(
          SiyiNative.readScreenNodes(), 6000, 'readScreenNodes'
        ).catch(() => null)

        if (structured?.available && Array.isArray(structured.nodes) && structured.nodes.length > 0) {
          const nodes = structured.nodes
          // 文字部分：去重保序，供 AI 理解界面内容
          const seen = new Set<string>()
          const texts: string[] = []
          for (const n of nodes) {
            const t = (n.text || '').trim()
            if (t && !seen.has(t)) {
              seen.add(t)
              texts.push(t)
            }
          }
          const lines: string[] = []
          lines.push('【屏幕文字】')
          lines.push(texts.length ? clampText(texts.join('\n'), 2000) : '（无可读文字）')

          // 可点击元素：给出文字 + 中心坐标，AI 可直接点
          const clickables = nodes.filter(n => n.clickable || n.editable || n.checkable)
          if (clickables.length) {
            lines.push('')
            lines.push('【可点击元素（可直接用于 phone.tap 的 x/y）】')
            clickables.slice(0, 40).forEach((n, i) => {
              const label = (n.text || '').trim() || n.viewId || n.className || '(无文字)'
              const kind = n.editable ? '输入框' : n.checkable ? '勾选框' : '按钮'
              lines.push(`${i + 1}. ${label}  [${kind}]  x=${n.cx} y=${n.cy}`)
            })
          } else {
            lines.push('')
            lines.push('【可点击元素】（本屏未识别到可点击元素，可能是受保护界面或纯图片界面）')
          }
          return ExecutionResult.success(lines.join('\n'))
        }

        // 退化路径：结构化读屏不可用（旧版原生壳）时仍返回纯文本
        const r = await withNativeTimeout(SiyiNative.readScreen(), 6000, 'readScreen')
        if (!r?.available) return ExecutionResult.failure('无障碍服务不可用')
        const text = clampText(r.text || '')
        if (!text.trim()) return ExecutionResult.success('当前屏幕没有可读文字（可能是空白页或受保护界面）。')
        return ExecutionResult.success(text)
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '读屏失败')
      }
    },
  },

  {
    id: 'phone.tap_text',
    description: '按屏幕上的文字点击元素（如「发送」「确定」「设置」）。比 phone.tap 按坐标点更安全：原生侧直接定位该文字所在控件并点其中心，不受屏幕尺寸/密度变化影响。找不到该文字会明确报错，不会误点别处。',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '要点击的元素上显示的文字，如「发送」「确定」「下一步」。' },
        exact: { type: 'boolean', description: '是否精确匹配全文。默认 false（包含匹配即可），建议先用 false。' },
      },
      required: ['text'],
    },
    tags: ['按文字点击', '点击文字', '点按钮', '点发送', '点确定', '点这个'],
    riskLevel: 'R3_MODIFY',
    execute: async (params) => {
      const err = await requireAccessibility()
      if (err) return ExecutionResult.failure(err)
      const text = String(params.text ?? '').trim()
      if (!text) return ExecutionResult.failure('需要提供要点击的文字')
      try {
        const r = await withNativeTimeout(
          SiyiNative.tapNode({ text, exact: params.exact === true }), 5000, 'tapNode'
        )
        return r?.success
          ? ExecutionResult.success(`已点击「${text}」`)
          : ExecutionResult.failure(r?.error || `未找到文字为「${text}」的可点击元素`)
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '点击失败')
      }
    },
  },

  {
    id: 'phone.launch_app',
    description: '启动指定包名的应用并切到前台。需要先知道包名，可用 phone.list_apps 查询。',
    parameters: {
      type: 'object',
      properties: {
        packageName: { type: 'string', description: '应用包名，如 com.tencent.mm（微信）' },
      },
      required: ['packageName'],
    },
    // 注意：这里不要放泛化的「应用」单字 tag。
    // matchTools 是纯关键词计数，「手机上装了什么应用」会同时命中
    // launch_app 的「应用」和 list_apps 的「应用列表」，
    // 若计分相同则按注册顺序选前者 → 误调用启动而非查询。
    tags: ['打开', '启动', '打开应用', '启动应用', '打开app', 'launch'],
    riskLevel: 'R1_LIGHT',
    execute: async (params) => {
      const pkg = String(params.packageName || '').trim()
      if (!pkg) return ExecutionResult.failure('缺少 packageName')
      try {
        const r = await withNativeTimeout(SiyiNative.launchApp({ packageName: pkg }), 8000, 'launchApp')
        return r?.success
          ? ExecutionResult.success(`已启动 ${pkg}`)
          : ExecutionResult.failure(r?.error || `启动失败：${pkg}（包名可能不对，或应用未安装）`)
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '启动失败')
      }
    },
  },

  {
    id: 'phone.list_apps',
    description: '列出手机上已安装的应用（名称 + 包名），用于查询要启动哪个应用。',
    parameters: {
      type: 'object',
      properties: {
        limit: { type: 'number', description: '最多返回多少个，默认 60。应用多时可调到 100。' },
      },
    },
    tags: ['应用列表', '装了什么', '装了什么应用', '有哪些应用', '有什么应用',
           '手机上装', '已安装', '找应用', '应用清单'],
    riskLevel: 'R0_INFO',
    execute: async (params) => {
      try {
        const limit = typeof params.limit === 'number' ? params.limit : 60
        const r = await withNativeTimeout(SiyiNative.getInstalledApps({ limit }), 8000, 'getInstalledApps')
        const apps = r?.result?.apps
        if (!apps || apps.length === 0) {
          return ExecutionResult.failure(r?.error || '未取到应用列表')
        }
        const lines = apps.map(a => `${a.name || a.packageName}  (${a.packageName})`)
        return ExecutionResult.success(lines.join('\n'))
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '查询失败')
      }
    },
  },

  {
    id: 'phone.tap',
    description: '在屏幕坐标 (x, y) 处点击一次。坐标单位是屏幕物理像素，可用 phone.read_screen 或用户描述推断。高风险：可能触发付款、发送、删除等操作。',
    parameters: {
      type: 'object',
      properties: {
        x: { type: 'number', description: '屏幕横坐标（物理像素）。可用 phone.read_screen 先确认元素位置，或按屏幕宽度估算。' },
        y: { type: 'number', description: '屏幕纵坐标（物理像素）。注意状态栏和导航栏占用的高度也要算进去。' },
      },
      required: ['x', 'y'],
    },
    tags: ['点击', '点一下', '按', 'tap'],
    riskLevel: 'R3_MODIFY',
    execute: async (params) => {
      const err = await requireAccessibility()
      if (err) return ExecutionResult.failure(err)
      const x = Number(params.x)
      const y = Number(params.y)
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return ExecutionResult.failure('需要有效的 x、y 坐标')
      }
      try {
        const r = await withNativeTimeout(SiyiNative.tap({ x, y }), 5000, 'tap')
        return r?.success
          ? ExecutionResult.success(`已在 (${x}, ${y}) 点击`)
          : ExecutionResult.failure(r?.error || '点击失败')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '点击失败')
      }
    },
  },

  {
    id: 'phone.swipe',
    description: '从 (x1,y1) 滑动到 (x2,y2)，用于翻页、滚动列表、下拉刷新。高风险：可能触发意外操作。',
    parameters: {
      type: 'object',
      properties: {
        x1: { type: 'number', description: '起点横坐标（物理像素）' },
        y1: { type: 'number', description: '起点纵坐标（物理像素）' },
        x2: { type: 'number', description: '终点横坐标（物理像素）' },
        y2: { type: 'number', description: '终点纵坐标（物理像素）' },
        duration: { type: 'number', description: '滑动时长毫秒，默认 300。快速滑动用 150，缓慢拖拽用 600+。' },
      },
      required: ['x1', 'y1', 'x2', 'y2'],
    },
    tags: ['滑动', '滚动', '翻页', 'swipe', '上滑', '下滑'],
    riskLevel: 'R3_MODIFY',
    execute: async (params) => {
      const err = await requireAccessibility()
      if (err) return ExecutionResult.failure(err)
      const nums = ['x1', 'y1', 'x2', 'y2'].map(k => Number(params[k]))
      if (nums.some(n => !Number.isFinite(n))) {
        return ExecutionResult.failure('需要有效的 x1、y1、x2、y2')
      }
      const duration = typeof params.duration === 'number' ? params.duration : 300
      try {
        const r = await withNativeTimeout(
          SiyiNative.swipe({ x1: nums[0], y1: nums[1], x2: nums[2], y2: nums[3], duration }),
          5000, 'swipe')
        return r?.success
          ? ExecutionResult.success('滑动完成')
          : ExecutionResult.failure(r?.error || '滑动失败')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '滑动失败')
      }
    },
  },

  {
    id: 'phone.nav_back',
    description: '触发系统返回键。',
    parameters: null,
    tags: ['返回', '后退', 'back'],
    riskLevel: 'R1_LIGHT',
    execute: async () => {
      try {
        const r = await withNativeTimeout(SiyiNative.navBack(), 5000, 'navBack')
        return r?.success ? ExecutionResult.success('已返回') : ExecutionResult.failure('返回失败')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '返回失败')
      }
    },
  },

  {
    id: 'phone.nav_home',
    description: '触发系统主页键，回到桌面。',
    parameters: null,
    tags: ['主页', '桌面', 'home', '回桌面'],
    riskLevel: 'R1_LIGHT',
    execute: async () => {
      try {
        const r = await withNativeTimeout(SiyiNative.navHome(), 5000, 'navHome')
        return r?.success ? ExecutionResult.success('已回到桌面') : ExecutionResult.failure('操作失败')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '操作失败')
      }
    },
  },

  {
    id: 'phone.nav_recents',
    description: '打开最近任务列表。',
    parameters: null,
    tags: ['最近任务', '多任务', 'recents'],
    riskLevel: 'R1_LIGHT',
    execute: async () => {
      try {
        const r = await withNativeTimeout(SiyiNative.navRecents(), 5000, 'navRecents')
        return r?.success ? ExecutionResult.success('已打开最近任务') : ExecutionResult.failure('操作失败')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : '操作失败')
      }
    },
  },

  /**
   * root 命令 —— 为「本地 AI 全权管理手机」预埋的能力入口。
   *
   * 设计取向：能力先铺好，但默认只放行只读命令；写操作必须显式 allowDangerous。
   * 未 root 设备上调用会得到明确失败，不会静默。
   *
   * 风险等级定为 R3_MODIFY：root 可改动系统任何部分，等价于最高权限。
   * 因此本工具未开启「全权管理」开关时，AI 只能查询、不能改动。
   */
  {
    id: 'phone.root',
    description:
      '以 root 权限执行一条 shell 命令。设备未 root 时不可用。默认只允许只读命令（id/getprop/ls/cat/df/dumpsys/pm/settings 等）；修改系统或数据需用户在设置里开启「全权管理」后，AI 才能传 allowDangerous。',
    parameters: {
      type: 'object',
      properties: {
        command: { type: 'string', description: '要执行的 shell 命令，如 "pm list packages" 或 "getprop ro.product.model"。' },
        allowDangerous: {
          type: 'boolean',
          description: '是否允许非只读命令。仅在用户已明确开启全权管理时才应传 true。默认 false。',
        },
        timeoutMs: { type: 'number', description: '超时毫秒，默认 15000，范围 1000-60000。' },
      },
      required: ['command'],
    },
    tags: ['root', '最高权限', '系统命令', 'shell', '全权管理'],
    riskLevel: 'R3_MODIFY',
    execute: async (params) => {
      const command = String(params.command ?? '').trim()
      if (!command) return ExecutionResult.failure('需要提供 command')
      try {
        const r = await withNativeTimeout(
          SiyiNative.runRootCommand({
            command,
            allowDangerous: params.allowDangerous === true,
            timeoutMs: typeof params.timeoutMs === 'number' ? params.timeoutMs : 15000,
          }),
          20000,
          'runRootCommand'
        )
        if (!r?.available) {
          return ExecutionResult.failure(r?.error || '设备未 root，无法执行 root 命令')
        }
        if (r.blocked) {
          return ExecutionResult.failure(r.error || '该命令不在只读白名单内，需要用户开启全权管理')
        }
        if (!r.success) {
          return ExecutionResult.failure(
            `命令失败（exit ${r.exitCode ?? '?'}）：${(r.stderr || r.error || '无输出').slice(0, 800)}`
          )
        }
        const out = (r.stdout || '').trim()
        return ExecutionResult.success(out ? out.slice(0, 3000) : '（命令成功，无输出）')
      } catch (e) {
        return ExecutionResult.failure(e instanceof Error ? e.message : 'root 命令执行失败')
      }
    },
  },
]

/** 无障碍能力是否就绪（供 UI 提示用） */
export async function isA11yReady(): Promise<boolean> {
  try {
    const st = await withNativeTimeout(SiyiNative.isAccessibilityRunning(), 3000, 'isA11y')
    return !!st?.running
  } catch {
    return false
  }
}
