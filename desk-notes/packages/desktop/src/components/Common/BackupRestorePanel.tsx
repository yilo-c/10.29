import React, { useState, useEffect, useRef, useCallback } from 'react'
import Icon from './Icon'

import { motion } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { exportAllToJSON, parseAndValidateBackup, applyBackupToStore, downloadAsFile } from '../../utils/export'
import { getBackupMeta, getBackupHistory } from '../../utils/backup'
import { formatRelativeTime } from '../../utils/helpers'

interface Props {
  onClose: () => void
}

const BackupRestorePanel: React.FC<Props> = ({ onClose }) => {
  const { t } = useTranslation()
  const [lastBackup, setLastBackup] = useState<string | null>(null)
  const [backupCount, setBackupCount] = useState(0)
  const [result, setResult] = useState<{ summary: import('../../utils/export').RestoreSummary; warnings: string[] } | null>(null)
  const [data, setData] = useState<import('../../utils/export').BackupData | null>(null)
  const [showFinalConfirm, setShowFinalConfirm] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [exported, setExported] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [checked, setChecked] = useState(true)

  const refreshMeta = useCallback(() => {
    const meta = getBackupMeta()
    setLastBackup(meta ? formatRelativeTime(meta.timestamp) : null)
    setBackupCount(getBackupHistory().length)
  }, [])

  useEffect(() => { refreshMeta() }, [refreshMeta])

  const handleExport = () => {
    const json = exportAllToJSON(() => useStore.getState())
    const ts = new Date().toISOString().replace(/[:.]/g, '-')
    downloadAsFile(json, t('backup.fileNamePattern', { ts }), 'application/json')
    setExported(true)
    setTimeout(() => setExported(false), 2000)
  }

  const handleFileSelect = async (file: File) => {
    setError(null)
    setResult(null)
    setData(null)
    setShowFinalConfirm(false)
    setImporting(true)

    try {
      const content = await file.text()
      const parsed = parseAndValidateBackup(content)
      setResult({ summary: parsed.summary, warnings: parsed.warnings })
      setData(parsed.data)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      setError(/fetch\s*failed|failed\s*to\s*fetch|networkerror|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(msg)
        ? t('backup.backendNoResponse') : msg)
    } finally {
      setImporting(false)
    }
  }

  const handleOpenFile = async () => {
    const ei = window.electronAPI as ElectronAPI | undefined
    if (ei?.showOpenDialog) {
      setError(null)
      setResult(null)
      setData(null)
      setShowFinalConfirm(false)
      setImporting(true)

      try {
        const res = await ei.showOpenDialog()
        if (res.canceled || !res.content) { setImporting(false); return }
        const parsed = parseAndValidateBackup(res.content)
        setResult({ summary: parsed.summary, warnings: parsed.warnings })
        setData(parsed.data)
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Unknown error'
        setError(/fetch\s*failed|failed\s*to\s*fetch|networkerror|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(msg)
          ? t('backup.backendNoResponse') : msg)
      } finally {
        setImporting(false)
      }
    } else {
      fileInputRef.current?.click()
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFileSelect(file)
    e.target.value = ''
  }

  const handleRestore = () => {
    if (!data) return
    if (!showFinalConfirm) {
      setShowFinalConfirm(true)
      return
    }
    applyBackupToStore(data)
    onClose()
  }

  const secClass = 'px-4 py-3 border-b last:border-b-0'
  const labelClass = 'text-xs font-medium mb-1.5'
  const descClass = 'text-xs mb-3'


  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[10001] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('backup.title')}
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.15 }}
        className="relative w-[460px] max-w-[90vw] max-h-[80vh] flex flex-col min-h-0 rounded-2xl shadow-2xl overflow-hidden border"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
          backdropFilter: 'blur(24px)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-floppy-disk text-xs" style={{ color: 'var(--text-secondary)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('backup.title')}</span>
          </div>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">

          {/* Section 1: Export */}
          <div className={secClass}>
            <h4 className={labelClass} style={{ color: 'var(--text-secondary)' }}>
              <Icon icon="fa-solid fa-download mr-1.5 text-2xs" />
              {t('backup.exportTitle')}
            </h4>
            <p className={descClass} style={{ color: 'var(--text-dim)' }}>
              {t('backup.exportDesc')}
            </p>
            <button onClick={handleExport}
              className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] hover:bg-white/[0.08]"
              style={{ color: 'var(--text-primary)' }}>
              <Icon icon="fa-solid fa-floppy-disk mr-1.5" />
              {exported ? t('backup.exportSuccess') : t('backup.exportBtn')}
            </button>
          </div>

          {/* Section 2: Import */}
          <div className={secClass}>
            <h4 className={labelClass} style={{ color: 'var(--text-secondary)' }}>
              <Icon icon="fa-solid fa-upload mr-1.5 text-2xs" />
              {t('backup.importTitle')}
            </h4>
            <p className={descClass} style={{ color: 'var(--text-dim)' }}>
              {t('backup.importDesc')}
            </p>

            <input
              ref={fileInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={handleInputChange}
            />

            {!result && !error && (
              <button onClick={handleOpenFile} disabled={importing}
                className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-40"
                style={{ color: 'var(--text-primary)' }}>
                <Icon icon={`fa-solid ${importing ? 'fa-spinner fa-spin' : 'fa-folder-open'} mr-1.5`} />
                {importing ? '...' : t('backup.importBtn')}
              </button>
            )}

            {/* Error banner */}
            {error && (
              <div className="mt-2 px-3 py-2 rounded-lg text-xs bg-red-500/10"
                style={{ color: 'var(--text-primary)' }}>
                <Icon icon="fa-solid fa-circle-exclamation mr-1 text-red-400" />
                {t('backup.importError', { error })}
              </div>
            )}

            {/* Summary preview */}
            {result && (
              <div className="mt-3 rounded-xl overflow-hidden border" style={{ borderColor: 'var(--panel-border-accent)' }}>
                <div className="px-3 py-2 text-xs font-medium border-b" style={{ borderColor: 'var(--separator)', color: 'var(--text-primary)' }}>
                  <Icon icon="fa-solid fa-list mr-1.5" />
                  {t('backup.summaryTitle')}
                </div>
                <div className="p-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                  {[
                    { key: 'todos', label: t('backup.summaryTodos', { count: result.summary.todos }) },
                    { key: 'notes', label: t('backup.summaryNotes', { count: result.summary.notes }) },
                    { key: 'conversations', label: t('backup.summaryConversations', { count: result.summary.conversations }) },
                    { key: 'folders', label: t('backup.summaryFolders', { count: result.summary.folders }) },
                    { key: 'categories', label: t('backup.summaryCategories', { count: result.summary.categories }) },
                    { key: 'themes', label: t('backup.summaryThemes', { count: result.summary.themes }) },
                    { key: 'aiProfiles', label: t('backup.summaryAIProfiles', { count: result.summary.aiProfiles }) },
                  ].map(item => (
                    <div key={item.key} style={{ color: 'var(--text-muted)' }}>{item.label}</div>
                  ))}
                </div>
                {result.warnings.length > 0 && (
                  <div className="px-3 pb-2">
                    {result.warnings.map((w, i) => (
                      <div key={i} className="text-2xs text-yellow-400/70 flex items-start gap-1">
                        <Icon icon="fa-solid fa-triangle-exclamation mt-0.5" />
                        {w}
                      </div>
                    ))}
                  </div>
                )}

                {/* Restore button */}
                {showFinalConfirm ? (
                  <div className="flex items-center gap-2 px-3 py-2.5 border-t" style={{ borderColor: 'var(--separator)' }}>
                    <span className="text-xs flex-1" style={{ color: 'var(--text-muted)' }}>
                      <Icon icon="fa-solid fa-triangle-exclamation mr-1 text-amber-400" />
                      {t('backup.importWarning')}
                    </span>
                    <div className={checked ? '' : 'opacity-40'}>
                      <label className="flex items-center gap-1.5 cursor-pointer">
                        <input type="checkbox" checked={checked} onChange={e => setChecked(e.target.checked)}
                          className="w-3 h-3 accent-fluent-blue" />
                        <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('backup.confirmReady')}</span>
                      </label>
                    </div>
                    <button onClick={handleRestore} disabled={!checked}
                      className="px-2.5 py-1 rounded-lg text-xs bg-red-500/15 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-40">
                      {t('backup.importConfirm')}
                    </button>
                    <button onClick={() => setShowFinalConfirm(false)}
                      className="px-2.5 py-1 rounded-lg text-xs transition-colors"
                      style={{ color: 'var(--text-muted)' }}>
                      {t('common.cancel')}
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 px-3 py-2.5 border-t" style={{ borderColor: 'var(--separator)' }}>
                    <button onClick={handleRestore}
                      className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-fluent-blue/15 text-fluent-blue/80 hover:bg-fluent-blue/25"
                      style={{ color: 'var(--text-primary)' }}>
                      <Icon icon="fa-solid fa-rotate-left mr-1" />
                      {t('backup.importConfirm')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Section 3: Auto backup status */}
          <div className={secClass}>
            <h4 className={labelClass} style={{ color: 'var(--text-secondary)' }}>
              <Icon icon="fa-solid fa-clock-rotate-left mr-1.5 text-2xs" />
              {t('backup.autoBackup')}
            </h4>
            <div className="flex items-center gap-2 text-xs flex-wrap" style={{ color: 'var(--text-dim)' }}>
              {lastBackup ? (
                <span>{t('backup.lastBackup', { time: lastBackup })}</span>
              ) : (
                <span>{t('backup.noBackup')}</span>
              )}
              {backupCount > 0 && (
                <span className="px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
                  {t('backup.backupCount', { count: backupCount })}
                </span>
              )}
            </div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default BackupRestorePanel
