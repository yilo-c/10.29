import React, { useEffect, useRef } from 'react'
import Icon from './Icon'

import type { AIAction } from '../../utils/ai/types'

export interface SlashCommand {
  id: string
  label: string
  icon: string
  action: AIAction
}

interface SlashCommandMenuProps {
  x: number
  y: number
  commands: SlashCommand[]
  onSelect: (cmd: SlashCommand) => void
  onClose: () => void
}

const SlashCommandMenu: React.FC<SlashCommandMenuProps> = ({ x, y, commands, onSelect, onClose }) => {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  return (
    <div
      ref={menuRef}
      className="fixed z-[99999] rounded-xl border shadow-2xl py-1 min-w-[160px] overflow-hidden"
      style={{
        left: x, top: y,
        background: 'var(--panel-bg-solid, rgba(28,28,38,0.97))',
        borderColor: 'var(--border-color, rgba(255,255,255,0.08))',
      }}
    >
      {commands.map(cmd => (
        <button
          key={cmd.id}
          onClick={() => { onSelect(cmd) }}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors hover:bg-white/[0.06] text-left"
          style={{ color: 'var(--text-primary, rgba(255,255,255,0.85))' }}
        >
          <Icon icon={`${cmd.icon} text-2xs w-4 text-center`} style={{ color: 'var(--fluent-blue, var(--brand))' }} />
          {cmd.label}
        </button>
      ))}
    </div>
  )
}

export default SlashCommandMenu
