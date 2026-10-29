import type { AIConfig } from '../../types'
import type { AIProvider } from './types'
import { LocalAIProvider } from './local'
import { RuleAIProvider } from './rule'
import { HybridAIProvider } from './hybrid'
import { WsAIProvider } from './ws-provider'
import { WsClient } from '../../ws-client/ws-client'
import { installBackendEvents } from '../../store/backendEventsStore'
import { attachSyncQueue } from '../syncQueue'
import { DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from './constants'
export { DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from './constants'

export type { AIProvider, AIAction } from './types'
export { AI_ACTION_LABELS, AI_ACTION_ICONS, AI_PROMPTS } from './types'
export { AI_PATTERN_FAIL_PREFIX } from './types'
export { LocalAIProvider } from './local'
export { RuleAIProvider } from './rule'
export { HybridAIProvider } from './hybrid'
export { WsAIProvider } from './ws-provider'

// Shared WebSocket client singleton
let _wsClient: WsClient | null = null
let _wsProvider: WsAIProvider | null = null
// A1: 后端事件统一接入层的卸载句柄
let _uninstallBackendEvents: (() => void) | null = null

/** Get or create the shared WebSocket client. */
export function getWsClient(): WsClient {
  if (!_wsClient) {
    _wsClient = new WsClient()
    // A1：客户端创建时一次性注册全部后端事件处理器（task.resumable /
    // sync.conflict / sandbox.timeout* / cron.job_alert / exec.step / morning_brief）
    _uninstallBackendEvents = installBackendEvents(_wsClient)
    // 1.1：绑定离线写队列——重连 connected 时自动补发，启动时恢复持久化队列
    attachSyncQueue(_wsClient)
    _wsClient.connect()
  }
  return _wsClient
}

/** Reset the WebSocket client (for testing). */
export function resetWsClient(): void {
  if (_uninstallBackendEvents) {
    _uninstallBackendEvents()
    _uninstallBackendEvents = null
  }
  if (_wsClient) {
    _wsClient.disconnect()
    _wsClient = null
  }
}

/** P1-4: 设置远端设备的真实主 token（手机配对后或桌面手填远程后端时调用）。
 *  写入全局并销毁现有单例；下次 getWsClient() 会以新 URL（localStorage）+ 新 token 重建并自动重连
 *  （远端连接自动以 ``?token=<token>`` 形式携带，loopback 直连忽略）。销毁而非仅 disconnect
 *  至关重要——否则已建单例的 ``_authToken`` 仍是旧值，新 token 永不生效。 */
export function configureWsAuthToken(token: string | null): void {
  ;(globalThis as unknown as { __SIYI_WS_AUTH_TOKEN__?: string }).__SIYI_WS_AUTH_TOKEN__ = token ?? undefined
  // 销毁单例（断开并置空），使新 URL + 新 token 立即生效；调用方随后 getWsClient() 重建并自动重连。
  // 同时覆盖桌面「手填远程 WS URL + token」与手机配对下发 token 两类场景。
  resetWsClient()
}

/** Create a new AI provider instance with the given config */
export function getAIProvider(config?: AIConfig): AIProvider {
  const cfg: AIConfig = config ?? { enabled: false, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama', autoTagging: false }
  if (cfg.strategy === 'ws') {
    // Cache the WsAIProvider singleton - each new instance registers an
    // onStatusChange listener that is never released, causing memory leaks.
    if (!_wsProvider) {
      _wsProvider = new WsAIProvider(cfg, getWsClient())
    } else {
      _wsProvider.updateConfig(cfg)
    }
    return _wsProvider
  }
  if (cfg.strategy === 'hybrid') {
    return new HybridAIProvider(cfg)
  }
  if (cfg.strategy === 'offline') {
    return new RuleAIProvider(cfg)
  }
  return new LocalAIProvider(cfg)
}

/** Reset for testing - disposes cached WsAIProvider */
export function resetAIProvider(): void {
  if (_wsProvider) {
    _wsProvider.dispose()
    _wsProvider = null
  }
}

/** Process text with AI using given config */
export async function streamAI(
  config: AIConfig,
  action: import('./types').AIAction,
  text: string,
  onChunk: (text: string) => void,
  onDone: (fullText: string) => void,
  onError: (err: string) => void,
): Promise<void> {
  if (!config.enabled) {
    onError('AI not enabled')
    return
  }
  const provider = getAIProvider(config)
  try {
    const fullText = await provider.processText(action, text, onChunk)
    onDone(fullText)
  } catch (err: unknown) {
    onError(err instanceof Error ? err.message : String(err))
  }
}
