// Test setup for vitest + jsdom

// 默认 WebSocket 桩：jsdom 环境下 globalThis.WebSocket 会解析到 Node 内置的
// undici 实现，模块加载时创建的真实连接会在测试结束后异步派发事件，触发
// 跨域 Event instanceof 校验失败（ERR_INVALID_ARG_TYPE）的 uncaught exception。
// 需要真实行为的测试（如 ws-client）会在 beforeEach 中自行覆写并在 afterEach 恢复。
class TestWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  readyState = TestWebSocket.CONNECTING
  url?: string
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  constructor(url?: string | URL, _protocols?: string | string[]) {
    this.url = url?.toString()
  }
  send() {}
  close() {
    this.readyState = TestWebSocket.CLOSED
  }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() {
    return false
  }
}
;(globalThis as { WebSocket: unknown }).WebSocket = TestWebSocket

// Mock matchMedia for jsdom
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// Mock scrollIntoView (jsdom doesn't implement it)
Element.prototype.scrollIntoView = () => {}

// Mock localStorage
const store = new Map<string, string>()
Object.defineProperty(window, 'localStorage', {
  value: {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
    get length() { return store.size },
    key: (i: number) => [...store.keys()][i] ?? null,
  },
})
