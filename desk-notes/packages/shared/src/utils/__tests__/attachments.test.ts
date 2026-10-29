import { describe, it, expect } from 'vitest'
import {
  parseSiyiFileUri,
  guessAttachmentKind,
  extractAttachmentsFromMarkdown,
  buildAttachmentMarkdown,
  toSiyiFileUri,
  countAttachmentsByKind,
  formatAttachmentSummary,
} from '../attachments'

describe('parseSiyiFileUri', () => {
  it('parses valid siyi-file:// URI', () => {
    const result = parseSiyiFileUri('siyi-file://notes/abc/photo.jpg')
    expect(result).toEqual({ path: 'notes/abc/photo.jpg' })
  })

  it('returns null for non-siyi URIs', () => {
    expect(parseSiyiFileUri('https://example.com')).toBeNull()
  })

  it('returns null for empty input', () => {
    expect(parseSiyiFileUri('')).toBeNull()
  })

  it('returns null for null input', () => {
    expect(parseSiyiFileUri(null as unknown as string)).toBeNull()
  })
})

describe('guessAttachmentKind', () => {
  it('image from extension', () => {
    expect(guessAttachmentKind('photo.jpg')).toBe('image')
    expect(guessAttachmentKind('image.png')).toBe('image')
    expect(guessAttachmentKind('animation.gif')).toBe('image')
  })

  it('video from extension', () => {
    expect(guessAttachmentKind('video.mp4')).toBe('video')
    expect(guessAttachmentKind('movie.mov')).toBe('video')
  })

  it('pdf from extension', () => {
    expect(guessAttachmentKind('doc.pdf')).toBe('pdf')
  })

  it('document from extension', () => {
    expect(guessAttachmentKind('report.docx')).toBe('document')
    expect(guessAttachmentKind('notes.doc')).toBe('document')
  })

  it('file for unknown extension', () => {
    expect(guessAttachmentKind('data.bin')).toBe('file')
  })

  it('uses mime type when provided', () => {
    expect(guessAttachmentKind('data', 'image/jpeg')).toBe('image')
    expect(guessAttachmentKind('data', 'video/mp4')).toBe('video')
  })
})

describe('buildAttachmentMarkdown', () => {
  it('builds image markdown', () => {
    const md = buildAttachmentMarkdown({
      uri: 'siyi-file://notes/abc/photo.jpg',
      name: 'photo.jpg',
      kind: 'image',
    })
    expect(md).toBe('![photo.jpg](siyi-file://notes/abc/photo.jpg)')
  })

  it('builds file link markdown', () => {
    const md = buildAttachmentMarkdown({
      uri: 'siyi-file://notes/abc/report.pdf',
      name: 'report.pdf',
      kind: 'pdf',
    })
    expect(md).toBe('[report.pdf](siyi-file://notes/abc/report.pdf)')
  })
})

describe('toSiyiFileUri', () => {
  it('generates correct URI', () => {
    expect(toSiyiFileUri('notes/abc/photo.jpg')).toBe('siyi-file://notes/abc/photo.jpg')
  })

  it('normalizes backslashes', () => {
    expect(toSiyiFileUri('notes\\abc\\photo.jpg')).toBe('siyi-file://notes/abc/photo.jpg')
  })
})

describe('extractAttachmentsFromMarkdown', () => {
  it('extracts image attachments', () => {
    const content = '![photo](siyi-file://notes/abc/photo.jpg)'
    const result = extractAttachmentsFromMarkdown(content)
    expect(result).toHaveLength(1)
    expect(result[0].kind).toBe('image')
    expect(result[0].path).toBe('notes/abc/photo.jpg')
  })

  it('extracts file attachments', () => {
    const content = '[report](siyi-file://notes/abc/report.pdf)'
    const result = extractAttachmentsFromMarkdown(content)
    expect(result).toHaveLength(1)
    expect(result[0].kind).toBe('pdf')
  })

  it('handles mixed content', () => {
    const content = `# Notes
![img1](siyi-file://notes/abc/photo.jpg)
Some text
[doc](siyi-file://notes/abc/doc.pdf)`
    const result = extractAttachmentsFromMarkdown(content)
    expect(result).toHaveLength(2)
  })

  it('deduplicates same URI', () => {
    const content = '![img](siyi-file://notes/abc/photo.jpg) [img](siyi-file://notes/abc/photo.jpg)'
    const result = extractAttachmentsFromMarkdown(content)
    expect(result).toHaveLength(1)
  })

  it('returns empty for no attachments', () => {
    expect(extractAttachmentsFromMarkdown('plain text')).toEqual([])
  })

  it('returns empty for empty content', () => {
    expect(extractAttachmentsFromMarkdown('')).toEqual([])
  })
})

describe('countAttachmentsByKind', () => {
  it('counts by type', () => {
    const content = '![a](siyi-file://notes/a.png) ![b](siyi-file://notes/b.jpg) [c](siyi-file://notes/c.pdf)'
    const counts = countAttachmentsByKind(content)
    expect(counts.image).toBe(2)
    expect(counts.pdf).toBe(1)
  })
})

describe('formatAttachmentSummary', () => {
  it('formats summary text', () => {
    const content = '![a](siyi-file://notes/a.png) [b](siyi-file://notes/b.pdf)'
    const summary = formatAttachmentSummary(content)
    expect(summary).toContain('图片')
    expect(summary).toContain('PDF')
  })
})
