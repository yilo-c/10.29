import { useState, useEffect, useCallback } from 'react'
import { haptic } from '../../../lib/haptic'
import SiyiNative from '../../../lib/native/siyiNative'
import type {
  BatteryStatus, StorageInfo, MemoryInfo, NetworkStatus,
  RootStatus, AccessibilityStatus,
} from '../../../lib/native/siyiNative'
import WordbookView from '../wordbook/WordbookView'
import TransferView from '../transfer/TransferView'
import InfoView from '../info/InfoView'
import ReferenceView from '../reference/ReferenceView'

/* ── 工具定义 ── */

type ToolKey = 'wordbook' | 'transfer' | 'info' | 'reference' | 'system'

interface ToolEntry {
  key: ToolKey
  name: string
  desc: string
  emoji: string
  accent: string
}

/**
 * 工具清单。
 *
 * 前四项是原版 ToolsView 的既有功能（本文件在仓库上传时丢失，
 * 导致 WordbookView / TransferView / InfoView / ReferenceView 无法进入）。
 * 第五项「系统」是本次新增：暴露手机原生权限与设备状态，也是 AI 自动化能力的开关入口。
 */
const TOOLS: ToolEntry[] = [
  { key: 'wordbook',   name: '词库',   desc: '单词本与听写复习',       emoji: '📖', accent: '#0A84FF' },
  { key: 'transfer',   name: '传送',   desc: '与桌面端互传内容',       emoji: '📤', accent: '#34C759' },
  { key: 'info',       name: '资讯',   desc: '技术资讯与 AI 简报',     emoji: '📰', accent: '#FF9F0A' },
  { key: 'reference',  name: '参考',   desc: '日历、黄历与指南针',     emoji: '🧭', accent: '#AF52DE' },
  { key: 'system',     name: '系统',   desc: '设备状态与权限管理',     emoji: '⚙️', accent: '#8E8E93' },
]

/* ── 系统页小组件 ── */

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
    <div style={{ flex: 1, minWidth: 0, padding: 'var(--space-3)', background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)' }}>
      <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 'var(--text-title-3)', fontWeight: 600, color, lineHeight: 1.15 }}>{value}</div>
      {hint && <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginTop: 2 }}>{hint}</div>}
    </div>
  )
}

function PermissionRow({ title, desc, status, tone, action, onAction }: {
  title: string
  desc: string
  status: string
  tone: 'ok' | 'warn' | 'bad'
  action?: string
  onAction?: () => void
}) {
  const toneColor = tone === 'ok' ? 'var(--tone-smooth)' : tone === 'bad' ? 'var(--danger)' : 'var(--tone-caution)'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
      padding: 'var(--space-3) var(--space-4)', background: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-md)', minHeight: 'var(--row-min-height)',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 'var(--text-subhead)', fontWeight: 500, color: 'var(--text-primary)' }}>{title}</div>
        <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2, lineHeight: 1.35 }}>{desc}</div>
      </div>
      <span style={{
        flexShrink: 0, fontSize: 'var(--text-caption-2)', fontWeight: 600,
        color: toneColor, padding: '3px 8px', background: 'var(--bg-glass)', borderRadius: 'var(--radius-full)',
      }}>{status}</span>
      {action && onAction && (
        <button onClick={onAction} style={{
          flexShrink: 0, padding: '5px 10px', border: '0.5px solid var(--border-default)',
          borderRadius: 'var(--radius-sm)', background: 'transparent',
          color: 'var(--accent)', fontSize: 'var(--text-caption-1)', fontWeight: 600, cursor: 'pointer',
        }}>{action}</button>
      )}
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 'var(--text-caption-1)', fontWeight: 600, color: 'var(--text-tertiary)',
      textTransform: 'uppercase', letterSpacing: '0.6px',
      padding: 'var(--space-5) var(--space-1) var(--space-2)',
    }}>{children}</div>
  )
}

/* ── 系统页 ── */

function SystemPanel() {
  const [battery, setBattery] = useState<BatteryStatus | null>(null)
  const [storage, setStorage] = useState<StorageInfo | null>(null)
  const [memory, setMemory] = useState<MemoryInfo | null>(null)
  const [network, setNetwork] = useState<NetworkStatus | null>(null)
  const [root, setRoot] = useState<RootStatus | null>(null)
  const [a11y, setA11y] = useState<AccessibilityStatus | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    const safe = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
      try { return await fn() } catch { return null }
    }
    const [b, s, m, n, r, a] = await Promise.all([
      safe(() => SiyiNative.getBatteryStatus()),
      safe(() => SiyiNative.getStorageInfo()),
      safe(() => SiyiNative.getMemoryInfo()),
      safe(() => SiyiNative.getNetworkStatus()),
      safe(() => SiyiNative.isRootAvailable()),
      safe(() => SiyiNative.isAccessibilityRunning()),
    ])
    setBattery(b); setStorage(s); setMemory(m); setNetwork(n); setRoot(r); setA11y(a)
    setLoading(false)
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const b = battery?.result
  const st = storage?.result
  const mem = memory?.result
  const net = network?.result
  const a11yOn = a11y?.running === true
  const rootOn = root?.available === true
  const usedPct = st ? parseFloat(st.usedPercent) : null
  const memPct = mem && mem.totalMem > 0 ? (1 - mem.availMem / mem.totalMem) * 100 : null

  return (
    <div className="page-scroll">
      <div className="page-body">

        <SectionLabel>设备状态</SectionLabel>
        <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
          <MetricCard label="电量" value={b ? `${b.level}%` : '—'}
            hint={b?.isCharging ? '充电中' : undefined}
            tone={b && b.level <= 15 && !b.isCharging ? 'bad' : undefined} />
          <MetricCard label="网络"
            value={net ? (net.isWifi ? 'Wi-Fi' : net.isCellular ? '移动数据' : net.isConnected ? net.transport : '离线') : '—'}
            tone={net?.isConnected ? 'ok' : 'bad'} />
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-3)' }}>
          <MetricCard label="存储" value={usedPct != null ? `${usedPct.toFixed(0)}%` : '—'}
            hint={st ? '已用' : undefined}
            tone={usedPct != null && usedPct > 90 ? 'bad' : usedPct != null && usedPct > 80 ? 'warn' : undefined} />
          <MetricCard label="内存" value={memPct != null ? `${memPct.toFixed(0)}%` : '—'}
            hint={mem ? '占用' : undefined}
            tone={mem?.lowMemory ? 'bad' : undefined} />
        </div>

        <SectionLabel>权限与自动化</SectionLabel>
        <div className="stack">
          <PermissionRow
            title="无障碍服务"
            desc={a11yOn
              ? '已开启。AI 可读取屏幕内容并执行点击、滑动'
              : '未开启。开启后 AI 才能操作其它 App'}
            status={a11yOn ? '已授权' : '未授权'}
            tone={a11yOn ? 'ok' : 'warn'}
            action={a11yOn ? undefined : '去开启'}
            onAction={() => { haptic.light(); void SiyiNative.openAccessibilitySettings().catch(() => {}) }}
          />
          <PermissionRow
            title="Root 权限"
            desc={rootOn ? '已获取。可读取系统数据库与受限目录' : '未获取。小米便签读取等功能不可用'}
            status={rootOn ? '可用' : '不可用'}
            tone={rootOn ? 'ok' : 'bad'}
          />
        </div>

        <SectionLabel>说明</SectionLabel>
        <div style={{
          padding: 'var(--space-3) var(--space-4)', background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)', fontSize: 'var(--text-caption-1)',
          color: 'var(--text-secondary)', lineHeight: 1.6,
        }}>
          以上能力全部在设备本地执行，不经过网络。
          无障碍服务需要你在系统设置里手动开启，App 无法自行授权。
        </div>

        <button
          onClick={() => { haptic.light(); void refresh() }}
          disabled={loading}
          style={{
            width: '100%', marginTop: 'var(--space-5)', padding: 'var(--space-3)',
            border: '0.5px solid var(--border-default)', borderRadius: 'var(--radius-md)',
            background: 'transparent', color: 'var(--text-secondary)',
            fontSize: 'var(--text-footnote)', cursor: loading ? 'progress' : 'pointer',
          }}
        >{loading ? '刷新中…' : '刷新状态'}</button>
      </div>
    </div>
  )
}

/* ── 主视图 ── */

export default function ToolsView() {
  const [active, setActive] = useState<ToolKey | null>(null)

  /* 子页面：带返回按钮的 header + 对应视图 */
  if (active) {
    const entry = TOOLS.find(t => t.key === active)
    return (
      <div className="page">
        <header className="page-header">
          <button
            onClick={() => { haptic.light(); setActive(null) }}
            aria-label="返回"
            style={{
              width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: 'none', background: 'none', cursor: 'pointer',
              color: 'var(--accent)', fontSize: 20, marginLeft: -8, padding: 0,
            }}
          >‹</button>
          <div className="page-header__title" style={{ fontSize: 'var(--text-title-3)' }}>
            {entry?.name}
          </div>
        </header>

        {active === 'wordbook' && <WordbookView />}
        {active === 'transfer' && <TransferView />}
        {active === 'info' && <InfoView />}
        {active === 'reference' && <ReferenceView />}
        {active === 'system' && <SystemPanel />}
      </div>
    )
  }

  /* 工具列表：保持原版卡片布局，新增「系统」一项 */
  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header__title">工具</div>
      </header>

      <div className="page-scroll">
        <div className="page-body">
          <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)', marginBottom: 'var(--space-4)' }}>
            常用工具集合
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {TOOLS.map(tool => (
              <button
                key={tool.key}
                onClick={() => { haptic.light(); setActive(tool.key) }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
                  padding: 'var(--space-4)',
                  background: 'var(--bg-secondary)',
                  border: 'none', borderRadius: 'var(--radius-lg)',
                  cursor: 'pointer', textAlign: 'left', width: '100%',
                  minHeight: 72,
                }}
              >
                <span style={{
                  width: 44, height: 44, borderRadius: 12, flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: `${tool.accent}1F`, fontSize: 22,
                }} aria-hidden="true">{tool.emoji}</span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{
                    display: 'block', fontSize: 'var(--text-headline)',
                    fontWeight: 500, color: 'var(--text-primary)',
                  }}>{tool.name}</span>
                  <span style={{
                    display: 'block', fontSize: 'var(--text-footnote)',
                    color: 'var(--text-tertiary)', marginTop: 2,
                  }}>{tool.desc}</span>
                </span>
                <span style={{ color: 'var(--text-tertiary)', fontSize: 18, flexShrink: 0 }} aria-hidden="true">›</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
