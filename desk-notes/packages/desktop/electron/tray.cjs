const { Tray, Menu, nativeImage, app, ipcMain } = require('electron')
const path = require('path')
const fs = require('fs')
const { state, saveTrayPrefs, loadTrayPrefs } = require('./state.cjs')
const { toggleOrbWindow } = require('./orb-chat.cjs')

let recentNotes = []

function updateRecentNotes(notes) {
  recentNotes = notes.slice(0, 8).map(n => ({
    id: String(n.id || ''),
    title: typeof n.title === 'string' ? n.title : '',
  }))
  if (state.tray) updateTrayMenu()
}

function updateTrayIcon(connected) {
  if (!state.tray) return
  state._backendConnected = connected
  try {
    const iconPath = path.join(__dirname, '..', 'public', 'icon-16.png')
    if (fs.existsSync(iconPath)) {
      const icon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 })
      state.tray.setImage(icon)
    }
    state.tray.setToolTip(connected ? '思忆便签 — 后端已连接' : '思忆便签 — 后端未连接')
  } catch { /* ignore */ }
  updateTrayMenu()
}

function updateTrayMenu() {
  const prefs = loadTrayPrefs()
  const template = [
    {
      label: state._backendConnected ? '● 后端已连接' : '○ 后端未连接',
      enabled: false,
    },
    { type: 'separator' },
    {
      label: '显示主窗口',
      click: () => {
        if (state.win) { state.win.show(); state.win.focus() }
      },
    },
    { type: 'separator' },
  ]

  if (recentNotes.length > 0) {
    for (const note of recentNotes) {
      template.push({
        label: note.title || '(无标题)',
        click: () => {
          if (state.win && !state.win.isDestroyed()) {
            state.win.show()
            state.win.focus()
            state.win.webContents.send('tray-action', { type: 'open-note', noteId: note.id })
          }
        },
      })
    }
    template.push({ type: 'separator' })
  }

  template.push({
    label: '浮动助手',
    accelerator: 'Alt+Shift+F',
    click: toggleOrbWindow,
  })

  template.push(
    {
      label: '新建便签',
      accelerator: 'Alt+N',
      click: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.show()
          state.win.focus()
          state.win.webContents.send('global-shortcut', { action: 'new-note' })
        }
      },
    },
    {
      label: '新建待办',
      accelerator: 'Alt+T',
      click: () => {
        if (state.win && !state.win.isDestroyed()) {
          state.win.show()
          state.win.focus()
          state.win.webContents.send('global-shortcut', { action: 'new-todo' })
        }
      },
    },
    { type: 'separator' },
    {
      label: '开机自启',
      type: 'checkbox',
      checked: prefs.autoLaunch,
      click: (menuItem) => {
        const enabled = menuItem.checked
        app.setLoginItemSettings({ openAtLogin: enabled })
        saveTrayPrefs({ ...prefs, autoLaunch: enabled })
        if (state.win && !state.win.isDestroyed()) {
          state.win.webContents.send('tray-action', { type: 'auto-launch-changed', enabled })
        }
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.isQuitting = true
        app.quit()
      },
    },
  )

  try {
    state.tray.setContextMenu(Menu.buildFromTemplate(template))
  } catch (err) {
    console.error('[tray] Failed to build tray menu:', err)
  }
}

function createTray() {
  const iconSize = 16
  const canvas = nativeImage.createFromBuffer(
    Buffer.alloc(iconSize * iconSize * 4),
    { width: iconSize, height: iconSize }
  )
  let trayIcon = canvas
  const iconPath = path.join(__dirname, '..', 'public', 'icon-16.png')
  if (fs.existsSync(iconPath)) {
    trayIcon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 })
  }

  state.tray = new Tray(trayIcon)
  state.tray.setToolTip('思忆便签')

  updateTrayMenu()

  state.tray.on('click', () => {
    if (state.win) {
      if (state.win.isVisible()) {
        state.win.hide()
      } else {
        state.win.show()
        state.win.focus()
      }
    }
  })
}

// ── IPC handlers ─────────────────────────────────────────────────────────

ipcMain.on('backend-status', (event, connected) => {
  updateTrayIcon(connected)
})

ipcMain.on('recent-notes:update', (event, notes) => {
  if (Array.isArray(notes)) updateRecentNotes(notes)
})

module.exports = {
  createTray,
  updateTrayMenu,
  updateRecentNotes,
}
