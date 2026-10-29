import { describe, it, expect, beforeEach } from 'vitest'
import { tokenManager } from '../tokenManager'

function mockLocalStorage() {
  let store: Record<string, string> = {}
  const mock = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value },
    removeItem: (key: string) => { delete store[key] },
    clear: () => { store = {} },
    get length() { return Object.keys(store).length },
    key: (i: number) => Object.keys(store)[i] ?? null,
  }
  Object.defineProperty(globalThis, 'localStorage', { value: mock, configurable: true })
}

describe('tokenManager', () => {
  beforeEach(() => {
    mockLocalStorage()
    tokenManager.clearAll()
  })

  it('defaults apiToken to null', () => {
    expect(tokenManager.apiToken).toBeNull()
  })

  it('stores and retrieves apiToken', () => {
    tokenManager.apiToken = 'test-token'
    expect(tokenManager.apiToken).toBe('test-token')
  })

  it('removes apiToken when set to null', () => {
    tokenManager.apiToken = 'test-token'
    tokenManager.apiToken = null
    expect(tokenManager.apiToken).toBeNull()
  })

  it('returns default baseUrl when not set', () => {
    expect(tokenManager.baseUrl).toBe('http://192.168.1.100:8000')
  })

  it('stores custom baseUrl', () => {
    tokenManager.baseUrl = 'http://example.com:8080'
    expect(tokenManager.baseUrl).toBe('http://example.com:8080')
  })

  it('defaults isPaired to false', () => {
    expect(tokenManager.isPaired).toBe(false)
  })

  it('handles boolean values for isPaired', () => {
    tokenManager.isPaired = true
    expect(tokenManager.isPaired).toBe(true)
    tokenManager.isPaired = false
    expect(tokenManager.isPaired).toBe(false)
  })

  it('defaults promptBeforeCloud to true', () => {
    expect(tokenManager.promptBeforeCloud).toBe(true)
  })

  it('defaults preferDesktopAi to true', () => {
    expect(tokenManager.preferDesktopAi).toBe(true)
  })

  it('defaults cloudAiEnabled to false', () => {
    expect(tokenManager.cloudAiEnabled).toBe(false)
  })

  it('stores and retrieves cloudApiKey', () => {
    tokenManager.cloudApiKey = 'sk-test-key'
    expect(tokenManager.cloudApiKey).toBe('sk-test-key')
  })

  it('removes cloudApiKey when set to null', () => {
    tokenManager.cloudApiKey = 'sk-test-key'
    tokenManager.cloudApiKey = null
    expect(tokenManager.cloudApiKey).toBe('')
  })

  it('returns default cloudApiBaseUrl when not set', () => {
    expect(tokenManager.cloudApiBaseUrl).toBe('https://api.deepseek.com')
  })

  it('stores userId and deviceId', () => {
    tokenManager.userId = 'user-1'
    tokenManager.deviceId = 'device-1'
    expect(tokenManager.userId).toBe('user-1')
    expect(tokenManager.deviceId).toBe('device-1')
  })

  it('clearAll removes all stored values', () => {
    tokenManager.apiToken = 'token'
    tokenManager.isPaired = true
    tokenManager.cloudApiKey = 'sk-key'
    tokenManager.clearAll()
    expect(tokenManager.apiToken).toBeNull()
    expect(tokenManager.isPaired).toBe(false)
    expect(tokenManager.cloudApiKey).toBe('')
  })
})
