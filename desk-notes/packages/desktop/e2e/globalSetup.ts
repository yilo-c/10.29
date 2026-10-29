/**
 * E2E 真后端 harness（C-12，2026-09-12）
 *
 * globalSetup：spawn 一个隔离的 headless Python 后端（思忆集app/backend/headless_main），
 * 等待 WS 端口就绪后把连接信息经 process.env 传给各 worker：
 *   - SIYI_E2E_WS_URL   ws://127.0.0.1:<port>/ws   （fixtures 注入 __SIYI_WS_URL__）
 *   - SIYI_E2E_WS_TOKEN 主 token（读自临时数据目录下 auth_token 文件；loopback 免 token，
 *                     仅作既有 token 文件通道的透传，远端端口场景可用）
 *   - SIYI_E2E_DATA_DIR 临时数据目录（teardown 删除）
 *
 * 数据隔离（绝不写真实 思忆集app/data/data）：
 *   - SIYI_DATA_DIR → os.tmpdir() 全新空目录（local_storage/credential/confirmation/
 *     auth_token/task_queue/trace 等全部落临时目录；与 pytest _isolated_backend 同套路）
 *   - SIYI_DISABLE_DESKNOTES_BRIDGE=1 → 禁用 desk-notes 文件桥，notes 走临时目录 SQLite，
 *     杜绝后端回写 %LOCALAPPDATA%/repo 内真实 siyi-notes 数据
 *   - SIYI_TEST_OFFLINE=1 → 跳过 RSS 抓取 / autonomy AI 调度等真实网络后台任务
 *   - SIYI_LOCK_TAKEOVER=0 → 禁止接管他人实例锁（绝不杀用户真实后端）；锁被占则本进程
 *     退出并给出明确报错
 *
 * 端口策略：默认 18765（与前端 FALLBACK_URL 一致）。若已被占用（真实后端在跑）→ 直接失败，
 * 提示停止真实后端或设 SIYI_E2E_WS_PORT 换端口（换端口时 fixtures 自动注入 __SIYI_WS_URL__）。
 *
 * 预置数据（notification-center 视觉基线需要非空通知列表）：
 *   后端没有 notification.create 这类写入 RPC（通知只由 EventBus 订阅事件经
 *   NotificationDispatcher 落库），所以通知记录在 **spawn 之前** 用同一个 venv python
 *   直接写进临时数据目录的 local_storage（键 notifications，SQLite 后端）。
 *   先写后起 = 后端首次读必然命中这批数据，绕开 LocalDataStore 的进程内 LRU 缓存
 *   陈旧问题。成功置 SIYI_E2E_NOTIF_PRESET=1，失败只降级为 0（notification spec
 *   据此 skip，不牵连其余用例）。见导出 NOTIFICATION_PRESETS。
 *
 * 跳过：SIYI_E2E_SKIP_BACKEND=1（CI 无 Python 依赖时的 browser-only 模式）。
 * teardown：globalSetup 返回的清理函数（进程树 taskkill + 删临时目录）。
 */
import { spawn, execFileSync } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_PORT = 18765
const READY_TIMEOUT_MS = 180_000

/**
 * 仓库根定位：不能信任单一 __dirname——Playwright 对 globalSetup 做 TS 转译，
 * 转译缓存位置可能使路径推导偏移（实测 spawn 报 ENOENT，即拼出的 cwd 不存在）。
 * 从 e2e 目录与当前 cwd 分别向上走，找含「思忆集app」子目录的祖先；再验存在性。
 */
function resolveRepoRoot(): string {
  const starts = [__dirname, process.cwd()]
  for (const start of starts) {
    let dir = path.resolve(start)
    for (let i = 0; i < 8; i++) {
      if (fs.existsSync(path.join(dir, '思忆集app', 'backend', 'headless_main.py'))) return dir
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  }
  throw new Error('[e2e-harness] 无法定位仓库根（找不到 思忆集app/backend/headless_main.py），设 SIYI_E2E_SKIP_BACKEND=1 可跳过真后端')
}

function findPython(repoRoot: string): string {
  const appDir = path.join(repoRoot, '思忆集app')
  const candidates = [
    process.env.SIYI_E2E_PYTHON,
    path.join(appDir, '.venv', 'Scripts', 'python.exe'),
    path.join(appDir, '.venv', 'bin', 'python'),
    path.join(repoRoot, '.venv3.11', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv3.11', 'bin', 'python'),
  ].filter(Boolean) as string[]
  // 思忆集app\.venv 为遗留环境（实测缺 watchdog），逐个真实探测后端关键依赖，
  // 取第一个可 import aiohttp+watchdog 的；全部不合格则退回第一个存在的并告警。
  let firstExisting = ''
  for (const c of candidates) {
    if (!fs.existsSync(c)) continue
    if (!firstExisting) firstExisting = c
    try {
      const r = execFileSync(c, ['-c', 'import aiohttp, watchdog'], { stdio: 'ignore', timeout: 20_000 })
      void r
      return c
    } catch {
      console.log(`[e2e-harness] 候选 venv 缺后端依赖，跳过: ${c}`)
    }
  }
  if (firstExisting) {
    console.log(`[e2e-harness] 警告：所有候选 venv 均未通过依赖探测，仍用 ${firstExisting}`)
    return firstExisting
  }
  throw new Error(
    `[e2e-harness] 找不到后端 venv python（试过: ${candidates.join(', ')}）。` +
    '可用 SIYI_E2E_PYTHON 指定，或设 SIYI_E2E_SKIP_BACKEND=1 以 browser-only 模式跑 E2E。',
  )
}

function portInUse(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const sock = net.connect({ host: '127.0.0.1', port })
    sock.once('connect', () => { sock.destroy(); resolve(true) })
    sock.once('error', () => { sock.destroy(); resolve(false) })
    sock.setTimeout(1500, () => { sock.destroy(); resolve(false) })
  })
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function waitForHealth(port: number, proc: () => { exited: boolean, code: number | null }): Promise<void> {
  const deadline = Date.now() + READY_TIMEOUT_MS
  while (Date.now() < deadline) {
    const st = proc()
    if (st.exited) {
      throw new Error(
        `[e2e-harness] 后端进程提前退出（code=${st.code}）。常见原因：` +
        '①%TEMP%/siyi_headless.lock 被真实后端持有（已禁接管）——先停止真实后端再跑 E2E；' +
        '②venv 依赖缺失。详见临时目录 backend.log。',
      )
    }
    try {
      // 判据：HTTP 可应答即就绪——/health 返回的是聚合健康报告（全新临时数据目录
      // 下部分子系统可能为 degraded，不能等 status==="ok"），而端口开始监听发生在
      // server.start()（RPC 注册均在此之前完成），故应答 == RPC 分发可用。
      const res = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(2000) })
      if (res.status < 500) return
    } catch {
      /* 尚未监听，继续等 */
    }
    await sleep(500)
  }
  throw new Error(`[e2e-harness] 后端在 ${READY_TIMEOUT_MS / 1000}s 内未就绪（端口 ${port}）`)
}

async function readToken(dataDir: string, timeoutMs = 15_000): Promise<string> {
  const tokenPath = path.join(dataDir, 'auth_token')
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const t = fs.readFileSync(tokenPath, 'utf-8').trim()
      if (t) return t
    } catch {
      /* token 文件在 server 启动阶段生成，稍候 */
    }
    await sleep(300)
  }
  return ''
}

/** 通知中心基线用的一条预置记录（形状与后端 NotificationDispatcher._persist 一致）。 */
export interface NotificationPreset {
  id: string
  event_type: string
  title: string
  body: string
  priority: string
  read: boolean
}

/**
 * 四条覆盖 notification 模块的全部渲染分支：优先级徽标（high/urgent 有、low/normal 无）、
 * 未读点（3 未读 + 1 已读）、四种 event_type 中文标签、相对时间分档。
 * id 固定前缀 e2e-preset-，幂等重跑时先删同 id 旧记录再写。
 */
export const NOTIFICATION_PRESETS: NotificationPreset[] = [
  { id: 'e2e-preset-0', event_type: 'task.due', title: '任务到期', body: 'E2E 预置：下午三点评审会还有 10 分钟开始', priority: 'high', read: false },
  { id: 'e2e-preset-1', event_type: 'inbox.delivered', title: '新收件箱条目', body: 'E2E 预置：来自自动化任务的投递结果', priority: 'normal', read: false },
  { id: 'e2e-preset-2', event_type: 'sync.failed', title: '同步失败', body: 'E2E 预置：云同步连续失败，请检查网络后重试', priority: 'urgent', read: false },
  { id: 'e2e-preset-3', event_type: 'vocabulary.due_reminder', title: '单词到期复习', body: 'E2E 预置：今日有 12 个单词到期', priority: 'low', read: true },
]

/**
 * 往临时数据目录写 notification 记录（必须早于 spawn，见文件头说明）。
 * 复用后端的 LocalDataStore（数据目录由 SIYI_DATA_DIR 决定），避免自己拼 SQLite
 * 表结构；presets 经环境变量传参，绕开 Windows 命令行引号转义。
 */
function presetNotifications(python: string, appDir: string, dataDir: string): void {
  const script = [
    'import datetime, json, os, sys',
    'sys.path.insert(0, os.getcwd())',
    'from data.local_storage import LocalDataStore',
    "presets = json.loads(os.environ.get('SIYI_E2E_NOTIF_PRESET_JSON', '[]'))",
    'store = LocalDataStore()',
    "records = store.load_json('notifications', [])",
    'if not isinstance(records, list):',
    '    records = []',
    "preset_ids = set(p['id'] for p in presets)",
    'records = [r for r in records if not (isinstance(r, dict) and r.get(\"id\") in preset_ids)]',
    'now = datetime.datetime.now(datetime.timezone.utc)',
    'for i, p in enumerate(presets):',
    '    records.append({',
    "        'id': p['id'],",
    "        'event_type': p['event_type'],",
    "        'title': p['title'],",
    "        'body': p['body'],",
    "        'priority': p['priority'],",
    "        'created_at': (now - datetime.timedelta(minutes=3 * i)).isoformat(),",
    "        'read': bool(p.get('read')),",
    "        'raw': {'e2e_preset': True},",
    '    })',
    "store.save_json('notifications', records[-500:])",
    "print(json.dumps({'seeded': len(presets), 'total': len(records)}))",
  ].join('\n')
  const out = execFileSync(python, ['-c', script], {
    cwd: appDir,
    encoding: 'utf-8',
    timeout: 60_000,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      SIYI_DATA_DIR: dataDir,
      SIYI_E2E_NOTIF_PRESET_JSON: JSON.stringify(NOTIFICATION_PRESETS),
      PYTHONIOENCODING: 'utf-8',
    },
  })
  console.log(`[e2e-harness] notification 预置完成: ${String(out).trim()}`)
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  process.env.SIYI_E2E_NOTIF_PRESET = '0'
  if (process.env.SIYI_E2E_SKIP_BACKEND === '1') {
    console.log('[e2e-harness] SIYI_E2E_SKIP_BACKEND=1：跳过真后端（browser-only 模式）')
    return async () => {}
  }

  // 外部已提供后端（如 scripts/start_test_backend.py 常驻调试模式）：
  // 只做就绪等待，不 spawn、不 teardown——进程归外部会话管理。
  if (process.env.SIYI_E2E_WS_URL) {
    const extPort = Number(new URL(process.env.SIYI_E2E_WS_URL).port || DEFAULT_PORT)
    console.log(`[e2e-harness] 检测到外部 SIYI_E2E_WS_URL=${process.env.SIYI_E2E_WS_URL}，等待其就绪后复用`)
    await waitForHealth(extPort, () => ({ exited: false, code: null }))
    const extDataDir = process.env.SIYI_E2E_DATA_DIR
    if (extDataDir && fs.existsSync(extDataDir)) {
      // 外部后端 + 已知数据目录：补写预置（此时后端在跑，读路径若被 LRU 缓存住
      // 可能不反映新数据；能补就补，不行就让 notification spec 自行 skip）。
      try {
        presetNotifications(findPython(resolveRepoRoot()), path.join(resolveRepoRoot(), '思忆集app'), extDataDir)
        process.env.SIYI_E2E_NOTIF_PRESET = '1'
      } catch (err) {
        console.log(`[e2e-harness] 外部后端预置 notification 失败（notification spec 将跳过）: ${String(err)}`)
      }
    } else {
      console.log('[e2e-harness] 外部后端未提供 SIYI_E2E_DATA_DIR：notification 预置跳过')
    }
    return async () => {}
  }

  const port = Number(process.env.SIYI_E2E_WS_PORT || DEFAULT_PORT)
  if (await portInUse(port)) {
    throw new Error(
      `[e2e-harness] 端口 ${port} 已被占用——E2E 需要独占隔离后端实例（绝不连真实后端，` +
      '以免写入真实数据）。请先停止 127.0.0.1 上的思忆集后端，或设 SIYI_E2E_WS_PORT=<free port> 换端口。',
    )
  }

  const repoRoot = resolveRepoRoot()
  const appDir = path.join(repoRoot, '思忆集app')
  if (!fs.existsSync(appDir)) {
    throw new Error(`[e2e-harness] 后端目录不存在: ${appDir}`)
  }
  const python = findPython(repoRoot)
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'siyi-e2e-data-'))
  const logPath = path.join(dataDir, 'backend.log')
  const logFd = fs.openSync(logPath, 'a')

  console.log(`[e2e-harness] python=${python}`)
  console.log(`[e2e-harness] 隔离数据目录=${dataDir}（e2e 结束即删，绝不触碰 data\\data）`)

  // 预置 notification 记录：**必须在 spawn 之前**写完（见文件头说明——先写后起，
  // 后端首次读即命中这批数据，不受 LocalDataStore 进程内 LRU 缓存影响）。
  // 失败只降级（SIYI_E2E_NOTIF_PRESET=0 → notification spec 自行 skip），不牵连其余用例。
  try {
    presetNotifications(python, appDir, dataDir)
    process.env.SIYI_E2E_NOTIF_PRESET = '1'
  } catch (err) {
    console.log(`[e2e-harness] 警告：notification 预置失败，通知中心基线将跳过 —— ${String(err)}`)
  }

  const child = spawn(python, ['-m', 'backend.headless_main'], {
    cwd: appDir,
    stdio: ['ignore', logFd, logFd],
    windowsHide: true,
    env: {
      ...process.env,
      SIYI_WS_HOST: '127.0.0.1',
      SIYI_WS_PORT: String(port),
      SIYI_DATA_DIR: dataDir,
      SIYI_DISABLE_DESKNOTES_BRIDGE: '1',
      // 默认保持离线（SIYI_TEST_OFFLINE=1）以隔离真实出网；需要真实（含 loopback）
      // 网络通道的用例（如 chat-ai-mock 注入本地 mock provider）可设
      // SIYI_E2E_TEST_OFFLINE=0 单跑放开，不影响其余用例的默认隔离。
      SIYI_TEST_OFFLINE: process.env.SIYI_E2E_TEST_OFFLINE ?? '1',
      SIYI_LOCK_TAKEOVER: '0',
      SIYI_WS_AUTH_ENABLED: '1',
      PYTHONIOENCODING: 'utf-8',
    },
  })

  let exited = false
  let exitCode: number | null = null
  child.on('exit', (code) => { exited = true; exitCode = code })

  try {
    await waitForHealth(port, () => ({ exited, code: exitCode }))
  } catch (err) {
    fs.closeSync(logFd)
    console.error(`[e2e-harness] 启动失败，后端日志尾部：\n${tail(logPath)}`)
    throw err
  }

  const token = await readToken(dataDir)
  process.env.SIYI_E2E_WS_URL = `ws://127.0.0.1:${port}/ws`
  process.env.SIYI_E2E_WS_TOKEN = token
  process.env.SIYI_E2E_DATA_DIR = dataDir
  console.log(`[e2e-harness] 后端就绪 -> ${process.env.SIYI_E2E_WS_URL}（token ${token ? '已从 auth_token 文件取得' : '为空(loopback 免 token 仍可用)'}）`)

  return async () => {
    const pid = child.pid
    try {
      if (!exited && pid) {
        if (process.platform === 'win32') {
          execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
        } else {
          child.kill()
        }
      }
    } catch {
      /* 已退出即视为清理成功 */
    }
    try { fs.closeSync(logFd) } catch { /* 可能已关闭 */ }
    try {
      // 日志留在删除前打印尾部，便于 CI 排障
      console.log(`[e2e-harness] teardown，后端日志尾部：\n${tail(logPath, 20)}`)
      fs.rmSync(dataDir, { recursive: true, force: true })
    } catch { /* 目录占用时忽略，OS 会回收 temp */ }
    console.log('[e2e-harness] 后端已终止，临时数据目录已清理')
  }
}

function tail(file: string, lines = 40): string {
  try {
    return fs.readFileSync(file, 'utf-8').split('\n').slice(-lines).join('\n')
  } catch {
    return '(无日志)'
  }
}
