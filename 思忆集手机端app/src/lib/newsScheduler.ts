import { LocalNotifications } from '@capacitor/local-notifications'

/**
 * 资讯定时抓取调度 / Scheduled news fetch
 *
 * 需求场景（用户原话）：
 *   「在资讯页面每天定时准点抓取我偏好的新闻」
 *
 * 实现方式：
 *   用 Capacitor LocalNotifications 注册每日重复通知，到点唤醒；
 *   App 在前台时由监听器直接触发抓取；不在前台时通知栏提醒用户。
 *
 * 为什么不用后台任务：
 *   Android 后台限制严格，长期后台抓取需要前台服务 + 常驻通知，
 *   耗电且小米的省电策略会杀进程。用「到点通知 → 用户点开 → 抓取」
 *   是更务实且省电的方案，可达性也更高。
 *
 * 配置持久化在 localStorage，跨启动保留。
 */

const CONFIG_KEY = 'siyi-news-schedule'
const NOTIF_ID_BASE = 8800

export interface NewsScheduleConfig {
  enabled: boolean
  /** HH:MM 24 小时制 */
  time: string
  /** 只抓这些源；空=全部 */
  sourceIds: string[]
  /** 只抓这些区域 */
  regions: ('cn' | 'intl' | 'tech')[]
  /** 每天抓几次（早/晚），简化版只支持单次 */
  lastRunDate?: string
}

export const DEFAULT_SCHEDULE: NewsScheduleConfig = {
  enabled: false,
  time: '08:00',
  sourceIds: [],
  regions: [],
}

export function loadSchedule(): NewsScheduleConfig {
  try {
    const raw = localStorage.getItem(CONFIG_KEY)
    if (!raw) return { ...DEFAULT_SCHEDULE }
    const parsed = JSON.parse(raw) as Partial<NewsScheduleConfig>
    return {
      enabled: !!parsed.enabled,
      time: typeof parsed.time === 'string' && /^\d{2}:\d{2}$/.test(parsed.time) ? parsed.time : '08:00',
      sourceIds: Array.isArray(parsed.sourceIds) ? parsed.sourceIds.filter(x => typeof x === 'string') : [],
      regions: Array.isArray(parsed.regions) ? parsed.regions.filter(x => ['cn', 'intl', 'tech'].includes(x)) : [],
      lastRunDate: typeof parsed.lastRunDate === 'string' ? parsed.lastRunDate : undefined,
    }
  } catch {
    return { ...DEFAULT_SCHEDULE }
  }
}

export function saveSchedule(cfg: NewsScheduleConfig): void {
  try {
    localStorage.setItem(CONFIG_KEY, JSON.stringify(cfg))
  } catch { /* 存储满则放弃 */ }
}

function parseHM(hm: string): { h: number; m: number } {
  const [h, m] = hm.split(':').map(x => parseInt(x, 10))
  return {
    h: Number.isFinite(h) && h >= 0 && h <= 23 ? h : 8,
    m: Number.isFinite(m) && m >= 0 && m <= 59 ? m : 0,
  }
}

/** 申请通知权限（首次开启时需要） */
export async function ensureNotificationPermission(): Promise<boolean> {
  try {
    const cur = await LocalNotifications.checkPermissions()
    if (cur.display === 'granted') return true
    const req = await LocalNotifications.requestPermissions()
    return req.display === 'granted'
  } catch {
    return false
  }
}

/**
 * 注册每日定时通知。
 *
 * 用 `on: { hour, minute }` + `repeats: true`，Android 侧由系统托管，
 * 即使 App 被划掉也能在到点时弹出通知。
 */
export async function scheduleDailyFetch(cfg: NewsScheduleConfig): Promise<{ ok: boolean; error?: string }> {
  try {
    // 先清掉旧排期，避免重复堆积
    await cancelDailyFetch()

    if (!cfg.enabled) return { ok: true }

    const granted = await ensureNotificationPermission()
    if (!granted) {
      return { ok: false, error: '通知权限未授予，无法定时提醒。请在系统设置中允许通知。' }
    }

    const { h, m } = parseHM(cfg.time)
    await LocalNotifications.schedule({
      notifications: [{
        id: NOTIF_ID_BASE,
        title: '📰 今日资讯',
        body: '点开查看为你抓取的最新资讯',
        schedule: { on: { hour: h, minute: m }, repeats: true, allowWhileIdle: true },
        smallIcon: 'ic_stat_icon_config_sample',
        channelId: 'siyi-news',
      }],
    })
    return { ok: true }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : '排期失败' }
  }
}

export async function cancelDailyFetch(): Promise<void> {
  try {
    await LocalNotifications.cancel({ notifications: [{ id: NOTIF_ID_BASE }] })
  } catch { /* 未注册过忽略 */ }
}

/**
 * 判断今天是否已抓过（避免打开 App 时重复抓取）。
 * 返回 true 表示应该抓。
 */
export function shouldFetchNow(cfg: NewsScheduleConfig): boolean {
  if (!cfg.enabled) return false
  const today = new Date().toISOString().slice(0, 10)
  if (cfg.lastRunDate === today) return false

  const { h, m } = parseHM(cfg.time)
  const now = new Date()
  const target = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m, 0, 0)
  // 到点之后才抓（允许提前 5 分钟内的容差）
  return now.getTime() >= target.getTime() - 5 * 60 * 1000
}

export function markFetchedToday(cfg: NewsScheduleConfig): NewsScheduleConfig {
  const next = { ...cfg, lastRunDate: new Date().toISOString().slice(0, 10) }
  saveSchedule(next)
  return next
}
