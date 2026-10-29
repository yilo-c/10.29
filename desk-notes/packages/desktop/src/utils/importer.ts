import { swallowError } from './swallowError'
/**
 * Import module — parses files of various formats and converts them
 * into app-internal data structures (FloatingNote / TodoItem).
 */

export interface ImportResult {
  notes: Array<{ title: string; content: string; tags?: string[] }>
  todos: Array<{ text: string; done: boolean; category?: string }>
  errors: string[]
}

/**
 * Parse imported content based on file extension and content.
 */
export function parseImportedFile(filename: string, content: string): ImportResult {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  const nameLower = filename.toLowerCase()

  if (ext === 'json') return parseJSON(content)
  if (ext === 'txt' && nameLower.includes('todo')) return parseTodoTxt(content)
  if (ext === 'txt' || ext === 'md') return parseMarkdownFile(filename, content)

  return { notes: [], todos: [], errors: [`不支持的文件格式: .${ext}`] }
}

/**
 * Detect format from content when extension is ambiguous.
 */
export function detectFormat(content: string): 'markdown' | 'json' | 'todo' | 'plain' {
  const trimmed = content.trim()
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try { JSON.parse(trimmed); return 'json' } catch (e) { /* fall through */ swallowError('importer:32', e) }
  }
  // Check for markdown task list items
  if (/^[-*]\s\[[ x]\]/m.test(trimmed)) return 'markdown'
  // Check for todo.txt format
  if (/^[xX\s]?\s*\([A-Z]\)\s/m.test(trimmed) || /^x\s/m.test(trimmed)) return 'todo'
  return 'markdown'
}

/**
 * Parse JSON export from this app or compatible format.
 */
function parseJSON(content: string): ImportResult {
  const result: ImportResult = { notes: [], todos: [], errors: [] }
  try {
    const data = JSON.parse(content)
    // Format from our own export: { todos, categories, floatingNotes }
    if (data.floatingNotes && Array.isArray(data.floatingNotes)) {
      for (const n of data.floatingNotes) {
        result.notes.push({
          title: n.title || '导入的便签',
          content: n.content || '',
          tags: n.tags || [],
        })
        if (n.todos && Array.isArray(n.todos)) {
          for (const t of n.todos) {
            result.todos.push({ text: t.text, done: t.done, category: t.category })
          }
        }
      }
    }
    if (data.todos && Array.isArray(data.todos)) {
      for (const t of data.todos) {
        result.todos.push({ text: t.text, done: t.done, category: t.category })
      }
    }
    if (!data.floatingNotes && !data.todos) {
      // Bare array of notes or todos
      result.errors.push('JSON 格式无法识别，请使用思忆便签导出的 JSON 文件')
    }
  } catch (e: unknown) {
    result.errors.push(`JSON 解析失败: ${e instanceof Error ? e.message : '格式错误'}`)
  }
  return result
}

/**
 * Parse Todo.txt format.
 * Format: (A) task text +project @context due:2025-01-01
 * Also handles simplified: x task text, X task text
 */
function parseTodoTxt(content: string): ImportResult {
  const result: ImportResult = { notes: [], todos: [], errors: [] }
  const lines = content.split('\n').filter(l => l.trim())

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue

    // Check for "x " prefix (done) or "X " prefix
    const done = /^x\s/i.test(trimmed)
    const cleanText = trimmed.replace(/^x\s/i, '').trim()

    // Extract priority (A), (B), (C) — optional
    const priorityMatch = cleanText.match(/^\(([A-Z])\)\s*/)
    const textWithoutPriority = priorityMatch ? cleanText.slice(priorityMatch[0].length) : cleanText

    result.todos.push({
      text: textWithoutPriority,
      done,
    })
  }

  if (result.todos.length === 0) {
    result.errors.push('未找到有效的待办事项')
  }

  return result
}

/**
 * Parse a Markdown or plain text file.
 * Recognises:
 * - # Title as note title
 * - - [ ] / - [x] as todos
 * - Content as note body
 */
function parseMarkdownFile(filename: string, content: string): ImportResult {
  const result: ImportResult = { notes: [], todos: [], errors: [] }

  // Try to extract title from filename first
  let titleFromFile = filename.replace(/\.(md|txt)$/i, '').trim()
  if (titleFromFile === '-' || titleFromFile === '_') titleFromFile = ''

  const lines = content.split('\n')

  // Detect if this file is primarily a todo list
  const todoLines = lines.filter(l => /^\s*[-*]\s+\[[ x]\]/i.test(l))
  const headingMatch = content.match(/^#\s+(.+)/m)

  if (todoLines.length > lines.length * 0.3 && todoLines.length >= 2) {
    // Primarily a todo list
    for (const line of lines) {
      const match = line.match(/^\s*[-*]\s+\[([ x])\]\s+(.+)/i)
      if (match) {
        result.todos.push({
          text: match[2].trim(),
          done: match[1] === 'x',
        })
      }
    }
    // If there's non-todo content, add as a note too
    const nonTodoContent = lines.filter(l => !/^\s*[-*]\s+\[[ x]\]/i.test(l)).join('\n').trim()
    if (nonTodoContent && nonTodoContent !== content.trim()) {
      result.notes.push({
        title: headingMatch?.[1]?.trim() || titleFromFile || '导入的笔记',
        content: nonTodoContent,
      })
    }
  } else {
    // Primarily a note
    // Remove the first # heading (used as title)
    let body = content
    let title = titleFromFile || '导入的笔记'
    if (headingMatch) {
      title = headingMatch[1].trim()
      body = content.replace(/^#\s+.+/m, '').trim()
    }

    // Extract any todo lists from the note body
    const bodyLines = body.split('\n')
    const extractedTodos: Array<{ text: string; done: boolean }> = []
    const cleanBodyLines = bodyLines.filter(l => {
      const match = l.match(/^\s*[-*]\s+\[([ x])\]\s+(.+)/i)
      if (match) {
        extractedTodos.push({ text: match[2].trim(), done: match[1] === 'x' })
        return false
      }
      return true
    })

    if (extractedTodos.length > 0) {
      // Add the main content as a note
      result.notes.push({
        title,
        content: cleanBodyLines.join('\n').trim(),
      })
      // Add extracted todos
      for (const t of extractedTodos) {
        result.todos.push(t)
      }
    } else {
      result.notes.push({
        title,
        content: body,
      })
    }
  }

  return result
}
