import { useState, useEffect } from 'react'
import { floatingBall } from '../../../lib/floatingBall'

export default function FloatingBallToggle() {
  const [enabled, setEnabled] = useState(false)
  const [activeMood, setActiveMood] = useState('idle')

  useEffect(() => {
    const unsub = floatingBall.onChange(s => {
      setEnabled(s.visible)
      setActiveMood(s.mood)
    })
    floatingBall.init()
    return unsub
  }, [])

  const toggle = async () => {
    if (enabled) {
      await floatingBall.hide()
    } else {
      const ok = await floatingBall.show()
      if (!ok) {
        // 权限被拒绝 — 引导用户
        alert('请在系统设置中允许「思忆便签」的「显示在其他应用上层」权限')
      }
    }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      {enabled && (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {(['idle', 'happy', 'thinking', 'alert'] as const).map(m => (
            <button
              key={m}
              onClick={() => floatingBall.setMood(m)}
              aria-label={`情绪: ${m}`}
              style={{
                width: 20, height: 20, borderRadius: '50%',
                border: activeMood === m ? '2px solid var(--accent)' : '2px solid transparent',
                background: MOOD_COLORS[m],
                cursor: 'pointer', padding: 0,
              }}
            />
          ))}
        </div>
      )}
      <button
        onClick={toggle}
        style={{
          marginLeft: 'auto',
          width: 44, height: 28, borderRadius: 14,
          border: 'none', cursor: 'pointer', position: 'relative',
          background: enabled ? 'var(--accent)' : 'var(--bg-tertiary)',
          transition: 'background 0.2s ease',
        }}
      >
        <div style={{
          position: 'absolute', top: 3,
          left: enabled ? 23 : 3,
          width: 22, height: 22, borderRadius: '50%',
          background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
          transition: 'left 0.2s ease',
        }} />
      </button>
    </div>
  )
}

const MOOD_COLORS: Record<string, string> = {
  idle: '#22C55E',
  happy: '#3B82F6',
  thinking: '#F59E0B',
  alert: '#EF4444',
}
