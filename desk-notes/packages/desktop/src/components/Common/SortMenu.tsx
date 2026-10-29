import React, { useState, useRef, useEffect } from 'react'
import Icon from './Icon'


export interface SortOption {
  mode: string
  tKey: string
}

interface SortMenuProps {
  options: SortOption[]
  current: string
  asc: boolean
  onSelect: (mode: string) => void
  onToggleDir: () => void
  t: (key: string, params?: Record<string, string | number>) => string
  titleKey: string
}

const SortMenu: React.FC<SortMenuProps> = ({ options, current, asc, onSelect, onToggleDir, t, titleKey }) => {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div className="relative flex-shrink-0" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
        title={t(titleKey)}
      >
        <Icon icon="fa-solid fa-arrow-up-wide-short text-xs" />
      </button>
      {open && (
        <div
          className="absolute top-full left-1/2 -translate-x-1/2 mt-1 z-50 border border-white/[0.08] rounded-xl py-1 shadow-2xl min-w-[130px] backdrop-blur-xl"
          style={{ background: 'var(--panel-bg-solid)' }}
        >
          {options.map(opt => {
            const active = opt.mode === current
            return (
              <button
                key={opt.mode}
                onClick={() => {
                  if (active) onToggleDir()
                  else onSelect(opt.mode)
                  setOpen(false)
                }}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors text-left hover:bg-white/[0.04]"
                style={{ color: active ? 'var(--text-primary)' : 'var(--text-secondary)' }}
              >
                <span className="flex-1">{t(opt.tKey)}</span>
                {active && (
                  <Icon icon={`fa-solid ${asc ? 'fa-arrow-up' : 'fa-arrow-down'} text-2xs`} />
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default SortMenu
