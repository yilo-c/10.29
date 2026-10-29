import { describe, test, expect, beforeEach } from 'vitest'
import {
  builtinTemplates,
  getAllTemplates,
  getTemplateById,
  isBuiltin,
  createCustomTemplate,
  updateCustomTemplate,
  deleteCustomTemplate,
} from '../templates'

describe('templates', () => {
  beforeEach(() => {
    localStorage.removeItem('desk-notes-custom-templates')
  })

  test('builtinTemplates has 5 entries', () => {
    expect(builtinTemplates.length).toBe(5)
    for (const t of builtinTemplates) {
      expect(t.id).toBeTruthy()
      expect(t.label).toBeTruthy()
      expect(t.icon).toBeTruthy()
      expect(t.title).toBeTruthy()
      expect(t.content).toBeTruthy()
      expect(t.source).toBe('builtin')
    }
  })

  test('getAllTemplates returns builtins when no custom', () => {
    const all = getAllTemplates()
    expect(all.length).toBe(5)
    expect(all.every(t => t.source === 'builtin')).toBe(true)
  })

  test('getTemplateById finds builtin', () => {
    const t = getTemplateById('meeting')
    expect(t).not.toBeNull()
    expect(t?.label).toBe('会议记录')
  })

  test('getTemplateById returns null for unknown id', () => {
    expect(getTemplateById('nonexistent')).toBeNull()
  })

  test('isBuiltin returns true for builtins', () => {
    expect(isBuiltin('meeting')).toBe(true)
    expect(isBuiltin('weekly')).toBe(true)
    expect(isBuiltin('idea')).toBe(true)
    expect(isBuiltin('reading')).toBe(true)
    expect(isBuiltin('todo-list')).toBe(true)
  })

  test('isBuiltin returns false for custom ids', () => {
    expect(isBuiltin('custom-xxx')).toBe(false)
  })

  test('createCustomTemplate adds to storage', () => {
    const t = createCustomTemplate('My Template', 'My Title', '<p>hello</p>', 'fa-star')
    expect(t.label).toBe('My Template')
    expect(t.title).toBe('My Title')
    expect(t.content).toBe('<p>hello</p>')
    expect(t.icon).toBe('fa-star')
    expect(t.source).toBe('custom')
    expect(t.id).toMatch(/^custom-/)
    // Verify it appears in getAllTemplates
    const all = getAllTemplates()
    expect(all.length).toBe(6) // 5 builtins + 1 custom
    expect(all.some(x => x.id === t.id)).toBe(true)
  })

  test('createCustomTemplate uses default icon', () => {
    const t = createCustomTemplate('No Icon', 'Title', '')
    expect(t.icon).toBe('fa-file-pen')
  })

  test('updateCustomTemplate modifies fields', () => {
    const t = createCustomTemplate('Orig', 'Orig Title', 'orig content')
    const result = updateCustomTemplate(t.id, { label: 'Updated', content: 'new content' })
    expect(result).toBe(true)
    const updated = getTemplateById(t.id)
    expect(updated?.label).toBe('Updated')
    expect(updated?.title).toBe('Orig Title')
    expect(updated?.content).toBe('new content')
  })

  test('updateCustomTemplate returns false for builtins', () => {
    expect(updateCustomTemplate('meeting', { label: 'x' })).toBe(false)
  })

  test('updateCustomTemplate returns false for unknown id', () => {
    expect(updateCustomTemplate('nonexistent', { label: 'x' })).toBe(false)
  })

  test('deleteCustomTemplate removes from storage', () => {
    const t = createCustomTemplate('Delete me', 'Title', '')
    expect(getAllTemplates().length).toBe(6)
    const result = deleteCustomTemplate(t.id)
    expect(result).toBe(true)
    expect(getAllTemplates().length).toBe(5)
  })

  test('deleteCustomTemplate returns false for builtins', () => {
    expect(deleteCustomTemplate('meeting')).toBe(false)
  })

  test('deleteCustomTemplate returns false for unknown id', () => {
    expect(deleteCustomTemplate('nonexistent')).toBe(false)
  })

  test('createCustomTemplate multiple preserves all', () => {
    createCustomTemplate('A', 'A', '')
    createCustomTemplate('B', 'B', '')
    createCustomTemplate('C', 'C', '')
    const all = getAllTemplates()
    const custom = all.filter(t => t.source === 'custom')
    expect(custom.length).toBe(3)
  })
})
