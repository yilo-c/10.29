import React, { useState, useRef, useCallback, useEffect } from 'react'
import Icon from './Icon'

import { motion } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { parseImportedFile, ImportResult } from '../../utils/importer'
import { uid } from '../../utils/helpers'
import { useTranslation } from '../../i18n'
import { errorStore } from '../../store/errorStore'

interface Props {
  onClose: () => void
}

const ImportModal: React.FC<Props> = ({ onClose }) => {
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const addTodo = useStore(s => s.addTodo)
  const { t } = useTranslation()
  const [result, setResult] = useState<ImportResult | null>(null)
  const [importing, setImporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const dropRef = useRef<HTMLDivElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const processFiles = useCallback(async (files: FileList | File[]) => {
    setImporting(true)
    const combined: ImportResult = { notes: [], todos: [], errors: [] }

    for (const file of Array.from(files)) {
      try {
        const text = await file.text()
        const parsed = parseImportedFile(file.name, text)
        combined.notes.push(...parsed.notes)
        combined.todos.push(...parsed.todos)
        combined.errors.push(...parsed.errors)
      } catch (e: unknown) {
        combined.errors.push(t('importModal.readError', { file: file.name, error: e instanceof Error ? e.message : t('common.empty') }))
      }
    }

    setResult(combined)
    setImporting(false)
  }, [t])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      processFiles(e.target.files)
    }
  }, [processFiles])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    if (e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files)
    }
  }, [processFiles])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }, [])

  const doImport = useCallback(() => {
    if (!result) return

    for (const n of result.notes) {
      addFloatingNote({
        id: uid(),
        type: 'text',
        title: n.title,
        content: n.content,
        tags: n.tags,
        x: 100 + Math.random() * 200,
        y: 100 + Math.random() * 200,
        width: 280,
        height: 260,
        zIndex: useStore.getState().nextZ,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      })
    }

    for (const t of result.todos) {
      addTodo(t.text, t.category)
    }

    onClose()
    // Show brief success feedback
    errorStore.success(t('importModal.success', { notes: result.notes.length, todos: result.todos.length }))
  }, [result, addFloatingNote, addTodo, onClose, t])

  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const selectFiles = useCallback(() => {
    fileRef.current?.click()
  }, [])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[10002] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('importModal.title')}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.15 }}
        className="relative w-[460px] max-w-[90vw] max-h-[75vh] flex flex-col rounded-2xl shadow-2xl overflow-hidden border"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
          backdropFilter: 'blur(24px)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-file-import text-xs" style={{ color: 'var(--text-secondary)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('importModal.title')}</span>
          </div>
          <button onClick={onClose}
            aria-label="关闭"
            className="w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {/* File picker / drop zone */}
          <input ref={fileRef} type="file" multiple accept=".md,.json,.txt,.todo" className="hidden"
            onChange={handleFileChange} />

          {!result && !importing && (
            <div
              ref={dropRef}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onClick={selectFiles}
              className={`flex flex-col items-center justify-center py-12 px-6 rounded-xl border-2 border-dashed cursor-pointer transition-all ${
                dragOver
                  ? 'border-fluent-blue bg-fluent-blue/[0.06]'
                  : 'border-white/[0.10] hover:border-white/[0.20] hover:bg-white/[0.03]'
              }`}
            >
              <Icon icon="fa-solid fa-cloud-arrow-up text-2xl mb-3" style={{ color: 'var(--text-dim)' }} />
              <span className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>
                {dragOver ? t('importModal.dropActive') : t('importModal.dropInactive')}
              </span>
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {t('importModal.supportedFormats')}
              </span>
            </div>
          )}

          {/* Importing state */}
          {importing && (
            <div className="flex flex-col items-center justify-center py-12">
              <div className="w-6 h-6 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin mb-3" />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('importModal.parsing')}</span>
            </div>
          )}

          {/* Result preview */}
          {result && !importing && (
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{t('importModal.parseResult')}</span>
                <button onClick={() => setResult(null)}
                  className="text-xs px-2 py-1 rounded-lg transition-colors"
                  style={{ color: 'var(--text-muted)' }}>
                  {t('importModal.reselect')}
                </button>
              </div>

              {(result.notes.length > 0 || result.todos.length > 0) ? (
                <div className="flex flex-col gap-2">
                  {result.notes.length > 0 && (
                    <div className="rounded-xl px-3 py-2" style={{ background: 'var(--hover-bg)' }}>
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <Icon icon="fa-regular fa-note-sticky text-xs text-fluent-blue/70" />
                        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                          {t('importModal.notes', { count: result.notes.length })}
                        </span>
                      </div>
                      <div className="flex flex-col gap-1 max-h-[120px] overflow-y-auto">
                        {result.notes.map((n, i) => (
                          <div key={`note-${i}-${n.title}`} className="flex items-center gap-2">
                            <span className="text-xs truncate flex-1" style={{ color: 'var(--text-secondary)' }}>
                              {n.title || t('importModal.unnamedNote')}
                            </span>
                            <span className="text-2xs whitespace-nowrap" style={{ color: 'var(--text-dim)' }}>
                              {t('importModal.chars', { count: n.content.length })}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {result.todos.length > 0 && (
                    <div className="rounded-xl px-3 py-2" style={{ background: 'var(--hover-bg)' }}>
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <Icon icon="fa-solid fa-list-check text-xs text-fluent-blue/70" />
                        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                          {t('importModal.todos', { count: result.todos.length })}
                        </span>
                      </div>
                      <div className="flex flex-col gap-0.5 max-h-[120px] overflow-y-auto">
                        {result.todos.slice(0, 20).map((t, i) => (
                          <div key={`todo-${i}-${t.text}`} className="flex items-center gap-1.5">
                            <span className={`text-2xs ${t.done ? 'text-green-400/60' : 'text-text-muted'}`}>
                              <Icon icon={`fa-${t.done ? 'check-' : ''}circle`} />
                            </span>
                            <span className="text-xs truncate" style={{ color: 'var(--text-secondary)' }}>
                              {t.text}
                            </span>
                          </div>
                        ))}
                        {result.todos.length > 20 && (
                          <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                            {t('importModal.moreItems', { count: result.todos.length - 20 })}
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ) : null}

              {/* Errors */}
              {result.errors.length > 0 && (
                <div className="rounded-xl px-3 py-2" style={{ background: 'rgba(239,68,68,0.08)' }}>
                  <div className="flex items-center gap-1.5 mb-1">
                    <Icon icon="fa-solid fa-triangle-exclamation text-xs text-red-400" />
                    <span className="text-xs font-medium text-red-400">
                      {t('importModal.warnings', { count: result.errors.length })}
                    </span>
                  </div>
                  <div className="flex flex-col gap-0.5 max-h-[80px] overflow-y-auto">
                    {result.errors.map((err, i) => (
                      <span key={`err-${i}`} className="text-xs text-red-300/80">{err}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {result && !importing && (
          <div className="flex items-center justify-end gap-2 px-4 py-3 border-t" style={{ borderColor: 'var(--separator)' }}>
            <button onClick={onClose}
              className="px-3 py-1.5 rounded-lg text-xs transition-colors"
              style={{ color: 'var(--text-muted)' }}>
              {t('common.cancel')}
            </button>
            <button onClick={doImport}
              className="px-3 py-1.5 rounded-lg text-xs bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/20 transition-colors"
              disabled={result.notes.length === 0 && result.todos.length === 0}>
              {t('importModal.importItems', { count: result.notes.length + result.todos.length })}
            </button>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}

export default ImportModal
