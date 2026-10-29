import { swallowError } from './swallowError'
/**
 * PIN hashing utilities (P1-12 fix, V2.1 批次4).
 *
 * History:
 * - v1: SHA-256 truncated to 32 hex (128-bit), FNV-1a 32-bit fallback — weak.
 * - v2: full 64-hex SHA-256 with fixed salt — still offline-bruteforceable for
 *   4~6 位数字 PIN（10^4~10^6 空间，单次 SHA-256 极快）。
 * - v3 (current): PBKDF2(SHA-256, ≥600K iterations) + 16B 随机盐，存储格式
 *   `pbkdf2$<iterations>$<salt_hex>$<hash_hex>`。PIN 空间小，必须慢哈希拖慢
 *   离线爆破（600K 迭代 ≈ 数百 ms/次）。
 *
 * Migration: verifyPin() 透明兼容 v2/v1 —— 旧用户验证通过后仍可解锁；下次设置
 * PIN（SettingsSecurity）写入 v3。verify 时拒绝迭代 < 600K 的 v3（防降级攻击）。
 */

const SALT_V2 = 'siyi-pin-v2-'
const SALT_V1 = 'siyi-pin-'
const PBKDF2_ITERATIONS = 600_000
const MIN_PBKDF2_ITERATIONS = 600_000  // 拒绝过低迭代的 v3 哈希（防降级）
const SALT_BYTES = 16

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('')
}

function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  const pairs = hex.match(/.{2}/g) || []
  // 显式 ArrayBuffer 构造，保证返回 Uint8Array<ArrayBuffer> 以满足 BufferSource
  const out = new Uint8Array(new ArrayBuffer(pairs.length))
  pairs.forEach((b, i) => { out[i] = parseInt(b, 16) })
  return out
}

function randomSaltHex(bytes: number = SALT_BYTES): string {
  const buf = new Uint8Array(bytes)
  crypto.getRandomValues(buf)
  return bytesToHex(buf)
}

async function sha256Hex(input: string): Promise<string> {
  if (!window.crypto?.subtle) {
    throw new Error('SubtleCrypto unavailable — cannot hash PIN safely')
  }
  const data = new TextEncoder().encode(input)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return bytesToHex(new Uint8Array(digest))
}

/** PBKDF2(SHA-256) 派生，返回 64-hex。 */
async function pbkdf2Hex(pin: string, saltHex: string, iterations: number): Promise<string> {
  const keyMaterial = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(pin), 'PBKDF2', false, ['deriveBits']
  )
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: hexToBytes(saltHex), iterations, hash: 'SHA-256' },
    keyMaterial, 256
  )
  return bytesToHex(new Uint8Array(bits))
}

/** Hash PIN for storage（v3：PBKDF2 + 随机盐，≥600K 迭代）。 */
export async function hashPin(pin: string): Promise<string> {
  const salt = randomSaltHex()
  const hash = await pbkdf2Hex(pin, salt, PBKDF2_ITERATIONS)
  return `pbkdf2$${PBKDF2_ITERATIONS}$${salt}$${hash}`
}

/**
 * Constant-time comparison helper.
 * Hex strings of equal length only — falls back to false otherwise.
 */
function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i)
  }
  return diff === 0
}

/**
 * Verify a user-entered PIN against a stored hash.
 *
 * Accepts v3（PBKDF2）、v2 与 legacy v1 哈希（透明迁移）。Use this in
 * LockScreen — never compare hashes by raw string equality.
 */
export async function verifyPin(pin: string, storedHash: string | null | undefined): Promise<boolean> {
  if (!storedHash) return false

  // v3: pbkdf2$iterations$salt$hash
  const parts = storedHash.split('$')
  if (parts.length === 4 && parts[0] === 'pbkdf2') {
    const iterations = parseInt(parts[1], 10)
    if (!Number.isFinite(iterations) || iterations < MIN_PBKDF2_ITERATIONS) {
      return false  // 过低迭代 = 降级攻击，拒绝
    }
    const candidate = await pbkdf2Hex(pin, parts[2], iterations)
    return constantTimeEqual(candidate, parts[3])
  }

  // v2 legacy: full 64-hex SHA-256 with v2 salt
  const v2 = await hashPinLegacyV2(pin)
  if (constantTimeEqual(v2, storedHash)) return true

  // v1 legacy: SHA-256 truncated to 32 hex with old salt
  if (storedHash.length === 32) {
    try {
      const v1 = (await sha256Hex(SALT_V1 + pin)).slice(0, 32)
      if (constantTimeEqual(v1, storedHash)) return true
    } catch (e) {
      /* SubtleCrypto missing — old hash unverifiable, fall through */
      swallowError('pinHash:68', e)
    }
  }

  return false
}

async function hashPinLegacyV2(pin: string): Promise<string> {
  return sha256Hex(SALT_V2 + pin)
}
