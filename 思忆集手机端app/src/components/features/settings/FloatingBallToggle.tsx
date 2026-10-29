import { useState, useEffect, useCallback } from 'react'
import { floatingBall } from '../../../lib/floatingBall'

/**
 * 悬浮球开关。
 *
 * 修复的 UX 问题：
 *  1. 原实现权限被拒时调 `alert()` —— Capacitor WebView 里 alert 常常不显示，
 *     用户点了开关毫无反应，以为坏了。改为页面内提示。
 *  2. 原实现不区分「权限被拒」和「原生插件不可用」（浏览器预览时），
 *     两种情况都提示「去系统设置授权」，误导用户。现在分开说明。
 *  3. 增加「重试」入口：用户去系统设置授权后返回，无需退出重进。
 */
export default function FloatingBallToggle() {
  const [enabled, setEnabled] = useState(false)
  const [activeMood, setActiveMood] = useState('idle')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ kind: 'warn' | 'info'; text: string } | null>(null)

  useEffect(() => {
    const unsub = floatingBall.onChange(s => {
      setEnabled(s.visible)
      setActiveMood(s.mood)
    })
    void floatingBall.init()
    return unsub
  }, [])

  const toggle = useCallback(async () => {
    if (busy) return
    setBusy(true)
    setNotice(null)
    try {
      if (enabled) {
        await floatingBall.hide()
      } else {
        // 先检查权限，避免无谓地弹系统设置
        const granted = await floatingBall.refreshPermission()
        if (!granted) {
          const res = await floatingBall.show()
          if (!res.ok) {
            if (res.reason === 'unavailable') {
              setNotice({
                kind: 'info',
                text: '当前环境不支持悬浮球（浏览器预览模式）。安装 APK 后即可使用。',
              })
            } else {
              setNotice({
                kind: 'warn',
                text: '需要「显示在其他应用上层」权限。请在系统设置中允许后回来重试。',
              })
            }
            return
          }
        } else {
          const res = await floatingBall.show()
          if (!res.ok) {
            setNotice({ kind: 'warn', text: '悬浮球启动失败，请稍后重试。' })
          }
        }
      }
    } finally {
      setBusy(false)
    }
  }, [enabled, busy])

  const retry = useCallback(async () => {
    setBusy(true)
    const granted = await floatingBall.refreshPermission()
    setBusy(false)
    if (granted) {
      setNotice(null)
      await floatingBall.show()
    } else {
      setNotice({ kind: 'warn', text: '仍未获得权限，请确认已在系统设置中开启「显示在其他应用上层」。' })
    }
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        {enabled && (
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {(['idle', 'happy', 'thinking', 'alert'] as const).map(m => (
              <button
                key={m}
                onClick={() => { void floatingBall.setMood(m) }}
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
          onClick={() => { void toggle() }}
          disabled={busy}
          aria-label={enabled ? '关闭悬浮球' : '开启悬浮球'}
          role="switch"
          aria-checked={enabled}
          style={{
            marginLeft: 'auto',
            width: 44, height: 28, borderRadius: 14,
            border: 'none', cursor: busy ? 'progress' : 'pointer', position: 'relative',
            background: enabled ? 'var(--accent)' : 'var(--bg-tertiary)',
            transition: 'background 0.2s ease',
            opacity: busy ? 0.6 : 1,
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

      {notice && (
        <div style={{
          padding: 'var(--space-2) var(--space-3)',
          borderRadius: 'var(--radius-sm)',
          background: notice.kind === 'warn' ? 'rgba(255,159,10,0.12)' : 'var(--bg-glass)',
          color: notice.kind === 'warn' ? 'var(--tone-caution)' : 'var(--text-secondary)',
          fontSize: 'var(--text-caption-1)',
          lineHeight: 1.5,
          display: 'flex',
          alignItems: 'flex-start',
          gap: 8,
        }}>
          <span style={{ flex: 1 }}>{notice.text}</span>
          {notice.kind === 'warn' && (
            <button
              onClick={() => { void retry() }}
              style={{
                flexShrink: 0, padding: '2px 8px',
                border: '0.5px solid currentColor', borderRadius: 'var(--radius-sm)',
                background: 'transparent', color: 'inherit',
                fontSize: 'var(--text-caption-2)', fontWeight: 600, cursor: 'pointer',
              }}
            >重试</button>
          )}
        </div>
      )}
    </div>
  )
}

const MOOD_COLORS: Record<string, string> = {
  idle: '#22C55E',
  happy: '#3B82F6',
  thinking: '#F59E0B',
  alert: '#EF4444',
}
