import { useState, useEffect, useCallback } from 'react'
import { haptic } from '../../../lib/haptic'
import SiyiNative from '../../../lib/native/siyiNative'
import type { RootStatus, AccessibilityStatus } from '../../../lib/native/siyiNative'
import {
  IconBook, IconSend, IconGlobe, IconCompass, IconSettings,
  IconChevronRight,
} from '../../ui'
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
  Icon: React.FC<{ size?: number; accent?: boolean }>
  accent: string
}

/**
 * 工具清单。
 *
 * 2026-10-30 改版（用户反馈「图标像老式电脑图标、功能堆得杂乱」）：
 *   1. emoji（📖📤📰🧭⚙️）全部换成统一的线性图标 —— emoji 在不同
 *      ROM 上字形差异极大，且与 App 内其它图标风格割裂，是「老气」的主因之一。
 *   2. 长按/点击区域统一，箭头改用 IconChevronRight 而不是字符 ›。
 *   3. 系统页不再罗列电量/存储/内存等系统信息 —— 这些系统下拉栏里都有，
 *      放进 App 只是噪音。只保留「AI 能否操作手机」真正相关的授权开关。
 */
const TOOLS: ToolEntry[] = [
  { key: 'wordbook',  name: '词库',  desc: '单词本与听写复习',   Icon: IconBook,     accent: '#0A84FF' },
  { key: 'transfer',  name: '传送',  desc: '与桌面端互传内容',   Icon: IconSend,     accent: '#34C759' },
  { key: 'info',      name: '资讯',  desc: '技术资讯与 AI 简报', Icon: IconGlobe,    accent: '#FF9F0A' },
  { key: 'reference', name: '参考',  desc: '日历、黄历与指南针', Icon: IconCompass,  accent: '#AF52DE' },
  { key: 'system',    name: '授权',  desc: 'AI 操作手机的权限',  Icon: IconSettings, accent: '#8E8E93' },
]

/* ── 授权页小组件 ── */

/** 权限行：单一开关语义，不用卡片堆叠 */
function PermissionRow({ title, desc, on, onAction, actionLabel, tone }: {
  title: string
  desc: string
  on: boolean
  onAction?: () => void
  actionLabel?: string
  tone?: 'ok' | 'warn' | 'bad'
}) {
  const dotColor = tone === 'ok' ? 'var(--tone-smooth)'
    : tone === 'bad' ? 'var(--danger)'
    : on ? 'var(--tone-smooth)' : 'var(--tone-caution)'
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
      padding: '14px 0',
      borderBottom: '0.5px solid var(--border-subtle)',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6,
          fontSize: 'var(--text-subhead)', fontWeight: 500,
          color: 'var(--text-primary)',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: dotColor, flexShrink: 0,
          }} />
          {title}
        </div>
        <div style={{
          fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
          marginTop: 3, lineHeight: 1.45,
        }}>{desc}</div>
      </div>
      {onAction && (
        <button
          onClick={onAction}
          style={{
            padding: '6px 13px', borderRadius: 999,
            border: '0.5px solid var(--accent-border)',
            background: 'var(--accent-surface)', color: 'var(--accent)',
            fontSize: 'var(--text-caption-1)', fontWeight: 600,
            cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
          }}
        >{actionLabel ?? '去开启'}</button>
      )}
    </div>
  )
}

/** 授权面板：只保留与「AI 能否操作手机」直接相关的项 */
function SystemPanel() {
  const [root, setRoot] = useState<RootStatus | null>(null)
  const [a11y, setA11y] = useState<AccessibilityStatus | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    const safe = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
      try { return await fn() } catch { return null }
    }
    const [r, a] = await Promise.all([
      // 方法名以 lib/native/siyiNative.ts 的真实签名 为准（不是 getXxxStatus）
      safe(() => SiyiNative.isRootAvailable()),
      safe(() => SiyiNative.isAccessibilityRunning()),
    ])
    setRoot(r); setA11y(a); setLoading(false)
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const a11yOn = a11y?.running === true
  const rootOn = root?.available === true

  return (
    <div className="page-scroll">
      <div className="page-body">
        <div style={{
          fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
          lineHeight: 1.55, marginBottom: 'var(--space-4)',
        }}>
          这两项决定 AI 能否代替你操作手机。全部在本机执行，不经过网络。
        </div>

        <div style={{
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-lg)',
          padding: '0 14px',
        }}>
          <PermissionRow
            title="无障碍服务"
            desc={a11yOn
              ? '已开启，AI 可读取屏幕并执行点击、滑动'
              : '未开启。开启后 AI 才能操作其它 App'}
            on={a11yOn}
            tone={a11yOn ? 'ok' : 'warn'}
            onAction={a11yOn ? undefined : () => {
              haptic.light()
              void SiyiNative.openAccessibilitySettings().catch(() => {})
            }}
          />
          <div style={{ borderBottom: 'none' }}>
            <PermissionRow
              title="Root 权限"
              desc={rootOn
                ? '已获取，可读取系统数据库与受限目录'
                : '未获取。小米便签读取等功能不可用'}
              on={rootOn}
              tone={rootOn ? 'ok' : 'bad'}
            />
          </div>
        </div>

        <button
          onClick={() => { haptic.light(); void refresh() }}
          disabled={loading}
          style={{
            width: '100%', marginTop: 'var(--space-5)', padding: '12px',
            border: '0.5px solid var(--border-default)', borderRadius: 'var(--radius-md)',
            background: 'transparent', color: 'var(--text-secondary)',
            fontSize: 'var(--text-footnote)', cursor: loading ? 'progress' : 'pointer',
          }}
        >{loading ? '检测中…' : '重新检测'}</button>
      </div>
    </div>
  )
}

/* ── 主视图 ── */

export default function ToolsView() {
  const [active, setActive] = useState<ToolKey | null>(null)

  if (active) {
    const entry = TOOLS.find(t => t.key === active)
    return (
      <div className="page">
        <header className="page-header">
          <button
            onClick={() => { haptic.light(); setActive(null) }}
            aria-label="返回"
            style={{
              width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: 'none', background: 'none', cursor: 'pointer',
              color: 'var(--accent)', marginLeft: -8, padding: 0,
            }}
          >
            <IconChevronRight size={22} />
          </button>
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

  return (
    <div className="page">
      <header className="page-header">
        <div className="page-header__title">工具</div>
      </header>

      <div className="page-scroll">
        <div className="page-body">
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {TOOLS.map((tool, i) => {
              const Icon = tool.Icon
              return (
                <button
                  key={tool.key}
                  onClick={() => { haptic.light(); setActive(tool.key) }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 14,
                    padding: '15px 4px',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: i < TOOLS.length - 1 ? '0.5px solid var(--border-subtle)' : 'none',
                    cursor: 'pointer', textAlign: 'left', width: '100%',
                  }}
                >
                  {/* 图标：圆角方形淡色底 + 单色线性图标，与全局图标系统一致 */}
                  <span style={{
                    width: 38, height: 38, borderRadius: 11, flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: `${tool.accent}14`,
                    color: tool.accent,
                  }} aria-hidden="true">
                    <Icon size={21} />
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{
                      display: 'block', fontSize: 'var(--text-subhead)',
                      fontWeight: 550, color: 'var(--text-primary)',
                    }}>{tool.name}</span>
                    <span style={{
                      display: 'block', fontSize: 'var(--text-caption-1)',
                      color: 'var(--text-tertiary)', marginTop: 2,
                    }}>{tool.desc}</span>
                  </span>
                  <span style={{ color: 'var(--text-tertiary)', flexShrink: 0, opacity: 0.55 }} aria-hidden="true">
                    <IconChevronRight size={17} />
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
