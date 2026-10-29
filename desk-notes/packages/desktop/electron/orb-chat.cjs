const { BrowserWindow, screen, ipcMain } = require('electron')
const path = require('path')
const { state, loadWindowPrefs, saveWindowPrefs } = require('./state.cjs')
// AG-C2②（C-32④）：所有创建/调整窗口的坐标路径必须过 orb-geometry clamp，
// 防 NaN/负坐标/超大值/拔副屏把窗口开到屏幕外。
const {
  clampAnchorToWorkArea,
  sanitizePetGeometry,
} = require('./orb-geometry.cjs')

// ── Pet config ──────────────────────────────────────────────────────────────

const DEFAULT_PET_CONFIG = {
  windowWidth: 288,
  windowHeight: 316,
  hitArea: { x: 24, y: 76, width: 240, height: 240 },
}

let petConfig = { ...DEFAULT_PET_CONFIG }

// ── Click-through polling ──────────────────────────────────────────────────

function startOrbClickThrough(w) {
  const interval = setInterval(() => {
    if (w.isDestroyed()) { clearInterval(interval); return }
    try {
      const cursor = screen.getCursorScreenPoint()
      const bounds = w.getBounds()
      const relX = cursor.x - bounds.x
      const relY = cursor.y - bounds.y
      const ha = petConfig.hitArea
      const inOrb = relX >= ha.x && relX <= ha.x + ha.width
                 && relY >= ha.y && relY <= ha.y + ha.height
      w.setIgnoreMouseEvents(!inOrb, { forward: true })
    } catch { /* ignore polling errors */ }
  }, 100)
  return interval
}

// ── Orb window ─────────────────────────────────────────────────────────────

function createOrbWindow() {
  if (state.orbWindow && !state.orbWindow.isDestroyed()) {
    state.orbWindow.show()
    state.orbWindow.focus()
    return
  }

  const workArea = screen.getPrimaryDisplay().workArea
  const prefs = loadWindowPrefs()
  if (prefs.petConfig) petConfig = sanitizePetGeometry(prefs.petConfig, petConfig)
  const rawX = prefs.orbX ?? (workArea.x + workArea.width - 100)
  const rawY = prefs.orbY ?? (workArea.y + workArea.height - 120)
  // prefs 里的 orbX/orbY 可能是负数/拔掉的副屏坐标/损坏值 → 夹回当前工作区
  const anchor = clampAnchorToWorkArea(
    { x: rawX, y: rawY },
    { width: petConfig.windowWidth, height: petConfig.windowHeight },
    workArea,
  )
  const hash = '#view=floating-orb'

  state.orbWindow = new BrowserWindow({
    width: petConfig.windowWidth,
    height: petConfig.windowHeight,
    x: anchor.x,
    y: anchor.y,
    frame: false,
    transparent: true,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）
      sandbox: true,
    },
  })

  if (state.isDev) {
    ;(async () => {
      for (const port of [5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://127.0.0.1:${port}`)
          if (resp.ok) {
            state.orbWindow.loadURL(`http://127.0.0.1:${port}/${hash}`)
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      state.orbWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
    })()
  } else {
    state.orbWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
  }

  state.orbWindow.__clickThroughInterval = startOrbClickThrough(state.orbWindow)

  state.orbWindow.on('closed', () => {
    if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) {
      state.floatingChatWindow.close()
    }
    state.orbWindow = null
  })
}

// ── Chat position calculation ──────────────────────────────────────────────

function calcChatPosition(orbBounds) {
  const display = screen.getDisplayMatching(orbBounds)
  const wa = display.workArea
  const cw = 340
  const ch = 440
  let x, y

  x = orbBounds.x + orbBounds.width
  y = Math.round(orbBounds.y - (ch - orbBounds.height) / 2)
  if (x + cw <= wa.x + wa.width && y + ch <= wa.y + wa.height && y >= wa.y) {
    return { x: Math.round(x), y: Math.round(y) }
  }

  x = orbBounds.x - cw
  y = Math.round(orbBounds.y - (ch - orbBounds.height) / 2)
  if (x >= wa.x && y + ch <= wa.y + wa.height && y >= wa.y) {
    return { x: Math.round(x), y: Math.round(y) }
  }

  x = Math.round(orbBounds.x - (cw - orbBounds.width) / 2)
  y = orbBounds.y - ch
  if (x + cw <= wa.x + wa.width && x >= wa.x && y >= wa.y) {
    return { x: Math.round(x), y: Math.round(y) }
  }

  x = Math.round(orbBounds.x - (cw - orbBounds.width) / 2)
  y = orbBounds.y + orbBounds.height
  if (x + cw <= wa.x + wa.width && x >= wa.x && y + ch <= wa.y + wa.height) {
    return { x: Math.round(x), y: Math.round(y) }
  }

  return {
    x: Math.round(Math.max(wa.x, Math.min(orbBounds.x + orbBounds.width, wa.x + wa.width - cw))),
    y: Math.round(Math.max(wa.y, Math.min(orbBounds.y, wa.y + wa.height - ch))),
  }
}

// ── Floating chat window ───────────────────────────────────────────────────

function openFloatingChat() {
  if (!state.orbWindow || state.orbWindow.isDestroyed()) {
    createOrbWindow()
  }

  if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) {
    state.floatingChatWindow.show()
    state.floatingChatWindow.focus()
    return
  }

  const orbBounds = state.orbWindow.getBounds()
  const pos = calcChatPosition(orbBounds)
  // 兜底 clamp：calcChatPosition 的候选分支只检查部分边界，
  // 这里统一保证整窗落在目标工作区内（orbBounds 异常时也不开到屏幕外）。
  const clamped = clampAnchorToWorkArea(
    pos,
    { width: 340, height: 440 },
    screen.getDisplayMatching(orbBounds).workArea,
  )
  const hash = '#view=floating-chat'

  state.floatingChatWindow = new BrowserWindow({
    width: 340,
    height: 440,
    x: clamped.x,
    y: clamped.y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    minWidth: 260,
    minHeight: 300,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      // P0-5: 渲染进程沙箱（纵深防御）
      sandbox: true,
    },
  })

  if (state.isDev) {
    ;(async () => {
      for (const port of [5190, 5191, 5192]) {
        try {
          const resp = await fetch(`http://127.0.0.1:${port}`)
          if (resp.ok) {
            state.floatingChatWindow.loadURL(`http://127.0.0.1:${port}/${hash}`)
            return
          }
        } catch { /* window may have been destroyed */ }
      }
      state.floatingChatWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
    })()
  } else {
    state.floatingChatWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), { hash })
  }

  state.floatingChatWindow.on('closed', () => {
    state.floatingChatWindow = null
  })
}

function toggleOrbWindow() {
  if (state.orbWindow && !state.orbWindow.isDestroyed()) {
    if (state.orbWindow.isVisible()) {
      state.orbWindow.hide()
      if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) {
        state.floatingChatWindow.hide()
      }
    } else {
      state.orbWindow.show()
      state.orbWindow.focus()
    }
  } else {
    createOrbWindow()
  }
}

// ── IPC handlers ───────────────────────────────────────────────────────────

ipcMain.on('floating-orb:open-chat', () => {
  openFloatingChat()
})

ipcMain.on('floating-chat:hide', () => {
  if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) {
    state.floatingChatWindow.hide()
  }
})

ipcMain.on('pet:configure', (event, config) => {
  if (!config) return
  // AG-C2②：渲染层上报的几何整体消毒（NaN/负值/超大值回落 current），绝不写 NaN
  petConfig = sanitizePetGeometry(config, petConfig)
  if (state.orbWindow && !state.orbWindow.isDestroyed()) {
    state.orbWindow.setBounds({
      width: petConfig.windowWidth,
      height: petConfig.windowHeight,
    })
    // 变大后原位置可能溢出工作区 → 锚点夹回（不动相对摆放意图）
    try {
      const wa = screen.getDisplayMatching(state.orbWindow.getBounds()).workArea
      const b = state.orbWindow.getBounds()
      const anchor = clampAnchorToWorkArea(
        { x: b.x, y: b.y },
        { width: petConfig.windowWidth, height: petConfig.windowHeight },
        wa,
      )
      if (anchor.x !== b.x || anchor.y !== b.y) {
        state.orbWindow.setBounds({ ...b, x: anchor.x, y: anchor.y })
      }
    } catch { /* display matching may fail on teardown */ }
  }
  const prefs = loadWindowPrefs()
  saveWindowPrefs({ ...prefs, petConfig: { ...petConfig } })
})

// ── 第二档 #6：悬浮球长按录音 STT 跨窗口投递 ────────────────────────────────
// 流程：悬浮球录音 → 转写 → sendVoiceDraft(text) → 主进程 stash + 转发聊天窗口
//  - 聊天窗口已打开：立即 webContents.send('floating-chat:voice-draft', text)
//  - 聊天窗口未开：openFloatingChat()，聊天窗口挂载后通过
//    getPendingVoiceDraft() 拉取暂存文本并清空（吃掉创建竞态）
ipcMain.on('floating-orb:voice-draft', (_event, text) => {
  if (typeof text !== 'string' || !text.trim()) return
  if (state.floatingChatWindow && !state.floatingChatWindow.isDestroyed()) {
    state.pendingVoiceDraft = null
    state.floatingChatWindow.webContents.send('floating-chat:voice-draft', text)
  } else {
    state.pendingVoiceDraft = text
    openFloatingChat()
  }
})

ipcMain.handle('floating-chat:get-pending-draft', () => {
  const draft = state.pendingVoiceDraft
  state.pendingVoiceDraft = null
  return draft
})

module.exports = {
  createOrbWindow,
  toggleOrbWindow,
}
