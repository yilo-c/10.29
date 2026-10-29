import React, { useEffect, useState, useCallback, useRef } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'

const PWAProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { t } = useTranslation()
  const [offline, setOffline] = useState(!navigator.onLine)
  const [showOffline, setShowOffline] = useState(offline)
  const [updateReady, setUpdateReady] = useState(false)
  const hideTimerRef = useRef<number | null>(null)

  // Online/offline detection
  useEffect(() => {
    const onOnline = () => {
      setOffline(false)
      setShowOffline(false)
    }
    const onOffline = () => {
      setOffline(true)
      setShowOffline(true)
      if (hideTimerRef.current !== null) clearTimeout(hideTimerRef.current)
      hideTimerRef.current = window.setTimeout(() => setShowOffline(false), 8000)
    }
    window.addEventListener('online', onOnline)
    window.addEventListener('offline', onOffline)
    return () => {
      window.removeEventListener('online', onOnline)
      window.removeEventListener('offline', onOffline)
      if (hideTimerRef.current !== null) clearTimeout(hideTimerRef.current)
    }
  }, [])

  // Service Worker update notification
  useEffect(() => {
    const handler = () => setUpdateReady(true)
    window.addEventListener('sw-update-ready', handler)
    return () => window.removeEventListener('sw-update-ready', handler)
  }, [])

  const handleUpdate = useCallback(() => {
    navigator.serviceWorker?.getRegistration().then(reg => {
      if (reg?.waiting) {
        reg.waiting.postMessage({ type: 'SKIP_WAITING' })
        window.location.reload()
      }
    })
    setUpdateReady(false)
  }, [])

  return (
    <>
      {children}

      {/* Offline banner */}
      {showOffline && (
        <div
          className="fixed top-0 left-0 right-0 z-[9999] flex items-center justify-center gap-2 px-4 py-2 text-xs font-medium"
          style={{ background: 'rgba(239,68,68,0.9)', color: '#fff' }}
        >
          <Icon icon="fa-solid fa-wifi text-xs" />
          <span>{offline ? t('pwaProvider.offline') : t('pwaProvider.online')}</span>
        </div>
      )}

      {/* Update notification */}
      {updateReady && (
        <div
          className="fixed bottom-4 right-4 z-[9999] flex items-center gap-3 px-4 py-3 rounded-xl border shadow-lg"
          style={{
            background: 'var(--panel-bg, var(--surface))',
            borderColor: 'var(--panel-border, rgba(255,255,255,0.08))',
          }}
        >
          <Icon icon="fa-solid fa-rotate text-xs" style={{ color: 'var(--brand)' }} />
          <span className="text-xs" style={{ color: 'var(--text-primary, var(--text-secondary))' }}>
            {t('pwaProvider.updateReady')}
          </span>
          <button
            onClick={handleUpdate}
            className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all"
            style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
          >
            {t('pwaProvider.update')}
          </button>
        </div>
      )}
    </>
  )
}

export default PWAProvider
