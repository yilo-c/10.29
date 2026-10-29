import React, { useState, useRef, useCallback } from 'react'
import Icon from './Icon'

import { useStore } from '../../store/useStore'
import { uid } from '../../utils/helpers'
import { getAllTemplates, NoteTemplate } from '../../utils/templates'
import { useTranslation } from '../../i18n'

interface TemplatePickerProps {
  onClose: () => void
  onManage?: () => void
}

function stripHtml(html: string): string {
  const div = document.createElement('div')
  div.innerHTML = html
  return div.textContent || div.innerText || ''
}

const TemplatePicker: React.FC<TemplatePickerProps> = ({ onClose, onManage }) => {
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const nextZ = useStore(s => s.nextZ)
  const defaultTemplateId = useStore(s => s.defaultTemplateId)
  const { t } = useTranslation()
  const templates = getAllTemplates()

  const [hoveredTmpl, setHoveredTmpl] = useState<NoteTemplate | null>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleMouseEnter = useCallback((tmpl: NoteTemplate) => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    hoverTimer.current = setTimeout(() => setHoveredTmpl(tmpl), 300)
  }, [])

  const handleMouseLeave = useCallback(() => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    setHoveredTmpl(null)
  }, [])

  const handleSelect = (tmpl: NoteTemplate) => {
    const id = uid()
    addFloatingNote({
      id,
      type: 'text',
      title: tmpl.title,
      content: tmpl.content,
      x: 100 + Math.random() * 80,
      y: 100 + Math.random() * 80,
      width: 320,
      height: 320,
      zIndex: nextZ,
      floated: true,
    })
    const ei = window.electronAPI as ElectronAPI | undefined
    if (ei) {
      const note = useStore.getState().floatingNotes.find(n => n.id === id)
      ei.createFloatingWindow({ id, screenX: 100 + Math.random() * 80, screenY: 100 + Math.random() * 80, width: 320, height: 320, noteData: note })
    }
    onClose()
  }

  return (
    <div
      className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 border border-white/[0.08] rounded-xl py-1 shadow-2xl min-w-[180px] backdrop-blur-xl max-h-[60vh] overflow-y-auto"
      onMouseDown={e => e.stopPropagation()}
      style={{ background: 'var(--panel-bg-solid)' }}>
      <div className="px-3 py-1.5 text-2xs text-text-muted border-b border-white/[0.04]">{t('toolbar.selectTemplate')}</div>
      {templates.map(tmpl => (
        <button
          key={tmpl.id}
          onClick={() => handleSelect(tmpl)}
          onMouseEnter={() => handleMouseEnter(tmpl)}
          onMouseLeave={handleMouseLeave}
          className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-text-primary hover:bg-white/[0.06] hover:text-text-primary transition-colors text-left relative"
        >
          <Icon icon={`fa-solid ${tmpl.icon} w-3.5 text-center text-xs text-text-secondary`} />
          <span className="flex-1 truncate">{tmpl.label}</span>
          {tmpl.id === defaultTemplateId && (
            <Icon icon="fa-solid fa-star text-[8px] text-amber-400/60" />
          )}
          {tmpl.source === 'custom' && (
            <span className="text-[8px] px-1 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>
              custom
            </span>
          )}
          {/* Preview tooltip */}
          {hoveredTmpl?.id === tmpl.id && (
            <div
              className="absolute left-full ml-2 top-0 w-56 rounded-xl border border-white/[0.08] p-3 shadow-2xl backdrop-blur-xl pointer-events-none"
              style={{ background: 'var(--panel-bg-solid)' }}
            >
              <div className="text-xs font-medium text-text-primary truncate mb-1">{tmpl.title}</div>
              <div className="text-2xs text-text-secondary line-clamp-6 leading-relaxed">
                {stripHtml(tmpl.content).substring(0, 200)}
              </div>
            </div>
          )}
        </button>
      ))}
      {onManage && (
        <>
          <div className="border-t border-white/[0.04] my-1" />
          <button
            onClick={() => { onClose(); onManage?.() }}
            className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-white/55 hover:bg-white/[0.06] hover:text-text-secondary transition-colors text-left"
          >
            <Icon icon="fa-solid fa-gear w-3.5 text-center text-xs" />
            {t('toolbar.manageTemplates')}
          </button>
        </>
      )}
    </div>
  )
}

export default TemplatePicker
