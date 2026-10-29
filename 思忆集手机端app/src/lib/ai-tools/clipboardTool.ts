import { type AiTool, ExecutionResult } from '../toolRegistry'

export function createClipboardTool(readClipboard: () => string | Promise<string>): AiTool {
  return {
    id: 'clipboard.read',
    description: '读取系统剪贴板内容',
    parameters: null,
    tags: ['剪贴板', 'clipboard', '粘贴'],
    riskLevel: 'R1_LIGHT',
    async execute(_params: Record<string, unknown>): Promise<ExecutionResult> {
      const content = await Promise.resolve(readClipboard())
      if (content) {
        return ExecutionResult.success(`剪贴板内容: ${content}`)
      }
      return ExecutionResult.success('剪贴板为空')
    },
  }
}
