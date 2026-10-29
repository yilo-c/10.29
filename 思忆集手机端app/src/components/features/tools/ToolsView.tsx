import { useState, useEffect, useCallback } from 'react'
import { haptic } from '../../../lib/haptic'
import SiyiNative from '../../../lib/native/siyiNative'
import type {
  BatteryStatus, StorageInfo, MemoryInfo, NetworkStatus,
  InstalledAppsResult, RootStatus, AccessibilityStatus,
} from '../../../lib/native/siyiNative'

/* ── 类型 ── */

interface DeviceSnapshot {
  battery: BatteryStatus | null
  storage: StorageInfo | null
  memory: MemoryInfo | null
  network: NetworkStatus | null
  root: RootStatus | null
  accessibility: AccessibilityStatus | null
  apps: InstalledAppsResult | null
}

type SectionKey = 'device' | 'automation' | 'data'

/* ── 工具函数 ── */

function fmtBytes(n: number | undefined): string {
  if (n == null || isNaN(n)) return '—'
  const gb = n / 1024 / 1024 / 1024
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(n / 1024 / 1024).toFixed(0)} MB`
}

/* ── 小组件 ── */

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 'var(--text-caption-1)',
      fontWeight: 'var(--font-weight-semibold)' as unknown as number,
      color: 'var(--text-tertiary)',
      textTransform: 'uppercase',
      letterSpacing: '0.6px',
      padding: 'var(--space-5) var(--space-1) var(--space-2)',
    }}>{children}</div>
  )
}

function MetricCard({ label, value, hint, tone }: {
  label: string
  value: string
  hint?: string
  tone?: 'ok' | 'warn' | 'bad'
}) {
  const color = tone === 'bad' ? 'var(--danger)'
    : tone === 'warn' ? 'var(--tone-caution)'
    : 'var(--text-primary)'
  return (
    <div className="card card--flat" style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 'var(--text-title-3)', fontWeight: 'var(--font-weight-semibold)' as unknown as number, color, lineHeight: 1.15 }}>
        {value}
      </div>
      {hint && (
        <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginTop: 2 }}>
          {hint}
        </div>
      )}
    </div>
  )
}

function ActionRow({ title, description, status, statusTone, onClick, busy }: {
  title: string
  description: string
  status?: string
  statusTone?: 'ok' | 'warn' | 'bad'
  onClick: () => void
  busy?: boolean
}) {
  const toneColor = statusTone === 'ok' ? 'var(--tone-smooth)'
    : statusTone === 'bad' ? 'var(--danger)'
    : 'var(--tone-caution)'
  return (
    <button
      onClick={onClick}
      disabled={busy}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-3)',
        padding: 'var(--space-3) var(--space-4)',
        background: 'var(--bg-secondary)',
        border: 'none',
        borderRadius: 'var(--radius-md)',
        textAlign: 'left',
        cursor: busy ? 'progress' : 'pointer',
        opacity: busy ? 0.6 : 1,
        minHeight: 'var(--row-min-height)',
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 'var(--text-subhead)',
          fontWeight: 'var(--font-weight-medium)' as unknown as number,
          color: 'var(--text-primary)',
        }}>{title}</div>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginTop: 2,
          lineHeight: 1.35,
        }}>{description}</div>
      </div>
      {status && (
        <span style={{
          flexShrink: 0,
          fontSize: 'var(--text-caption-2)',
          fontWeight: 'var(--font-weight-semibold)' as unknown as number,
          color: toneColor,
          padding: '3px 8px',
          background: 'var(--bg-glass)',
          borderRadius: 'var(--radius-full)',
        }}>{status}</span>
      )}
    </button>
  )
}

/* ── 主视图 ── */

export default function ToolsView() {
  const [snap, setSnap] = useState<DeviceSnapshot>({
    battery: null, storage: null, memory: null,
    network: null, root: null, accessibility: null, apps: null,
  })
  const [loading, setLoading] = useState(false)
  const [appsBusy, setAppsBusy] = useState(false)
  const [section, setSection] = useState<SectionKey>('device')

  /**
   * 统一刷新设备快照。
   * 每个调用独立 catch，单个能力不可用（比如没装无障碍服务）不应该
   * 拖垮整个面板 —— 手机端能力可用性差异很大，必须逐项降级。
   */
  const refresh = useCallback(async () => {
    setLoading(true)
    const safe = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
      try { return await fn() } catch { return null }
    }
    const [battery, storage, memory, network, root, accessibility] = await Promise.all([
      safe(() => SiyiNative.getBatteryStatus()),
      safe(() => SiyiNative.getStorageInfo()),
      safe(() => SiyiNative.getMemoryInfo()),
      safe(() => SiyiNative.getNetworkStatus()),
      safe(() => SiyiNative.isRootAvailable()),
      safe(() => SiyiNative.isAccessibilityRunning()),
    ])
    setSnap(s => ({ ...s, battery, storage, memory, network, root, accessibility }))
    setLoading(false)
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const loadApps = useCallback(async () => {
    setAppsBusy(true)
    try {
      const apps = await SiyiNative.getInstalledApps({ limit: 200 })
      setSnap(s => ({ ...s, apps }))
    } catch {
      setSnap(s => ({ ...s, apps: null }))
    }
    setAppsBusy(false)
  }, [])

  const openAccessibility = useCallback(async () => {
    haptic.light()
    try { await SiyiNative.openAccessibilitySettings() } catch { /* 用户可能取消 */ }
  }, [])

  const b = snap.battery?.result
  const st = snap.storage?.result
  const mem = snap.memory?.result
  const net = snap.network?.result
  const a11yOn = snap.accessibility?.running === true
  const rootOn = snap.root?.available === true

  const usedPercent = st ? parseFloat(st.usedPercent) : null
  const memUsedRatio = mem && mem.totalMem > 0
    ? (1 - mem.availMem / mem.totalMem) * 100
    : null

  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header__title">工具</div>
        <div className="page-header__spacer" />
        <button
          className={`tool-btn${loading ? ' tool-btn--active' : ''}`}
          onClick={() => { haptic.light(); void refresh() }}
          disabled={loading}
        >
          {loading ? '刷新中…' : '刷新'}
        </button>
      </header>

      <div className="toolbar">
        {([
          ['device', '设备'],
          ['automation', '自动化'],
          ['data', '数据'],
        ] as [SectionKey, string][]).map(([k, label]) => (
          <button
            key={k}
            className={`tool-btn${section === k ? ' tool-btn--active' : ''}`}
            onClick={() => { haptic.light(); setSection(k) }}
          >{label}</button>
        ))}
      </div>

      <div className="page-scroll">
        <div className="page-body">

          {section === 'device' && (
            <>
              <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
                <MetricCard
                  label="电量"
                  value={b ? `${b.level}%` : '—'}
                  hint={b?.isCharging ? '充电中' : b ? '未充电' : undefined}
                  tone={b && b.level <= 15 && !b.isCharging ? 'bad' : undefined}
                />
                <MetricCard
                  label="网络"
                  value={net ? (net.isWifi ? 'Wi-Fi' : net.isCellular ? '移动数据' : net.isConnected ? net.transport : '离线') : '—'}
                  tone={net?.isConnected ? 'ok' : 'bad'}
                  hint={net?.isConnected ? '已连接' : '未连接'}
                />
              </div>

              <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-3)' }}>
                <MetricCard
                  label="存储已用"
                  value={usedPercent != null ? `${usedPercent.toFixed(0)}%` : '—'}
                  hint={st ? `${fmtBytes(st.availableBytes)} 可用` : undefined}
                  tone={usedPercent != null && usedPercent > 90 ? 'bad' : usedPercent != null && usedPercent > 80 ? 'warn' : undefined}
                />
                <MetricCard
                  label="内存占用"
                  value={memUsedRatio != null ? `${memUsedRatio.toFixed(0)}%` : '—'}
                  hint={mem ? `${fmtBytes(mem.availMem)} 可用` : undefined}
                  tone={mem?.lowMemory ? 'bad' : undefined}
                />
              </div>

              <SectionTitle>系统</SectionTitle>
              <div className="stack">
                <ActionRow
                  title="无障碍服务"
                  description={a11yOn
                    ? '已开启，AI 可以读取屏幕并执行点击、滑动'
                    : '未开启。开启后 AI 才能操作其它 App'}
                  status={a11yOn ? '已开启' : '未开启'}
                  statusTone={a11yOn ? 'ok' : 'warn'}
                  onClick={openAccessibility}
                />
                <ActionRow
                  title="Root 权限"
                  description={rootOn ? '已获取，可读取系统数据库' : '未获取，小米便签读取等功能不可用'}
                  status={rootOn ? '可用' : '不可用'}
                  statusTone={rootOn ? 'ok' : 'bad'}
                  onClick={() => { haptic.light(); void refresh() }}
                />
              </div>
            </>
          )}

          {section === 'automation' && (
            <>
              <SectionTitle>AI 自动化能力</SectionTitle>
              <div className="stack">
                <ActionRow
                  title="读取屏幕内容"
                  description="让 AI 看到当前界面上的文字，需要无障碍服务"
                  status={a11yOn ? '就绪' : '需授权'}
                  statusTone={a11yOn ? 'ok' : 'warn'}
                  onClick={openAccessibility}
                />
                <ActionRow
                  title="模拟点击与滑动"
                  description="AI 可代你点击、滑动、返回，需要无障碍服务"
                  status={a11yOn ? '就绪' : '需授权'}
                  statusTone={a11yOn ? 'ok' : 'warn'}
                  onClick={openAccessibility}
                />
                <ActionRow
                  title="启动指定 App"
                  description="通过包名直接打开应用"
                  onClick={async () => {
                    haptic.light()
                    if (!snap.apps) { await loadApps() }
                  }}
                  busy={appsBusy}
                  status="查看列表"
                  statusTone="ok"
                />
              </div>

              {snap.apps?.result && (
                <>
                  <SectionTitle>已安装应用（{snap.apps.result.total}）</SectionTitle>
                  <div className="stack" style={{ maxHeight: 340, overflowY: 'auto' }}>
                    {snap.apps.result.apps.slice(0, 60).map(app => (
                      <button
                        key={app.packageName}
                        onClick={async () => {
                          haptic.light()
                          try { await SiyiNative.launchApp({ packageName: app.packageName }) } catch { /* ignore */ }
                        }}
                        style={{
                          display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                          gap: 2, padding: 'var(--space-3)',
                          background: 'var(--bg-secondary)', border: 'none',
                          borderRadius: 'var(--radius-sm)', cursor: 'pointer', textAlign: 'left',
                        }}
                      >
                        <span style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-primary)' }}>{app.name}</span>
                        <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>{app.packageName}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}

          {section === 'data' && (
            <>
              <SectionTitle>数据来源</SectionTitle>
              <div className="stack">
                <ActionRow
                  title="小米便签"
                  description={rootOn
                    ? '可通过 Root 读取系统便签数据库'
                    : '需要 Root 权限才能读取'}
                  status={rootOn ? '可读取' : '不可用'}
                  statusTone={rootOn ? 'ok' : 'bad'}
                  onClick={async () => {
                    haptic.light()
                    try { await SiyiNative.readRedmiNotes() } catch { /* ignore */ }
                  }}
                />
              </div>

              <SectionTitle>说明</SectionTitle>
              <div className="card card--flat" style={{
                fontSize: 'var(--text-caption-1)',
                color: 'var(--text-secondary)',
                lineHeight: 1.6,
              }}>
                这些能力由手机原生层提供，只在设备本地执行。
                标记为「需授权」的项目需要你在系统设置里手动开启对应权限。
              </div>
            </>
          )}

        </div>
      </div>
    </div>
  )
}
