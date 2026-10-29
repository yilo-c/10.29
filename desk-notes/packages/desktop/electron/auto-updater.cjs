const { autoUpdater } = (() => {
  try {
    return require('electron-updater')
  } catch { return { autoUpdater: null } }
})()

const { ipcMain } = require('electron')
const { state } = require('./state.cjs')

if (autoUpdater) {
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true

  autoUpdater.on('checking-for-update', () => {
    if (state.win && !state.win.isDestroyed()) state.win.webContents.send('update:checking')
  })

  autoUpdater.on('update-available', (info) => {
    if (state.win && !state.win.isDestroyed()) {
      state.win.webContents.send('update:available', {
        version: info.version,
        releaseDate: info.releaseDate,
        releaseNotes: info.releaseNotes,
      })
    }
  })

  autoUpdater.on('update-not-available', () => {
    if (state.win && !state.win.isDestroyed()) state.win.webContents.send('update:not-available')
  })

  autoUpdater.on('error', (err) => {
    if (state.win && !state.win.isDestroyed()) state.win.webContents.send('update:error', err.message || 'Unknown error')
  })

  autoUpdater.on('download-progress', (progress) => {
    if (state.win && !state.win.isDestroyed()) {
      state.win.webContents.send('update:progress', {
        percent: progress.percent,
        bytesPerSecond: progress.bytesPerSecond,
        transferred: progress.transferred,
        total: progress.total,
      })
    }
  })

  autoUpdater.on('update-downloaded', (info) => {
    if (state.win && !state.win.isDestroyed()) {
      state.win.webContents.send('update:downloaded', {
        version: info.version,
        releaseDate: info.releaseDate,
      })
    }
  })
}

// ── IPC handlers ──────────────────────────────────────────────────────────

ipcMain.handle('update:check', async () => {
  if (!autoUpdater) return { success: false, error: 'Auto updater not available in dev mode' }
  try {
    const result = await autoUpdater.checkForUpdates()
    return { success: true, result }
  } catch (err) {
    const msg = (err && typeof err === 'object' && err.message) ? err.message : String(err)
    return { success: false, error: msg }
  }
})

ipcMain.handle('update:download', async () => {
  if (!autoUpdater) return { success: false, error: 'Auto updater not available in dev mode' }
  try {
    await autoUpdater.downloadUpdate()
    return { success: true }
  } catch (err) {
    const msg = (err && typeof err === 'object' && err.message) ? err.message : String(err)
    return { success: false, error: msg }
  }
})

ipcMain.handle('update:install', async () => {
  if (!autoUpdater) return { success: false, error: 'Auto updater not available in dev mode' }
  setImmediate(() => autoUpdater.quitAndInstall())
  return { success: true }
})

module.exports = {
  get autoUpdater() { return autoUpdater },
}
