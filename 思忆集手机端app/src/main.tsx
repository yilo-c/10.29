import React from 'react'
import ReactDOM from 'react-dom/client'
import * as Sentry from '@sentry/react'
import { setPlatformMobile, setPlatformStorage } from '@desk-notes/shared'
import App from './App'
import ErrorBoundary from './components/ui/ErrorBoundary'
import { initSecureStore } from './lib/secureStore'
import { capacitorPlatformStorage } from './lib/platformStorage'
import './index.css'
import './styles/utilities.css'

// Let the shared store know it's running on mobile so desktop-only fields are excluded from persist
setPlatformMobile()
// V2.1 批次4（P0-8 债）：真正接线 setPlatformStorage —— persist 存储从 WebView
// localStorage 切换到 Capacitor Preferences（加密），PIN hash 等敏感数据不再明文落盘。
// 旧 localStorage 数据由适配器首次读取时自动迁移。必须在任何 useStore 读取之前调用。
setPlatformStorage(capacitorPlatformStorage)

const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined

if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.MODE,
    tracesSampleRate: 0.1,
    beforeSend(event) {
      // Don't send events in development
      if (import.meta.env.DEV) return null
      return event
    },
  })
}

// Hydrate secure store (Capacitor Preferences → in-memory cache) BEFORE first
// render. Migrates any legacy localStorage credentials into Preferences on
// first run. Without this, tokenManager getters for sensitive keys would
// return null until hydration completes mid-render. See secureStore.ts.
initSecureStore().finally(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>,
  )
})
