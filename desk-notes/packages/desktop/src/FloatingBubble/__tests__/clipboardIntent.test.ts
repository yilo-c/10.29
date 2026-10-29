import { describe, it, expect } from 'vitest'
import { classifyClipboardIntent } from '../clipboardIntent'

describe('classifyClipboardIntent', () => {
  it('returns null for URLs', () => {
    expect(classifyClipboardIntent('https://example.com/article')).toBeNull()
    expect(classifyClipboardIntent('http://github.com/user/repo')).toBeNull()
  })

  it('returns add_word for short English words', () => {
    expect(classifyClipboardIntent('hello')).toBe('add_word')
    expect(classifyClipboardIntent('apple')).toBe('add_word')
  })

  it('returns translate for English sentences', () => {
    expect(classifyClipboardIntent('The quick brown fox jumps over the lazy dog')).toBe('translate')
    expect(classifyClipboardIntent('This is a test sentence for translation')).toBe('translate')
  })

  it('returns translate for English paragraphs', () => {
    const longText = 'Large language models have revolutionized natural language processing. These models can understand and generate human-like text across a wide range of tasks, from translation to summarization.'
    expect(classifyClipboardIntent(longText)).toBe('translate')
  })

  it('returns save_note for task-like Chinese text', () => {
    expect(classifyClipboardIntent('明天需要交报告')).toBe('save_note')
    expect(classifyClipboardIntent('记得买牛奶和面包')).toBe('save_note')
  })

  it('returns save_note for short Chinese task text', () => {
    expect(classifyClipboardIntent('记得买牛奶和面包')).toBe('save_note')
  })

  it('returns save_note for English task text with task keywords', () => {
    expect(classifyClipboardIntent('remind me to buy milk')).toBe('save_note')
    expect(classifyClipboardIntent('todo: finish homework')).toBe('save_note')
  })

  it('returns summarize for long Chinese text', () => {
    const longText = '这篇文章讨论了人工智能在医疗领域的应用前景。研究人员发现，深度学习模型可以帮助医生更准确地诊断疾病。同时，AI技术也在药物研发方面展现出巨大潜力。多家科技公司已经投入大量资源开发相关应用。'
    expect(classifyClipboardIntent(longText)).toBe('summarize')
  })

  it('returns word_count for numeric data', () => {
    expect(classifyClipboardIntent('42')).toBe('word_count')
    expect(classifyClipboardIntent('100 200 300')).toBe('word_count')
  })

  it('returns polish for moderate-length Chinese text', () => {
    const text = '今天天气很好我们去公园散步吧'
    expect(classifyClipboardIntent(text)).toBe('polish')
  })
})
