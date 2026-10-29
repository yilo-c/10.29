/** Reading position RPC client 鈥?syncs reading progress from mobile to desktop. */
import { callRpc } from './rpcClient'
import { swallowError } from './swallowError'

export interface ReadingPosition {
  note_id: string
  scroll_position: number
  percentage: number
  device_id: string
  updated_at: number
}

/** Push reading position to desktop backend */
export async function pushReadingPosition(
  noteId: string,
  scrollPosition: number,
  percentage: number,
  deviceId = 'mobile',
): Promise<void> {
  try {
    await callRpc('reading.push', {
      note_id: noteId,
      scroll_position: scrollPosition,
      percentage,
      device_id: deviceId,
    })
  } catch (e) {
    // Best-effort: reading position sync is non-critical
    swallowError('readingClient:26', e)
  }
}

/** Get reading position for a specific note */
export async function getReadingPosition(noteId: string): Promise<ReadingPosition | null> {
  try {
    return await callRpc<ReadingPosition | null>('reading.get', { note_id: noteId })
  } catch {
    return null
  }
}

/** Relay quick-capture content to desktop inbox (QuickRelayChannel) */
export async function relayQuickCapture(
  content: string,
  title?: string,
  deviceId = 'mobile',
): Promise<{ id?: string; status?: string } | null> {
  try {
    return await callRpc<{ id: string; status: string }>('inbox.relay', {
      content,
      title: title || content.slice(0, 50),
      source: 'mobile_quick_capture',
      device_id: deviceId,
    })
  } catch {
    return null
  }
}
