import { describe, it, expect } from 'vitest'
import {
  parseWikiLinks,
  resolveWikiLink,
  replaceWikiLinks,
  updateWikiLinksOnRename,
  buildFuseIndex,
  buildBacklinkIndex,
  WIKI_LINK_REGEX,
  PROTOCOL_LINK_REGEX,
  type NoteStub,
  type NoteWithContent,
} from '../wikiLinks'

const stubs: NoteStub[] = [
  { id: '1', title: 'Meeting Notes' },
  { id: '2', title: 'TODO List' },
  { id: '3', title: 'Project Alpha' },
  { id: '4', title: 'meeting notes' },
]

describe('parseWikiLinks', () => {
  it('finds a simple wiki link', () => {
    expect(parseWikiLinks('Read [[Meeting Notes]]')).toEqual([
      { title: 'Meeting Notes', display: 'Meeting Notes' },
    ])
  })

  it('finds a wiki link with display text', () => {
    expect(parseWikiLinks('See [[Meeting Notes|the notes]]')).toEqual([
      { title: 'Meeting Notes', display: 'the notes' },
    ])
  })

  it('finds multiple wiki links', () => {
    const result = parseWikiLinks('[[A]] and [[B|b]]')
    expect(result).toHaveLength(2)
    expect(result[0]).toEqual({ title: 'A', display: 'A' })
    expect(result[1]).toEqual({ title: 'B', display: 'b' })
  })

  it('returns empty array for text without links', () => {
    expect(parseWikiLinks('plain text')).toEqual([])
  })

  it('returns empty array for empty string', () => {
    expect(parseWikiLinks('')).toEqual([])
  })

  it('trims whitespace from title and display', () => {
    expect(parseWikiLinks('[[  Spaced Title  |  Alias  ]]')).toEqual([
      { title: 'Spaced Title', display: 'Alias' },
    ])
  })
})

describe('resolveWikiLink', () => {
  const fuse = buildFuseIndex(stubs)

  it('finds exact match', () => {
    const result = resolveWikiLink('Meeting Notes', stubs, fuse)
    expect(result?.id).toBe('1')
  })

  it('finds case-insensitive match', () => {
    const result = resolveWikiLink('todo list', stubs, fuse)
    expect(result?.id).toBe('2')
  })

  it('finds fuzzy match when no exact/ci match', () => {
    const result = resolveWikiLink('Project Beta', stubs, fuse)
    expect(result?.id).toBe('3')
  })

  it('returns null for empty input', () => {
    expect(resolveWikiLink('', stubs, fuse)).toBeNull()
  })

  it('returns null for empty notes array', () => {
    expect(resolveWikiLink('Meeting Notes', [], fuse)).toBeNull()
  })

  it('returns null when no match found', () => {
    const result = resolveWikiLink('Nonexistent Title That Absolutely Does Not Exist', stubs, fuse)
    expect(result).toBeNull()
  })
})

describe('replaceWikiLinks', () => {
  const navigateToRef = (_id: string) => {}

  it('replaces resolved link with purple anchor', () => {
    const html = replaceWikiLinks('See [[Meeting Notes]]', stubs, navigateToRef)
    expect(html).toContain('href="#ref-1"')
    expect(html).toContain('style="color:#a78bfa')
    expect(html).toContain('data-wiki-link="true"')
    expect(html).not.toContain('[[Meeting Notes]]')
  })

  it('replaces unresolved link with dashed red anchor', () => {
    const html = replaceWikiLinks('See [[Unknown Note]]', stubs, navigateToRef)
    expect(html).toContain('data-dead-link="true"')
    expect(html).toContain('style="color:#ef4444')
    expect(html).toContain('data-link-title="Unknown Note"')
  })

  it('uses display text in resolved link', () => {
    const html = replaceWikiLinks('[[Meeting Notes|the notes]]', stubs, navigateToRef)
    expect(html).toContain('>the notes<')
  })

  it('uses display text in unresolved link', () => {
    const html = replaceWikiLinks('[[Missing|fallback]]', stubs, navigateToRef)
    expect(html).toContain('>fallback<')
  })

  it('accepts optional fuse index', () => {
    const fuse = buildFuseIndex(stubs)
    const html = replaceWikiLinks('[[meeting]]', stubs, navigateToRef, fuse)
    expect(html).toContain('href="#ref-')
  })
})

describe('updateWikiLinksOnRename', () => {
  const allNotes: NoteWithContent[] = [
    { id: 'n1', title: 'Note 1', content: 'See [[Old Title]] here', todos: [{ text: 'check [[Old Title]]' }] },
    { id: 'n2', title: 'Note 2', content: 'See [[Old Title|alias]] here' },
    { id: 'n3', title: 'Note 3', content: 'No references here' },
  ]

  it('renames wiki links in content', () => {
    const patches = updateWikiLinksOnRename('Old Title', 'New Title', allNotes)
    const n1 = patches.find(p => p.noteId === 'n1')
    expect(n1?.content).toBe('See [[New Title]] here')
  })

  it('renames wiki links with display text', () => {
    const patches = updateWikiLinksOnRename('Old Title', 'New Title', allNotes)
    const n2 = patches.find(p => p.noteId === 'n2')
    expect(n2?.content).toBe('See [[New Title|alias]] here')
  })

  it('renames wiki links in todos', () => {
    const patches = updateWikiLinksOnRename('Old Title', 'New Title', allNotes)
    const n1 = patches.find(p => p.noteId === 'n1')
    expect(n1?.todos?.[0].text).toBe('check [[New Title]]')
  })

  it('does not include notes with no matches', () => {
    const patches = updateWikiLinksOnRename('Old Title', 'New Title', allNotes)
    const n3 = patches.find(p => p.noteId === 'n3')
    expect(n3).toBeUndefined()
  })

  it('returns empty array for no matches', () => {
    const patches = updateWikiLinksOnRename('Nonexistent', 'New', allNotes)
    expect(patches).toEqual([])
  })
})

describe('buildBacklinkIndex', () => {
  it('finds backlinks via 便签:// protocol', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: '参考 便签://b 的内容' },
      { id: 'b', title: 'B', content: '内容' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('b')).toEqual(['a'])
  })

  it('finds backlinks via [[Title]]', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: '参考 [[Note B]] 的内容' },
      { id: 'b', title: 'Note B', content: '内容' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('b')).toEqual(['a'])
  })

  it('finds backlinks in todos', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: '', todos: [{ text: 'check [[B]]' }] },
      { id: 'b', title: 'B', content: '' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('b')).toEqual(['a'])
  })

  it('does not count self-references', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: '参考 便签://a 和 [[A]]' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('a')).toBeUndefined()
  })

  it('aggregates multiple sources', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: '参考 [[Target]]' },
      { id: 'b', title: 'B', content: '参考 便签://target' },
      { id: 'target', title: 'Target', content: '内容' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('target')?.sort()).toEqual(['a', 'b'])
  })

  it('handles notes with no backlinks', () => {
    const notes: NoteWithContent[] = [
      { id: 'a', title: 'A', content: 'plain text' },
    ]
    const index = buildBacklinkIndex(notes)
    expect(index.get('a')).toBeUndefined()
  })
})

describe('buildFuseIndex', () => {
  it('creates a fuse index with correct keys', () => {
    const fuse = buildFuseIndex(stubs)
    const result = fuse.search('meeting')
    expect(result.length).toBeGreaterThan(0)
    expect(result[0].item.id).toBe('1')
  })
})

describe('WIKI_LINK_REGEX', () => {
  it('matches [[Title]]', () => {
    WIKI_LINK_REGEX.lastIndex = 0
    const m = WIKI_LINK_REGEX.exec('[[Hello]]')
    expect(m?.[1]).toBe('Hello')
    expect(m?.[2]).toBeUndefined()
  })

  it('matches [[Title|Display]]', () => {
    WIKI_LINK_REGEX.lastIndex = 0
    const m = WIKI_LINK_REGEX.exec('[[Hello|World]]')
    expect(m?.[1]).toBe('Hello')
    expect(m?.[2]).toBe('World')
  })
})

describe('PROTOCOL_LINK_REGEX', () => {
  it('matches 便签://id', () => {
    PROTOCOL_LINK_REGEX.lastIndex = 0
    const m = PROTOCOL_LINK_REGEX.exec('参考 便签://abc123')
    expect(m?.[1]).toBe('abc123')
  })
})
