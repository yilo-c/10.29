import { useState, useMemo } from 'react'
import { getHuangli } from '@desk-notes/shared'
import { IconChevronDown } from '../../ui'

export default function AlmanacStrip() {
  const [expanded, setExpanded] = useState(false)

  const data = useMemo(() => getHuangli(new Date()), [])

  if (!data) return null

  const yi = data.yiJi.yi
  const ji = data.yiJi.ji

  return (
    <div style={{
      margin: '4px 20px 0',
      borderRadius: 'var(--radius-md)',
      background: 'var(--bg-secondary)',
      flexShrink: 0,
      overflow: 'hidden',
    }}>
      <button
        onClick={() => { setExpanded(v => !v); navigator.vibrate?.(6) }}
        style={{
          display: 'flex',
          alignItems: 'center',
          width: '100%',
          padding: '8px 12px',
          border: 'none',
          background: 'none',
          cursor: 'pointer',
          gap: 8,
          color: 'var(--text-secondary)',
          fontSize: 'var(--text-caption-1)',
        }}
      >
        <span style={{ color: 'var(--tone-smooth)', fontWeight: 500, flexShrink: 0 }}>宜</span>
        <span style={{
          flex: 1,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textAlign: 'left',
        }}>
          {yi.slice(0, 4).join(' · ')}
        </span>
        <span style={{
          flexShrink: 0,
          color: 'var(--text-tertiary)',
          display: 'flex',
          alignItems: 'center',
          transition: 'transform 0.2s ease',
          transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
        }}>
          <IconChevronDown size={14} />
        </span>
      </button>

      {expanded && (
        <div style={{
          padding: '0 12px 10px',
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-secondary)',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          animation: 'fadeInUp 0.15s ease',
        }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ color: 'var(--tone-smooth)', fontWeight: 500, width: 16, flexShrink: 0 }}>宜</span>
            <span style={{ lineHeight: 1.5 }}>{yi.join(' · ')}</span>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <span style={{ color: 'var(--tone-caution)', fontWeight: 500, width: 16, flexShrink: 0 }}>忌</span>
            <span style={{ lineHeight: 1.5 }}>{ji.join(' · ')}</span>
          </div>
          <div style={{
            display: 'flex',
            gap: 12,
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
            marginTop: 2,
          }}>
            <span>冲{data.chongSha.chong}</span>
            <span>煞{data.chongSha.sha}</span>
            <span>喜神{data.fangwei.xi}</span>
          </div>
          <div style={{
            fontSize: 10,
            color: 'var(--text-disabled)',
            marginTop: 2,
          }}>
            依据传统历法理论，仅供决策参考
          </div>
        </div>
      )}
    </div>
  )
}
