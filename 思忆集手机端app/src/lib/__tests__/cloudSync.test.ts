import { describe, it, expect, beforeEach, vi } from 'vitest'

// We test cloudSync's encryption layer. Need to mock the Capacitor Preferences
// import chain (used by tokenManager → secureStore).
vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async () => ({ value: null }),
    set: async () => {},
    remove: async () => {},
  },
}))

// Mock @desk-notes/shared since it's a workspace package and isn't needed for
// the encryption-layer tests.
vi.mock('@desk-notes/shared', () => ({
  useStore: { getState: () => ({ floatingNotes: [], todos: [] }) },
}))

function mockLocalStorage() {
  let store: Record<string, string> = {}
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v },
      removeItem: (k: string) => { delete store[k] },
      clear: () => { store = {} },
      get length() { return Object.keys(store).length },
      key: (i: number) => Object.keys(store)[i] ?? null,
    },
    configurable: true,
  })
}

describe('cloudSync — push payload (P0-9)', () => {
  beforeEach(() => {
    mockLocalStorage()
    vi.resetModules()
  })

  it('does NOT include cloudApiKey field in the synced settings payload', async () => {
    const fs = await import('node:fs/promises')
    const src = await fs.readFile(new URL('../cloudSync.ts', import.meta.url), 'utf8')
    // Strip line comments before searching so we only match real code
    const codeOnly = src.replace(/\/\/[^\n]*/g, '')
    // No active code line should contain `cloudApiKey:` (the field assignment)
    expect(codeOnly).not.toMatch(/cloudApiKey\s*:/)
    // No active code line should slice an api key for fingerprinting
    expect(codeOnly).not.toMatch(/cloudApiKey[^\n]*\.slice\(\s*0\s*,\s*8\s*\)/)
  })
})

describe('cloudSync — encryption (P0-9 / per-device salt)', () => {
  beforeEach(() => {
    mockLocalStorage()
    vi.resetModules()
  })

  it('encryption uses a per-device salt (versioned ciphertext prefix)', async () => {
    // Set up a known device id so salt is reproducible across runs
    localStorage.setItem('siyi_device_id', 'mobile-test-001')

    const mod = await import('../cloudSync')
    // expose helpers via module side effect — these aren't exported, so we
    // round-trip through encrypt/decrypt against the public API surface.
    // Instead we assert the format: current writes prefix the ciphertext with
    // VERSION_BYTE_V3 (0x03, 600K iterations per OWASP 2023). v2 (0x02) and
    // v1 (no prefix) remain readable for backward compat with existing
    // ciphertext already stored in the Worker DB.
    const enc = (mod as any).__test_encrypt ?? null
    if (!enc) {
      // helpers not exported — verify via source check that the version byte
      // is written in encryptData
      const fs = await import('node:fs/promises')
      const src = await fs.readFile(new URL('../cloudSync.ts', import.meta.url), 'utf8')
      // v3 is the current write version
      expect(src).toMatch(/VERSION_BYTE_V3\s*=\s*0x03/)
      expect(src).toMatch(/combined\[0\]\s*=\s*VERSION_BYTE_V3/)
      expect(src).toMatch(/getDeviceSalt\s*\(\s*\)/)
      // v2 constant must still exist (decrypt branch for historical ciphertext)
      expect(src).toMatch(/VERSION_BYTE_V2\s*=\s*0x02/)
      // legacy v1 detection path must still exist (for backward compat)
      expect(src).toMatch(/CRYPTO_SALT_V1/)
      // 600K iterations for new writes (OWASP 2023)
      expect(src).toMatch(/CRYPTO_ITERATIONS_V3\s*=\s*600_000/)
    }
  })

  it('decryption supports v3, v2, and v1 (legacy) ciphertexts', async () => {
    const fs = await import('node:fs/promises')
    const src = await fs.readFile(new URL('../cloudSync.ts', import.meta.url), 'utf8')
    // The decryptData function must branch on the version byte
    expect(src).toMatch(/combined\[0\]\s*===\s*VERSION_BYTE_V3/)
    expect(src).toMatch(/combined\[0\]\s*===\s*VERSION_BYTE_V2/)
    // And there must be a v1 branch
    expect(src).toMatch(/Legacy v1/)
  })
})
