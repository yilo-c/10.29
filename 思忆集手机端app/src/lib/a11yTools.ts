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
    description: '读取当前屏幕上的所有文字（含按钮名称、列表项、输入框内容）。用于了解用户当前在哪个界面、有哪些可操作元素。',
    parameters: null,
    tags: ['屏幕', '读取', '当前界面', '看看', '屏幕上有什么', '读屏', '识别界面'],
    riskLevel: 'R0_INFO',
    execute: async () => {
      const err = await requireAccessibility()
      if (err) return ExecutionResult.failure(err)
      try {
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
    id: 'phone.launch_app',
    description: '启动指定包名的应用并切到前台。需要先知道包名，可用 phone.list_apps 查询。',
    parameters: {
      type: 'object',
      properties: {
        packageName: { type: 'string', description: '应用包名，如 com.tencent.mm（微信）' },
      },
      required: ['packageName'],
    },
    tags: ['打开', '启动', '应用', 'app', '微信', '打开应用'],
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
        limit: { type: 'number', description: '最多返回多少个，默认 60' },
      },
    },
    tags: ['应用列表', '装了什么', '有哪些应用', '找应用'],
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
        x: { type: 'number' },
        y: { type: 'number' },
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
        x1: { type: 'number' },
        y1: { type: 'number' },
        x2: { type: 'number' },
        y2: { type: 'number' },
        duration: { type: 'number', description: '滑动时长毫秒，默认 300' },
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
