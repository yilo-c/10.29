import { useState, useMemo } from 'react'
import { useStore } from '@desk-notes/shared'

/** Temporary placeholder — Phase 2 will replace with real data */
function computeAlmanac() {
  return {
    smooth: ['签约', '出行', '纳财'],
    neutral: ['入学', '嫁娶'],
    caution: ['动土', '安葬', '伐木'],
    chong: '猴',
    sha: '南',
  }
}

export default function AlmanacStrip() {
  const [expanded, setExpanded] = useState(false)
  const userProfile = useStore(s => s.userProfile)
  const hasProfile = !!userProfile?.birthDate

  const almanac = useMemo(() => {
    if (!hasProfile) return null
    return computeAlmanac()
  }, [hasProfile])

  if (!almanac) return null

  return (
    <div
      style={{
        margin: 'var(--space-2) var(--space-4) 0',
        borderRadius: 'var(--radius-sm)',
        background: 'var(--bg-glass)',
        border: '1px solid var(--border-subtle)',
        flexShrink: 0,
      }}
    >
      <button
        onClick={() => setExpanded(v => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          width: '100%',
          padding: '6px 12px',
          border: 'none',
          background: 'none',
          color: 'var(--text-secondary)',
          fontSize: 'var(--text-tiny)',
          cursor: 'pointer',
          gap: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
          <span style={{ color: 'var(--tone-smooth)', whiteSpace: 'nowrap' }}>较顺利</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {almanac.smooth.slice(0, 3).join('·')}
          </span>
          {!expanded && (
            <>
              <span style={{ color: 'var(--text-disabled)' }}>·</span>
              <span style={{ color: 'var(--tone-caution)', whiteSpace: 'nowrap' }}>宜谨慎</span>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {almanac.caution.slice(0, 2).join('·')}
              </span>
            </>
          )}
        </div>
        <span style={{ fontSize: 10, color: 'var(--text-tertiary)', flexShrink: 0 }}>
          {expanded ? '收起' : '展开'}
        </span>
      </button>

      {expanded && (
        <div
          style={{
            padding: '6px 12px 10px',
            borderTop: '1px solid var(--border-subtle)',
            fontSize: 'var(--text-tiny)',
            color: 'var(--text-tertiary)',
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
          }}
        >
          <div><span style={{ color: 'var(--tone-smooth)' }}>较顺利</span> {almanac.smooth.join(' · ')}</div>
          <div><span style={{ color: 'var(--tone-neutral)' }}>平日</span> {almanac.neutral.join(' · ')}</div>
          <div><span style={{ color: 'var(--tone-caution)' }}>宜谨慎</span> {almanac.caution.join(' · ')}</div>
          <div style={{ color: 'var(--text-disabled)' }}>冲{almanac.chong} · 煞{almanac.sha}</div>
          <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 2 }}>
            ℹ️ 依据传统历法，仅供决策参考
          </div>
        </div>
      )}
    </div>
  )
}
