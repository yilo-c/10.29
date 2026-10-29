import { type AiTool, ExecutionResult } from '../toolRegistry'

export function createTasksTool(createTask: (text: string) => Promise<string>): AiTool {
  return {
    id: 'tasks.create',
    description: '创建任务 — 将文本创建为新任务或提醒',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', description: '任务描述，可包含截止时间' },
      },
      required: ['text'],
    },
    tags: ['创建任务', '新建任务', '添加任务', '提醒我', '任务', 'create task', 'new task', 'remind me', 'task'],
    riskLevel: 'R3_MODIFY',
    async execute(params: Record<string, unknown>): Promise<ExecutionResult> {
      const text = String(params.text ?? '')
      if (!text.trim()) {
        return ExecutionResult.failure('任务内容为空')
      }
      try {
        await createTask(text.trim())
        return ExecutionResult.success('任务已创建')
      } catch (err) {
        return ExecutionResult.failure(err instanceof Error ? err.message : '创建任务失败')
      }
    },
  }
}
