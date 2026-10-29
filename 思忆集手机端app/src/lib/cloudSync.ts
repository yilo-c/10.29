/**
 * 思忆集手机端云同步模块
 *
 * 通过 Cloudflare Worker 加密同步笔记/待办/设置。
 * WiFi 局域网内直连桌面端 WebSocket，外网走 Worker。
 */

import { useStore } from '@desk-notes/shared'
import { tokenManager } from './tokenManager'
import { secureGet, secureSet } from './secureStore'

const WORKER_URL_KEY = 'siyi_worker_url'
const SYNC_TOKEN_KEY = 'siyi_sync_token'
const REGISTER_SECRET_KEY = 'siyi_register_secret'
const DEVICE_ID_KEY = 'siyi_device_id'
const SYNC_CURSOR_KEY = 'siyi_sync_cursor'

// V2.1 批次3（V3-05）：同步客户端版本号，随 upload/download 上报
// （与桌面端 sync/cloud_sync_client.py 的 SYNC_CLIENT_VERSION 保持一致）
const SYNC_CLIENT_VERSION = '1.2.0'

const FETCH_TIMEOUT_MS = 15_000

async function fetchWithTimeout(input: RequestInfo, init?: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)
  try {
    return await fetch(input, { ...init, signal: controller.signal })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw new Error('cloudSync timeout')
    throw err
  } finally {
    clearTimeout(timer)
  }
}
function getWorkerUrl(): string {
  // worker URL is not sensitive (not a credential) — localStorage is fine
  return localStorage.getItem(WORKER_URL_KEY) || ''
}

function getSyncToken(): string {
  // P1-1: sync token 是凭据，通过 secureStore 读取（Android EncryptedSharedPreferences / iOS Keychain）
  return secureGet(SYNC_TOKEN_KEY) || ''
}

function getRegisterSecret(): string {
  // P1-1: register_secret 用于向 Worker 注册设备，通过 secureStore 读取
  return secureGet(REGISTER_SECRET_KEY) || ''
}

function getDeviceId(): string {
  let id = localStorage.getItem(DEVICE_ID_KEY)
  if (!id) {
    id = `mobile-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
    localStorage.setItem(DEVICE_ID_KEY, id)
  }
  return id
}

function getCursor(): number {
  return parseInt(localStorage.getItem(SYNC_CURSOR_KEY) || '0', 10)
}

function setCursor(ts: number): void {
  localStorage.setItem(SYNC_CURSOR_KEY, String(ts))
}

/** Check if cloud sync is configured */
export function isCloudSyncConfigured(): boolean {
  return !!(getWorkerUrl() && getSyncToken())
}

/** Configure cloud sync URL, token, and register secret.
 *
 * P1-1: token 和 register_secret 通过 secureStore 持久化（加密存储），
 * 不再明文写入 localStorage。worker URL 非凭据，仍用 localStorage。
 */
export function configureCloudSync(
  workerUrl: string,
  token: string,
  registerSecret?: string,
): void {
  localStorage.setItem(WORKER_URL_KEY, workerUrl.replace(/\/+$/, ''))
  secureSet(SYNC_TOKEN_KEY, token)
  if (registerSecret !== undefined) {
    secureSet(REGISTER_SECRET_KEY, registerSecret)
  }
}

/** Push local data to Worker */
export async function pushLocalData(): Promise<boolean> {
  if (!isCloudSyncConfigured()) return false

  const store = useStore.getState()
  const resources: Record<string, unknown> = {
    notes: store.floatingNotes.map(n => ({
      id: n.id, title: n.title, content: n.content,
      type: n.type, folderId: n.folderId, tags: n.tags,
      createdAt: n.createdAt, updatedAt: n.updatedAt,
    })),
    todos: store.todos.map(t => ({
      id: t.id, text: t.text, done: t.done,
      category: t.category, priority: t.priority,
      dueDate: t.dueDate, createdAt: t.createdAt,
      // H15 修复：推送 updatedAt 让远端能做正确的 last-write-wins 合并。
      // 缺省时 fallback 到 createdAt 以保持与旧版本兼容。
      updatedAt: t.updatedAt ?? t.createdAt,
    })),
    settings: {
      // P0-9: cloudApiKey 字段已移除。
      // 历史 bug：上传 cloudApiKey.slice(0, 8) 作为 "fingerprint"，但
      // 这 8 字符已足以暴露用户身份（OpenAI/DeepSeek key 前缀含可识别
      // 特征）。即使加密后也不该把密钥任何片段发到服务端 — 同步 token
      // 与用户密码强度直接相关，PBKDF2 的固定 salt + 短同步密码下离线
      // 爆破完全可行。
      cloudAiEnabled: tokenManager.cloudAiEnabled,
      preferDesktopAi: tokenManager.preferDesktopAi,
      promptBeforeCloud: tokenManager.promptBeforeCloud,
    },
  }

  let allOk = true
  for (const [resource, data] of Object.entries(resources)) {
    const plaintext = JSON.stringify(data)
    // Use Web Crypto via tiny helper
    const encrypted = await encryptData(plaintext, getSyncToken())
    const checksum = await sha256(plaintext)

    try {
      const resp = await fetchWithTimeout(`${getWorkerUrl()}/api/sync/upload`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${getSyncToken()}`,
        },
        body: JSON.stringify({
          resource,
          data_encrypted: encrypted,
          checksum,
          // V2.1 批次3（V3-05）：补发 client_version，避免被 Worker min 版本协商拒绝
          client_version: SYNC_CLIENT_VERSION,
        }),
      })
      if (!resp.ok) {
        const err = await resp.text()
        console.warn(`[cloudSync] push ${resource} failed:`, err)
        allOk = false
      }
    } catch (err) {
      console.warn(`[cloudSync] push ${resource} error:`, err)
      allOk = false
    }
  }

  // V2.1 批次3（P1-5）：push 后**不写游标**——游标仅由服务端水位（pull 返回的
  // updated_at）推进。旧代码 `setCursor(Date.now()/1000)` 用设备时钟写水位，
  // 时钟偏移会导致游标超前/滞后 Worker 时间戳，漏拉或重复拉取。
  return allOk
}

/** Pull remote data from Worker and merge into local store */
export async function pullRemoteData(): Promise<boolean> {
  if (!isCloudSyncConfigured()) return false

  try {
    const since = getCursor()
    const resp = await fetchWithTimeout(
      // V2.1 批次3（V3-05）：补发 client_version，避免被 Worker min 版本协商拒绝
      `${getWorkerUrl()}/api/sync/download?since=${since}&device_id=${getDeviceId()}&client_version=${SYNC_CLIENT_VERSION}`,
      { headers: { 'Authorization': `Bearer ${getSyncToken()}` } },
    )
    if (!resp.ok) return false

    const { items } = await resp.json() as {
      items: Array<{ resource: string; data_encrypted: string; checksum: string; updated_at: number }>
    }

    const store = useStore.getState()
    for (const item of items) {
      try {
        const plaintext = await decryptData(item.data_encrypted, getSyncToken())
        const data = JSON.parse(plaintext)

        switch (item.resource) {
          case 'notes':
            // Merge notes by ID
            for (const remoteNote of data) {
              // P2 审查修复：在循环内重新获取 state，避免快照过期导致重复添加
              const exists = useStore.getState().floatingNotes.find(n => n.id === remoteNote.id)
              if (!exists || (remoteNote.updatedAt || 0) > (exists.updatedAt || 0)) {
                // P1 修复：直接 setState 绕过 addFloatingNote 的同步副作用，避免 push 回环
                if (exists) {
                  useStore.setState(s => ({
                    floatingNotes: s.floatingNotes.map(n =>
                      n.id === remoteNote.id ? { ...n, ...remoteNote } : n
                    ),
                  }))
                } else {
                  // P1 审查修复：补齐 FloatingNote 必填 UI 字段
                  useStore.setState(s => ({ floatingNotes: [...s.floatingNotes, {
                    id: remoteNote.id,
                    title: remoteNote.title || '无标题',
                    content: remoteNote.content || '',
                    type: remoteNote.type || 'text',
                    x: remoteNote.x ?? 0,
                    y: remoteNote.y ?? 0,
                    zIndex: remoteNote.zIndex ?? 1,
                    width: remoteNote.width ?? 320,
                    height: remoteNote.height ?? 200,
                    createdAt: remoteNote.createdAt || Date.now(),
                    updatedAt: remoteNote.updatedAt || Date.now(),
                    tags: remoteNote.tags || [],
                    editMode: remoteNote.editMode || 'markdown',
                  }] }))
                }
              }
            }
            break
          case 'todos':
            for (const remoteTodo of data) {
              const exists = store.todos.find(t => t.id === remoteTodo.id)
              // H15 修复：用 updatedAt 比较而非 createdAt（旧逻辑导致远端
              // todo 编辑后永远拉不下来 — todo 创建后 createdAt 不变）。
              // 兼容旧 payload：缺 updatedAt 时回退到 createdAt。
              const remoteTime = remoteTodo.updatedAt ?? remoteTodo.createdAt ?? 0
              const localTime = exists?.updatedAt ?? exists?.createdAt ?? 0
              if (!exists || remoteTime > localTime) {
                // P1 修复：保留 remoteTodo.id，避免本地生成新 id 导致重复同步
                useStore.setState(s => ({
                  todos: exists
                    ? s.todos.map(t => t.id === remoteTodo.id ? { ...t, ...remoteTodo } : t)
                    : [...s.todos, { ...remoteTodo, id: remoteTodo.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}` }],
                }))
              }
            }
            break
          case 'settings':
            // Only apply non-sensitive settings
            if (data.cloudAiEnabled !== undefined) {
              tokenManager.cloudAiEnabled = data.cloudAiEnabled
            }
            break
        }

        if (item.updated_at > since) setCursor(item.updated_at)
      } catch (err) {
        console.warn(`[cloudSync] decrypt/merge failed for ${item.resource}:`, err)
      }
    }

    return true
  } catch (err) {
    console.warn('[cloudSync] pull error:', err)
    return false
  }
}

/** Register device with Worker.
 *
 * P1-1: 成功注册后 token 通过 secureStore 持久化（加密存储）。
 */
export async function registerDevice(name: string, token: string): Promise<boolean> {
  const url = getWorkerUrl()
  if (!url || !token) return false

  // P1-1: register_secret 从 secureStore 读取
  const registerSecret = getRegisterSecret()
  const body: Record<string, string> = { device_id: getDeviceId(), name, token }
  if (registerSecret) {
    body.register_secret = registerSecret
  }

  try {
    const resp = await fetchWithTimeout(`${url}/api/sync/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) return false

    // P1-1: token 通过 secureStore 持久化，不再明文写入 localStorage
    secureSet(SYNC_TOKEN_KEY, token)
    return true
  } catch {
    return false
  }
}

// ── Crypto helpers (Web Crypto API) ──

// PBKDF2 salt strategy:
// - v1 (legacy): fixed string 'siyi-sync-v1'. Cross-user shared salt — once
//   an attacker has the ciphertext + knows users will pick weak sync tokens,
//   a single offline attack rainbow-tables ALL users at once.
// - v2 (historical): per-device salt = 'siyi-sync-v2:' + deviceId. Forces the
//   attacker to grind PBKDF2 once per device, restoring the per-user cost
//   model. deviceId is not secret (it's in URLs/headers anyway), but it's
//   sufficient to prevent shared rainbow tables. 100K iterations.
// - v3 (historical): same per-device salt as v2, but 600K iterations (OWASP 2023
//   ≥600K). 不同 device_id 无法互解 —— 跨设备同步在加密层就是断的。
// - v4 (current): 同步组 salt = 'siyi-sync-v4:' + sync_group_id，**同组不同设备可互解、跨组不可解**。
//   sync_group_id = sha256(register_secret)[:16]，同 Worker 部署的设备共享。
//   salt 由接收端用自己的 group_id 派生（不入信封 —— 跨组即使 token 相同也派生不同密钥）。
//   信封头只带 iterations（4 字节），支持未来调整迭代次数而不换版本字节。
//
// Backward compat: ciphertexts include a 1-byte version prefix in the IV
// position to indicate which salt formula + iteration count to apply on decrypt.
const CRYPTO_SALT_V1 = 'siyi-sync-v1'
const CRYPTO_SALT_V2_PREFIX = 'siyi-sync-v2:'
const CRYPTO_SALT_V4_PREFIX = 'siyi-sync-v4:'
const CRYPTO_ITERATIONS_V1V2 = 100_000
const CRYPTO_ITERATIONS_V3 = 600_000
const CRYPTO_ITERATIONS_V4 = 600_000
const VERSION_BYTE_V2 = 0x02  // historical: 100K iterations, per-device salt
const VERSION_BYTE_V3 = 0x03  // historical: 600K iterations, per-device salt
const VERSION_BYTE_V4 = 0x04  // current: group salt + envelope-embedded iterations

function getDeviceSalt(): string {
  // Reuse the existing device id as a stable per-device differentiator
  return CRYPTO_SALT_V2_PREFIX + getDeviceId()
}

/**
 * P0.1: 从 register_secret 派生稳定的 sync_group_id。
 * 所有共享同一 register_secret 的设备派生出相同 id → 派生相同 AES 密钥 → 跨设备互解。
 * 只取 sha256 前 16 hex 字符（64 bit），完整 register_secret 不入密文/日志。
 */
async function deriveSyncGroupId(registerSecret: string): Promise<string> {
  if (!registerSecret) return ''
  const hashBuf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(registerSecret))
  const hex = Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2, '0')).join('')
  return hex.slice(0, 16)
}

/** P0.1: 获取当前 sync_group_id（基于 register_secret）；缺省返回空字符串走 v3 降级。 */
async function getSyncGroupId(): Promise<string> {
  return deriveSyncGroupId(getRegisterSecret())
}

/** P0.1: v4 同步组 salt。 */
function getGroupSalt(syncGroupId: string): string {
  return CRYPTO_SALT_V4_PREFIX + syncGroupId
}

async function deriveKey(token: string, salt: string, iterations: number = CRYPTO_ITERATIONS_V1V2): Promise<CryptoKey> {
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(token), 'PBKDF2', false, ['deriveKey'],
  )
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: new TextEncoder().encode(salt), iterations, hash: 'SHA-256' },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

async function encryptData(plaintext: string, token: string): Promise<string> {
  // P0.1: 优先写 v4（group salt），同组不同设备可互解、跨组不可解。
  // 缺 register_secret 时降级 v3（per-device salt，仅同设备可解）。
  const syncGroupId = await getSyncGroupId()
  if (syncGroupId) {
    const salt = getGroupSalt(syncGroupId)
    const key = await deriveKey(token, salt, CRYPTO_ITERATIONS_V4)
    const iv = crypto.getRandomValues(new Uint8Array(12))
    const encrypted = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv }, key, new TextEncoder().encode(plaintext),
    )
    const ctBytes = new Uint8Array(encrypted)
    // [0x04][iter(4 BE)][iv(12)][ct+tag]
    const combined = new Uint8Array(1 + 4 + iv.length + ctBytes.length)
    let off = 0
    combined[off++] = VERSION_BYTE_V4
    const dv = new DataView(combined.buffer)
    dv.setUint32(off, CRYPTO_ITERATIONS_V4, false); off += 4  // big-endian
    combined.set(iv, off); off += iv.length
    combined.set(ctBytes, off)
    return btoa(String.fromCharCode(...combined))
  }
  // 降级：v3 per-device salt, 600K iterations
  const key = await deriveKey(token, getDeviceSalt(), CRYPTO_ITERATIONS_V3)
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const encrypted = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, key, new TextEncoder().encode(plaintext),
  )
  const combined = new Uint8Array(1 + iv.length + encrypted.byteLength)
  combined[0] = VERSION_BYTE_V3
  combined.set(iv, 1)
  combined.set(new Uint8Array(encrypted), 1 + iv.length)
  return btoa(String.fromCharCode(...combined))
}

async function decryptData(ciphertext: string, token: string): Promise<string> {
  const combined = new Uint8Array(atob(ciphertext).split('').map(c => c.charCodeAt(0)))
  // Detect format by leading version byte.
  let salt: string
  let iterations: number
  let ivBytes: Uint8Array
  let dataBytes: Uint8Array
  if (combined[0] === VERSION_BYTE_V4) {
    // v4: iterations 从信封头读，salt 由接收端 group_id 派生
    // 头部：[ver(1)][iter(4)][iv(12)][ct+tag]
    if (combined.length < 17) throw new Error('v4 envelope too short for iter+iv')
    const syncGroupId = await getSyncGroupId()
    if (!syncGroupId) {
      throw new Error('v4 ciphertext requires sync_group_id (register_secret not configured?)')
    }
    const dv = new DataView(combined.buffer)
    iterations = dv.getUint32(1, false)  // big-endian
    ivBytes = combined.slice(5, 17)
    dataBytes = combined.slice(17)
    salt = getGroupSalt(syncGroupId)
  } else if (combined[0] === VERSION_BYTE_V3) {
    // v3: 600K iterations, per-device salt
    salt = getDeviceSalt()
    iterations = CRYPTO_ITERATIONS_V3
    ivBytes = combined.slice(1, 13)
    dataBytes = combined.slice(13)
  } else if (combined[0] === VERSION_BYTE_V2) {
    // v2: 100K iterations, per-device salt (historical)
    salt = getDeviceSalt()
    iterations = CRYPTO_ITERATIONS_V1V2
    ivBytes = combined.slice(1, 13)
    dataBytes = combined.slice(13)
  } else {
    // Legacy v1: no version byte, IV starts at byte 0, fixed salt, 100K.
    salt = CRYPTO_SALT_V1
    iterations = CRYPTO_ITERATIONS_V1V2
    ivBytes = combined.slice(0, 12)
    dataBytes = combined.slice(12)
  }
  const key = await deriveKey(token, salt, iterations)
  // Copy into fresh ArrayBuffer-backed views to satisfy stricter BufferSource
  // typings (Uint8Array.slice may otherwise be inferred as SharedArrayBuffer-backed).
  const iv = new Uint8Array(ivBytes)
  const data = new Uint8Array(dataBytes)
  const decrypted = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, data)
  return new TextDecoder().decode(decrypted)
}

async function sha256(data: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(data))
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('')
}

