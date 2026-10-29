/**
 * 设置桥 — 桌面 `settings.get` / `settings.set` 的封装。
 *
 * 用法：
 *   - `getRemoteSetting(key)` / `setRemoteSetting(key, value)` 是面向调用方的纯函数；
 *   - `bindRemoteSetting(key, applyRemote)` 让组件在 settings.changed 事件触发时收到
 *     最新值，组件需要决定如何把它写回本地 store；
 *   - `markLocalChange(key)` 让事件桥识别"这是我自己刚写下去的"，避免回环（10s 时间窗）。
 */
import { callRpc } from '../rpcClient'
import { onBackendEvent } from '../eventBridge'

const ECHO_GUARD_MS = 10_000
const localEchoes = new Map<string, number>()

function isOurOwnEcho(key: string): boolean {
  const ts = localEchoes.get(key)
  if (!ts) return false
  if (Date.now() - ts < ECHO_GUARD_MS) return true
  localEchoes.delete(key)
  return false
}

export function markLocalChange(key: string): void {
  localEchoes.set(key, Date.now())
}

export async function getRemoteSetting<T = unknown>(key: string): Promise<T | null> {
  try {
    const res = await callRpc<unknown>('settings.get', { key })
    return res as T
  } catch {
    return null
  }
}

export async function setRemoteSetting(key: string, value: unknown): Promise<boolean> {
  markLocalChange(key)
  try {
    await callRpc('settings.set', { key, value })
    return true
  } catch {
    return false
  }
}

export type SettingsApplyHandler = (key: string, value: unknown) => void

const bindings = new Map<string, Set<SettingsApplyHandler>>()
let installed = false

function ensureListener(): void {
  if (installed) return
  installed = true
  onBackendEvent('settings.changed', (event) => {
    const payload = (event.payload ?? {}) as Record<string, unknown>
    const key = typeof payload.key === 'string' ? payload.key : null
    if (!key) return
    if (isOurOwnEcho(key)) return
    const value = payload.value
    const bucket = bindings.get(key)
    if (bucket) {
      for (const h of bucket) {
        try { h(key, value) } catch { /* never let listener crash bridge */ }
      }
    }
    const wildcard = bindings.get('*')
    if (wildcard) {
      for (const h of wildcard) {
        try { h(key, value) } catch { /* ignore */ }
      }
    }
  })
}

/** 订阅某个 key 的远端变更。返回取消订阅函数。 */
export function bindRemoteSetting(key: string, handler: SettingsApplyHandler): () => void {
  ensureListener()
  let bucket = bindings.get(key)
  if (!bucket) {
    bucket = new Set()
    bindings.set(key, bucket)
  }
  bucket.add(handler)
  return () => {
    bucket?.delete(handler)
  }
}
