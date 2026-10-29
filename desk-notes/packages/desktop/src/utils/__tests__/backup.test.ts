import { describe, it, expect, vi, beforeEach, afterEach, } from 'vitest'

// Mock fileStore (imported by trash which backup may use indirectly)
vi.mock('../fileStore', () => ({
  readTrashFile: vi.fn(),
  writeTrashFile: vi.fn(() => Promise.resolve()),
}))

describe('backup', () => {
  let Backup: typeof import('../backup')

  beforeEach(async () => {
    localStorage.clear()
    vi.useFakeTimers()
    Backup = await import('../backup')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('no backup meta initially', () => {
    expect(Backup.getBackupMeta()).toBeNull()
  })

  it('saves and retrieves backup data', () => {
    const data = { todos: [{ id: '1', text: 'test' }] }
    Backup.saveBackup(data)

    const retrieved = Backup.getBackupData()
    expect(retrieved).toEqual(data)
  })

  it('getBackupData returns null when no backup exists', () => {
    expect(Backup.getBackupData()).toBeNull()
  })

  it('saveBackup sets metadata with timestamp and version', () => {
    const now = Date.now()
    vi.setSystemTime(now)

    Backup.saveBackup({ foo: 'bar' })

    const meta = Backup.getBackupMeta()
    expect(meta).not.toBeNull()
    expect(meta!.version).toBe(1)
    expect(meta!.timestamp).toBe(now)
  })

  it('silently handles localStorage full (saveBackup does not throw)', () => {
    // Mock setItem to throw
    const originalSetItem = localStorage.setItem
    localStorage.setItem = vi.fn(() => { throw new Error('QuotaExceededError') })

    expect(() => Backup.saveBackup({ large: 'data' })).not.toThrow()

    localStorage.setItem = originalSetItem
  })

  it('scheduleAutoBackup calls getData and onTick', () => {
    const getData = vi.fn(() => ({ key: 'value' }))
    const onTick = vi.fn()

    const cleanup = Backup.scheduleAutoBackup(getData, onTick)

    // Initial tick fires after 3s
    vi.advanceTimersByTime(3000)
    expect(getData).toHaveBeenCalledTimes(1)
    expect(onTick).toHaveBeenCalledTimes(1)
    expect(onTick).toHaveBeenCalledWith(expect.objectContaining({ version: 1 }))

    // Interval tick fires after another 5 min
    vi.advanceTimersByTime(5 * 60 * 1000)
    expect(getData).toHaveBeenCalledTimes(2)

    cleanup()
  })

  it('scheduleAutoBackup cleanup stops timers', () => {
    const getData = vi.fn(() => ({}))
    const cleanup = Backup.scheduleAutoBackup(getData)

    // Advance past initial tick
    vi.advanceTimersByTime(3000)
    expect(getData).toHaveBeenCalledTimes(1)

    cleanup()

    // Advance well past next interval — should not fire again
    vi.advanceTimersByTime(10 * 60 * 1000)
    expect(getData).toHaveBeenCalledTimes(1)
  })

  it('restoreFromBackup returns backup data', () => {
    const data = { todos: [] }
    Backup.saveBackup(data)
    expect(Backup.restoreFromBackup()).toEqual(data)
  })

  it('restoreFromBackup returns null when no backup', () => {
    expect(Backup.restoreFromBackup()).toBeNull()
  })

  it('downloadBackup does not throw when no backup exists', () => {
    // Should just warn, not throw
    expect(() => Backup.downloadBackup()).not.toThrow()
  })

  it('downloadBackup creates a download link', () => {
    Backup.saveBackup({ test: true })

    const appendChild = vi.fn()
    const removeChild = vi.fn()
    const click = vi.fn()
    const createObjectURL = vi.fn(() => 'blob:backup')
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = vi.fn()

    vi.spyOn(document, 'createElement').mockReturnValue({ href: '', download: '', click } as unknown as HTMLAnchorElement)
    vi.spyOn(document.body, 'appendChild').mockImplementation(appendChild)
    vi.spyOn(document.body, 'removeChild').mockImplementation(removeChild)

    Backup.downloadBackup()

    expect(createObjectURL).toHaveBeenCalled()
    expect(appendChild).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
    expect(removeChild).toHaveBeenCalled()
  })
})
