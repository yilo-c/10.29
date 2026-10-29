import { describe, it, expect, beforeEach, vi } from 'vitest'
import { secureGet, secureSet, isSensitiveKey, _resetForTests, initSecureStore } from '../secureStore'

// Mock @capacitor/preferences with an in-memory store so tests don't depend
// on the native plugin (which throws on the web).
vi.mock('@capacitor/preferences', () => {
  const store = new Map<string, string>()
  return {
    Preferences: {
      get: async ({ key }: { key: string }) => ({ value: store.get(key) ?? null }),
      set: async ({ key, value }: { key: string; value: string }) => { store.set(key, value) },
      remove: async ({ key }: { key: string }) => { store.delete(key) },
      clear: async () => { store.clear() },
      __peek: () => store,
    },
  }
})

function mockLocalStorage() {
  let store: Record<string, string> = {}
  const mock = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} },
    get length() { return Object.keys(store).length },
    key: (i: number) => Object.keys(store)[i] ?? null,
    __peek: () => store,
  }
  Object.defineProperty(globalThis, 'localStorage', { value: mock, configurable: true })
  return mock
}

describe('secureStore (P0-8)', () => {
  beforeEach(async () => {
    _resetForTests()
    mockLocalStorage()
    const { Preferences } = await import('@capacitor/preferences')
    ;(Preferences as any).__peek().clear()
  })

  it('isSensitiveKey gates known sensitive keys', () => {
    expect(isSensitiveKey('siyi_api_token')).toBe(true)
    expect(isSensitiveKey('siyi_cloud_api_key')).toBe(true)
    expect(isSensitiveKey('siyi_app_pin')).toBe(true)
    expect(isSensitiveKey('siyi-biometric-cred')).toBe(true)
    expect(isSensitiveKey('siyi_base_url')).toBe(false)
    expect(isSensitiveKey('siyi_device_id')).toBe(false)
  })

  it('secureSet stores value in cache and Preferences', async () => {
    await initSecureStore()
    secureSet('siyi_api_token', 'sk-test')
    expect(secureGet('siyi_api_token')).toBe('sk-test')
    // give the async Preferences.set time to resolve
    await new Promise(r => setTimeout(r, 0))
    const { Preferences } = await import('@capacitor/preferences')
    expect((Preferences as any).__peek().get('siyi_api_token')).toBe('sk-test')
  })

  it('secureSet(null) removes value', async () => {
    await initSecureStore()
    secureSet('siyi_api_token', 'sk-x')
    secureSet('siyi_api_token', null)
    expect(secureGet('siyi_api_token')).toBeNull()
  })

  it('migrates legacy localStorage values into Preferences on init', async () => {
    const ls = mockLocalStorage()
    ls.setItem('siyi_api_token', 'legacy-token')
    ls.setItem('siyi_cloud_api_key', 'sk-legacy-key')
    ls.setItem('siyi_base_url', 'http://192.168.1.5:8000')  // non-sensitive: stays put

    _resetForTests()
    await initSecureStore()

    expect(secureGet('siyi_api_token')).toBe('legacy-token')
    expect(secureGet('siyi_cloud_api_key')).toBe('sk-legacy-key')

    // localStorage should have been wiped of sensitive keys
    expect(ls.getItem('siyi_api_token')).toBeNull()
    expect(ls.getItem('siyi_cloud_api_key')).toBeNull()
    // non-sensitive remain
    expect(ls.getItem('siyi_base_url')).toBe('http://192.168.1.5:8000')

    // Preferences holds the new values
    const { Preferences } = await import('@capacitor/preferences')
    expect((Preferences as any).__peek().get('siyi_api_token')).toBe('legacy-token')
  })

  it('initSecureStore is idempotent', async () => {
    secureSet('siyi_api_token', 'a')  // before init — set still works on cache
    await initSecureStore()
    await initSecureStore()  // second call should not clobber
    expect(secureGet('siyi_api_token')).toBe('a')
  })

  it('secureGet returns null for unknown keys', () => {
    expect(secureGet('siyi_api_token')).toBeNull()
  })
})
