import { uid } from './helpers'

export interface VersionEntry {
  id: string
  noteId: string
  timestamp: number
  title: string
  content: string
  todos?: { id: string; text: string; done: boolean }[]
}

export interface DiffLine {
  type: 'same' | 'added' | 'removed'
  text: string
}

const MAX_VERSIONS_PER_NOTE = 30
const AUTO_VERSION_INTERVAL = 120_000 // 2 min between auto-snapshots

interface Storage {
  [noteId: string]: VersionEntry[]
}

let storage: Storage = {}
let loaded = false

function load(): void {
  if (loaded) return
  try {
    const raw = localStorage.getItem('desk-notes-history')
    if (raw) storage = JSON.parse(raw)
  } catch {
    storage = {}
  }
  loaded = true
}

function persist(): void {
  try {
    localStorage.setItem('desk-notes-history', JSON.stringify(storage))
  } catch {
    for (const noteId in storage) {
      storage[noteId] = storage[noteId].slice(-20)
    }
    try {
      localStorage.setItem('desk-notes-history', JSON.stringify(storage))
    } catch {
      // Give up
    }
  }
}

const lastAutoTime = new Map<string, number>()

export function addSnapshot(
  noteId: string,
  title: string,
  content: string,
  todos?: { id: string; text: string; done: boolean }[],
): VersionEntry {
  load()
  const entry: VersionEntry = {
    id: uid(),
    noteId,
    timestamp: Date.now(),
    title,
    content,
    todos: todos?.map(t => ({ id: t.id, text: t.text, done: t.done })),
  }
  if (!storage[noteId]) storage[noteId] = []
  storage[noteId].push(entry)
  if (storage[noteId].length > MAX_VERSIONS_PER_NOTE) {
    storage[noteId] = storage[noteId].slice(-MAX_VERSIONS_PER_NOTE)
  }
  persist()
  lastAutoTime.set(noteId, Date.now())
  return entry
}

export function addAutoSnapshot(
  noteId: string,
  title: string,
  content: string,
  todos?: { id: string; text: string; done: boolean }[],
): VersionEntry | null {
  const last = lastAutoTime.get(noteId) || 0
  if (Date.now() - last < AUTO_VERSION_INTERVAL) return null
  return addSnapshot(noteId, title, content, todos)
}

export function getHistory(noteId: string): VersionEntry[] {
  load()
  return storage[noteId] ? [...storage[noteId]] : []
}


/**
 * Simple line-based diff between two texts.
 * Uses a longest-common-subsequence approach.
 */
export function computeDiff(oldText: string, newText: string): DiffLine[] {
  if (!oldText && !newText) return []
  const a = oldText ? oldText.split('\n') : ['']
  const b = newText ? newText.split('\n') : ['']
  const dp = lcsMatrix(a, b)
  const result: DiffLine[] = []
  let i = a.length
  let j = b.length
  const stack: DiffLine[] = []

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      stack.push({ type: 'same', text: a[i - 1] })
      i--
      j--
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      stack.push({ type: 'added', text: b[j - 1] })
      j--
    } else {
      stack.push({ type: 'removed', text: a[i - 1] })
      i--
    }
  }

  while (stack.length > 0) result.push(stack.pop()!)
  return result
}

function lcsMatrix(a: string[], b: string[]): number[][] {
  const m = a.length
  const n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1])
    }
  }
  return dp
}
