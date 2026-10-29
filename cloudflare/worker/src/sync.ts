/**
 * 思忆集 Worker — 同步端点
 *
 * POST /api/sync/upload  — 推送加密数据
 *   Body: { resource, data_encrypted, checksum, device_id }
 *   Auth: Bearer <token>
 *
 * GET /api/sync/download?since=<timestamp>&device_id=<id>
 *   Returns: [{ resource, data_encrypted, checksum, updated_at }]
 *
 * POST /api/sync/register  — 注册设备
 *   Body: { device_id, name, token }
 */

import type { Env } from './index'

interface SyncUploadBody {
  resource: string        // 'notes' | 'todos' | 'settings' | 'info_brief' | 'info_sections'
  data_encrypted: string  // AES-256-GCM ciphertext (base64)
  checksum: string        // SHA-256 of plaintext
  client_version?: string // V3-05：客户端版本，用于 min_client_version 协商
}

interface RegisterBody {
  device_id: string
  name: string
  token: string
}

function errorJson(msg: string, status = 400): Response {
  return new Response(JSON.stringify({ error: msg }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// ── V2.1 批次3：加密/工具 helpers ─────────────────────────────

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
}

function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes)
  crypto.getRandomValues(buf)
  return Array.from(buf).map(b => b.toString(16).padStart(2, '0')).join('')
}

// P2：token 加盐慢哈希（PBKDF2）。防 D1 泄露后离线爆破 token。
// 设备数少（个人应用 <10），authDevice 遍历比对可接受；10k 迭代 ≈ 每设备 ~10ms。
const TOKEN_PBKDF2_ITERATIONS = 10_000

async function pbkdf2TokenHash(token: string, salt: string): Promise<string> {
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(token), 'PBKDF2', false, ['deriveBits']
  )
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: new TextEncoder().encode(salt), iterations: TOKEN_PBKDF2_ITERATIONS, hash: 'SHA-256' },
    keyMaterial, 256
  )
  return Array.from(new Uint8Array(bits)).map(b => b.toString(16).padStart(2, '0')).join('')
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return diff === 0
}

// 全端点限流（D1 窗口计数，60s）。返回 true 表示应拒绝。
async function rateLimited(env: Env, ip: string, endpoint: string, limit: number): Promise<boolean> {
  const windowStart = Math.floor(Date.now() / 60_000) * 60
  await env.SYNC_DB.prepare(
    `INSERT INTO rate_limits (ip, endpoint, window_start, count) VALUES (?, ?, ?, 1)
     ON CONFLICT(ip, endpoint, window_start) DO UPDATE SET count = count + 1`
  ).bind(ip, endpoint, windowStart).run()
  const cur = await env.SYNC_DB.prepare(
    'SELECT count FROM rate_limits WHERE ip = ? AND endpoint = ? AND window_start = ?'
  ).bind(ip, endpoint, windowStart).first<{ count: number }>()
  return (cur?.count ?? 1) > limit
}

// V2.1 批次3：幂等 DDL 迁移（devices.salt 列 + rate_limits 表）
async function ensureSchema(env: Env): Promise<void> {
  try {
    await env.SYNC_DB.prepare('ALTER TABLE devices ADD COLUMN salt TEXT').run()
  } catch (e) {
    if (!(e instanceof Error) || !/duplicate column/i.test(e.message)) throw e
  }
  await env.SYNC_DB.prepare(
    `CREATE TABLE IF NOT EXISTS rate_limits (
       ip TEXT, endpoint TEXT, window_start INTEGER, count INTEGER,
       PRIMARY KEY (ip, endpoint, window_start)
     )`
  ).run()
}

// T7 缺口 1（2026-08-30）：白名单扩展 info_brief / info_sections（资讯简报 /
// 资讯板块配置，桌面 standalone 推送路径使用的类型键名，与 Python 侧
// sync/_cloud_sync_crypto.py 的 RESOURCE_INFO_* 一致）。仍走同一 AES-GCM v4
// 加密信封与 upload 契约，仅放行 resource 键名。
const ALLOWED_RESOURCES = new Set(['notes', 'todos', 'settings', 'info_brief', 'info_sections'])
const MAX_ENCRYPTED_BYTES = 1_500_000  // ~1.5MB 上限（防超大 payload 打爆 D1）
const CHECKSUM_RE = /^[0-9a-f]{64}$/i

// V3-05 决策 b：语义化版本比较（支持 1 / 1.2 / 1.2.3）。
function isVersionAtLeast(client: string, min: string): boolean {
  const a = client.split('.').map(Number)
  const b = min.split('.').map(Number)
  const len = Math.max(a.length, b.length)
  for (let i = 0; i < len; i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return true
}

// V3-05 决策 b：服务端拒绝低于 min_client_version 的客户端并返回明确错误码。
// 返回 null 表示放行；否则返回 426 响应（SYNC_VERSION_TOO_OLD）。
function rejectStaleVersion(
  client: string | undefined,
  env: Env
): Response | null {
  const min = env.MIN_CLIENT_VERSION || '1'
  if (!client || !isVersionAtLeast(client, min)) {
    return new Response(
      JSON.stringify({
        error: 'client version too old',
        code: 'SYNC_VERSION_TOO_OLD',
        min_client_version: min,
      }),
      {
        status: 426,
        headers: { 'Content-Type': 'application/json' },
      }
    )
  }
  return null
}

async function authDevice(request: Request, env: Env): Promise<string | null> {
  const auth = request.headers.get('Authorization')
  if (!auth || !auth.startsWith('Bearer ')) return null
  const token = auth.slice(7)

  // V2.1 批次3（P2）：token 加盐慢哈希比对。旧行（无 salt）走 SHA-256 快路径兼容；
  // 新行走 PBKDF2(salt)。设备数少，遍历比对可接受。
  const rows = await env.SYNC_DB.prepare(
    'SELECT device_id, token_hash, salt FROM devices WHERE revoked_at IS NULL'
  ).all<{ device_id: string; token_hash: string; salt: string | null }>()

  for (const row of rows.results) {
    if (row.salt) {
      const hash = await pbkdf2TokenHash(token, row.salt)
      if (timingSafeEqual(hash, row.token_hash)) return row.device_id
    } else {
      const legacy = await sha256Hex(token)
      if (legacy === row.token_hash) return row.device_id
    }
  }
  return null
}

export async function handleSyncRequest(
  request: Request,
  env: Env,
  cors: Record<string, string>
): Promise<Response> {
  const url = new URL(request.url)
  const path = url.pathname.replace('/api/sync', '')

  // ── POST /api/sync/register ──
  if (path === '/register' && request.method === 'POST') {
    const body = await request.json<RegisterBody & { register_secret?: string }>()
    if (!body.device_id || !body.name || !body.token) {
      return errorJson('device_id, name, token required')
    }
    // 输入校验：长度限制（防超大字段撑爆 D1）
    const deviceId = String(body.device_id).slice(0, 128)
    const name = String(body.name).slice(0, 64)
    const token = String(body.token).slice(0, 256)
    if (!deviceId || !name || !token) {
      return errorJson('device_id, name, token required')
    }
    // H1 修复：要求预共享密钥，防止任意设备注册
    if (!env.REGISTER_SECRET || body.register_secret !== env.REGISTER_SECRET) {
      return errorJson('invalid register_secret', 403)
    }
    // V2.1 批次3：register/revoke 限流（5/min/IP，防爆破 register_secret）
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown'
    if (await rateLimited(env, ip, 'register', 5)) {
      return errorJson('rate limited', 429)
    }
    await ensureSchema(env)

    // V2.1 批次3（P1-7）：被吊销设备拒绝重注册（不再用 INSERT OR REPLACE 清空 revoked_at）
    const revokedRow = await env.SYNC_DB.prepare(
      'SELECT revoked_at FROM devices WHERE device_id = ?'
    ).bind(deviceId).first<{ revoked_at: number | null }>()
    if (revokedRow && revokedRow.revoked_at != null) {
      return errorJson('device revoked, re-registration not allowed', 403)
    }

    // P2：token 加盐慢哈希（随机盐 + PBKDF2）
    const salt = randomHex(16)
    const tokenHash = await pbkdf2TokenHash(token, salt)

    // V3-02 schema 演进：设备吊销需要 revoked_at 列，DDL 幂等迁移（已存在则忽略）
    try {
      await env.SYNC_DB.prepare('ALTER TABLE devices ADD COLUMN revoked_at INTEGER').run()
    } catch (e) {
      if (!(e instanceof Error) || !/duplicate column/i.test(e.message)) throw e
    }

    // ON CONFLICT DO UPDATE 仅更新 name/token_hash/salt，**保留 revoked_at**——
    // 吊销状态跨重注册持续生效（防 INSERT OR REPLACE 清空 revoked_at 的绕过）。
    await env.SYNC_DB.prepare(
      `INSERT INTO devices (device_id, name, token_hash, salt, created_at) VALUES (?, ?, ?, ?, unixepoch())
       ON CONFLICT(device_id) DO UPDATE SET
         name = excluded.name,
         token_hash = excluded.token_hash,
         salt = excluded.salt`
    ).bind(deviceId, name, tokenHash, salt).run()

    return new Response(JSON.stringify({ success: true }), {
      headers: { ...cors, 'Content-Type': 'application/json' },
    })
  }

  // ── POST /api/sync/revoke-device ── 设备吊销（V3-02）
  if (path === '/revoke-device' && request.method === 'POST') {
    const body = await request.json<{ device_id?: string; register_secret?: string }>()
    const targetDevice = typeof body.device_id === 'string' ? body.device_id.trim().slice(0, 128) : ''
    if (!targetDevice) {
      return errorJson('device_id required')
    }
    // 鉴权同 register：要求预共享密钥，避免任意设备可吊销他人
    if (!env.REGISTER_SECRET || body.register_secret !== env.REGISTER_SECRET) {
      return errorJson('invalid register_secret', 403)
    }
    const ip = request.headers.get('CF-Connecting-IP') || 'unknown'
    if (await rateLimited(env, ip, 'revoke', 5)) {
      return errorJson('rate limited', 429)
    }
    await ensureSchema(env)
    await env.SYNC_DB.prepare(
      'UPDATE devices SET revoked_at = unixepoch() WHERE device_id = ? AND revoked_at IS NULL'
    ).bind(targetDevice).run()
    return new Response(JSON.stringify({ success: true }), {
      headers: { ...cors, 'Content-Type': 'application/json' },
    })
  }

  // ── Require auth for sync endpoints ──
  const deviceId = await authDevice(request, env)
  if (!deviceId) {
    return errorJson('unauthorized', 401)
  }
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown'

  // ── POST /api/sync/upload ──
  if (path === '/upload' && request.method === 'POST') {
    const body = await request.json<SyncUploadBody>()
    // V3-05 决策 b：拒绝低于 min_client_version 的客户端
    const staleUpload = rejectStaleVersion(body.client_version, env)
    if (staleUpload) return staleUpload
    if (!body.resource || !body.data_encrypted || !body.checksum) {
      return errorJson('resource, data_encrypted, checksum required')
    }
    // V2.1 批次3：输入校验 — resource 白名单 + 大小上限 + checksum 格式
    if (!ALLOWED_RESOURCES.has(body.resource)) {
      return errorJson(
        'invalid resource (allowed: notes, todos, settings, info_brief, info_sections)',
        400
      )
    }
    if (typeof body.data_encrypted !== 'string' || body.data_encrypted.length > MAX_ENCRYPTED_BYTES) {
      return errorJson('data_encrypted too large or invalid', 413)
    }
    if (typeof body.checksum !== 'string' || !CHECKSUM_RE.test(body.checksum)) {
      return errorJson('invalid checksum format', 400)
    }
    if (await rateLimited(env, ip, 'upload', 120)) {
      return errorJson('rate limited', 429)
    }

    await env.SYNC_DB.prepare(
      `INSERT OR REPLACE INTO sync_data (device_id, resource, data_encrypted, checksum, updated_at)
       VALUES (?, ?, ?, ?, unixepoch())`
    ).bind(deviceId, body.resource, body.data_encrypted, body.checksum).run()

    return new Response(JSON.stringify({ success: true }), {
      headers: { ...cors, 'Content-Type': 'application/json' },
    })
  }

  // ── GET /api/sync/download?since=<ts> ──
  if (path === '/download' && request.method === 'GET') {
    const rawSince = url.searchParams.get('since') ?? '0'
    const since = Number(rawSince)
    // V2.1 批次3：since 必须为非负整数（防 NaN/负数/溢出 SQL 注入面）
    if (!Number.isFinite(since) || since < 0 || !/^\d+$/.test(rawSince)) {
      return errorJson('invalid since (must be non-negative integer)', 400)
    }
    // V3-05 决策 b：拒绝低于 min_client_version 的客户端
    const staleDownload = rejectStaleVersion(
      url.searchParams.get('client_version') ?? undefined,
      env
    )
    if (staleDownload) return staleDownload
    if (await rateLimited(env, ip, 'download', 120)) {
      return errorJson('rate limited', 429)
    }

    // B-09（2026-08-27）：同秒更新丢失窗口修复。
    // 原 SQL 用「严格大于」（updated_at 大于 since）：设备 A 在 T 秒 push notes、
    // 设备 B 同 T 秒 push todos，桌面 pull 拿到 A 后 cursor=T，下次 since=T 时
    // 同秒条目被严格大于过滤掉，永远拿不到 B（直到 B 再更新）——同秒窗口数据丢失。
    // 改「大于等于」（updated_at >= since）：同秒条目在下一次拉取仍可达；
    // 客户端按 (resource, device_id) 去重 + LWW 幂等吸收重复合并
    // （见 cloud_sync_client.pull_remote_data）。SELECT 增补 device_id 供去重。
    const rows = await env.SYNC_DB.prepare(
      `SELECT resource, device_id, data_encrypted, checksum, updated_at
       FROM sync_data
       WHERE device_id != ? AND updated_at >= ?
       ORDER BY updated_at ASC`
    ).bind(deviceId, since).all()

    return new Response(JSON.stringify({ items: rows.results }), {
      headers: { ...cors, 'Content-Type': 'application/json' },
    })
  }

  return errorJson('not found', 404)
}
