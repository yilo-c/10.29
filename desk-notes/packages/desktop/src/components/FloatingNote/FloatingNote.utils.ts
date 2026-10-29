import type { FloatingNote as FN } from '../../types'

export const TAG_COLORS = ['#60a5fa', '#f87171', '#34d399', '#fbbf24', '#a78bfa', '#fb923c', '#f472b6', '#22d3ee', '#818cf8', '#e879f9']

export function hashColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length]
}

export function formatDueDate(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`
}

export interface NoteProps { note: FN; standalone?: boolean; matched?: boolean; searchKeyword?: string }

export function notePropsEqual(prev: NoteProps, next: NoteProps) {
  const a = prev.note
  const b = next.note
  return a.id === b.id
    && a.title === b.title
    && a.content === b.content
    && a.x === b.x && a.y === b.y
    && a.width === b.width && a.height === b.height
    && a.zIndex === b.zIndex
    && a.opacity === b.opacity
    && a.color === b.color
    && a.pinned === b.pinned
    && a.locked === b.locked
    && a.collapsed === b.collapsed
    && a.archived === b.archived
    && a.refCount === b.refCount
    && prev.standalone === next.standalone
    && prev.matched === next.matched
    && prev.searchKeyword === next.searchKeyword
    && JSON.stringify(a.tags) === JSON.stringify(b.tags)
    && JSON.stringify(a.todos) === JSON.stringify(b.todos)
}
