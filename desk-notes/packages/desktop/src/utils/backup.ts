const BACKUP_KEY = 'desk-notes-backup'
const META_KEY = 'desk-notes-backup-meta'
const BACKUP_INDEX_KEY = 'desk-notes-backup-index'

const MAX_BACKUPS = 5

interface BackupMeta {
  timestamp: number
  version: number
}

interface BackupIndex {
  keys: string[]
}

function getBackupIndex(): BackupIndex {
  try {
    const raw = localStorage.getItem(BACKUP_INDEX_KEY)
    return raw ? JSON.parse(raw) : { keys: [] }
  } catch (e) {
    console.warn('[backup] failed to read backup index:', e)
    return { keys: [] }
  }
}

function saveBackupIndex(index: BackupIndex): void {
  try {
    localStorage.setItem(BACKUP_INDEX_KEY, JSON.stringify(index))
  } catch (e) {
    console.warn('[backup] failed to save backup index:', e)
  }
}

/**
 * Save current data to localStorage backup (keeps last {MAX_BACKUPS} versions)
 */
export function saveBackup(data: unknown): void {
  try {
    const ts = Date.now()
    const key = `${BACKUP_KEY}-${ts}`
    localStorage.setItem(key, JSON.stringify(data))
    const meta: BackupMeta = { timestamp: ts, version: 1 }
    localStorage.setItem(META_KEY, JSON.stringify(meta))

    // Rotate old backups
    const index = getBackupIndex()
    index.keys.unshift(key)
    while (index.keys.length > MAX_BACKUPS) {
      const old = index.keys.pop()!
      try { localStorage.removeItem(old) } catch (e) {
        console.warn('[backup] failed to remove old backup:', e)
      }
    }
    saveBackupIndex(index)
  } catch (e) {
    console.warn('[backup] failed to save:', e)
  }
}

/**
 * Get the most recent backup's metadata
 */
export function getBackupMeta(): BackupMeta | null {
  try {
    const raw = localStorage.getItem(META_KEY)
    if (!raw) return null
    return JSON.parse(raw) as BackupMeta
  } catch (e) {
    console.warn('[backup] failed to parse backup meta:', e)
    return null
  }
}

/**
 * Schedule auto backup: first tick after 3s, then every 5 minutes
 * Returns cleanup function
 */
export function scheduleAutoBackup(
  getData: () => unknown,
  onTick?: (meta: BackupMeta) => void
): () => void {
  const tick = () => {
    const data = getData()
    saveBackup(data)
    const meta = getBackupMeta()
    if (meta && onTick) onTick(meta)
  }

  // First backup after 3 seconds
  const initial = setTimeout(tick, 3000)
  // Then every 5 minutes
  const interval = setInterval(tick, 5 * 60 * 1000)

  return () => {
    clearTimeout(initial)
    clearInterval(interval)
  }
}

/**
 * Get backup data from localStorage (latest backup)
 */
export function getBackupData<T = unknown>(): T | null {
  try {
    const index = getBackupIndex()
    const key = index.keys[0] || BACKUP_KEY
    const raw = localStorage.getItem(key)
    if (!raw) return null
    return JSON.parse(raw) as T
  } catch (e) {
    console.warn('[backup] failed to read backup data:', e)
    return null
  }
}

/**
 * Get all available backup timestamps (most recent first)
 */
export function getBackupHistory(): { timestamp: number; key: string }[] {
  const index = getBackupIndex()
  return index.keys.map(k => ({
    timestamp: Number(k.split('-').pop()!),
    key: k,
  })).filter(b => !isNaN(b.timestamp))
}

/**
 * Download current backup as JSON file
 */
export function downloadBackup(): void {
  const index = getBackupIndex()
  const key = index.keys[0] || BACKUP_KEY
  const raw = localStorage.getItem(key)
  if (!raw) {
    console.warn('No backup data to download')
    return
  }

  const blob = new Blob([raw], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  a.download = `思忆便签-备份-${ts}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Restore data from localStorage backup
 */
export function restoreFromBackup<T = unknown>(): T | null {
  return getBackupData<T>()
}
