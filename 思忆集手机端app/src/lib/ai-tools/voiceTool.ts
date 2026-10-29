import { type AiTool, ExecutionResult } from '../toolRegistry'

export function createVoiceTool(launchVoiceCapture: () => void): AiTool {
  return {
    id: 'quick_capture.voice',
    description: '语音快速捕获 — 启动语音录制并转文字',
    parameters: null,
    tags: ['语音', '录音', 'voice', '录音机'],
    riskLevel: 'R1_LIGHT',
    async execute(_params: Record<string, unknown>): Promise<ExecutionResult> {
      try {
        launchVoiceCapture()
        return ExecutionResult.success('语音捕获已启动')
      } catch (err) {
        return ExecutionResult.failure(err instanceof Error ? err.message : '启动语音捕获失败')
      }
    },
  }
}
