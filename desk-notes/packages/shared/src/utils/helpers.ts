import { Category, TodoItem } from '../types'

export const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6)

/**
 * Format a timestamp for display in note title area.
 * Today → show time (14:30), Yesterday → "昨天", earlier → date (05/08)
 */
export function formatRelativeTime(timestamp: number, locale?: string): string {
  const date = new Date(timestamp)
  const now = new Date()

  const sameDay = date.toDateString() === now.toDateString()
  if (sameDay) {
    return date.toLocaleTimeString(locale === 'en' ? 'en-US' : 'zh-CN', { hour: '2-digit', minute: '2-digit' })
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) {
    return locale === 'en' ? 'Yesterday' : '昨天'
  }

  return `${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`
}

/**
 * Strip HTML tags, returning plain text.
 */
export function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, '').trim()
}

/**
 * Sanitize HTML string — removes dangerous elements and event handlers.
 * Allows only structural/formatting tags safe for contentEditable templates.
 */
export function sanitizeHtml(html: string): string {
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html')
    // Remove dangerous elements
    for (const tag of ['script', 'iframe', 'object', 'embed', 'form', 'link', 'style', 'meta', 'base']) {
      doc.querySelectorAll(tag).forEach(el => el.remove())
    }
    // Strip event handlers and javascript: URLs from all elements
    const all = doc.querySelectorAll('*')
    for (const el of all) {
      for (const attr of [...el.attributes]) {
        if (attr.name.startsWith('on') || (attr.name === 'href' && attr.value.startsWith('javascript:'))) {
          el.removeAttribute(attr.name)
        }
      }
    }
    return doc.body.innerHTML
  } catch {
    // If DOMParser fails, strip all tags as safe fallback
    return html.replace(/<[^>]*>/g, '')
  }
}

/**
 * Count the number of CJK characters (or words for western text).
 */
export function countText(text: string): { count: number; label: string } {
  if (!text) return { count: 0, label: '字符' }
  const cjk = text.match(/[一-鿿㐀-䶿豈-﫿]/g)
  if (cjk) return { count: cjk.length, label: '字' }
  const words = text.trim() ? text.trim().split(/\s+/).length : 0
  return { count: words, label: '字符' }
}

export const defaultCategories: Category[] = []

export const defaultTodos: TodoItem[] = []

/**
 * Convert hex color (#fff or #ffffff) to RGB object.
 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const s = hex.replace('#', '').trim()
  if (!/^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(s)) return null
  if (s.length === 3) {
    const r = parseInt(s[0] + s[0], 16)
    const g = parseInt(s[1] + s[1], 16)
    const b = parseInt(s[2] + s[2], 16)
    return { r, g, b }
  }
  if (s.length === 6) {
    const r = parseInt(s.slice(0, 2), 16)
    const g = parseInt(s.slice(2, 4), 16)
    const b = parseInt(s.slice(4, 6), 16)
    return { r, g, b }
  }
  return null
}

/**
 * Predefined color presets for note tinting.
 */
export const COLOR_PRESETS = [
  { label: '默认', color: undefined },
  { label: '暖阳', color: '#f97316' },
  { label: '薄雾', color: '#a78bfa' },
  { label: '晨曦', color: '#fbbf24' },
  { label: '青草', color: '#34d399' },
  { label: '湖蓝', color: '#60a5fa' },
  { label: '樱花', color: '#f472b6' },
  { label: '极光', color: '#22d3ee' },
]
