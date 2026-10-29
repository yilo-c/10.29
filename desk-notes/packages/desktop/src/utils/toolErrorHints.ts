/**
 * D2（前后端对齐规划）：工具/能力错误友好化映射。
 *
 * 后端 broker/guard/能力层返回的结构化 reason（action blocked by broker /
 * 能力域异常）对用户不可读。本层把已知 reason 映射为"原因 + 建议"，
 * trace 面板与聊天错误卡片共用。
 *
 * 后端参考：assistant/action_broker.py、core/action_guard.py、
 * assistant/capability/_file_ops.py。
 */

export interface ToolErrorHint {
  title: string
  advice: string
}

/** reason → 友好提示；未知 reason 返回 null（调用方回退原文展示）。 */
export function describeToolError(text: string): ToolErrorHint | null {
  if (!text) return null
  const t = text.toLowerCase()

  if (t.includes('capability_not_found')) {
    return {
      title: '调用了未注册的能力',
      advice: '助手使用了不存在的能力名（如 browser.navigate）。实际可用能力以能力清单为准，可提示助手改用已注册的能力（如 browser.open）。',
    }
  }
  if (t.includes('path_outside_allowed_root')) {
    return {
      title: '文件路径在安全根目录之外',
      advice: '文件工具只能访问工作区/数据目录。如需处理桌面等位置的文件，可先手动复制到工作区内，或改用截图等无需文件访问的方式。',
    }
  }
  if (t.includes('llm_source_high_risk')) {
    return {
      title: '高风险操作被安全守卫拦截',
      advice: 'AI 发起的系统级命令（shell 等）默认硬拦截，审批通过也不会放行。可在设置中开启"高风险操作告警模式"（warn-only）后重试。',
    }
  }
  if (t.includes('timed_out_rejected') || t.includes('sandbox.timeout')) {
    return {
      title: '审批超时被自动拒绝',
      advice: '待审批操作超过时限被自动处置。重新发起操作并及时在审批面板处理。',
    }
  }
  if (t.includes('approval_required') || t.includes('pending approval')) {
    return {
      title: '操作等待审批',
      advice: '该操作需要你在审批面板点击批准后才会执行。',
    }
  }
  if (t.includes('websocket closed') || t.includes('not connected')) {
    return {
      title: '后端连接中断',
      advice: '与本地后端的连接断开导致生成失败。后端会自动重连，稍后重试即可；若持续失败请检查后端进程。',
    }
  }
  return null
}

/** 判断文本是否包含已知的结构化错误 reason。 */
export function hasKnownToolError(text: string): boolean {
  return describeToolError(text) !== null
}
