export type RiskLevel = 'R0_INFO' | 'R1_LIGHT' | 'R2_DRAFT' | 'R3_MODIFY'

export interface AiTool {
  id: string
  description: string
  parameters: Record<string, unknown> | null
  tags: string[]
  riskLevel: RiskLevel
  execute: (params: Record<string, unknown>) => Promise<ExecutionResult>
}

export interface ExecutionResult {
  success: boolean
  data?: string
  error?: string
}

export const ExecutionResult = {
  success: (data: string): ExecutionResult => ({ success: true, data }),
  failure: (error: string): ExecutionResult => ({ success: false, error }),
}

export type FunctionCallSpec = {
  name: string
  description: string
  parameters: Record<string, unknown>
}

export class ToolRegistry {
  private tools = new Map<string, AiTool>()

  register(tool: AiTool): void {
    this.tools.set(tool.id, tool)
  }

  unregister(toolId: string): void {
    this.tools.delete(toolId)
  }

  getTool(toolId: string): AiTool | undefined {
    return this.tools.get(toolId)
  }

  getAllTools(): AiTool[] {
    return Array.from(this.tools.values())
  }

  get size(): number {
    return this.tools.size
  }

  registerAll(tools: AiTool[]): void {
    for (const tool of tools) {
      this.register(tool)
    }
  }

  matchTools(text: string): AiTool[] {
    const lower = text.toLowerCase()
    const scored: { tool: AiTool; score: number }[] = []

    for (const tool of this.tools.values()) {
      let score = 0
      for (const tag of tool.tags) {
        if (lower.includes(tag.toLowerCase())) {
          score++
        }
      }
      if (score > 0) {
        scored.push({ tool, score })
      }
    }

    return scored.sort((a, b) => b.score - a.score).map(s => s.tool)
  }

  dispatch(toolId: string, params: Record<string, unknown>): Promise<ExecutionResult> {
    const tool = this.tools.get(toolId)
    if (!tool) {
      return Promise.resolve(ExecutionResult.failure(`Tool not found: ${toolId}`))
    }
    return tool.execute(params)
  }

  toFunctionCallSpec(): FunctionCallSpec[] {
    return this.getAllTools().map(tool => ({
      name: tool.id,
      description: tool.description,
      parameters: tool.parameters as Record<string, unknown> ?? {
        type: 'object',
        properties: {} as Record<string, unknown>,
      },
    }))
  }
}
