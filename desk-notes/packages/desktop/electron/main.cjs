const { app, BrowserWindow, globalShortcut, protocol, shell } = require('electron')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { execSync } = require('child_process')

// ── V2.1 批次4（P1-13）：移除 Chromium 主进程沙箱禁用 ─────────────────────────
// 历史：Electron 42 默认主进程沙箱导致 AppData 写入 EPERM；根因是 userData 路径
// 含 npm scope 的 @ 字符（@desk-notes/desktop），已通过 app.setName('desk-notes')
// 修掉（路径变为 ...\AppData\Roaming\desk-notes\）。故移除 no-sandbox，恢复沙箱。
// 若沙箱下 EPERM 复发：修 userData 目录 ACL（icacls），而非再次关闭沙箱。
// 安全补偿措施保持：contextIsolation:true + nodeIntegration:false + CSP header +
// will-navigate/window-open URL 白名单。
// disable-gpu-sandbox 保留（GPU 兼容，与文件写入 EPERM 无关）。
app.commandLine.appendSwitch('disable-gpu-sandbox')

// ── 关键修复：覆盖应用名，避免 userData 路径含 @ 字符 ─────────────────────
// npm scope @desk-notes 会让 Electron 推导出 userData 路径：
//   C:\Users\<user>\AppData\Roaming\@desk-notes\desktop\
// @ 字符在 Windows 上会导致 Electron 42 的 fs.writeFileSync 报 EPERM (-4048)
// 改为 desk-notes 后路径变为：
//   C:\Users\<user>\AppData\Roaming\desk-notes\
// 必须在 app.whenReady() 之前、任何使用 userData 的代码之前调用
app.setName('desk-notes')

/**
 * 使用 PowerShell Copy-Item 复制文件，绕过 Electron 42 的 fs 模块 EPERM 问题。
 * 注意：cmd /c copy 在 Trae IDE 沙箱中被禁用，因此只使用 PowerShell 后备。
 */
function robustCopyFile(srcPath, dstPath) {
  // 先尝试 fs.copyFileSync
  let fsErr = null
  try {
    fs.copyFileSync(srcPath, dstPath)
    return true
  } catch (e) {
    if (e.code !== 'EPERM' && e.code !== 'EBUSY' && e.code !== 'EACCES') {
      throw e
    }
    fsErr = e
  }
  // EPERM: 使用 PowerShell Copy-Item 后备（cmd /c 在 Trae IDE 沙箱中被禁用）
  try {
    const psCmd = `powershell -NoProfile -Command "Copy-Item -LiteralPath '${srcPath}' -Destination '${dstPath}' -Force"`
    execSync(psCmd, { encoding: 'utf-8', stdio: 'pipe' })
    return true
  } catch (psErr) {
    const psStderr = psErr.stderr ? psErr.stderr.toString().trim() : ''
    const psStdout = psErr.stdout ? psErr.stdout.toString().trim() : ''
    console.error(`[robustCopyFile] PowerShell failed: status=${psErr.status}, stderr=${psStderr}, stdout=${psStdout}`)
    throw new Error(`All copy methods failed: fs=${fsErr ? fsErr.message : 'n/a'}, ps=${psErr.message}${psStderr ? ' | psStderr: ' + psStderr : ''}`)
  }
}

// ── 关键修复：使用 AppData\Local 而非 AppData\Roaming 作为 userData ─────────
// Electron 42 在 Trae IDE 沙箱环境下无法写入 AppData\Roaming 目录（EPERM），
// 但可以正常写入 AppData\Local（os.tmpdir() 即位于 AppData\Local\Temp）。
// 因此将 userData 改为 AppData\Local\desk-notes，避开 Roaming 目录的权限限制。
// 必须在 app.whenReady() 之前、任何使用 userData 的代码之前调用。
function resolveUserDataDir() {
  // 1. 优先使用环境变量覆盖（用于测试或自定义路径）
  const envDir = process.env.ELECTRON_USER_DATA_DIR
  if (envDir) {
    try {
      if (!fs.existsSync(envDir)) fs.mkdirSync(envDir, { recursive: true })
      console.log('[main] userData dir (env):', envDir)
      return envDir
    } catch (e) {
      console.warn('[main] env userData dir not writable:', envDir, e.message)
    }
  }

  // 2. 默认使用 AppData\Local\desk-notes
  const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
  const localDir = path.join(localAppData, 'desk-notes')
  try {
    if (!fs.existsSync(localDir)) fs.mkdirSync(localDir, { recursive: true })
    // 写入测试：验证目录可写
    const testFile = path.join(localDir, '.write-test')
    fs.writeFileSync(testFile, 'ok', 'utf-8')
    fs.unlinkSync(testFile)
    console.log('[main] userData dir (local):', localDir)
    return localDir
  } catch (e) {
    console.warn('[main] AppData\\Local not writable:', localDir, e.message)
  }

  // 3. 后备：使用项目目录下的 data 文件夹（开发环境）
  const devDir = path.join(__dirname, '..', 'data')
  try {
    if (!fs.existsSync(devDir)) fs.mkdirSync(devDir, { recursive: true })
    const testFile = path.join(devDir, '.write-test')
    fs.writeFileSync(testFile, 'ok', 'utf-8')
    fs.unlinkSync(testFile)
    console.warn('[main] fallback to project data dir:', devDir)
    return devDir
  } catch (e) {
    console.error('[main] project data dir not writable either:', devDir, e.message)
  }

  // 4. 最终后备：使用 os.tmpdir()（不理想但保证可用）
  const tmpDir = path.join(os.tmpdir(), 'desk-notes-data')
  try {
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true })
  } catch { /* ignore */ }
  console.error('[main] fallback to tmpdir:', tmpDir)
  return tmpDir
}

const RESOLVED_USER_DATA_DIR = resolveUserDataDir()
app.setPath('userData', RESOLVED_USER_DATA_DIR)

// 迁移旧数据（@desk-notes 或 AppData\Roaming\desk-notes → 新目录）
migrateOldData()

/**
 * 迁移单个文件：读取源文件内容 → 写入 tmpdir → 从 tmpdir 复制到目标。
 * 避免直接从 @desk-notes 复制到 desk-notes 时触发 Electron 42 的 EPERM。
 * 读取不受 EPERM 影响，tmpdir 也没有 @ 字符问题。
 */
function migrateFile(srcPath, dstPath) {
  const content = fs.readFileSync(srcPath)
  const tmpFile = path.join(os.tmpdir(), `siyi-migrate-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.tmp`)
  fs.writeFileSync(tmpFile, content)
  try {
    robustCopyFile(tmpFile, dstPath)
  } finally {
    try { fs.unlinkSync(tmpFile) } catch { /* ignore */ }
  }
}

function migrateOldData() {
  try {
    const roamingAppData = app.getPath('appData') // AppData\Roaming
    const newUserData = app.getPath('userData')

    // 检查迁移标记，避免重复迁移
    const marker = path.join(newUserData, '.migrated-from-old')
    if (fs.existsSync(marker)) {
      console.log('[main] migration already done, skipping')
      return
    }

    // 可能的旧数据目录：
    // 1. AppData\Roaming\@desk-notes\desktop（npm scope 名称）
    // 2. AppData\Roaming\desk-notes（旧默认路径）
    const oldDirs = [
      path.join(roamingAppData, '@desk-notes', 'desktop'),
      path.join(roamingAppData, 'desk-notes'),
    ].filter(p => fs.existsSync(p) && path.resolve(p) !== path.resolve(newUserData))

    if (oldDirs.length === 0) return

    console.log('[main] migrating data from', oldDirs, 'to', newUserData)

    // 确保新目录存在
    if (!fs.existsSync(newUserData)) {
      fs.mkdirSync(newUserData, { recursive: true })
    }

    let totalNotes = 0
    let totalAssets = 0
    let totalFailed = 0

    for (const oldUserData of oldDirs) {
      // 迁移 siyi-notes 目录（笔记数据）— 合并而非覆盖
      const oldNotes = path.join(oldUserData, 'siyi-notes')
      const newNotes = path.join(newUserData, 'siyi-notes')
      if (fs.existsSync(oldNotes)) {
        const oldNotesDir = path.join(oldNotes, 'notes')
        const newNotesDir = path.join(newNotes, 'notes')
        if (fs.existsSync(oldNotesDir)) {
          if (!fs.existsSync(newNotesDir)) {
            fs.mkdirSync(newNotesDir, { recursive: true })
          }
          // 复制所有 .md 文件（跳过已存在的，避免覆盖新数据）
          for (const file of fs.readdirSync(oldNotesDir)) {
            if (!file.endsWith('.md')) continue
            const srcFile = path.join(oldNotesDir, file)
            const dstFile = path.join(newNotesDir, file)
            if (!fs.existsSync(dstFile)) {
              try {
                migrateFile(srcFile, dstFile)
                totalNotes++
              } catch (copyErr) {
                totalFailed++
                console.error(`[main] failed to copy ${file}:`, copyErr.message)
              }
            }
          }

          // 复制 assets 目录（逐文件迁移）
          const oldAssets = path.join(oldNotes, 'assets')
          const newAssets = path.join(newNotes, 'assets')
          if (fs.existsSync(oldAssets)) {
            if (!fs.existsSync(newAssets)) {
              fs.mkdirSync(newAssets, { recursive: true })
            }
            for (const assetFile of fs.readdirSync(oldAssets)) {
              const srcAsset = path.join(oldAssets, assetFile)
              const dstAsset = path.join(newAssets, assetFile)
              if (!fs.existsSync(dstAsset) && fs.statSync(srcAsset).isFile()) {
                try {
                  migrateFile(srcAsset, dstAsset)
                  totalAssets++
                } catch {
                  totalFailed++
                }
              }
            }
          }
        }
      }

      // 迁移 windows.json / tray-prefs.json（窗口位置等偏好）— 仅当新路径不存在时
      for (const prefFile of ['windows.json', 'tray-prefs.json']) {
        const oldPref = path.join(oldUserData, prefFile)
        const newPref = path.join(newUserData, prefFile)
        if (fs.existsSync(oldPref) && !fs.existsSync(newPref)) {
          try {
            migrateFile(oldPref, newPref)
            console.log('[main] migrated', prefFile)
          } catch (copyErr) {
            console.error(`[main] failed to migrate ${prefFile}:`, copyErr.message)
          }
        }
      }
    }

    console.log(`[main] migrated ${totalNotes} notes, ${totalAssets} assets, ${totalFailed} failed`)

    // 写入迁移标记，避免重复迁移
    try {
      fs.writeFileSync(marker, new Date().toISOString(), 'utf-8')
    } catch (markerErr) {
      console.error('[main] failed to write migration marker:', markerErr.message)
    }
    console.log('[main] migration complete')
  } catch (err) {
    console.error('[main] migration failed:', err)
  }
}

const { state } = require('./state.cjs')
const { autoUpdater } = require('./auto-updater.cjs')
const { createMainWindow } = require('./windows.cjs')
const { createTray } = require('./tray.cjs')
const { startReminderScheduler, stopReminderScheduler } = require('./reminder.cjs')
const { startClipboardMonitor, stopClipboardMonitor } = require('./clipboard.cjs')
const { startFileWatcher, stopFileWatcher, registerAssetsProtocol } = require('./file-store.cjs')
const { isSafeExternalUrl, isAllowedLocalNavigation } = require('./url-guard.cjs')

// ── D19 更新检查止血（个人自用基线默认关闭）─────────────────────────────────
// 原行为：打包版启动后固定 5s 触发一次 autoUpdater 检查，目标是 build.publish
// 推导出的 github.com/siyi/desk-notes/releases —— 该仓库当前不存在，检查必然失败
// 并把 update:error 回抛给渲染进程（白耗一次网络 + 污染日志）。
// 现行为：默认不再开机自检，更新检查改懒加载 —— 只在用户于设置面板主动点
// "检查更新"时走 IPC（update:check / update:download / update:install）。
// 恢复条件（两条同时满足才把开关置 true）：
//   1) publish.owner/repo 指向真实存在的仓库，且该 Release 附带 latest.yml；
//   2) latest.yml 里 url/path 与实际上传的产物文件名一致（已由 package.json 的
//      build.win.nsis.artifactName 固定为 ASCII 名，不再被 GitHub safe-name 改写）。
// 开关打开后间隔必须 ≥24h（UPDATE_CHECK_INTERVAL_MS），不做秒级轮询。
const UPDATE_AUTO_CHECK_ON_STARTUP = false
const UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

// ── Global error handlers ────────────────────────────────────────────────────
process.on('uncaughtException', (err) => {
  console.error('[main] Uncaught exception:', err)
})
process.on('unhandledRejection', (err) => {
  console.error('[main] Unhandled rejection:', err)
})

app.commandLine.appendSwitch('enable-transparent-visuals')

protocol.registerSchemesAsPrivileged([
  // P0 修复：移除 bypassCSP，让 assets:// 资源也受 CSP 约束
  { scheme: 'assets', privileges: { supportFetchAPI: true, corsEnabled: true, secure: true, stream: true } },
])

// ── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(() => {
  // P0 修复：添加 CSP header，补偿沙箱禁用的安全风险
  // 生产环境移除 script-src 'unsafe-inline'（dist/index.html 无内联脚本），
  // 开发环境保留（Vite HMR 注入内联脚本需要）。
  const { session } = require('electron')
  const isDev = state.isDev
  const scriptSrc = isDev
    ? "script-src 'self' 'unsafe-inline'; "
    : "script-src 'self'; "
  // V2.1 批次4（P1-14 收敛）：connect-src 不再允许任意 ws:/wss:——仅放行本机后端
  // （127.0.0.1/localhost 任意端口）。防止渲染进程被 XSS 后向任意 WebSocket 服务器
  // 外传数据（后端 _verify_auth 已强制非 localhost token 认证）。
  const connectSrc = "connect-src 'self' ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:*; "
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [
          "default-src 'self' assets: data: blob:; " +
          scriptSrc +
          "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; " +
          "img-src 'self' assets: data: blob: https:; " +
          connectSrc +
          "font-src 'self' data: https://cdnjs.cloudflare.com;",
        ],
      },
    })
  })

  // V2.1 批次4（screen:capture 授权）：仅放行 display-capture / fullscreen 权限
  // （本地桌面截图/全屏窗口），其余权限一律拒绝（配合 CSP/可信来源兜底）。
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'display-capture' || permission === 'fullscreen')
  })
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return permission === 'display-capture' || permission === 'fullscreen'
  })

  // 仅允许 http/https 交系统浏览器；file: 等协议一律拒绝（防打开本地文件）
  const safeExternalUrl = isSafeExternalUrl

  // Allow external links (target="_blank") to open in system browser
  app.on('web-contents-created', (_event, contents) => {
    contents.setWindowOpenHandler(({ url }) => {
      // V2.1 批次4（P1-16）：window.open 仅放行 http/https；file: 等协议拦截
      const safe = safeExternalUrl(url)
      if (safe) shell.openExternal(safe)
      return { action: 'deny' }
    })
    contents.on('will-navigate', (event, url) => {
      // 允许本机后端/开发服务器导航；其余（含 file:）拦截。
      // V2.1 批次4：URL 解析精确匹配 hostname（杜绝 startswith 前缀绕过，
      // 如 http://127.0.0.1.evil.com），且 file: URL 不再交给 shell.openExternal。
      if (isAllowedLocalNavigation(url)) return
      event.preventDefault()
      const safe = safeExternalUrl(url)
      if (safe) shell.openExternal(safe)
    })
  })

  registerAssetsProtocol()
  createMainWindow()
  createTray()
  startReminderScheduler()
  startClipboardMonitor()

  // ── ORB 窗懒加载（内存治理 B）────────────────────────────────────────────
  // 移除历史版本"启动后 1.5s + 5s 双保险自动创建 ORB"——它让即使从不使用
  // 的浮动宠物窗也常驻一个透明渲染进程（约 250MB）。ORB 改为**首次交互按需
  // 创建**：托盘菜单、Alt+Shift+F、createViewWindow('floating') 触发点都会
  // 自动建窗；当前启动流程不做任何自动创建。

  startFileWatcher()

  if (!state.isDev && autoUpdater && UPDATE_AUTO_CHECK_ON_STARTUP) {
    setTimeout(() => autoUpdater.checkForUpdatesAndNotify(), UPDATE_CHECK_INTERVAL_MS)
  }
})

app.on('window-all-closed', () => {
  // Don't quit on window close; tray keeps running
})

app.on('before-quit', () => {
  app.isQuitting = true
  stopReminderScheduler()
  stopClipboardMonitor()
  stopFileWatcher()
  globalShortcut.unregisterAll()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow()
  if (state.win) { state.win.show(); state.win.focus() }
})
