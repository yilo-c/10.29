import { describe, it, expect } from 'vitest'
import { parseImportedFile, detectFormat } from '../importer'

describe('detectFormat', () => {
  it('detects JSON', () => {
    expect(detectFormat('{"todos":[]}')).toBe('json')
    expect(detectFormat('[{"text":"foo"}]')).toBe('json')
  })

  it('detects markdown with task list', () => {
    const md = '- [ ] Buy milk\n- [x] Write report'
    expect(detectFormat(md)).toBe('markdown')
  })

  it('falls back to markdown for plain text', () => {
    expect(detectFormat('Hello world\nThis is a note.')).toBe('markdown')
  })

  it('returns "markdown" for empty content', () => {
    expect(detectFormat('')).toBe('markdown')
  })
})

describe('parseImportedFile', () => {
  it('parses a JSON export with floatingNotes and todos', () => {
    const json = JSON.stringify({
      floatingNotes: [
        { title: 'Note 1', content: 'Hello', tags: ['tag1'] },
        { title: 'Todo Note', todos: [{ text: 'Task 1', done: false }] },
      ],
      todos: [{ text: 'Standalone', done: true }],
    })

    const result = parseImportedFile('backup.json', json)
    expect(result.notes).toHaveLength(2)
    expect(result.todos).toHaveLength(2)
    expect(result.errors).toHaveLength(0)
    expect(result.notes[0].title).toBe('Note 1')
    expect(result.notes[0].content).toBe('Hello')
    expect(result.notes[0].tags).toEqual(['tag1'])
  })

  it('parses a bare JSON array', () => {
    const json = JSON.stringify({ notes: [{ title: 'Test', content: 'Body' }] })
    const result = parseImportedFile('data.json', json)
    expect(result.errors.length).toBeGreaterThan(0)
  })

  it('handles malformed JSON', () => {
    const result = parseImportedFile('bad.json', '{invalid')
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toContain('JSON 解析失败')
  })

  it('parses a markdown todo list', () => {
    const md = '- [ ] 任务一\n- [x] 任务二\n\n一些说明文字'
    const result = parseImportedFile('tasks.md', md)
    expect(result.todos).toHaveLength(2)
    expect(result.todos[0]).toEqual({ text: '任务一', done: false })
    expect(result.todos[1]).toEqual({ text: '任务二', done: true })
  })

  it('parses a markdown note with heading', () => {
    const md = '# 我的笔记\n\n这是正文内容。\n\n- [ ] 顺便做个任务'
    const result = parseImportedFile('note.md', md)
    expect(result.notes).toHaveLength(1)
    expect(result.notes[0].title).toBe('我的笔记')
    expect(result.todos).toHaveLength(1)
    expect(result.todos[0].text).toBe('顺便做个任务')
  })

  it('parses a simple text file', () => {
    const content = '这是一条简单的笔记内容。\n\n第二段。'
    const result = parseImportedFile('note.txt', content)
    expect(result.notes).toHaveLength(1)
    expect(result.notes[0].title).toBe('note')
    expect(result.notes[0].content).toContain('这是一条简单的笔记内容。')
  })

  it('parses todo.txt format with done marker', () => {
    const content = 'x Buy groceries\n(A) Call mom'
    const result = parseImportedFile('todo.txt', content)
    expect(result.todos).toHaveLength(2)
    expect(result.todos[0].done).toBe(true)
    expect(result.todos[0].text).toBe('Buy groceries')
    expect(result.todos[1].done).toBe(false)
    expect(result.todos[1].text).toBe('Call mom')
  })

  it('returns error for unsupported file extension', () => {
    const result = parseImportedFile('data.csv', 'a,b,c')
    expect(result.errors.length).toBeGreaterThan(0)
    expect(result.errors[0]).toContain('不支持的文件格式')
  })

  it('handles empty JSON content gracefully', () => {
    const result = parseImportedFile('empty.json', '{}')
    expect(result.notes).toHaveLength(0)
    expect(result.todos).toHaveLength(0)
    expect(result.errors.length).toBeGreaterThan(0)
  })

  it('extracts filename without extension as title for md files', () => {
    const result = parseImportedFile('meeting-notes.md', 'Just some text\nno heading')
    expect(result.notes[0].title).toBe('meeting-notes')
  })
})
