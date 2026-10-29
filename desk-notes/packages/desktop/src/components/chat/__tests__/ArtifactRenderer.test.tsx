import { describe, expect, it } from 'vitest'
import { normalizeChatArtifacts } from '../artifacts'

describe('normalizeChatArtifacts', () => {
  it('keeps valid structured artifacts', () => {
    const result = normalizeChatArtifacts([
      { type: 'markdown', id: 'm1', content: '# hello' },
      { type: 'image', id: 'i1', src: 'https://example.com/a.png', alt: 'chart' },
      { type: 'chart', id: 'c1', chartType: 'bar', labels: ['A'], series: [{ name: 'count', values: [2] }] },
    ])
    expect(result).toHaveLength(3)
  })

  it('drops unsafe and incomplete artifacts', () => {
    const result = normalizeChatArtifacts([
      { type: 'image', id: 'http', src: 'http://example.com/a.png', alt: 'unsafe' },
      { type: 'image', id: 'svg', src: 'data:image/svg+xml;base64,abc', alt: 'unsafe' },
      { type: 'chart', id: 'nan', chartType: 'line', labels: ['A'], series: [{ name: 'x', values: [Number.NaN] }] },
      { type: 'mermaid', id: 'empty', definition: '  ' },
    ])
    expect(result).toEqual([])
  })

  it('limits untrusted artifact count', () => {
    const result = normalizeChatArtifacts(Array.from({ length: 60 }, (_, index) => ({ type: 'markdown', id: String(index), content: 'x' })))
    expect(result).toHaveLength(50)
  })
})
