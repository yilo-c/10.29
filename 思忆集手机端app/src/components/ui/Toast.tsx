import { useState, useRef, useCallback, useEffect } from 'react'

export interface ToastData {
  message: string
  action?: { label: string; onClick: () => void }
  duration?: number
}

let _setToast: ((data: ToastData | null) => void) | null = null

export function showToast(data: ToastData) {
  _setToast?.(data)
}

export default function Toast() {
  const [data, setData] = useState<ToastData | null>(null)
  const [leaving, setLeaving] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const hide = useCallback(() => {
    setLeaving(true)
    setTimeout(() => { setData(null); setLeaving(false) }, 200)
  }, [])

  _setToast = useCallback((d: ToastData | null) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (!d) { hide(); return }
    setData(d)
    setLeaving(false)
    timerRef.current = setTimeout(hide, d.duration ?? 3000)
  }, [hide])

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  if (!data) return null

  return (
    <div style={{
      position: 'fixed',
      bottom: 'calc(90px + env(safe-area-inset-bottom, 0px))',
      left: 20, right: 20,
      display: 'flex', justifyContent: 'center',
      zIndex: 2000,
      pointerEvents: 'none',
    }}>
      <div style={{
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-md)',
        padding: '10px 16px',
        display: 'flex', alignItems: 'center', gap: 12,
        pointerEvents: 'auto',
        fontSize: 'var(--text-footnote)',
        color: 'var(--text-secondary)',
        boxShadow: 'var(--shadow-md)',
        animation: `${leaving ? 'fadeOutDown' : 'fadeInUp'} 0.2s ease`,
      }}>
        <span>{data.message}</span>
        {data.action && (
          <button
            onClick={() => { data.action!.onClick(); hide() }}
            style={{
              background: 'none', border: 'none',
              color: 'var(--accent)', fontWeight: 600,
              cursor: 'pointer', fontSize: 'var(--text-footnote)',
              padding: '4px 8px', whiteSpace: 'nowrap',
            }}
          >
            {data.action.label}
          </button>
        )}
      </div>
    </div>
  )
}
