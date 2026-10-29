import { IntentType, AiTarget } from './aiRouter'
import { ToolRegistry } from './toolRegistry'

export interface ExecutionReceipt {
  requestId: string
  intentType: IntentType
  target: AiTarget
  status: ExecutionStatus
  result?: string
  error?: string
  timestamp: number
  durationMs?: number
}

export type ExecutionStatus = 'PENDING' | 'ROUTED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'QUEUED'

export class ActionBroker {
  constructor(
    private toolRegistry: ToolRegistry,
  ) {}

  async execute(intent: IntentType, input: string, _requestId?: string): Promise<ExecutionReceipt> {
    const id = _requestId ?? crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const startTime = performance.now()

    switch (intent) {
      case IntentType.LOCAL_CRUD:
        return this.executeLocalAction(input, id, startTime)

      case IntentType.INFO_FETCH:
        return this.executeInfoFetch(input, id, startTime)

      case IntentType.NAVIGATION:
      case IntentType.COMPLEX_REASONING:
        return {
          requestId: id,
          intentType: intent,
          target: AiTarget.QUEUED_PENDING,
          status: 'ROUTED',
          timestamp: Date.now(),
          durationMs: Math.round(performance.now() - startTime),
        }
    }
  }

  private async executeLocalAction(input: string, requestId: string, startTime: number): Promise<ExecutionReceipt> {
    const match = await this.tryDispatchToTool(input)

    if (!match) {
      return {
        requestId,
        intentType: IntentType.LOCAL_CRUD,
        target: AiTarget.QUEUED_PENDING,
        status: 'ROUTED',
        timestamp: Date.now(),
        durationMs: Math.round(performance.now() - startTime),
      }
    }

    return match
  }

  private async tryDispatchToTool(input: string): Promise<ExecutionReceipt | null> {
    const matched = this.toolRegistry.matchTools(input)
    if (matched.length === 0) return null

    const tool = matched[0]
    const result = await tool.execute({ text: input })

    if (result.success) {
      return {
        requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        intentType: IntentType.LOCAL_CRUD,
        target: AiTarget.LOCAL_DATABASE,
        status: 'COMPLETED',
        result: result.data,
        timestamp: Date.now(),
      }
    }

    return {
      requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      intentType: IntentType.LOCAL_CRUD,
      target: AiTarget.LOCAL_DATABASE,
      status: 'FAILED',
      error: result.error,
      timestamp: Date.now(),
    }
  }

  private async executeInfoFetch(_input: string, requestId: string, startTime: number): Promise<ExecutionReceipt> {
    // Basic info fetch: just acknowledge routing
    return {
      requestId,
      intentType: IntentType.INFO_FETCH,
      target: AiTarget.NETWORK_API,
      status: 'ROUTED',
      timestamp: Date.now(),
      durationMs: Math.round(performance.now() - startTime),
    }
  }
}
