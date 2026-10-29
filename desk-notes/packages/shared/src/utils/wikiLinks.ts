import Fuse from 'fuse.js'

export interface WikiLinkMatch {
  title: string
  display: string
}

export interface NoteStub {
  id: string
  title: string
}

export interface NoteWithContent extends NoteStub {
  content: string
  todos?: { text: string }[]
}

// Matches [[Title]] and [[Title|Display Text]]
const WIKI_LINK_REGEX = /\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]/g

// Matches 便签://note-id for backlink scanning
const PROTOCOL_LINK_REGEX = /便签:\/\/([a-zA-Z0-9_-]+)/g

export { WIKI_LINK_REGEX, PROTOCOL_LINK_REGEX }

/** Escape special regex chars in a string */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Parse all [[wiki links]] from text.
 * Returns an array of { title, display } — display falls back to title.
 */
export function parseWikiLinks(text: string): WikiLinkMatch[] {
  const result: WikiLinkMatch[] = []
  const re = new RegExp(WIKI_LINK_REGEX)
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    result.push({
      title: m[1].trim(),
      display: (m[2] || m[1]).trim(),
    })
  }
  return result
}

/**
 * Build a Fuse index over note titles for fuzzy matching.
 */
export function buildFuseIndex(notes: NoteStub[]): Fuse<NoteStub> {
  return new Fuse(notes, {
    keys: ['title'],
    threshold: 0.4,
    minMatchCharLength: 1,
    includeScore: true,
  })
}

/**
 * Resolve a wiki link title to a note.
 * Priority: exact match → case-insensitive match → fuzzy match → null
 */
export function resolveWikiLink(
  linkTitle: string,
  notes: NoteStub[],
  fuse?: Fuse<NoteStub>,
): NoteStub | null {
  if (!linkTitle || notes.length === 0) return null

  // 1. Exact match
  const exact = notes.find(n => n.title === linkTitle)
  if (exact) return exact

  // 2. Case-insensitive match
  const lower = linkTitle.toLowerCase()
  const ci = notes.find(n => (n.title || '').toLowerCase() === lower)
  if (ci) return ci

  // 3. Fuzzy match via Fuse
  if (fuse) {
    const results = fuse.search(linkTitle)
    if (results.length > 0) return results[0].item
  }

  return null
}

/**
 * Replace [[wiki links]] in text with HTML anchor tags.
 * Resolved links: purple (#a78bfa), navigateToRef on click
 * Unresolved links: dashed red (#ef4444), tooltip "Note not found"
 *
 * @param text - plain text or HTML content
 * @param notes - all notes for title resolution
 * @param navigateToRef - click handler for resolved links
 * @param fuse - optional pre-built Fuse index
 * @returns HTML string with wiki links replaced by <a> tags
 */
export function replaceWikiLinks(
  text: string,
  notes: NoteStub[],
  _navigateToRef: (id: string) => void,
  fuse?: Fuse<NoteStub>,
): string {
  const f = fuse || buildFuseIndex(notes)
  return text.replace(WIKI_LINK_REGEX, (_full, title: string, display?: string) => {
    const linkTitle = title.trim()
    const displayText = (display || linkTitle).trim()
    const resolved = resolveWikiLink(linkTitle, notes, f)

    if (resolved) {
      const escapedTitle = escapeRegex(linkTitle)
      return `<a href="#ref-${resolved.id}" data-wiki-link="true" data-note-title="${escapedTitle}" style="color:#a78bfa;text-decoration:underline;cursor:pointer;">${displayText}</a>`
    }

    return `<a href="#" data-dead-link="true" data-link-title="${escapeRegex(linkTitle)}" style="color:#ef4444;text-decoration:underline dashed;cursor:pointer;" title="便签「${linkTitle}」未找到">${displayText}</a>`
  })
}

/**
 * Scan all notes for [[oldTitle]] references and produce patches to rename them.
 * Used when a note title changes.
 *
 * @returns Array of patches: { noteId, content?, todos? }
 */
export function updateWikiLinksOnRename(
  oldTitle: string,
  newTitle: string,
  allNotes: NoteWithContent[],
): Array<{ noteId: string; content?: string; todos?: { text: string }[] }> {
  const pattern = new RegExp(`\\[\\[${escapeRegex(oldTitle)}(\\|[^\\]]+)?\\]\\]`, 'g')
  const patches: Array<{ noteId: string; content?: string; todos?: { text: string }[] }> = []

  for (const note of allNotes) {
    let changed = false
    let newContent: string | undefined
    let newTodos: { text: string }[] | undefined

    if (note.content && pattern.test(note.content)) {
      pattern.lastIndex = 0
      newContent = note.content.replace(pattern, (_full, alias) => {
        return alias ? `[[${newTitle}${alias}]]` : `[[${newTitle}]]`
      })
      changed = true
    }

    if (note.todos && note.todos.length > 0) {
      let todoChanged = false
      const updated = note.todos.map(t => {
        if (t.text && pattern.test(t.text)) {
          pattern.lastIndex = 0
          todoChanged = true
          return { ...t, text: t.text.replace(pattern, (_full, alias) => {
            return alias ? `[[${newTitle}${alias}]]` : `[[${newTitle}]]`
          })}
        }
        return t
      })
      if (todoChanged) {
        newTodos = updated
        changed = true
      }
    }

    if (changed) {
      patches.push({ noteId: note.id, content: newContent, todos: newTodos })
    }
  }

  return patches
}

/**
 * Build a backlink index: for each note ID, which other notes link to it.
 * Scans both 便签://id and [[Title]] patterns.
 *
 * @returns Map<targetNoteId, sourceNoteIds[]>
 */
export function buildBacklinkIndex(notes: NoteWithContent[]): Map<string, string[]> {
  const index = new Map<string, Set<string>>()

  for (const source of notes) {
    const combined = `${source.content || ''} ${(source.todos || []).map(t => t.text).join(' ')}`

    // Scan 便签://id references
    const protoRe = new RegExp(PROTOCOL_LINK_REGEX)
    let m: RegExpExecArray | null
    while ((m = protoRe.exec(combined)) !== null) {
      const targetId = m[1]
      if (targetId !== source.id) {
        if (!index.has(targetId)) index.set(targetId, new Set())
        index.get(targetId)!.add(source.id)
      }
    }

    // Scan [[Title]] references
    const titleRe = new RegExp(WIKI_LINK_REGEX)
    while ((m = titleRe.exec(combined)) !== null) {
      const linkTitle = m[1].trim()
      // Resolve title to ID (exact or case-insensitive only for performance)
      const target = notes.find(
        n => n.title === linkTitle || (n.title || '').toLowerCase() === linkTitle.toLowerCase()
      )
      if (target && target.id !== source.id) {
        if (!index.has(target.id)) index.set(target.id, new Set())
        index.get(target.id)!.add(source.id)
      }
    }
  }

  // Convert Sets to arrays
  const result = new Map<string, string[]>()
  for (const [id, sources] of index) {
    result.set(id, [...sources])
  }
  return result
}
