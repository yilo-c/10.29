// B 组收尾：让 AI 知道「权限缺失时该主动拉起授权」
//
// 背景：B2 日历 / B3 精确闹钟 / B4 使用统计 / B5 Shizuku 这四个能力
// 实现都完整，device_request_permission 工具也做好了，但 AI 不知道
// 什么时候该用它。实测中的表现是：工具返回「缺权限」之后，AI 回一句
// 「请去系统设置里开启」，用户得自己翻三层菜单 —— 体验就断在这里。
//
// 做法：往系统提示里加一段很短的行为约束，把「哪种返回对应哪个 kind」
// 讲清楚，并约定同会话同 kind 只主动拉一次，避免反复弹框骚扰。
//
// 为什么用 systemPrompt 而不是改工具返回：工具有 51 个，逐个写引导会
// 很啰嗦；而且最终是模型决定要不要调工具，放在提示里最直接。
//
// systemPrompt 服务的真实 API（已核实 dsh-system-prompt/lib/index.js:253）：
//   section({ name, order, text })  order 必须是有限数字
// order 取 3200，落在工具说明之后（TOOL_COMPUTER_USE 3000、MCP_SERVERS 3100），
// 不抢人格设定和基础行为准则的位置。

export const name = 'siyi-permission-guide'
export const inject = ['systemPrompt']

const SECTION_NAME = 'siyi:permission-guide'
const SECTION_ORDER = 3200

const GUIDE = [
  '设备能力权限处理：当 device_* 工具因为缺少系统权限而拿不到数据时，不要只回「请去系统设置里开启」。',
  '按下面的对应关系，主动调用一次 device_request_permission，直接把系统授权界面拉起来：',
  '日历读写（device_calendar_* 返回 read:false 或 write:false）用 kind=calendar；',
  '精确闹钟（device_alarm_status 返回 canExact:false 或 permGranted:false）用 kind=exact_alarm；',
  '使用情况访问（device_usage_status 返回 granted:false，或 device_usage_summary 返回 degraded:true）用 kind=usage_stats；',
  'Shizuku 静默安装（device_shizuku_status 返回 not_installed / not_running / not_granted）用 kind=shizuku。',
  '同一会话内同一个 kind 最多主动拉起一次；用户拒绝后不要再提，直接说明该能力当前不可用，并给出降级后的替代做法。',
].join('')

export function apply(ctx) {
  const sp = (() => {
    try { return ctx.systemPrompt } catch { return undefined }
  })() || (() => {
    try { return ctx.get('systemPrompt') } catch { return undefined }
  })()

  if (!sp || typeof sp.section !== 'function') {
    // 服务不可用就让插件安静失效，绝不能因为这段提示词把引擎带崩
    try { process.stderr.write('[siyi-permission-guide] warn: systemPrompt 服务不可用，插件跳过\n') } catch {}
    return
  }

  try {
    sp.section({ name: SECTION_NAME, order: SECTION_ORDER, text: GUIDE })
    try { process.stderr.write('[siyi-permission-guide] info: 已注入权限引导（4 个 kind）\n') } catch {}
  } catch (e) {
    try { process.stderr.write('[siyi-permission-guide] warn: 注入失败 ' + (e && e.message) + '\n') } catch {}
  }
}
