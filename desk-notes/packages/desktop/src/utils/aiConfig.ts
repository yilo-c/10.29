/**
 * Centralized AI config helper.
 *
 * Previously many UI components hard-coded `'http://localhost:11434'` and
 * `'qwen2.5-7b:latest'` inline. This module is the single source of truth so
 * the defaults can move (e.g. to user settings) without touching call sites.
 */
import type { AIConfig } from '../types'
import { DEFAULT_LOCAL_API_URL, DEFAULT_LOCAL_MODEL } from './ai'

/**
 * Build a local-Ollama `AIConfig` using the shared defaults.
 *
 * @param overrides Optional partial override (e.g. `{ enabled: aiEnabled }`).
 */
export function getLocalAIConfig(overrides?: Partial<AIConfig>): AIConfig {
  return {
    enabled: true,
    apiUrl: DEFAULT_LOCAL_API_URL,
    model: DEFAULT_LOCAL_MODEL,
    protocol: 'ollama',
    autoTagging: false,
    ...(overrides ?? {}),
  }
}
