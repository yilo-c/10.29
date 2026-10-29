import React, { useState, useEffect, useCallback } from 'react'
import Icon from './Icon'

import { useStore } from '../../store/useStore'
import {
  getAllTemplates,
  createCustomTemplate,
  updateCustomTemplate,
  deleteCustomTemplate,
  isBuiltin,
  NoteTemplate,
} from '../../utils/templates'
import { useTranslation } from '../../i18n'

interface Props {
  onClose: () => void
}

const TemplateManagerPanel: React.FC<Props> = ({ onClose }) => {
  const { t } = useTranslation()
  const defaultTemplateId = useStore(s => s.defaultTemplateId)
  const setDefaultTemplateId = useStore(s => s.setDefaultTemplateId)
  const [templates, setTemplates] = useState<NoteTemplate[]>([])
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')

  useEffect(() => {
    setTemplates(getAllTemplates())
  }, [])

  const refresh = useCallback(() => {
    setTemplates(getAllTemplates())
  }, [])

  const handleCreate = () => {
    const label = prompt(t('toolbar.templateName'))
    if (!label) return
    createCustomTemplate(label, label, '')
    refresh()
  }

  const startEdit = (tmpl: NoteTemplate) => {
    setEditingId(tmpl.id)
    setEditLabel(tmpl.label)
    setEditTitle(tmpl.title)
    setEditContent(tmpl.content)
  }

  const saveEdit = () => {
    if (!editingId) return
    updateCustomTemplate(editingId, { label: editLabel, title: editTitle, content: editContent })
    setEditingId(null)
    refresh()
  }

  const handleDelete = (id: string) => {
    if (isBuiltin(id)) return
    if (!confirm(t('common.delete'))) return
    deleteCustomTemplate(id)
    if (defaultTemplateId === id) setDefaultTemplateId(null)
    refresh()
  }

  const setDefault = (id: string | null) => {
    setDefaultTemplateId(defaultTemplateId === id ? null : id)
  }


  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
  return (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('toolbar.template')}
      onMouseDown={onClose}
      style={{ background: 'rgba(0,0,0,0.45)' }}
    >
      <div
        className="w-full max-w-[480px] max-h-[75vh] border rounded-xl shadow-2xl overflow-hidden backdrop-blur-xl flex flex-col"
        onMouseDown={e => e.stopPropagation()}
        style={{
          background: 'var(--panel-bg-solid, rgba(22,22,32,0.95))',
          borderColor: 'var(--panel-border, rgba(255,255,255,0.08))',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.06))' }}>
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
            {t('toolbar.manageTemplates')}
          </span>
          <div className="flex items-center gap-2">
            <button onClick={handleCreate} className="text-2xs px-2 py-1 rounded flex items-center gap-1 transition-all"
              style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}>
              <Icon icon="fa-solid fa-plus" />
              {t('common.add')}
            </button>
            <button onClick={onClose}
              className="w-5 h-5 flex items-center justify-center rounded text-white/55 hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs">
              <Icon icon="fa-solid fa-xmark" />
            </button>
          </div>
        </div>

        {/* Template list */}
        <div className="flex-1 overflow-y-auto py-1">
          {templates.length === 0 ? (
            <div className="flex flex-col items-center py-8 gap-2">
              <Icon icon="fa-regular fa-file-lines text-lg" style={{ color: 'var(--text-muted)' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('toolbar.noTemplates')}</span>
            </div>
          ) : (
            templates.map(tmpl => (
              <div key={tmpl.id} className="px-3 py-2 border-b" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
                {editingId === tmpl.id ? (
                  <div className="flex flex-col gap-1.5">
                    <input value={editLabel} onChange={e => setEditLabel(e.target.value)}
                      className="text-xs px-2 py-1 rounded outline-none border"
                      style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                      placeholder="Label" />
                    <input value={editTitle} onChange={e => setEditTitle(e.target.value)}
                      className="text-xs px-2 py-1 rounded outline-none border"
                      style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                      placeholder="Title" />
                    <textarea value={editContent} onChange={e => setEditContent(e.target.value)}
                      rows={3}
                      className="text-xs px-2 py-1 rounded outline-none border resize-none"
                      style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                      placeholder="HTML content" />
                    <div className="flex items-center gap-1">
                      <button onClick={saveEdit} className="text-2xs px-2 py-1 rounded"
                        style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--fluent-blue)' }}>
                        {t('common.save')}
                      </button>
                      <button onClick={() => setEditingId(null)} className="text-2xs px-2 py-1 rounded"
                        style={{ color: 'var(--text-muted)' }}>
                        {t('common.cancel')}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2.5">
                    <Icon icon={`fa-solid ${tmpl.icon} w-3.5 text-center text-xs`} style={{ color: 'var(--text-secondary)' }} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{tmpl.label}</span>
                        {tmpl.source === 'custom' && (
                          <span className="text-[8px] px-1 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>custom</span>
                        )}
                      </div>
                      <div className="text-2xs truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>{tmpl.title}</div>
                    </div>
                    {/* Default star */}
                    <button onClick={() => setDefault(tmpl.id)}
                      className={`w-4 h-4 flex items-center justify-center rounded transition-all text-2xs ${tmpl.id === defaultTemplateId ? 'text-amber-400/80' : 'text-text-dim hover:text-white/55'}`}
                      title={t('toolbar.setDefaultTemplate')}>
                      <Icon icon="fa-solid fa-star" />
                    </button>
                    {/* Edit (custom only) */}
                    {tmpl.source === 'custom' && (
                      <button onClick={() => startEdit(tmpl)}
                        className="w-4 h-4 flex items-center justify-center rounded text-text-muted hover:text-white/55 transition-all text-2xs">
                        <Icon icon="fa-solid fa-pen" />
                      </button>
                    )}
                    {/* Delete (custom only) */}
                    {tmpl.source === 'custom' && (
                      <button onClick={() => handleDelete(tmpl.id)}
                        className="w-4 h-4 flex items-center justify-center rounded text-text-muted hover:text-red-400/60 transition-all text-2xs">
                        <Icon icon="fa-solid fa-trash-can" />
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

export default TemplateManagerPanel
