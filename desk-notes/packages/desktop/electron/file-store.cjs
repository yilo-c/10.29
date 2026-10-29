const { ipcMain, BrowserWindow } = require('electron')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { execSync } = require('child_process')
const { pathToFileURL } = require('url')
const { state, DATA_DIR, NOTES_DIR, ASSETS_DIR } = require('./state.cjs')
// V2.1 批次4（b4-e3）：越界防护提取为纯函数模块，便于单测覆盖
const { guardPath } = require('./path-guard.cjs')

// ── Directory helpers ──────────────────────────────────────────────────────

function ensureDataDirs() {
  fs.mkdirSync(NOTES_DIR, { recursive: true })
}

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

/**
 * 原子写入文件：先写入临时文件，再用多种方式复制到目标路径。
 * 避免 EPERM 错误（Electron 42 在 AppData 目录的 fs 模块持续失败）。
 * 临时文件使用 OS 临时目录，避免目标目录的文件锁定问题。
 * 所有 fs 操作失败后，使用 cmd / PowerShell 后备方案。
 */
async function atomicWriteFile(targetPath, content) {
  const tmpDir = os.tmpdir()
  const tmpFile = path.join(tmpDir, `siyi-note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`)

  // 步骤 1：写入临时文件（OS 临时目录通常没有权限问题）
  await fs.promises.writeFile(tmpFile, content, 'utf-8')

  // 步骤 2：尝试 fs 直接写入目标
  try {
    await fs.promises.writeFile(targetPath, content, 'utf-8')
    try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
    return
  } catch (e) {
    if (e.code !== 'EPERM' && e.code !== 'EBUSY' && e.code !== 'EACCES') {
      try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
      throw e
    }
    // EPERM/EBUSY/EACCES：继续尝试后备方案
  }

  // 步骤 3：fs.copyFile 后备
  try {
    await fs.promises.copyFile(tmpFile, targetPath)
    try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
    return
  } catch (copyErr) {
    // copyFile 也失败，继续尝试
  }

  // 步骤 4：fs.rename 后备（先删除目标）
  try {
    try { await fs.promises.unlink(targetPath) } catch { /* ignore */ }
    await fs.promises.rename(tmpFile, targetPath)
    return
  } catch (renameErr) {
    // rename 也失败，继续尝试
  }

  // 步骤 5：cmd / PowerShell 后备（绕过 Electron 42 的 fs 模块）
  try {
    robustCopyFileSync(tmpFile, targetPath)
    try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
    return
  } catch (robustErr) {
    try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
    throw new Error(`atomicWriteFile all methods failed: ${robustErr.message}`)
  }
}

/**
 * 同步原子写入（用于迁移等同步场景）
 */
function atomicWriteFileSync(targetPath, content) {
  const tmpDir = os.tmpdir()
  const tmpFile = path.join(tmpDir, `siyi-note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`)

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
  } catch (copyErr) {
    // 继续尝试
  }

  try {
    try { fs.unlinkSync(targetPath) } catch { /* ignore */ }
    fs.renameSync(tmpFile, targetPath)
    return
  } catch (renameErr) {
    // 继续尝试
  }

  // cmd / PowerShell 后备
  try {
    robustCopyFileSync(tmpFile, targetPath)
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
    return
  } catch (robustErr) {
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
    throw new Error(`atomicWriteFileSync all methods failed: ${robustErr.message}`)
  }
}

/** Validate noteId to prevent path injection */
function isValidNoteId(id) {
  return typeof id === 'string' && id.length > 0 && id.length <= 128 && !/[\\/]/.test(id) && !id.includes('..')
}

/** Validate filename to prevent path injection */
function isValidFilename(name) {
  return typeof name === 'string' && /^[a-zA-Z0-9_-]+\.(png|jpg|jpeg|gif|webp|svg|mp4|webm|mov|avi|mkv|wmv|flv)$/.test(name)
}

// ── Note file parsing / serialization ──────────────────────────────────────

/**
 * Parse YAML frontmatter + body from a .md file string.
 * Returns { frontmatter: {}, body: '' }
 */
function parseNoteFile(raw) {
  const result = { frontmatter: {}, body: raw || '' }
  if (!raw) return result
  const trimmed = raw.trimStart()
  if (!trimmed.startsWith('---')) return result
  const end = trimmed.indexOf('---', 3)
  if (end === -1) return result
  const fmRaw = trimmed.slice(3, end).trim()
  result.body = trimmed.slice(end + 3).trimStart()
  for (const line of fmRaw.split('\n')) {
    const colIdx = line.indexOf(':')
    if (colIdx === -1) continue
    const key = line.slice(0, colIdx).trim()
    let val = line.slice(colIdx + 1).trim()
    if ((val.startsWith("'") && val.endsWith("'")) || (val.startsWith('"') && val.endsWith('"'))) {
      val = val.slice(1, -1)
    }
    if (val && (val[0] === '[' || val[0] === '{')) {
      try { result.frontmatter[key] = JSON.parse(val); continue } catch { /* fall through */ }
    }
    if (val.startsWith('[') && val.endsWith(']')) {
      const inner = val.slice(1, -1).trim()
      result.frontmatter[key] = inner ? inner.split(',').map(s => s.trim().replace(/^['"]|['"]$/g, '')) : []
    } else if (val === 'true') {
      result.frontmatter[key] = true
    } else if (val === 'false') {
      result.frontmatter[key] = false
    } else if (/^\d+$/.test(val)) {
      result.frontmatter[key] = parseInt(val, 10)
    } else if (/^\d+\.\d+$/.test(val)) {
      result.frontmatter[key] = parseFloat(val)
    } else {
      result.frontmatter[key] = val
    }
  }
  return result
}

/**
 * Serialize a note object into .md with YAML frontmatter.
 */
function serializeNoteFile(note) {
  const { content, ...meta } = note
  const lines = ['---']
  for (const [key, val] of Object.entries(meta)) {
    if (val === undefined || val === null) continue
    if (Array.isArray(val)) {
      if (val.length > 0 && typeof val[0] === 'object') {
        lines.push(`${key}: '${JSON.stringify(val)}'`)
      } else {
        lines.push(`${key}: [${val.map(v => typeof v === 'string' ? `"${v}"` : v).join(', ')}]`)
      }
    } else if (typeof val === 'object' && val !== null) {
      lines.push(`${key}: '${JSON.stringify(val)}'`)
    } else if (typeof val === 'string') {
      if (val.includes(':') || val.includes('#') || val.includes("'")) {
        lines.push(`${key}: "${val}"`)
      } else {
        lines.push(`${key}: ${val}`)
      }
    } else {
      lines.push(`${key}: ${val}`)
    }
  }
  lines.push('---', '')
  lines.push(content || '')
  return lines.join('\n')
}

/**
 * Read localStorage from the main renderer (used during migration).
 */
async function readRendererLocalStorage(win, key) {
  try {
    return await win.webContents.executeJavaScript(
      `(() => { try { return localStorage.getItem(${JSON.stringify(key)}) } catch { return null } })()`,
      true
    )
  } catch {
    return null
  }
}

/**
 * Clear localStorage key in the renderer.
 */
async function clearRendererLocalStorage(win, key) {
  try {
    await win.webContents.executeJavaScript(
      `(() => { try { localStorage.removeItem(${JSON.stringify(key)}) } catch {} })()`,
      true
    )
  } catch { /* ignore */ }
}

// ── File watcher ─────────────────────────────────────────────────────────────

let watcher = null
let watcherDebounce = null

function startFileWatcher() {
  try {
    ensureDataDirs()
    watcher = fs.watch(NOTES_DIR, (eventType, filename) => {
      if (!filename || !filename.endsWith('.md')) return
      if (watcherDebounce) clearTimeout(watcherDebounce)
      watcherDebounce = setTimeout(() => {
        const noteId = filename.slice(0, -3)
        for (const w of BrowserWindow.getAllWindows()) {
          if (!w.isDestroyed()) {
            w.webContents.send('note-file-changed', { noteId, event: eventType })
          }
        }
      }, 300)
    })
  } catch (err) {
    console.error('[main] Failed to start file watcher:', err)
  }
}

function stopFileWatcher() {
  if (watcher) {
    watcher.close()
    watcher = null
  }
  if (watcherDebounce) {
    clearTimeout(watcherDebounce)
    watcherDebounce = null
  }
}

// ── File Store IPC handlers ────────────────────────────────────────────────

ipcMain.handle('file-store:init', async (event) => {
  ensureDataDirs()
  const w = BrowserWindow.fromWebContents(event.sender)
  if (!w) return { migrated: false }

  const storePath = path.join(DATA_DIR, 'store.json')
  if (fs.existsSync(storePath)) {
    return { migrated: false }
  }

  const raw = await readRendererLocalStorage(w, 'desk-notes-storage')
  if (!raw) return { migrated: false }

  try {
    const parsed = JSON.parse(raw)
    const { state: persistedState } = parsed
    if (!persistedState) return { migrated: false }

    const notes = persistedState.floatingNotes || []
    for (const note of notes) {
      if (!note.id || typeof note.id !== 'string') {
        console.warn('[migration] skipping note without valid id:', note)
        continue
      }
      const mdPath = path.join(NOTES_DIR, `${note.id}.md`)
      atomicWriteFileSync(mdPath, serializeNoteFile({ ...note, id: note.id }))
    }

    const { floatingNotes, ...storeState } = persistedState
    atomicWriteFileSync(storePath, JSON.stringify({ ...parsed, state: storeState }))

    const trashRaw = await readRendererLocalStorage(w, 'desk-notes-trash')
    if (trashRaw) {
      atomicWriteFileSync(path.join(DATA_DIR, 'trash.json'), trashRaw)
    }

    await clearRendererLocalStorage(w, 'desk-notes-storage')
    await clearRendererLocalStorage(w, 'desk-notes-trash')

    return { migrated: true }
  } catch (err) {
    console.error('[file-store] Migration failed:', err)
    return { migrated: false }
  }
})

ipcMain.handle('file-store:read-file', async (event, relativePath) => {
  try {
    if (typeof relativePath !== 'string') return null
    const fullPath = guardPath(DATA_DIR, relativePath)
    if (!fs.existsSync(fullPath)) return null
    return fs.readFileSync(fullPath, 'utf-8')
  } catch (err) {
    console.error(`[file-store] read-file failed: ${relativePath}`, err)
    return null
  }
})

ipcMain.handle('file-store:write-file', async (event, relativePath, content) => {
  try {
    if (typeof relativePath !== 'string') throw new Error('invalid path')
    const fullPath = guardPath(DATA_DIR, relativePath)
    fs.mkdirSync(path.dirname(fullPath), { recursive: true })
    await atomicWriteFile(fullPath, content)
  } catch (err) {
    console.error(`[file-store] write-file failed: ${relativePath}`, err)
    throw err
  }
})

ipcMain.handle('file-store:delete-file', async (event, relativePath) => {
  try {
    if (typeof relativePath !== 'string') return
    const fullPath = guardPath(DATA_DIR, relativePath)
    if (fs.existsSync(fullPath)) {
      // 重试删除，避免文件被短暂锁定导致 EPERM
      let lastErr = null
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          fs.unlinkSync(fullPath)
          lastErr = null
          break
        } catch (e) {
          lastErr = e
          if (e.code === 'EPERM' || e.code === 'EBUSY' || e.code === 'EACCES') {
            await new Promise(r => setTimeout(r, 100 * (attempt + 1)))
            continue
          }
          throw e
        }
      }
      if (lastErr) throw lastErr
    }
    const noteMatch = relativePath.match(/^notes\/(.+)\.md$/)
    if (noteMatch) {
      const noteId = noteMatch[1]
      for (const w of BrowserWindow.getAllWindows()) {
        if (w !== event.sender && !w.isDestroyed()) {
          w.webContents.send('note-deleted', { id: noteId })
        }
      }
    }
  } catch (err) {
    console.error(`[file-store] delete-file failed: ${relativePath}`, err)
  }
})

ipcMain.handle('file-store:read-note', async (event, noteId) => {
  try {
    if (!isValidNoteId(noteId)) return null
    const mdPath = path.join(NOTES_DIR, `${noteId}.md`)
    if (!fs.existsSync(mdPath)) return null
    const raw = fs.readFileSync(mdPath, 'utf-8')
    const { frontmatter, body } = parseNoteFile(raw)
    // 注入文件名 id 作为兜底，防止 frontmatter 缺少 id 字段
    return { id: noteId, ...frontmatter, content: body }
  } catch (err) {
    console.error(`[file-store] read-note failed: ${noteId}`, err)
    return null
  }
})

ipcMain.handle('file-store:write-note', async (event, noteId, noteData) => {
  try {
    if (noteId == null || noteId === 'undefined' || !isValidNoteId(noteId)) throw new Error(`invalid noteId: ${noteId}`)
    ensureDataDirs()
    const mdPath = path.join(NOTES_DIR, `${noteId}.md`)
    // 确保笔记数据包含 id 字段
    const dataWithId = { ...noteData, id: noteId }
    const content = serializeNoteFile(dataWithId)
    // 临时停止文件监视器，避免 fs.watch 锁定目录导致 EPERM
    const watcherWasActive = watcher !== null
    if (watcherWasActive) {
      try { watcher.close() } catch { /* ignore */ }
      watcher = null
    }
    // 使用原子写入（临时文件 + copyFile/rename）避免 EPERM
    let lastErr = null
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await atomicWriteFile(mdPath, content)
        lastErr = null
        break
      } catch (e) {
        lastErr = e
        if (e.code === 'EPERM' || e.code === 'EBUSY' || e.code === 'EACCES') {
          await new Promise(r => setTimeout(r, 100 * (attempt + 1)))
          continue
        }
        throw e
      }
    }
    // 恢复文件监视器
    if (watcherWasActive) {
      setTimeout(() => startFileWatcher(), 50)
    }
    if (lastErr) throw lastErr
    for (const w of BrowserWindow.getAllWindows()) {
      if (w !== event.sender && !w.isDestroyed()) {
        w.webContents.send('note-synced', { id: noteId })
      }
    }
  } catch (err) {
    console.error(`[file-store] write-note failed: ${noteId}`, err)
    throw err
  }
})

ipcMain.handle('file-store:list-notes', async () => {
  try {
    ensureDataDirs()
    const files = fs.readdirSync(NOTES_DIR)
    return files.filter(f => f.endsWith('.md')).map(f => f.slice(0, -3))
  } catch (err) {
    console.error('[file-store] list-notes failed:', err)
    return []
  }
})

ipcMain.handle('file-store:note-exists', async (event, noteId) => {
  if (!isValidNoteId(noteId)) return false
  const mdPath = path.join(NOTES_DIR, `${noteId}.md`)
  return fs.existsSync(mdPath)
})

// ── Image Store IPC ──────────────────────────────────────────────────────────

ipcMain.handle('image:save', async (event, { dataUrl, name }) => {
  try {
    ensureDataDirs()
    fs.mkdirSync(ASSETS_DIR, { recursive: true })
    const base64 = dataUrl.replace(/^data:(image|video)\/[\w+\-.]+;base64,/, '')
    const ext = name ? name.split('.').pop() : 'png'
    const filename = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}.${ext}`
    if (!isValidFilename(filename)) throw new Error('Invalid filename')
    const filePath = path.join(ASSETS_DIR, filename)
    const buffer = Buffer.from(base64, 'base64')
    // 使用原子写入避免 EPERM（含 cmd/PowerShell 后备）
    const tmpDir = os.tmpdir()
    const tmpFile = path.join(tmpDir, `siyi-asset-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`)
    await fs.promises.writeFile(tmpFile, buffer)
    let saved = false
    // 尝试 1：fs.writeFile
    try {
      await fs.promises.writeFile(filePath, buffer)
      saved = true
    } catch (e) {
      if (e.code !== 'EPERM' && e.code !== 'EBUSY' && e.code !== 'EACCES') throw e
    }
    // 尝试 2：fs.copyFile
    if (!saved) {
      try {
        await fs.promises.copyFile(tmpFile, filePath)
        saved = true
      } catch { /* 继续尝试 */ }
    }
    // 尝试 3：fs.rename
    if (!saved) {
      try {
        try { await fs.promises.unlink(filePath) } catch { /* ignore */ }
        await fs.promises.rename(tmpFile, filePath)
        saved = true
      } catch { /* 继续尝试 */ }
    }
    // 尝试 4：cmd / PowerShell 后备
    if (!saved) {
      try {
        robustCopyFileSync(tmpFile, filePath)
        saved = true
      } catch (robustErr) {
        throw new Error(`image save all methods failed: ${robustErr.message}`)
      }
    }
    try { await fs.promises.unlink(tmpFile) } catch { /* ignore */ }
    return { path: `assets://${filename}` }
  } catch (err) {
    console.error('[image] save failed:', err)
    return { path: null }
  }
})

ipcMain.handle('image:read', async (event, filename) => {
  try {
    if (!isValidFilename(filename)) return null
    const filePath = guardPath(ASSETS_DIR, filename)
    if (!fs.existsSync(filePath)) return null
    const data = fs.readFileSync(filePath)
    const ext = filename.split('.').pop()
    const mime = ext === 'png' ? 'image/png' : ext === 'jpg' || ext === 'jpeg' ? 'image/jpeg'
      : ext === 'gif' ? 'image/gif' : ext === 'webp' ? 'image/webp' : ext === 'svg' ? 'image/svg+xml'
      : ext === 'mp4' ? 'video/mp4' : ext === 'webm' ? 'video/webm' : ext === 'mov' ? 'video/quicktime'
      : ext === 'avi' ? 'video/x-msvideo' : ext === 'mkv' ? 'video/x-matroska'
      : ext === 'wmv' ? 'video/x-ms-wmv' : ext === 'flv' ? 'video/x-flv' : 'image/png'
    return `data:${mime};base64,${data.toString('base64')}`
  } catch (err) {
    console.error('[image] read failed:', err)
    return null
  }
})

ipcMain.handle('image:delete', async (event, filename) => {
  try {
    if (!isValidFilename(filename)) return false
    const filePath = guardPath(ASSETS_DIR, filename)
    if (fs.existsSync(filePath)) {
      let lastErr = null
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          fs.unlinkSync(filePath)
          lastErr = null
          break
        } catch (e) {
          lastErr = e
          if (e.code === 'EPERM' || e.code === 'EBUSY' || e.code === 'EACCES') {
            await new Promise(r => setTimeout(r, 100 * (attempt + 1)))
            continue
          }
          throw e
        }
      }
      if (lastErr) throw lastErr
    }
    return true
  } catch (err) {
    console.error('[image] delete failed:', err)
    return false
  }
})

ipcMain.handle('image:list', async () => {
  try {
    ensureDataDirs()
    fs.mkdirSync(ASSETS_DIR, { recursive: true })
    const files = fs.readdirSync(ASSETS_DIR)
    return files.filter(f => isValidFilename(f))
  } catch (err) {
    console.error('[image] list failed:', err)
    return []
  }
})

// ── Usage document IPC ─────────────────────────────────────────────────────

const USAGE_DOC_PATH = path.join(
  state.isDev ? path.join(__dirname, '..', '..', '..') : process.resourcesPath,
  '使用说明.md'
)

ipcMain.handle('usage:read', async () => {
  try {
    if (!fs.existsSync(USAGE_DOC_PATH)) return null
    return fs.readFileSync(USAGE_DOC_PATH, 'utf-8')
  } catch (err) {
    console.error('[usage] read failed:', err)
    return null
  }
})

// ── assets:// protocol handler ─────────────────────────────────────────────

const { protocol, net } = require('electron')

function registerAssetsProtocol() {
  protocol.handle('assets', (request) => {
    try {
      const urlStr = request.url
      const filePath = decodeURIComponent(urlStr.replace(/^assets:\/\//, ''))
      const fullPath = guardPath(ASSETS_DIR, filePath)
      if (!fs.existsSync(fullPath)) {
        return new Response(null, { status: 404, statusText: 'Not Found' })
      }
      return net.fetch(pathToFileURL(fullPath).href)
    } catch (err) {
      console.error('[protocol] assets handler error:', err)
      return new Response(null, { status: 500, statusText: 'Internal Server Error' })
    }
  })
}

module.exports = {
  ensureDataDirs,
  guardPath,
  isValidNoteId,
  isValidFilename,
  parseNoteFile,
  serializeNoteFile,
  atomicWriteFile,
  atomicWriteFileSync,
  startFileWatcher,
  stopFileWatcher,
  registerAssetsProtocol,
}
