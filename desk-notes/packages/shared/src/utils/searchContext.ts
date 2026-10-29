/**
 * Extract surrounding context for a keyword match in text.
 * Returns a snippet of the match with `padding` characters on each side.
 */
export function extractContext(text: string, keyword: string, padding = 20): string {
  const lower = text.toLowerCase()
  const kw = keyword.toLowerCase()
  const idx = lower.indexOf(kw)
  if (idx === -1) return text.slice(0, padding * 2 + keyword.length)

  const start = Math.max(0, idx - padding)
  const end = Math.min(text.length, idx + keyword.length + padding)
  let snippet = text.slice(start, end)
  if (start > 0) snippet = '…' + snippet
  if (end < text.length) snippet = snippet + '…'
  return snippet
}

/**
 * Wrap keyword matches in HTML content with <mark> tags.
 * Preserves HTML structure — only matches text outside of HTML tags.
 */
export function highlightInHtml(html: string, keyword: string): string {
  if (!keyword) return html
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`(${escaped})`, 'gi')
  // Split by HTML tags — even indices are text, odd indices are tags
  const parts = html.split(/(<[^>]*>)/)
  return parts.map((part, i) => {
    if (i % 2 === 0) {
      return part.replace(re, '<mark class="search-highlight">$1</mark>')
    }
    return part
  }).join('')
}
