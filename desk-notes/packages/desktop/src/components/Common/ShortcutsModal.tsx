import React, { useEffect, useRef } from 'react'
import Icon from './Icon'

import { useTranslation } from '../../i18n'
import { listShortcuts, formatComboDisplay } from '../../utils/shortcutRegistry'

interface ShortcutGroup {
  title: string
  items: { keys: string; desc: string }[]
}

interface Props {
  isOpen: boolean
  onClose: () => void
}

const ShortcutsModal: React.FC<Props> = ({ isOpen, onClose }) => {
  const { t } = useTranslation()
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  useEffect(() => {
    if (isOpen) overlayRef.current?.focus()
  }, [isOpen])

  if (!isOpen) return null

  // B2：单一事实源 —— 导航/全局快捷键从统一注册中心读取，
  // 与实际处理器保持一致（修复旧硬编码列表与真实绑定不符的问题）。
  const registryGroups: ShortcutGroup[] = []
  for (const def of listShortcuts()) {
    let group = registryGroups.find((g) => g.title === def.group)
    if (!group) {
      group = { title: def.group, items: [] }
      registryGroups.push(group)
    }
    group.items.push({ keys: formatComboDisplay(def.combo), desc: def.label })
  }

  const groups: ShortcutGroup[] = [
    ...registryGroups,
    {
      title: t('shortcuts.editor'),
      items: [
        { keys: 'Ctrl+I', desc: t('shortcuts.autocomplete') },
        { keys: '/', desc: t('shortcuts.slashMenu') },
        { keys: 'Tab', desc: t('shortcuts.complete') },
        { keys: 'Escape', desc: t('shortcuts.closeMenu') },
      ],
    },
  ]

  return (
    <div
      ref={overlayRef}
      tabIndex={-1}
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      onClick={(e) => { if (e.target === overlayRef.current) onClose() }}
    >
      <div
        className="border rounded-xl shadow-2xl overflow-hidden w-[420px] max-h-[80vh]"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-10 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-keyboard text-text-muted text-xs" />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('shortcuts.title')}
            </span>
          </div>
          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[60vh] px-4 py-3 space-y-4">
          {groups.map((group) => (
            <div key={group.title}>
              <div className="text-xs font-medium uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
                {group.title}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => (
                  <div
                    key={item.keys}
                    className="flex items-center justify-between py-1.5 px-2 rounded-lg"
                    style={{ background: 'var(--hover-bg)' }}
                  >
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                      {item.desc}
                    </span>
                    <kbd
                      className="text-xs font-mono px-2 py-0.5 rounded border"
                      style={{
                        color: 'var(--text-primary)',
                        background: 'var(--input-bg)',
                        borderColor: 'var(--panel-border)',
                      }}
                    >
                      {item.keys}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer hint */}
        <div className="px-4 py-2 border-t" style={{ borderColor: 'var(--separator)' }}>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Press <kbd className="font-mono px-1 py-0.5 rounded border text-2xs" style={{ borderColor: 'var(--panel-border)' }}>Escape</kbd> to close
          </span>
        </div>
      </div>
    </div>
  )
}

export default ShortcutsModal
