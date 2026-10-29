import { setPlatformStorage, setTrashHandler } from '@desk-notes/shared'
import { useStore } from './useStore'
import { fileStorage, initFileStore, loadAllNotes } from '../utils/fileStore'
import { moveToTrash, initTrash } from '../utils/trash'

// Platform adapter: inject Electron file storage into shared persist middleware
setPlatformStorage(fileStorage)

// Platform adapter: inject Electron trash handler into shared store
setTrashHandler((item) => moveToTrash(item))

// ── Boot sequence ────────────────────────────────────────────────────────────
useStore.setState({ bootPhase: 'booting' })

initFileStore()
  .then(() => initTrash())
  .then(async () => {
    const notes = await loadAllNotes()
    if (notes.length > 0) {
      const restored = (notes as unknown as { pinned?: boolean; zIndex?: number }[]).map(n =>
        n.pinned ? { ...n, zIndex: 999999 } : n
      )
      useStore.setState({ floatingNotes: restored as never, bootPhase: 'ready' })
    } else {
      useStore.setState({ bootPhase: 'ready' })
    }
  })
  .catch((err: unknown) => {
    console.error('[init] boot failed:', err)
    useStore.setState({ bootPhase: 'failed', bootError: String(err) })
  })
