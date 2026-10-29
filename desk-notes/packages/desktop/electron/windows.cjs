const { BrowserWindow, screen, ipcMain, nativeTheme, dialog, clipboard, globalShortcut, app, desktopCapturer, nativeImage } = require('electron')
const path = require('path')
const fs = require('fs')
const { state, NOTES_DIR, loadWindowPrefs, saveWindowPrefs, loadTrayPrefs, saveTrayPrefs } = require('./state.cjs')
const { ensureDataDirs, isValidNoteId, parseNoteFile, serializeNoteFile, atomicWriteFileSync } = require('./file-store.cjs')
const { updateTrayMenu } = require('./tray.cjs')
const { toggleOrbWindow } = require('./orb-chat.cjs')
const { showClipboardPopup } = require('./clipboard.cjs')

const TITLE_BAR_HEIGHT = 44

// ── DevTools 开关（内存治理 A）─────────────────────────────────────────────
// 默认收起所有窗口的 DevTools 渲染进程（dev 模式一个 devtools ≈ 200-300MB，
// 3 类窗口会乘以 3）。仅当命令行显式传 `--devtools` 时才对已加载的窗口开启。
function shouldOpenDevTools() {
  return process.argv.includes('--devtools')
}

// ── Main window ────────────────────────────────────────────────────────────

function createMainWindow() {
  const savedBounds = loadWindowPrefs().mainWindow || {}
  const defaultW = 1200
  const defaultH = 800
  const winOpts = {
    width: savedBounds.width || defaultW,
    height: savedBounds.height || defaultH,
    // UX 2.3: 主窗口最小尺寸，防止恢复历史 bounds 或用户缩放后界面塌陷。
    // Electron 会自动将低于 minWidth/minHeight 的 bounds（含 savedBounds）钳制到最小值。
    minWidth: 720,
    minHeight: 480,
    frame: false,
    transparent: true,
    resizable: true,
    icon: path.join(__dirname, '..', 'public', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）。主进程 --no-sandbox 是 Electron 42
      // EPERM 写入问题的临时绕过；渲染进程仍启用沙箱限制渲染层权限。
      sandbox: true,
    },
  }

  if (savedBounds.x !== undefined && savedBounds.y !== undefined) {
    const displays = screen.getAllDisplays()
    const bounds = displays.reduce((acc, d) => ({
      x: Math.min(acc.x, d.bounds.x),
      y: Math.min(acc.y, d.bounds.y),
      width: Math.max(acc.width, d.bounds.x + d.bounds.width),
      height: Math.max(acc.height, d.bounds.y + d.bounds.height),
    }), { x: 0, y: 0, width: 0, height: 0 })
    const rightEdge = bounds.x + bounds.width
    const bottomEdge = bounds.y + bounds.height
    winOpts.x = Math.max(bounds.x, Math.min(savedBounds.x, rightEdge - 100))
    winOpts.y = Math.max(bounds.y, Math.min(savedBounds.y, bottomEdge - 100))
  }

  state.win = new BrowserWindow(winOpts)

  if (state.isDev) {
    ;(async () => {
      for (const port of [5175, 5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://localhost:${port}`)
          if (resp.ok) {
            state.win.loadURL(`http://localhost:${port}`)
            if (shouldOpenDevTools()) state.win.webContents.openDevTools({ mode: 'detach' })
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      state.win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
    })()
  } else {
    state.win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  state.win.on('close', (event) => {
    if (!app.isQuitting) {
      event.preventDefault()
      state.win.hide()
      return
    }
    for (const [, child] of state.noteWindows) {
      if (!child.isDestroyed()) child.close()
    }
    state.noteWindows.clear()
    if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) state.floatingChatWindow.close()
    if (state.orbWindow && !state.orbWindow.isDestroyed()) state.orbWindow.close()
    for (const [, child] of state.viewWindows) {
      if (!child.isDestroyed()) child.close()
    }
    state.viewWindows.clear()
    globalShortcut.unregisterAll()
  })

  registerMainShortcuts()
  registerMainBoundsPersistence()

  const syncTheme = () => {
    if (state.win && !state.win.isDestroyed()) {
      state.win.webContents.send('native-theme-changed', {
        darkMode: nativeTheme.shouldUseDarkColors,
      })
    }
  }
  nativeTheme.on('updated', syncTheme)
  state.win.webContents.on('did-finish-load', syncTheme)
}

// F-4（M1⑤）：globalShortcut 冲突检测 — register 返回 false/抛异常视为占用，
// 收集失败清单：1) console 日志 2) 主进程 did-finish-load 后推送
// 'global-shortcut-conflict' 供渲染层降级提示（不静默失效）。
function safeRegisterShortcuts(entries) {
  const failures = []
  for (const { accel, label, handler } of entries) {
    let ok = false
    try {
      ok = globalShortcut.register(accel, handler) === true
    } catch (err) {
      console.error(`[shortcut] ${accel}（${label}）注册抛出异常:`, err)
      ok = false
    }
    if (ok) {
      console.debug(`[shortcut] ${accel}（${label}）注册成功`)
    } else {
      console.warn(`[shortcut] ${accel}（${label}）注册失败：可能被其他应用占用，该快捷键本次会话降级为不可用`)
      failures.push({ accel, label })
    }
  }
  return failures
}

function registerMainShortcuts() {
  const failures = safeRegisterShortcuts([
    {
      accel: 'Alt+N',
      label: '新建便签',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.webContents.send('global-shortcut', { action: 'new-note' })
        }
      },
    },
    {
      accel: 'Alt+T',
      label: '新建待办',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.webContents.send('global-shortcut', { action: 'new-todo' })
        }
      },
    },
    {
      accel: 'Alt+Shift+H',
      label: '显示/隐藏主窗',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          if (state.win.isVisible()) {
            state.win.hide()
          } else {
            state.win.show()
            state.win.focus()
          }
        }
      },
    },
    {
      accel: 'Alt+Space',
      label: '快捷搜索',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.show()
          state.win.focus()
          state.win.webContents.send('global-shortcut', { action: 'quick-search' })
        }
      },
    },
    { accel: 'Alt+Shift+F', label: '悬浮球开关', handler: toggleOrbWindow },
    {
      accel: 'Alt+Shift+C',
      label: '快速捕获',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.show()
          state.win.focus()
          state.win.webContents.send('global-shortcut', { action: 'quick-capture' })
        }
      },
    },
    {
      accel: 'Alt+Shift+V',
      label: '剪贴板建签',
      handler: () => {
        if (state.win && !state.win.isDestroyed()) {
          const text = clipboard.readText()
          if (text && text.trim()) {
            state.win.show()
            state.win.focus()
            state.win.webContents.send('global-shortcut', { action: 'new-note', content: text.trim() })
          }
        }
      },
    },
    {
      accel: 'Alt+S',
      label: '剪贴板动作浮窗',
      handler: () => {
        if (!state.win || state.win.isDestroyed()) return
        const text = clipboard.readText()
        if (text && text.trim().length >= 5) {
          showClipboardPopup(text.trim())
        }
      },
    },
  ])

  if (failures.length > 0) {
    state.shortcutConflicts = failures
    const notify = () => {
      try {
        if (state.win && !state.win.isDestroyed()) {
          state.win.webContents.send('global-shortcut-conflict', { failures })
        }
      } catch { /* window may have been destroyed */ }
    }
    if (state.win && !state.win.isDestroyed()) {
      if (state.win.webContents.isLoading()) {
        state.win.webContents.once('did-finish-load', notify)
      } else {
        notify()
      }
    }
  } else {
    state.shortcutConflicts = []
  }
}

function registerMainBoundsPersistence() {
  const defaultW = 1200
  const defaultH = 800
  const saveBounds = () => {
    if (state.saveTimer) clearTimeout(state.saveTimer)
    state.saveTimer = setTimeout(() => {
      if (!state.win || state.win.isDestroyed()) return
      const bounds = state.win.getBounds()
      const prefs = loadWindowPrefs()
      const toSave = { x: bounds.x, y: bounds.y }
      if (bounds.width !== defaultW || bounds.height !== defaultH) {
        toSave.width = bounds.width
        toSave.height = bounds.height
      }
      prefs.mainWindow = toSave
      saveWindowPrefs(prefs)
    }, 500)
  }
  state.win.on('resize', saveBounds)
  state.win.on('move', saveBounds)
}

// ── Floating note windows ──────────────────────────────────────────────────

function createNoteWindow(data) {
  if (!isValidNoteId(data.id)) {
    console.error('[main] Invalid noteId for floating window:', data.id)
    return
  }

  if (state.noteWindows.has(data.id)) {
    const existing = state.noteWindows.get(data.id)
    if (!existing.isDestroyed()) {
      existing.focus()
      return
    }
    state.noteWindows.delete(data.id)
  }

  const mainBounds = state.win ? state.win.getBounds() : { x: 0, y: 0 }
  const screenX = mainBounds.x + (data.screenX || 100)
  const screenY = mainBounds.y + (data.screenY || 100)

  if (data.noteData) {
    try {
      ensureDataDirs()
      const mdPath = path.join(NOTES_DIR, `${data.id}.md`)
      atomicWriteFileSync(mdPath, serializeNoteFile(data.noteData))
    } catch (err) {
      console.error('[main] Failed to write note file before floating window:', err)
    }
  }

  const child = new BrowserWindow({
    width: data.width || 260,
    height: data.height || 200,
    x: Math.max(0, screenX),
    y: Math.max(0, screenY),
    frame: false,
    transparent: true,
    resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）
      sandbox: true,
    },
  })

  const bgMode = data.backgroundMode || state.currentBackgroundMode || 'acrylic'
  const hash = `#note=${data.id}&bg=${bgMode}`

  if (state.isDev) {
    ;(async () => {
      for (const port of [5175, 5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://localhost:${port}`)
          if (resp.ok) {
            child.loadURL(`http://localhost:${port}/${hash}`)
            if (shouldOpenDevTools()) child.webContents.openDevTools({ mode: 'detach' })
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      child.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
    })()
  } else {
    child.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
  }

  state.noteWindows.set(data.id, child)
  child.on('closed', () => {
    state.noteWindows.delete(data.id)
    try {
      const mdPath = path.join(NOTES_DIR, `${data.id}.md`)
      if (fs.existsSync(mdPath)) {
        const raw = fs.readFileSync(mdPath, 'utf-8')
        const { frontmatter, body } = parseNoteFile(raw)
        if (frontmatter.floated !== false) {
          frontmatter.floated = false
          atomicWriteFileSync(mdPath, serializeNoteFile({ ...frontmatter, content: body }))
        }
      }
    } catch { /* best-effort */ }
    if (state.win && !state.win.isDestroyed()) {
      state.win.webContents.send('note-returned', { id: data.id })
      state.win.webContents.send('note-synced', { id: data.id })
    }
  })
}

function createViewWindow(data) {
  const viewType = data.viewType

  if (viewType === 'floating') {
    createOrbWindow()
    return
  }

  if (state.viewWindows.has(viewType)) {
    const existing = state.viewWindows.get(viewType)
    if (!existing.isDestroyed()) {
      existing.focus()
      return
    }
    state.viewWindows.delete(viewType)
  }

  const sizeMap = {
    notes: { w: 380, h: 680 },
    todo: { w: 380, h: 680 },
    chat: { w: 420, h: 640 },
    info: { w: 480, h: 680 },
    'knowledge-graph': { w: 1000, h: 700 },
  }
  const size = sizeMap[viewType] || { w: 380, h: 680 }

  const defaultX = Math.max(0, (state.win ? state.win.getBounds().x : 0) + 100)
  const defaultY = Math.max(0, (state.win ? state.win.getBounds().y : 0) + 100)

  const child = new BrowserWindow({
    width: size.w,
    height: size.h,
    x: defaultX,
    y: defaultY,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）
      sandbox: true,
    },
  })

  const bgMode = data.backgroundMode || state.currentBackgroundMode || 'acrylic'
  const hash = `#view=${viewType}&bg=${bgMode}`

  if (state.isDev) {
    ;(async () => {
      for (const port of [5175, 5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://localhost:${port}`)
          if (resp.ok) {
            child.loadURL(`http://localhost:${port}/${hash}`)
            if (shouldOpenDevTools()) child.webContents.openDevTools({ mode: 'detach' })
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      child.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
    })()
  } else {
    child.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
  }

  state.viewWindows.set(viewType, child)
  child.on('closed', () => {
    state.viewWindows.delete(viewType)
  })
}

function closeNoteWindow(noteId) {
  const child = state.noteWindows.get(noteId)
  if (child && !child.isDestroyed()) {
    child.close()
  }
  state.noteWindows.delete(noteId)
}

// ── Window control IPC handlers ──────────────────────────────────────────

ipcMain.on('window-minimize', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (w && !w.isDestroyed()) w.minimize()
})

ipcMain.on('window-close', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (w && !w.isDestroyed()) w.close()
})

ipcMain.on('create-view-window', (event, data) => {
  if (!data || !data.viewType) return
  createViewWindow(data)
})

ipcMain.on('create-note-window', (event, data) => {
  if (!data || typeof data.id !== 'string') return
  createNoteWindow(data)
})

ipcMain.on('close-note-window', (event, noteId) => {
  if (typeof noteId !== 'string') return
  closeNoteWindow(noteId)
})

ipcMain.on('floating-windows:hide', () => {
  for (const [, child] of state.noteWindows) {
    if (!child.isDestroyed()) child.hide()
  }
})

ipcMain.on('floating-windows:show', () => {
  for (const [, child] of state.noteWindows) {
    if (!child.isDestroyed() && !child.isVisible()) child.show()
  }
})

ipcMain.on('window-fullscreen', (event) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (w && !w.isDestroyed()) {
    w.setFullScreen(!w.isFullScreen())
  }
})

ipcMain.on('window-always-on-top', (event, { on }) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (w && !w.isDestroyed()) {
    w.setAlwaysOnTop(on)
  }
})

ipcMain.on('window-drag-start', (event, { offsetX, offsetY }) => {
  if (state.dragInterval) clearInterval(state.dragInterval)
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w || w.isDestroyed()) return

  const dragBounds = w.getBounds()
  if (w === state.clipboardPopupWindow) state._clipboardDragging = true

  state.dragInterval = setInterval(() => {
    if (w.isDestroyed()) { clearInterval(state.dragInterval); state.dragInterval = null; return }
    const pt = screen.getCursorScreenPoint()
    w.setBounds({
      x: Math.round(pt.x + offsetX),
      y: Math.round(pt.y + offsetY),
      width: dragBounds.width,
      height: dragBounds.height,
    })
  }, 16)
})

ipcMain.on('window-drag-end', (event) => {
  if (state.dragInterval) { clearInterval(state.dragInterval); state.dragInterval = null }
  state._clipboardDragging = false
  const senderWin = BrowserWindow.fromWebContents(event.sender)
  if (senderWin && state.orbWindow && senderWin === state.orbWindow) {
    const b = state.orbWindow.getBounds()
    saveWindowPrefs({ orbX: b.x, orbY: b.y })
  }
})

ipcMain.on('background-mode:set', (event, mode) => {
  state.currentBackgroundMode = mode || 'acrylic'
})

ipcMain.on('window-ignore-mouse', (event, { ignore }) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w || w.isDestroyed()) return

  const old = w.__ignoreMouseInterval
  if (old) { clearInterval(old); w.__ignoreMouseInterval = null }

  if (!ignore) {
    w.setIgnoreMouseEvents(false, { forward: false })
    return
  }

  w.setIgnoreMouseEvents(true, { forward: true })
  w.__ignoreMouseInterval = setInterval(() => {
    try {
      if (w.isDestroyed()) {
        clearInterval(w.__ignoreMouseInterval)
        w.__ignoreMouseInterval = null
        return
      }
      const cursor = screen.getCursorScreenPoint()
      const bounds = w.getBounds()
      const relY = cursor.y - bounds.y
      const inTitleBar = relY >= 0 && relY < TITLE_BAR_HEIGHT
      if (inTitleBar) {
        w.setIgnoreMouseEvents(false)
      } else {
        w.setIgnoreMouseEvents(true, { forward: true })
      }
    } catch { /* ignore polling errors silently */ }
  }, 100)
})

ipcMain.on('window-resize', (event, { width, height }) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w || w.isDestroyed()) return
  const b = w.getBounds()
  const newWidth = Math.max(80, width)
  const newHeight = Math.max(80, height)
  w.setBounds({ x: b.x, y: b.y, width: newWidth, height: newHeight })
})

ipcMain.on('window-set-resizable', (event, { resizable }) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (w && !w.isDestroyed()) {
    w.setResizable(resizable)
  }
})

ipcMain.on('show-save-dialog', (event, { filename, content }) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w) return

  dialog.showSaveDialog(w, {
    defaultPath: filename,
    filters: [
      { name: 'Markdown', extensions: ['md'] },
      { name: 'JSON', extensions: ['json'] },
      { name: 'All Files', extensions: ['*'] },
    ],
  }).then(result => {
    if (!result.canceled && result.filePath) {
      try {
        atomicWriteFileSync(result.filePath, typeof content === 'string' ? content : String(content))
      } catch (err) {
        console.error('Failed to save file:', err)
      }
    }
  }).catch(err => {
    console.error('Failed to save file:', err)
  })
})

ipcMain.handle('show-open-dialog', async (event, options) => {
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w || w.isDestroyed()) return { canceled: true }
  const result = await dialog.showOpenDialog(w, {
    properties: ['openFile'],
    filters: [
      { name: 'Backup JSON', extensions: ['json'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    ...options,
  })
  if (result.canceled || result.filePaths.length === 0) {
    return { canceled: true }
  }
  try {
    const content = fs.readFileSync(result.filePaths[0], 'utf-8')
    return { canceled: false, content }
  } catch (err) {
    console.error('[main] Failed to read backup file:', err)
    return { canceled: true }
  }
})

ipcMain.on('set-auto-launch', (event, enable) => {
  app.setLoginItemSettings({ openAtLogin: enable })
  const prefs = loadTrayPrefs()
  saveTrayPrefs({ ...prefs, autoLaunch: enable })
  if (state.tray) updateTrayMenu()
})

ipcMain.handle('get-auto-launch', () => {
  return app.getLoginItemSettings().openAtLogin
})

// ── T3.4：桌面截图（Electron desktopCapturer）→ 返回 PNG dataURL ──────
// 走既有 contextBridge + ipc 通道（preload 暴露 captureScreen），不另起通道。
// 用 desktopCapturer.getSources 取全屏缩略图（nativeImage），toDataURL 得到
// PNG base64，前端直接喂给已有 ocr.extract_base64 链路，无需后端改动。
ipcMain.handle('screen:capture', async () => {
  try {
    const displays = screen.getAllDisplays()
    const primary = screen.getPrimaryDisplay()
    const { width, height } = primary.size
    // 取所有屏幕，合成一张（或仅主屏）；这里取首个 screen 源即全屏。
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width, height },
    })
    if (!sources || sources.length === 0) {
      return { success: false, error: '未找到可捕获的屏幕源' }
    }
    // 优先匹配主屏；否则取第一个
    const src = sources.find((s) => s.display_id === String(primary.id)) || sources[0]
    let img = src.thumbnail
    if (!img || img.isEmpty()) {
      return { success: false, error: '截图为空' }
    }
    // 多显示器时按主屏尺寸裁剪（thumbnail 可能含全部屏幕拼接）
    if (displays.length > 1) {
      try {
        img = nativeImage.createFromBuffer(img.toPNG()).crop({
          x: primary.bounds.x,
          y: primary.bounds.y,
          width,
          height,
        })
      } catch {
        /* 裁剪失败则回退整图 */
      }
    }
    const dataUrl = img.toDataURL('image/png')
    return { success: true, dataUrl, width, height }
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) }
  }
})

module.exports = { createMainWindow, createNoteWindow, createViewWindow, closeNoteWindow }

