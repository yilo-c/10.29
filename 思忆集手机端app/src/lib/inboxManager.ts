const STORAGE_KEY = 'siyi_inbox_items'

export interface InboxItem {
  id: string
  text: string
  source: 'share' | 'quick_capture' | 'sync'
  type: 'note' | 'task'
  createdAt: number
  processed: boolean
}

export function getInboxItems(): InboxItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

export function saveInboxItems(items: InboxItem[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
}

export function addInboxItem(item: Omit<InboxItem, 'id' | 'createdAt' | 'processed'>): void {
  const items = getInboxItems()
  items.unshift({
    ...item,
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    createdAt: Date.now(),
    processed: false,
  })
  saveInboxItems(items)
}

export function markInboxProcessed(id: string): void {
  const items = getInboxItems()
  const idx = items.findIndex(i => i.id === id)
  if (idx !== -1) { items[idx].processed = true; saveInboxItems(items) }
}

export function removeInboxItem(id: string): void {
  saveInboxItems(getInboxItems().filter(i => i.id !== id))
}

export function unprocessedCount(): number {
  return getInboxItems().filter(i => !i.processed).length
}
