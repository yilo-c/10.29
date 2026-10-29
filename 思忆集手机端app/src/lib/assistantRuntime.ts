import { IntentType, AiTarget, classifyIntent, classifyPrivacy, resolveTarget, type PrivacyLevelType, type UserAiPreference } from './aiRouter'
import { ActionBroker, type ExecutionReceipt } from './actionBroker'
import { CloudAiClient } from './cloudAiClient'
import { DesktopAiClient } from './desktopAiClient'
import { OfflineAiFallback } from './offlineFallback'

export interface AiRequest {
  text: string
  sessionId?: string
  privacyLevel?: PrivacyLevelType
}

export { IntentType, AiTarget }

const defaultPref: UserAiPreference = {
  cloudAiEnabled: false,
  promptBeforeCloud: true,
  preferDesktopAi: true,
}

export class AssistantRuntime {
  onStreamingResponse: ((chunk: string) => void) | null = null

  constructor(
    private broker: ActionBroker,
    private cloudAi: CloudAiClient,
    private desktopAi: DesktopAiClient,
    private offlineFallback: OfflineAiFallback,
  ) {}

  async process(
    request: AiRequest,
    isDesktopConnected = false,
    userPref: UserAiPreference = defaultPref,
  ): Promise<ExecutionReceipt> {
    const startTime = performance.now()
    const requestId = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`

    // Step 1: Classify intent
    const intent = classifyIntent(request.text)

    // Step 2: Privacy classification
    const privacy = classifyPrivacy(request.text, request.privacyLevel)

    // Step 3: Route
    const target = resolveTarget(intent, privacy, isDesktopConnected, userPref)

    // Step 4: Execute based on target
    switch (target) {
      case AiTarget.LOCAL_DATABASE:
      case AiTarget.NETWORK_API:
        return this.broker.execute(intent, request.text, requestId)

      case AiTarget.DESKTOP_LOCAL_AI:
        return this.executeDesktop(request, intent, startTime)

      case AiTarget.CLOUD_AI:
        return this.executeCloud(request, intent, startTime)

      case AiTarget.QUEUED_PENDING:
        return this.handleQueued(request, intent, target, startTime)

      case AiTarget.OFFLINE_CACHE:
        return this.handleQueued(request, intent, target, startTime)
    }
  }

  private async executeDesktop(request: AiRequest, intent: IntentType, startTime: number): Promise<ExecutionReceipt> {
    try {
      const response = await this.desktopAi.sendMessage(request.text, request.sessionId)
      this.offlineFallback.cacheResponse(request.text, response.reply)

      return {
        requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        intentType: intent,
        target: AiTarget.DESKTOP_LOCAL_AI,
        status: 'COMPLETED',
        result: response.reply,
        timestamp: Date.now(),
        durationMs: Math.round(performance.now() - startTime),
      }
    } catch (err) {
      return this.handleAiFailure(request, intent, AiTarget.DESKTOP_LOCAL_AI, err, startTime)
    }
  }

  private async executeCloud(request: AiRequest, intent: IntentType, startTime: number): Promise<ExecutionReceipt> {
    try {
      const response = await this.cloudAi.sendMessage(request.text, request.sessionId)
      this.offlineFallback.cacheResponse(request.text, response.reply)

      return {
        requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        intentType: intent,
        target: AiTarget.CLOUD_AI,
        status: 'COMPLETED',
        result: response.reply,
        timestamp: Date.now(),
        durationMs: Math.round(performance.now() - startTime),
      }
    } catch (err) {
      return this.handleAiFailure(request, intent, AiTarget.CLOUD_AI, err, startTime)
    }
  }

  private async handleAiFailure(
    request: AiRequest,
    intent: IntentType,
    originalTarget: AiTarget,
    _error: unknown,
    startTime: number,
  ): Promise<ExecutionReceipt> {
    // Check offline cache
    const cached = await this.offlineFallback.tryCached(request.text)
    if (cached) {
      this.offlineFallback.enqueueForRetry(request.text, request.sessionId ?? null, originalTarget)
      return {
        requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        intentType: intent,
        target: AiTarget.OFFLINE_CACHE,
        status: 'COMPLETED',
        result: cached.response,
        timestamp: Date.now(),
        durationMs: Math.round(performance.now() - startTime),
      }
    }

    // Queue for retry
    this.offlineFallback.enqueueForRetry(request.text, request.sessionId ?? null, originalTarget)
    return {
      requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      intentType: intent,
      target: originalTarget,
      status: 'FAILED',
      error: 'AI 服务不可用，已排队等待重试',
      timestamp: Date.now(),
      durationMs: Math.round(performance.now() - startTime),
    }
  }

  private async handleQueued(
    request: AiRequest,
    intent: IntentType,
    target: AiTarget,
    startTime: number,
  ): Promise<ExecutionReceipt> {
    // Check cache first
    const cached = await this.offlineFallback.tryCached(request.text)
    if (cached) {
      this.offlineFallback.enqueueForRetry(request.text, request.sessionId ?? null, target)
      return {
        requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        intentType: intent,
        target: AiTarget.OFFLINE_CACHE,
        status: 'COMPLETED',
        result: cached.response,
        timestamp: Date.now(),
        durationMs: Math.round(performance.now() - startTime),
      }
    }

    // Queue for later
    this.offlineFallback.enqueueForRetry(request.text, request.sessionId ?? null, target)
    return {
      requestId: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      intentType: intent,
      target,
      status: 'QUEUED',
      timestamp: Date.now(),
      durationMs: Math.round(performance.now() - startTime),
    }
  }
}
