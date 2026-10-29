import { describe, it, expect, beforeAll } from 'vitest'
import { hashPin, verifyPin } from '../pinHash'

/**
 * Make crypto.subtle available in vitest's default Node environment.
 *
 * Node 20+ exposes `globalThis.crypto` natively, but the source code reads
 * `window.crypto`. We point window.crypto at the same SubtleCrypto so the
 * production code path runs unmodified.
 */
beforeAll(() => {
  const native = (globalThis as any).crypto
  if (!native?.subtle) {
    throw new Error('Test environment must provide globalThis.crypto.subtle (Node 20+)')
  }
  if (typeof (globalThis as any).window === 'undefined') {
    Object.defineProperty(globalThis, 'window', {
      value: { crypto: native },
      configurable: true,
    })
  } else {
    ;(globalThis as any).window.crypto = native
  }
})

describe('hashPin (v3: PBKDF2 + random salt)', () => {
  it('returns v3 format pbkdf2$600000$salt_hex$hash_hex', async () => {
    const hash = await hashPin('1234')
    // 32-hex 随机盐 + 64-hex 派生哈希（256-bit）
    expect(hash).toMatch(/^pbkdf2\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}$/)
  })

  it('uses a random salt — same PIN yields different hashes, both still verify', async () => {
    const a = await hashPin('123456')
    const b = await hashPin('123456')
    expect(a).not.toBe(b)  // 随机盐：同 PIN 每次哈希不同，防彩虹表
    expect(await verifyPin('123456', a)).toBe(true)
    expect(await verifyPin('123456', b)).toBe(true)
  })

  it('produces different hashes for different PINs', async () => {
    const a = await hashPin('1234')
    const b = await hashPin('1235')
    expect(a).not.toBe(b)
  })

  it('does not leak the plain SHA-256 of salt+pin (v3 is PBKDF2, not plain hash)', async () => {
    const hash = await hashPin('0000')
    // v1 salt 是 'siyi-pin-' → 旧格式是 SHA256('siyi-pin-0000') 的前 32 hex
    // v3 输出是 pbkdf2 结构，绝不能等于裸 SHA-256 结果
    const enc = new TextEncoder()
    const v1Buf = await crypto.subtle.digest('SHA-256', enc.encode('siyi-pin-0000'))
    const v1Hex = Array.from(new Uint8Array(v1Buf)).map(b => b.toString(16).padStart(2, '0')).join('')
    expect(hash).not.toBe(v1Hex)
    expect(hash).not.toBe(v1Hex.slice(0, 32))
  })
})

describe('verifyPin (P0-7 / b4-m1 — must match v3 + legacy v2 + legacy v1)', () => {
  // 构造 legacy v2 哈希：全 64-hex SHA-256('siyi-pin-v2-' + pin)
  async function legacyV2Hash(pin: string): Promise<string> {
    const enc = new TextEncoder()
    const buf = await crypto.subtle.digest('SHA-256', enc.encode('siyi-pin-v2-' + pin))
    return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
  }

  it('accepts v3 hash for the same PIN', async () => {
    const stored = await hashPin('1234')
    expect(await verifyPin('1234', stored)).toBe(true)
  })

  it('rejects v3 hash for the wrong PIN', async () => {
    const stored = await hashPin('1234')
    expect(await verifyPin('9999', stored)).toBe(false)
  })

  it('migrates: accepts legacy v2 hash for the same PIN', async () => {
    const stored = await legacyV2Hash('1234')
    expect(await verifyPin('1234', stored)).toBe(true)
  })

  it('rejects legacy v2 hash for the wrong PIN', async () => {
    const stored = await legacyV2Hash('1234')
    expect(await verifyPin('9999', stored)).toBe(false)
  })

  it('rejects a downgraded v3 hash (iterations < 600K)', async () => {
    // 构造 pbkdf2 结构但迭代数被降到 1 —— 防降级攻击必须拒绝
    const stored = await hashPin('1234')
    const weak = stored.replace(/^pbkdf2\$600000\$/, 'pbkdf2$1$')
    expect(await verifyPin('1234', weak)).toBe(false)
  })

  it('returns false for empty stored hash', async () => {
    expect(await verifyPin('1234', '')).toBe(false)
  })

  it('migrates: accepts legacy v1 hash (32-hex SHA-256 with old salt)', async () => {
    // Simulate a hash that was stored before P0-7 fix
    const enc = new TextEncoder()
    const buf = await crypto.subtle.digest('SHA-256', enc.encode('siyi-pin-2024'))
    const v1Hash = Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('').slice(0, 32)

    // verifyPin should still accept the right PIN against v1 hash
    expect(await verifyPin('2024', v1Hash)).toBe(true)
    expect(await verifyPin('1111', v1Hash)).toBe(false)
  })

  it('rejects FNV-1a 32-bit fallback (≤ 8 hex) — too weak to support', async () => {
    const fakeFnvHash = 'a3b8c1d2'  // 8 hex chars, like the old fallback
    expect(await verifyPin('1234', fakeFnvHash)).toBe(false)
  })

  it('uses constant-time comparison (no early return on length mismatch beyond bool)', async () => {
    // We can't easily measure timing in unit tests, but we can ensure that
    // mismatched-length stored hashes return false rather than throw.
    expect(await verifyPin('1234', 'short')).toBe(false)
    expect(await verifyPin('1234', 'a'.repeat(128))).toBe(false)
  })

  it('rejects empty PIN against any stored hash', async () => {
    const stored = await hashPin('1234')
    expect(await verifyPin('', stored)).toBe(false)
  })
})
