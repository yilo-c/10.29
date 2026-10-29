const { BrowserWindow, screen, clipboard, ipcMain } = require('electron')
const path = require('path')
const { state } = require('./state.cjs')
const { guardPath } = require('./file-store.cjs')

let lastClipboardText = ''
let clipboardInterval = null

function startClipboardMonitor() {
  if (clipboardInterval) return
  lastClipboardText = clipboard.readText() || ''
  console.debug('[clipboard] monitor started, length:', lastClipboardText.length)
  clipboardInterval = setInterval(() => {
    try {
      const current = clipboard.readText() || ''
      if (current && current !== lastClipboardText) {
        console.debug('[clipboard] changed, length:', current.length)
        lastClipboardText = current
        for (const w of BrowserWindow.getAllWindows()) {
          if (!w.isDestroyed()) {
            w.webContents.send('clipboard-changed', { text: current })
          }
        }
      }
    } catch { /* ignore polling errors */ }
  }, 1500)
}

function stopClipboardMonitor() {
  if (clipboardInterval) {
    clearInterval(clipboardInterval)
    clipboardInterval = null
  }
}

// ── Clipboard Popup Window ───────────────────────────────────────────────

let lastClipboardPopupText = ''
let lastClipboardPopupTime = 0
const CLIPBOARD_POPUP_DEBOUNCE_MS = 1000

function showClipboardPopup(text, noBlurClose) {
  if (!text || text.trim().length < 5) return

  const now = Date.now()
  if (text === lastClipboardPopupText && (now - lastClipboardPopupTime) < CLIPBOARD_POPUP_DEBOUNCE_MS) {
    return
  }
  lastClipboardPopupText = text
  lastClipboardPopupTime = now

  if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
    state.clipboardPopupWindow.close()
  }

  const cursor = screen.getCursorScreenPoint()

  const textLen = text.length
  let popupW, popupH
  if (textLen < 100) {
    popupW = 280; popupH = 180
  } else if (textLen < 500) {
    popupW = 320; popupH = 260
  } else {
    popupW = 400; popupH = 360
  }

  let x = cursor.x + 12
  let y = cursor.y + 12

  const display = screen.getDisplayNearestPoint(cursor)
  const wa = display.workArea
  if (x + popupW > wa.x + wa.width) x = wa.x + wa.width - popupW - 8
  if (y + popupH > wa.y + wa.height) y = wa.y + wa.height - popupH - 8
  if (x < wa.x) x = wa.x + 8
  if (y < wa.y) y = wa.y + 8

  state.clipboardPopupWindow = new BrowserWindow({
    width: popupW,
    height: popupH,
    x: Math.round(x),
    y: Math.round(y),
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    show: false,
    focusable: false,
    minWidth: 240,
    minHeight: 140,
    maxWidth: 560,
    maxHeight: 520,
    backgroundColor: '#1a1a24',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）
      sandbox: true,
    },
  })

  const hash = '#view=clipboard-popup'

  if (state.isDev) {
    ;(async () => {
      for (const port of [5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://127.0.0.1:${port}`)
          if (resp.ok) {
            await state.clipboardPopupWindow.loadURL(`http://127.0.0.1:${port}/${hash}`)
            state.clipboardPopupWindow.showInactive()
            state.clipboardPopupWindow.webContents.send('clipboard-popup:text', { text })
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      state.clipboardPopupWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
      state.clipboardPopupWindow.webContents.on('did-finish-load', () => {
        if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
          state.clipboardPopupWindow.showInactive()
          state.clipboardPopupWindow.webContents.send('clipboard-popup:text', { text })
        }
      })
    })()
  } else {
    state.clipboardPopupWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
    state.clipboardPopupWindow.webContents.on('did-finish-load', () => {
      if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
        state.clipboardPopupWindow.showInactive()
        state.clipboardPopupWindow.webContents.send('clipboard-popup:text', { text })
      }
    })
  }

  let clipboardBlurTimer = null
  if (!noBlurClose) {
    state.clipboardPopupWindow.on('blur', () => {
      if (clipboardBlurTimer) clearTimeout(clipboardBlurTimer)
      clipboardBlurTimer = setTimeout(() => {
        if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
          state.clipboardPopupWindow.close()
        }
      }, 300)
    })
    state.clipboardPopupWindow.on('focus', () => {
      if (clipboardBlurTimer) {
        clearTimeout(clipboardBlurTimer)
        clipboardBlurTimer = null
      }
    })
  }

  state.clipboardPopupWindow.on('resize', () => {
    if (state._clipboardProgrammaticResize) return
    if (state._clipboardDragging) return
    try { state.clipboardPopupWindow.webContents.send('clipboard-popup:user-resized') } catch { /* window may be closed */ }
  })

  state.clipboardPopupWindow.on('closed', () => {
    state.clipboardPopupWindow = null
    if (state._clipboardResizeTimer) { clearTimeout(state._clipboardResizeTimer); state._clipboardResizeTimer = null }
  })
}

// ── IPC handlers ─────────────────────────────────────────────────────────

ipcMain.on('clipboard-popup:close', () => {
  if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
    state.clipboardPopupWindow.close()
  }
})

ipcMain.on('clipboard-popup:resize', (event, { width, height }) => {
  if (state._clipboardDragging) return
  if (state.clipboardPopupWindow && !state.clipboardPopupWindow.isDestroyed()) {
    const b = state.clipboardPopupWindow.getBounds()
    state._clipboardProgrammaticResize = true
    if (state._clipboardResizeTimer) clearTimeout(state._clipboardResizeTimer)
    try {
      state.clipboardPopupWindow.setBounds({
        x: b.x, y: b.y,
        width: typeof width === 'number' ? width : b.width,
        height: typeof height === 'number' ? height : b.height,
      })
    } finally {
      state._clipboardResizeTimer = setTimeout(() => { state._clipboardProgrammaticResize = false; state._clipboardResizeTimer = null }, 100)
    }
  }
})

module.exports = {
  startClipboardMonitor,
  stopClipboardMonitor,
  showClipboardPopup,
}
