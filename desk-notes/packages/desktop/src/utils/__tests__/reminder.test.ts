import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock electronAPI before importing reminder
const mockUpdateReminders = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  delete (globalThis as unknown as { electronAPI?: unknown }).electronAPI
})

describe('startReminderSync', () => {
  it('returns noop when electronAPI is unavailable', async () => {
    const { startReminderSync } = await import('../reminder')
    const cleanup = startReminderSync()
    expect(typeof cleanup).toBe('function')
    cleanup()
  })

  it('returns noop when updateReminders is missing', async () => {
    ;(globalThis as unknown as { electronAPI?: unknown }).electronAPI = {}
    const { startReminderSync } = await import('../reminder')
    const cleanup = startReminderSync()
    expect(typeof cleanup).toBe('function')
    cleanup()
  })

  it('sends todos to main process via IPC', async () => {
    (globalThis as unknown as { electronAPI?: { updateReminders: typeof mockUpdateReminders } }).electronAPI = { updateReminders: mockUpdateReminders }

    // Need to use the actual store
    const { useStore } = await import('../../store/useStore')
    const { startReminderSync } = await import('../reminder')

    // Add a todo so store has data
    useStore.getState().addTodo('test item')

    const cleanup = startReminderSync()
    expect(mockUpdateReminders).toHaveBeenCalledTimes(1)

    const sentTodos = mockUpdateReminders.mock.calls[0][0]
    expect(sentTodos).toBeInstanceOf(Array)
    expect(sentTodos.some((t: Record<string, unknown>) => t.text === 'test item')).toBe(true)

    cleanup()
    // Reset store for other tests
    useStore.getState().permanentlyDeleteTodo(
      useStore.getState().todos.find(t => t.text === 'test item')!.id
    )
  })

  it('deduplicates — does not send again if todos unchanged', async () => {
    (globalThis as unknown as { electronAPI?: { updateReminders: typeof mockUpdateReminders } }).electronAPI = { updateReminders: mockUpdateReminders }
    const { startReminderSync } = await import('../reminder')

    const cleanup = startReminderSync()
    expect(mockUpdateReminders).toHaveBeenCalledTimes(1)

    // Trigger store subscribe with same data
    const { useStore } = await import('../../store/useStore')
    // Subscribe is already active — modifying unrelated state should not trigger re-send
    useStore.getState().setSearchQuery('irrelevant change')
    await vi.waitFor(() => {
      // wait a tick for subscribe to fire
    }, { timeout: 100, interval: 50 })

    // Should still be called only once (dedup)
    expect(mockUpdateReminders).toHaveBeenCalledTimes(1)

    cleanup()
  })
})
