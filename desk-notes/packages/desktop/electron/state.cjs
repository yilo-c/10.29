const { app } = require('electron')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { execSync } = require('child_process')

// ── Data directories ──────────────────────────────────────────────────────
const DATA_DIR = path.join(app.getPath('userData'), 'siyi-notes')
const NOTES_DIR = path.join(DATA_DIR, 'notes')
const ASSETS_DIR = path.join(DATA_DIR, 'assets')
const NOTIFIED_FILE = path.join(DATA_DIR, '.notified.json')
const WINDOWS_PREFS_PATH = path.join(app.getPath('userData'), 'windows.json')
const TRAY_PREFS_PATH = path.join(app.getPath('userData'), 'tray-prefs.json')

// ── Robust file helpers (avoids EPERM in Electron 42) ─────────────────────
/**
 * 使用 PowerShell Copy-Item 复制文件，绕过 Electron 42 的 fs 模块 EPERM 问题。
 * 注意：cmd /c copy 在 Trae IDE 沙箱中被禁用，因此只使用 PowerShell 后备。
 */
function robustCopyFileSync(srcPath, dstPath) {
  try {
    execSync(`powershell -NoProfile -Command "Copy-Item -LiteralPath '${srcPath}' -Destination '${dstPath}' -Force"`, { stdio: 'pipe' })
    return true
  } catch (psErr) {
    const psStderr = psErr.stderr ? psErr.stderr.toString().trim() : ''
    throw new Error(`robustCopyFileSync failed (PowerShell): ${psErr.message}${psStderr ? ' | ' + psStderr : ''}`)
  }
}

function atomicWriteFileSync(targetPath, content) {
  const tmpDir = os.tmpdir()
  const tmpFile = path.join(tmpDir, `siyi-pref-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`)
  fs.writeFileSync(tmpFile, content, 'utf-8')
  try {
    fs.writeFileSync(targetPath, content, 'utf-8')
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
    return
  } catch (e) {
    if (e.code !== 'EPERM' && e.code !== 'EBUSY' && e.code !== 'EACCES') {
      try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
      throw e
    }
  }
  try {
    fs.copyFileSync(tmpFile, targetPath)
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
    return
  } catch { /* 继续尝试 */ }
  try {
    try { fs.unlinkSync(targetPath) } catch { /* ignore */ }
    fs.renameSync(tmpFile, targetPath)
    return
  } catch { /* 继续尝试 */ }
  // cmd / PowerShell 后备
  try {
    robustCopyFileSync(tmpFile, targetPath)
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
  } catch (robustErr) {
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
    throw new Error(`atomicWriteFileSync all methods failed: ${robustErr.message}`)
  }
}

// ── Preference helpers ────────────────────────────────────────────────────
function loadWindowPrefs() {
  try {
    if (fs.existsSync(WINDOWS_PREFS_PATH)) {
      return JSON.parse(fs.readFileSync(WINDOWS_PREFS_PATH, 'utf-8'))
    }
  } catch { /* ignore */ }
  return {}
}

function saveWindowPrefs(prefs) {
  try {
    const dir = path.dirname(WINDOWS_PREFS_PATH)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
    atomicWriteFileSync(WINDOWS_PREFS_PATH, JSON.stringify(prefs, null, 2))
  } catch { /* ignore */ }
}

function loadTrayPrefs() {
  try {
    if (fs.existsSync(TRAY_PREFS_PATH)) {
      return JSON.parse(fs.readFileSync(TRAY_PREFS_PATH, 'utf-8'))
    }
  } catch { /* ignore */ }
  return { autoLaunch: false }
}

function saveTrayPrefs(prefs) {
  try {
    const dir = path.dirname(TRAY_PREFS_PATH)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
    atomicWriteFileSync(TRAY_PREFS_PATH, JSON.stringify(prefs, null, 2))
  } catch { /* ignore */ }
}

// ── Shared mutable state ──────────────────────────────────────────────────
const state = {
  // Window references
  win: null,
  tray: null,
  noteWindows: new Map(),
  viewWindows: new Map(),
  orbWindow: null,
  floatingChatWindow: null,
  clipboardPopupWindow: null,

  // 第二档 #6：悬浮球长按录音 STT —— 跨窗口转写文本暂存
  // orb 窗口转写完成但聊天窗口尚未创建时，先存这里；聊天窗口挂载后通过
  // floating-chat:get-pending-draft 取出并清空（避免同一条转写被两次投递）。
  pendingVoiceDraft: null,

  // Environment
  isDev: process.env.NODE_ENV === 'development',

  // Background mode synced from renderer
  currentBackgroundMode: 'acrylic',

  // Backend connection status (for tray)
  _backendConnected: false,

  // Clipboard popup drag state (shared between clipboard.js and windows.js)
  _clipboardDragging: false,
  _clipboardProgrammaticResize: false,
  _clipboardResizeTimer: null,

  // Window drag interval
  dragInterval: null,

  // Main window save-bounds timer
  saveTimer: null,
}

module.exports = {
  state,
  DATA_DIR,
  NOTES_DIR,
  ASSETS_DIR,
  NOTIFIED_FILE,
  loadWindowPrefs,
  saveWindowPrefs,
  loadTrayPrefs,
  saveTrayPrefs,
}
