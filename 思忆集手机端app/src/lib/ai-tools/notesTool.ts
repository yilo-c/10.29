import { type AiTool, ExecutionResult } from '../toolRegistry'

export function createNotesTool(createNote: (text: string) => Promise<string>): AiTool {
  return {
    id: 'notes.create',
    description: '创建笔记 — 将文本保存为个人笔记',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '笔记内容' },
      },
      required: ['text'],
    },
    tags: ['创建笔记', '新建笔记', '写笔记', '记下来', '笔记', 'quick capture', '收件', 'capture', 'note'],
    riskLevel: 'R2_DRAFT',
    async execute(params: Record<string, unknown>): Promise<ExecutionResult> {
      const text = String(params.text ?? '')
      if (!text.trim()) {
        return ExecutionResult.failure('笔记内容为空')
      }
      try {
        await createNote(text.trim())
        return ExecutionResult.success('笔记已创建')
      } catch (err) {
        return ExecutionResult.failure(err instanceof Error ? err.message : '创建笔记失败')
      }
    },
  }
}
