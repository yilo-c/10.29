// Init must be first — sets platform adapters before any store access
import './store/init'
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { swallowError } from './utils/swallowError'

const ei = window.electronAPI as ElectronAPI | undefined
if (ei) {
  document.body.style.background = 'transparent'
  document.documentElement.style.background = 'transparent'
}

// Parse background mode from URL hash (floating windows inherit from main process)
try {
  const hash = window.location.hash.replace(/^#/, '')
  const params = new URLSearchParams(hash)
  const bgFromHash = params.get('bg')
  if (bgFromHash) {
    document.documentElement.setAttribute('data-background', bgFromHash)
    if (bgFromHash === 'pure-white') {
      document.documentElement.setAttribute('data-theme', 'light')
    } else if (bgFromHash === 'pure-black') {
      document.documentElement.setAttribute('data-theme', 'dark')
    }
  }
} catch (e) { /* ignore */ swallowError('main:27', e) }

// Restore theme & backgroundMode from localStorage before render to avoid flash
try {
  const raw = localStorage.getItem('desk-notes-storage')
  if (raw) {
    const parsed = JSON.parse(raw)
    const state = parsed?.state
    if (state) {
      const bgMode = state.backgroundMode
      // Only restore from localStorage if no hash override was applied
      if (!document.documentElement.getAttribute('data-background')) {
        if (bgMode === 'pure-white') {
          document.documentElement.setAttribute('data-background', 'pure-white')
          document.documentElement.setAttribute('data-theme', 'light')
        } else if (bgMode === 'pure-black') {
          document.documentElement.setAttribute('data-background', 'pure-black')
          document.documentElement.setAttribute('data-theme', 'dark')
        } else {
          document.documentElement.setAttribute('data-background', 'acrylic')
          const theme = state.theme
          if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light')
          }
        }
      }
    }
  }
} catch (e) { /* ignore */ swallowError('main:55', e) }

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
