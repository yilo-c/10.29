/**
 * 无障碍工具测试 / Accessibility tools tests
 *
 * 这些测试针对一个具体风险：
 *   工具注册了 ≠ AI 会用。matchTools 靠 tags 关键词命中，
 *   如果用户说法（"看看屏幕上有什么"）和 tags 对不上，
 *   工具就永远不会被触发，AI 只会用嘴回答。
 *
 * 因此这里验证的是「用户真实说法 → 能否命中正确工具」，
 * 而不是简单的函数返回值。
 *
 * 另外覆盖：未开无障碍时必须返回明确错误，不能静默失败。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// mock 原生层
const mockIsA11y = vi.fn()
const mockReadScreen = vi.fn()
const mockReadScreenNodes = vi.fn()
const mockTapNode = vi.fn()
const mockTap = vi.fn()
const mockSwipe = vi.fn()
const mockLaunch = vi.fn()
const mockInstalled = vi.fn()
const mockRunRoot = vi.fn()

vi.mock('../native/siyiNative', () => ({
  default: {
    isAccessibilityRunning: () => mockIsA11y(),
    readScreen: () => mockReadScreen(),
    readScreenNodes: () => mockReadScreenNodes(),
    tapNode: (o: unknown) => mockTapNode(o),
    tap: (o: unknown) => mockTap(o),
    swipe: (o: unknown) => mockSwipe(o),
    launchApp: (o: unknown) => mockLaunch(o),
    getInstalledApps: (o: unknown) => mockInstalled(o),
    runRootCommand: (o: unknown) => mockRunRoot(o),
    navBack: vi.fn(async () => ({ success: true })),
    navHome: vi.fn(async () => ({ success: true })),
    navRecents: vi.fn(async () => ({ success: true })),
  },
  withNativeTimeout: <T,>(p: Promise<T>) => p,
}))

import { a11yTools } from '../a11yTools'
import { ToolRegistry } from '../toolRegistry'

describe('a11yTools / 工具注册与匹配', () => {
  let reg: ToolRegistry

  beforeEach(() => {
    reg = new ToolRegistry()
    reg.registerAll(a11yTools)
  })

  it('10 个工具全部注册成功（含 tap_text 与 root）', () => {
    expect(reg.size).toBe(10)
    const ids = reg.getAllTools().map(t => t.id)
    expect(ids).toContain('phone.read_screen')
    expect(ids).toContain('phone.launch_app')
    expect(ids).toContain('phone.tap')
    expect(ids).toContain('phone.swipe')
    expect(ids).toContain('phone.tap_text')
    expect(ids).toContain('phone.root')
  })

  it.each([
    ['看看我屏幕上有什么', 'phone.read_screen'],
    ['读一下当前屏幕', 'phone.read_screen'],
    ['屏幕上显示的是什么', 'phone.read_screen'],
    ['打开微信', 'phone.launch_app'],
    ['手机上装了什么应用', 'phone.list_apps'],
    ['帮我点一下', 'phone.tap'],
    ['向上滑一下', 'phone.swipe'],
    ['返回上一页', 'phone.nav_back'],
    ['回到桌面', 'phone.nav_home'],
  ])('用户说「%s」应命中 %s', (utterance, expectedId) => {
    const matched = reg.matchTools(utterance)
    expect(matched.length).toBeGreaterThan(0)
    expect(matched[0].id).toBe(expectedId)
  })

  it('无关话题不应命中任何手机操作工具', () => {
    const matched = reg.matchTools('今天天气怎么样')
    const a11yIds = new Set(a11yTools.map(t => t.id))
    const hits = matched.filter(t => a11yIds.has(t.id))
    expect(hits).toHaveLength(0)
  })

  it('tap / swipe 标记为 R3_MODIFY（高风险）', () => {
    const tap = a11yTools.find(t => t.id === 'phone.tap')!
    const swipe = a11yTools.find(t => t.id === 'phone.swipe')!
    expect(tap.riskLevel).toBe('R3_MODIFY')
    expect(swipe.riskLevel).toBe('R3_MODIFY')
  })

  it('read_screen / list_apps 标记为 R0_INFO（只读）', () => {
    expect(a11yTools.find(t => t.id === 'phone.read_screen')!.riskLevel).toBe('R0_INFO')
    expect(a11yTools.find(t => t.id === 'phone.list_apps')!.riskLevel).toBe('R0_INFO')
  })
})

describe('a11yTools / 运行时行为', () => {
  const tool = (id: string) => a11yTools.find(t => t.id === id)!

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('无障碍未开启时，read_screen 返回可操作的中文错误', async () => {
    mockIsA11y.mockResolvedValue({ running: false })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(false)
    // 错误信息必须能指导用户去哪开，而不是"失败了"
    expect(r.error).toContain('无障碍')
    expect(r.error).toContain('设置')
  })

  it('无障碍开启时，read_screen 返回屏幕文字', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    // 显式声明结构化读屏不可用 → 走纯文本退化路径（旧版原生壳场景）
    mockReadScreenNodes.mockResolvedValue({ available: false, nodes: [], count: 0 })
    mockReadScreen.mockResolvedValue({ available: true, text: '小米笔记\n标题\n正文' })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(true)
    expect(r.data).toContain('小米笔记')
  })

  it('屏幕为空白时给出说明而非空字符串', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockReadScreenNodes.mockResolvedValue({ available: false, nodes: [], count: 0 })
    mockReadScreen.mockResolvedValue({ available: true, text: '   ' })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(true)
    expect(r.data).toContain('没有可读文字')
  })

  it('超长屏幕内容会被截断，避免塞爆上下文', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockReadScreenNodes.mockResolvedValue({ available: false, nodes: [], count: 0 })
    mockReadScreen.mockResolvedValue({ available: true, text: 'A'.repeat(5000) })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(true)
    expect(r.data!.length).toBeLessThan(3200)
    expect(r.data).toContain('已截断')
  })

  /* ── 结构化读屏：这是「AI 能真的点对地方」的前提 ── */

  it('结构化读屏可用时，read_screen 返回可点击元素及其坐标', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockReadScreenNodes.mockResolvedValue({
      available: true,
      count: 2,
      nodes: [
        { text: '发送', viewId: 'btn_send', className: 'Button', clickable: true,
          left: 800, top: 2100, right: 1000, bottom: 2200, cx: 900, cy: 2150 },
        { text: '输入框', viewId: 'et_input', className: 'EditText', clickable: false,
          editable: true, left: 100, top: 2000, right: 700, bottom: 2100, cx: 400, cy: 2050 },
      ],
    })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(true)
    // 必须带上真实坐标，否则 AI 只能瞎猜
    expect(r.data).toContain('x=900 y=2150')
    expect(r.data).toContain('发送')
    expect(r.data).toContain('可点击元素')
    // 输入框也应被识别为可操作元素（editable 而非 clickable）
    expect(r.data).toContain('x=400 y=2050')
  })

  it('结构化读屏返回空节点时，不应把空数组当作有效界面', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockReadScreenNodes.mockResolvedValue({ available: true, nodes: [], count: 0 })
    mockReadScreen.mockResolvedValue({ available: true, text: '' })
    const r = await tool('phone.read_screen').execute({})
    // 退化为纯文本路径，且明确说明没有可读文字
    expect(r.success).toBe(true)
    expect(r.data).toContain('没有可读文字')
  })

  it('结构化读屏抛错时不崩溃，退化为纯文本', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockReadScreenNodes.mockRejectedValue(new Error('native boom'))
    mockReadScreen.mockResolvedValue({ available: true, text: '降级成功' })
    const r = await tool('phone.read_screen').execute({})
    expect(r.success).toBe(true)
    expect(r.data).toContain('降级成功')
  })

  /* ── 按文字点击 ── */

  it('tap_text 命中时调用原生 tapNode 并传入文字', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockTapNode.mockResolvedValue({ success: true, available: true })
    const r = await tool('phone.tap_text').execute({ text: '发送' })
    expect(r.success).toBe(true)
    expect(mockTapNode).toHaveBeenCalledWith({ text: '发送', exact: false })
  })

  it('tap_text 找不到元素时返回明确错误，不静默成功', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockTapNode.mockResolvedValue({ success: false, available: true, error: '未找到文字为「不存在」的可点击元素' })
    const r = await tool('phone.tap_text').execute({ text: '不存在' })
    expect(r.success).toBe(false)
    expect(r.error).toContain('不存在')
  })

  it('tap_text 缺少文字参数时拒绝执行', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    const r = await tool('phone.tap_text').execute({})
    expect(r.success).toBe(false)
    expect(mockTapNode).not.toHaveBeenCalled()
  })

  /* ── root 预留能力：开关必须握在用户手里 ── */

  it('root 未授权时返回明确错误', async () => {
    mockRunRoot.mockResolvedValue({ success: false, available: false, error: '设备未 root，无法执行 root 命令' })
    const r = await tool('phone.root').execute({ command: 'id' })
    expect(r.success).toBe(false)
    expect(r.error).toContain('未 root')
  })

  it('root 只读白名单外的命令被拦截时，错误信息说明需要开启全权管理', async () => {
    mockRunRoot.mockResolvedValue({
      success: false, available: true, blocked: true,
      error: '命令「rm」不在只读白名单内，需在设置里开启「全权管理」后才允许执行',
    })
    const r = await tool('phone.root').execute({ command: 'rm -rf /data/x' })
    expect(r.success).toBe(false)
    expect(r.error).toContain('全权管理')
  })

  it('root 成功时返回命令输出', async () => {
    mockRunRoot.mockResolvedValue({ success: true, available: true, exitCode: 0, stdout: 'uid=0(root)', stderr: '' })
    const r = await tool('phone.root').execute({ command: 'id' })
    expect(r.success).toBe(true)
    expect(r.data).toContain('uid=0')
  })

  it('root 命令默认不传 allowDangerous（防止 AI 自行提权）', async () => {
    mockRunRoot.mockResolvedValue({ success: true, available: true, exitCode: 0, stdout: 'ok' })
    await tool('phone.root').execute({ command: 'id' })
    expect(mockRunRoot).toHaveBeenCalledWith(expect.objectContaining({ allowDangerous: false }))
  })

  it('tap 缺少坐标时拒绝执行', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    const r = await tool('phone.tap').execute({})
    expect(r.success).toBe(false)
    expect(r.error).toContain('坐标')
    expect(mockTap).not.toHaveBeenCalled()
  })

  it('tap 坐标非法时拒绝执行', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    const r = await tool('phone.tap').execute({ x: NaN, y: 100 })
    expect(r.success).toBe(false)
    expect(mockTap).not.toHaveBeenCalled()
  })

  it('tap 有效坐标时调用原生', async () => {
    mockIsA11y.mockResolvedValue({ running: true })
    mockTap.mockResolvedValue({ success: true })
    const r = await tool('phone.tap').execute({ x: 100, y: 200 })
    expect(r.success).toBe(true)
    expect(mockTap).toHaveBeenCalledWith({ x: 100, y: 200 })
  })

  it('launch_app 缺少包名时拒绝', async () => {
    const r = await tool('phone.launch_app').execute({})
    expect(r.success).toBe(false)
    expect(r.error).toContain('packageName')
  })

  it('list_apps 按真实返回结构解析（result.apps[].name）', async () => {
    mockInstalled.mockResolvedValue({
      ok: true,
      result: { apps: [{ name: '微信', packageName: 'com.tencent.mm' }], total: 1 },
    })
    const r = await tool('phone.list_apps').execute({ limit: 10 })
    expect(r.success).toBe(true)
    expect(r.data).toContain('微信')
    expect(r.data).toContain('com.tencent.mm')
  })
})
