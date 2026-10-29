import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { LocalAIProvider } from '../ai/local'
import { getAIProvider, streamAI } from '../ai'
import type { AIProvider } from '../ai/types'

// ── Helpers ──────────────────────────────────────────────────────────
function mockFetch(status = 200, body = ''): ReturnType<typeof vi.fn> {
  const fn = vi.fn()
  const reader = {
    read: vi.fn()
      .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(body) })
      .mockResolvedValueOnce({ done: true, value: undefined }),
  }
  fn.mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve('error body'),
    body: { getReader: () => reader },
  } as unknown as Response)
  return fn
}

function mockNDJSONStream(chunks: string[]): ReturnType<typeof vi.fn> {
  const fn = vi.fn()
  const lines = chunks.map(c => JSON.stringify({ response: c })).join('\n') + '\n' + JSON.stringify({ response: '', done: true })
  const reader = {
    read: vi.fn()
      .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(lines) })
      .mockResolvedValueOnce({ done: true, value: undefined }),
  }
  fn.mockResolvedValue({
    ok: true,
    status: 200,
    body: { getReader: () => reader },
  } as unknown as Response)
  return fn
}

function mockSSEStream(chunks: string[]): ReturnType<typeof vi.fn> {
  const fn = vi.fn()
  const data = chunks.map(c => `data: ${JSON.stringify({ choices: [{ delta: { content: c } }] })}\n`).join('') + 'data: [DONE]\n'
  const reader = {
    read: vi.fn()
      .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(data) })
      .mockResolvedValueOnce({ done: true, value: undefined }),
  }
  fn.mockResolvedValue({
    ok: true,
    status: 200,
    body: { getReader: () => reader },
  } as unknown as Response)
  return fn
}

const DEFAULT_CONFIG = {
  enabled: true,
  apiUrl: 'http://localhost:11434',
  model: 'test-model',
  protocol: 'ollama' as const,
  autoTagging: false,
}

// ── Tests ────────────────────────────────────────────────────────────
describe('AIProvider interface', () => {
  it('LocalAIProvider implements AIProvider', () => {
    const provider: AIProvider = new LocalAIProvider(DEFAULT_CONFIG)
    expect(provider.processText).toBeDefined()
    expect(provider.suggestTags).toBeDefined()
    expect(provider.parseIntent).toBeDefined()
    expect(provider.ask).toBeDefined()
  })
})

describe('LocalAIProvider', () => {
  let provider: LocalAIProvider
  let fetchSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    provider = new LocalAIProvider(DEFAULT_CONFIG)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe('processText (Ollama protocol)', () => {
    it('streams text via NDJSON', async () => {
      fetchSpy = mockNDJSONStream(['Hello', ' World'])
      vi.stubGlobal('fetch', fetchSpy)

      const chunks: string[] = []
      const result = await provider.processText('continue', 'test', (t) => chunks.push(t))

      expect(result).toBe('Hello World')
      expect(chunks).toEqual(['Hello', 'Hello World'])
      expect(fetchSpy).toHaveBeenCalledWith(
        'http://localhost:11434/api/generate',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('test-model'),
        }),
      )
    })

    it('handles API error', async () => {
      fetchSpy = mockFetch(500)
      vi.stubGlobal('fetch', fetchSpy)

      await expect(
        provider.processText('continue', 'test', () => {})
      ).rejects.toThrow('API 500')
    })

    it('handles network error', async () => {
      fetchSpy = vi.fn().mockRejectedValue(new Error('network failure'))
      vi.stubGlobal('fetch', fetchSpy)

      await expect(
        provider.processText('continue', 'test', () => {})
      ).rejects.toThrow('network failure')
    })

    it('supports abort signal', async () => {
      const controller = new AbortController()
      // Mock fetch to throw when signal is aborted
      fetchSpy = vi.fn().mockImplementation((_url, opts) => {
        if (opts?.signal?.aborted) return Promise.reject(new Error('The operation was aborted'))
        return new Promise(() => {})
      })
      vi.stubGlobal('fetch', fetchSpy)

      controller.abort()

      await expect(
        provider.processText('continue', 'test', () => {}, controller.signal)
      ).rejects.toThrow('aborted')
    })

    it('sends correct Ollama request body', async () => {
      fetchSpy = mockNDJSONStream(['ok'])
      vi.stubGlobal('fetch', fetchSpy)

      await provider.processText('summarize', 'long text', () => {})

      const callBody = JSON.parse(fetchSpy.mock.calls[0][1].body)
      expect(callBody.model).toBe('test-model')
      expect(callBody.stream).toBe(true)
      expect(callBody.prompt).toContain('long text')
      expect(callBody.prompt).toContain('总结')
    })
  })

  describe('processText (OpenAI protocol)', () => {
    beforeEach(() => {
      provider = new LocalAIProvider({
        ...DEFAULT_CONFIG,
        protocol: 'openai',
        apiKey: 'sk-test-key',
      })
    })

    it('streams text via SSE', async () => {
      fetchSpy = mockSSEStream(['Hello', ' World'])
      vi.stubGlobal('fetch', fetchSpy)

      const chunks: string[] = []
      const result = await provider.processText('continue', 'test', (t) => chunks.push(t))

      expect(result).toBe('Hello World')
      expect(chunks).toEqual(['Hello', 'Hello World'])
    })

    it('sends Authorization header', async () => {
      fetchSpy = mockSSEStream(['ok'])
      vi.stubGlobal('fetch', fetchSpy)

      await provider.processText('continue', 'test', () => {})

      expect(fetchSpy).toHaveBeenCalledWith(
        expect.stringContaining('/v1/chat/completions'),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer sk-test-key',
          }),
        }),
      )
    })

    it('sends correct OpenAI request body', async () => {
      fetchSpy = mockSSEStream(['ok'])
      vi.stubGlobal('fetch', fetchSpy)

      await provider.processText('translate_en', '你好', () => {})

      const callBody = JSON.parse(fetchSpy.mock.calls[0][1].body)
      expect(callBody.model).toBe('test-model')
      expect(callBody.messages).toEqual([{ role: 'user', content: expect.stringContaining('你好') }])
      expect(callBody.stream).toBe(true)
    })
  })

  describe('suggestTags', () => {
    it('returns tags from AI response', async () => {
      fetchSpy = mockNDJSONStream(['标签1, 标签2, 标签3'])
      vi.stubGlobal('fetch', fetchSpy)

      const tags = await provider.suggestTags('笔记内容', [])

      expect(tags.length).toBeGreaterThanOrEqual(1)
      expect(tags).toContain('标签1')
      expect(tags).toContain('标签2')
    })

    it('returns empty array for empty content', async () => {
      const tags = await provider.suggestTags('', [])
      expect(tags).toEqual([])
    })

    it('limits to 5 tags', async () => {
      const many = Array.from({ length: 10 }, (_, i) => `标签${i + 1}`).join(',')
      fetchSpy = mockNDJSONStream([many])
      vi.stubGlobal('fetch', fetchSpy)

      const tags = await provider.suggestTags('content', [])
      expect(tags.length).toBeLessThanOrEqual(5)
    })
  })

  describe('parseIntent', () => {
    it('parses create_note intent', async () => {
      fetchSpy = mockNDJSONStream([JSON.stringify({
        type: 'create_note',
        title: '会议记录',
        content: '下午3点开会',
        tags: ['会议'],
      })])
      vi.stubGlobal('fetch', fetchSpy)

      const result = await provider.parseIntent('> 记录会议下午3点')

      expect(result.type).toBe('create_note')
      expect(result.title).toBe('会议记录')
      expect(result.tags).toContain('会议')
    })

    it('parses create_todo intent', async () => {
      fetchSpy = mockNDJSONStream([JSON.stringify({
        type: 'create_todo',
        title: '买牛奶',
      })])
      vi.stubGlobal('fetch', fetchSpy)

      const result = await provider.parseIntent('> 今天记得买牛奶')

      expect(result.type).toBe('create_todo')
      expect(result.title).toBe('买牛奶')
    })

    it('returns unknown on parse failure', async () => {
      fetchSpy = mockNDJSONStream(['invalid json {{{'])
      vi.stubGlobal('fetch', fetchSpy)

      const result = await provider.parseIntent('garbage')

      expect(result.type).toBe('unknown')
    })
  })

  describe('ask (RAG)', () => {
    it('answers based on context', async () => {
      fetchSpy = mockNDJSONStream(['根据笔记内容，答案是42。'])
      vi.stubGlobal('fetch', fetchSpy)

      const answer = await provider.ask('答案是什么？', ['笔记：答案是42'])

      expect(answer).toBe('根据笔记内容，答案是42。')
    })

    it('handles empty context gracefully', async () => {
      fetchSpy = mockNDJSONStream(['没有找到相关笔记。'])
      vi.stubGlobal('fetch', fetchSpy)

      const answer = await provider.ask('问题', [])

      expect(answer).toBeTruthy()
    })
  })

  describe('updateConfig / getConfig', () => {
    it('updates config and returns copy', () => {
      provider.updateConfig({ model: 'new-model' })
      const cfg = provider.getConfig()
      expect(cfg.model).toBe('new-model')
      // Original is not mutated
      expect(provider.getConfig().protocol).toBe('ollama')
    })

    it('returns a copy, not a reference', () => {
      const cfg = provider.getConfig()
      cfg.model = 'hacked'
      expect(provider.getConfig().model).toBe('test-model')
    })
  })
})

describe('getAIProvider factory', () => {
  it('creates fresh instance each call', () => {
    const p1 = getAIProvider(DEFAULT_CONFIG)
    const p2 = getAIProvider(DEFAULT_CONFIG)
    expect(p1).not.toBe(p2)
    // Both should have same config
    expect(p1.getConfig().model).toBe('test-model')
    expect(p2.getConfig().model).toBe('test-model')
  })

  it('uses provided config', () => {
    const p = getAIProvider({ ...DEFAULT_CONFIG, model: 'custom' })
    expect(p.getConfig().model).toBe('custom')
  })
})

describe('streamAI', () => {
  let fetchSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchSpy = mockNDJSONStream(['compat result'])
    vi.stubGlobal('fetch', fetchSpy)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('calls onDone with full text', async () => {
    const onChunk = vi.fn()
    const onDone = vi.fn()
    const onError = vi.fn()

    await streamAI(DEFAULT_CONFIG, 'continue', 'text', onChunk, onDone, onError)

    expect(onDone).toHaveBeenCalledWith('compat result')
    expect(onError).not.toHaveBeenCalled()
  })

  it('calls onError when AI is disabled', async () => {
    const onDone = vi.fn()
    const onError = vi.fn()

    await streamAI({ ...DEFAULT_CONFIG, enabled: false }, 'continue', 't', () => {}, onDone, onError)

    expect(onError).toHaveBeenCalledWith('AI not enabled')
    expect(onDone).not.toHaveBeenCalled()
  })

  it('calls onError on failure', async () => {
    fetchSpy = vi.fn().mockRejectedValue(new Error('fail'))
    vi.stubGlobal('fetch', fetchSpy)

    const onDone = vi.fn()
    const onError = vi.fn()

    await streamAI(DEFAULT_CONFIG, 'continue', 't', () => {}, onDone, onError)

    expect(onError).toHaveBeenCalledWith('fail')
    expect(onDone).not.toHaveBeenCalled()
  })
})
