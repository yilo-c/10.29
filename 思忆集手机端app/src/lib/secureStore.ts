/**
 * Secure storage adapter for sensitive credentials (P0-8 fix).
 *
 * History:
 * Before this module, tokenManager.ts wrote API tokens, the DeepSeek/Cloud
 * API key, the PIN hash, and pairing tokens directly to localStorage. WebView
 * localStorage is plain text on disk under /data/data/com.siyi.notepad/
 * app_webview/, accessible via root, ADB pull (with allowBackup tricks), or
 * any XSS — and the app's CSP still permits 'unsafe-inline' for legacy
 * inline styles.
 *
 * This module wraps Capacitor Preferences:
 *   • Android: EncryptedSharedPreferences (AES-256 + Android Keystore)
 *   • iOS:     Keychain (kSecClassGenericPassword)
 *   • Web/dev: falls back to localStorage (best effort, dev-only)
 *
 * Design constraints:
 * - tokenManager.ts API is SYNCHRONOUS (used in React render paths). We keep
 *   that contract by maintaining an in-memory cache that is hydrated from
 *   Preferences once at startup.
 * - Writes go to memory immediately and are persisted asynchronously.
 * - First-run migration: any existing localStorage values get copied into
 *   Preferences and removed from localStorage.
 *
 * Caveat (acknowledged): until hydration completes, getters return null/
 * defaults. The app's bootstrap awaits initSecureStore() before rendering
 * anything that needs credentials.
 */

import { Preferences } from '@capacitor/preferences'
import { swallowError } from './swallowError'

const SENSITIVE_KEYS = new Set<string>([
  'siyi_api_token',
  'siyi_cloud_api_key',
  'siyi_app_pin',           // pin hash (used by zustand persist via storage shim)
  'siyi-biometric-cred',
  'siyi_sync_token',
  'siyi_pairing_secret',
  // P1-1: register_secret 用于向 Worker 注册设备，泄露后可冒充设备注册
  'siyi_register_secret',
  // V2.1 批次4（P1-12）：锁屏防爆破计数持久化（抗重启清零绕过 + 抗篡改）
  'siyi_lock_attempts',
  'siyi_lock_window_start',
])

const cache = new Map<string, string>()
let hydrated = false
let hydrationPromise: Promise<void> | null = null

/**
 * Whether a key is considered sensitive enough to land in secure storage.
 * Non-sensitive keys (preferences like base URL, booleans) stay in
 * localStorage to avoid the async hydration cost on every read.
 */
export function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEYS.has(key)
}

/**
 * Hydrate the in-memory cache from Capacitor Preferences and migrate any
 * legacy localStorage entries. Idempotent — safe to call multiple times.
 *
 * Should be awaited at app bootstrap before any render that needs secrets.
 */
export async function initSecureStore(): Promise<void> {
  if (hydrated) return
  if (hydrationPromise) return hydrationPromise

  hydrationPromise = (async () => {
    for (const key of SENSITIVE_KEYS) {
      try {
        const result = await Preferences.get({ key })
        if (result.value !== null && result.value !== undefined) {
          cache.set(key, result.value)
          continue
        }
      } catch (e) {
        // Preferences plugin missing (web dev) — fall through to localStorage
        swallowError('secureStore:74', e)
      }

      // Migration path: pick up legacy localStorage value and copy to Preferences
      try {
        const legacy = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null
        if (legacy !== null) {
          cache.set(key, legacy)
          try {
            await Preferences.set({ key, value: legacy })
            // Only remove from localStorage AFTER Preferences write succeeded
            localStorage.removeItem(key)
          } catch (e) {
            // Web fallback: keep value in localStorage
            swallowError('secureStore:87', e)
          }
        }
      } catch (e) {
        /* localStorage unavailable */
        swallowError('secureStore:91', e)
      }
    }
    hydrated = true
  })()

  return hydrationPromise
}

/** Synchronous read from in-memory cache. Returns null if missing or not hydrated. */
export function secureGet(key: string): string | null {
  return cache.get(key) ?? null
}

/**
 * Set a sensitive value. Updates the memory cache synchronously and persists
 * to secure storage asynchronously (fire-and-forget; errors logged).
 */
export function secureSet(key: string, value: string | null): void {
  if (value === null) {
    cache.delete(key)
    void Preferences.remove({ key }).catch(err => console.warn('[secureStore] remove failed:', key, err))
  } else {
    cache.set(key, value)
    void Preferences.set({ key, value }).catch(err => console.warn('[secureStore] set failed:', key, err))
  }
}

/** Wipe all sensitive entries (logout / pairing reset). */
export async function secureClear(): Promise<void> {
  cache.clear()
  for (const key of SENSITIVE_KEYS) {
    try {
      await Preferences.remove({ key })
    } catch (e) { /* ignore */ swallowError('secureStore:126', e) }
    try {
      if (typeof localStorage !== 'undefined') localStorage.removeItem(key)
    } catch (e) { /* ignore */ swallowError('secureStore:129', e) }
  }
}

/** For tests: reset the hydration state. Do not use in production. */
export function _resetForTests(): void {
  cache.clear()
  hydrated = false
  hydrationPromise = null
}
