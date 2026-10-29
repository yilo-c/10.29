import { afterEach, describe, expect, it } from 'vitest'
import { resolveDefaultWsUrl } from '../ws-client'

type RuntimeWithWsUrl = typeof globalThis & {
  __SIYI_WS_URL__?: string
}

afterEach(() => {
  const runtime = globalThis as RuntimeWithWsUrl
  delete runtime.__SIYI_WS_URL__
  localStorage.removeItem('siyi_ws_url')
})

describe('resolveDefaultWsUrl', () => {
  it('prefers runtime override over persisted value', () => {
    const runtime = globalThis as RuntimeWithWsUrl
    runtime.__SIYI_WS_URL__ = 'ws://100.101.102.103:18765/ws'
    localStorage.setItem('siyi_ws_url', 'ws://127.0.0.1:18765/ws')

    expect(resolveDefaultWsUrl()).toBe('ws://100.101.102.103:18765/ws')
  })

  it('uses persisted value when runtime override is absent', () => {
    localStorage.setItem('siyi_ws_url', 'ws://desktop-tailnet:18765/ws')

    expect(resolveDefaultWsUrl()).toBe('ws://desktop-tailnet:18765/ws')
  })

  it('falls back to localhost when no override exists', () => {
    expect(resolveDefaultWsUrl()).toBe('ws://127.0.0.1:18765/ws')
  })
})
