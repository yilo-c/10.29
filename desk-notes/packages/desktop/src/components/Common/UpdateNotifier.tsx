import React, { useState, useEffect, useCallback } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useTranslation } from '../../i18n'

interface UpdateInfo {
  version: string
  releaseDate?: string
  releaseNotes?: string
}

interface DownloadProgress {
  percent: number
  bytesPerSecond: number
  transferred: number
  total: number
}

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const UpdateNotifier: React.FC = () => {
  const { t } = useTranslation()
  const [status, setStatus] = useState<'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'not-available' | 'error'>('idle')
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [progress, setProgress] = useState<DownloadProgress | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    if (!isElectron || !ei.update) return

    const cleanups: (() => void)[] = []
    const timeoutIds: ReturnType<typeof setTimeout>[] = []

    cleanups.push(ei.update.onChecking(() => setStatus('checking')))
    cleanups.push(ei.update.onAvailable((info: Record<string, unknown>) => {
      setUpdateInfo(info as unknown as UpdateInfo)
      setStatus('available')
    }))
    cleanups.push(ei.update.onNotAvailable(() => {
      setStatus('not-available')
      timeoutIds.push(setTimeout(() => setStatus('idle'), 3000))
    }))
    cleanups.push(ei.update.onError((msg: string) => {
      setErrorMsg(msg)
      setStatus('error')
      timeoutIds.push(setTimeout(() => setStatus('idle'), 5000))
    }))
    cleanups.push(ei.update.onProgress((p: Record<string, unknown>) => {
      setProgress(p as unknown as DownloadProgress)
      setStatus('downloading')
    }))
    cleanups.push(ei.update.onDownloaded((info: Record<string, unknown>) => {
      setUpdateInfo(info as unknown as UpdateInfo)
      setStatus('downloaded')
    }))

    return () => {
      cleanups.forEach(fn => fn())
      timeoutIds.forEach(id => clearTimeout(id))
    }
  }, [])

  const handleDownload = useCallback(() => {
    ei!.update.download()
  }, [])

  const handleInstall = useCallback(() => {
    ei!.update.install()
  }, [])

  const handleDismiss = useCallback(() => {
    setStatus('idle')
  }, [])

  return (
    <AnimatePresence>
      {status !== 'idle' && status !== 'checking' && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 20 }}
          transition={{ duration: 0.2 }}
          className="fixed bottom-4 right-4 z-[10002] border border-white/[0.08] rounded-xl shadow-2xl overflow-hidden"
          style={{ background: 'var(--panel-bg-solid)', minWidth: 260, maxWidth: 340 }}
        >
          {/* Checking state */}
          {status === 'not-available' && (
            <div className="px-4 py-3 flex items-center gap-2">
              <Icon icon="fa-regular fa-circle-check text-sm text-green-400/70" />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('update.upToDate')}</span>
              <button onClick={handleDismiss}
                className="ml-auto text-2xs px-1.5 py-0.5 rounded hover:bg-white/[0.06] transition-colors"
                style={{ color: 'var(--text-muted)' }}>
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </div>
          )}

          {/* Available state */}
          {status === 'available' && updateInfo && (
            <div className="px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <Icon icon="fa-solid fa-download text-sm text-fluent-blue" />
                <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                  {t('update.available', { version: updateInfo.version })}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={handleDownload}
                  className="flex-1 text-xs px-2 py-1 rounded-lg bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/20 transition-colors">
                  {t('update.download')}
                </button>
                <button onClick={handleDismiss}
                  className="text-xs px-2 py-1 rounded-lg transition-colors"
                  style={{ color: 'var(--text-muted)' }}>
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          )}

          {/* Downloading state */}
          {status === 'downloading' && progress && (
            <div className="px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <Icon icon="fa-solid fa-spinner text-sm text-fluent-blue animate-spin" />
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('update.downloading')}
                </span>
                <span className="text-xs ml-auto" style={{ color: 'var(--text-muted)' }}>
                  {Math.round(progress.percent)}%
                </span>
              </div>
              <div className="w-full h-1 rounded-full bg-white/[0.06] overflow-hidden">
                <div className="h-full rounded-full bg-fluent-blue transition-all duration-300"
                  style={{ width: `${Math.round(progress.percent)}%` }} />
              </div>
            </div>
          )}

          {/* Downloaded state */}
          {status === 'downloaded' && (
            <div className="px-4 py-3">
              <div className="flex items-center gap-2 mb-2">
                <Icon icon="fa-solid fa-circle-check text-sm text-green-400/70" />
                <span className="text-xs" style={{ color: 'var(--text-primary)' }}>
                  {t('update.downloaded')}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button onClick={handleInstall}
                  className="flex-1 text-xs px-2 py-1 rounded-lg bg-green-500/15 text-green-400 hover:bg-green-500/20 transition-colors">
                  {t('update.install')}
                </button>
                <button onClick={handleDismiss}
                  className="text-xs px-2 py-1 rounded-lg transition-colors"
                  style={{ color: 'var(--text-muted)' }}>
                  {t('update.later')}
                </button>
              </div>
            </div>
          )}

          {/* Error state */}
          {status === 'error' && (
            <div className="px-4 py-3 flex items-center gap-2">
              <Icon icon="fa-solid fa-triangle-exclamation text-sm text-red-400/70" />
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {t('update.error')}: {errorMsg}
              </span>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default UpdateNotifier
