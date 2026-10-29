/**
 * 工具调用的人类可读化 / Human-readable tool descriptions
 *
 * 从 ApprovalDialog 抽出来的原因：
 * store 里要用 describeToolCall 构造审批请求，
 * 若直接 import 组件就会造成 store → 组件 的依赖倒挂。
 * 放在 lib 里让 store 和 UI 都能引用，互不依赖。
 */

export interface ToolCallDescription {
  label: string
  summary: string
  details: Array<{ label: string; value: string }>
}

/** 工具 id → 中文名 + 说明 */
export function describeToolCall(
  toolId: string,
  args: Record<string, unknown>,
): { label: string; summary: string; details: Array<{ label: string; value: string }> } {
  switch (toolId) {
    case 'phone.tap': {
      const x = Number(args.x ?? 0)
      const y = Number(args.y ?? 0)
      return {
        label: '点击屏幕',
        summary: `AI 想在屏幕坐标 (${x}, ${y}) 处点一下`,
        details: [
          { label: 'X 坐标', value: String(x) },
          { label: 'Y 坐标', value: String(y) },
        ],
      }
    }
    case 'phone.swipe': {
      const x1 = Number(args.x1 ?? 0), y1 = Number(args.y1 ?? 0)
      const x2 = Number(args.x2 ?? 0), y2 = Number(args.y2 ?? 0)
      return {
        label: '滑动屏幕',
        summary: `AI 想从 (${x1}, ${y1}) 滑到 (${x2}, ${y2})`,
        details: [
          { label: '起点', value: `(${x1}, ${y1})` },
          { label: '终点', value: `(${x2}, ${y2})` },
          { label: '时长', value: `${Number(args.duration ?? 300)} ms` },
        ],
      }
    }
    default:
      return {
        label: toolId,
        summary: `AI 想执行「${toolId}」`,
        details: Object.entries(args).slice(0, 5).map(([k, v]) => ({
          label: k,
          value: typeof v === 'object' ? JSON.stringify(v) : String(v),
        })),
      }
  }
}
