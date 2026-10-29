import { useState, useEffect } from 'react'
import { useStore } from '@desk-notes/shared'
import './styles/utilities.css'
import TodoView from './components/features/todo/TodoView'
import ReferenceView from './components/features/astrology/ReferenceView'
import NotesView from './components/features/notes/NotesView'
import SettingsView from './components/features/settings/SettingsView'

type Tab = 'todo' | 'reference' | 'notes' | 'settings'

const tabs: { key: Tab; label: string; icon: string }[] = [
  { key: 'todo', label: '待办', icon: '☑' },
  { key: 'reference', label: '参考', icon: '☰' },
  { key: 'notes', label: '笔记', icon: '◷' },
  { key: 'settings', label: '设置', icon: '◎' },
]

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('todo')
  const theme = useStore(s => s.theme)

  // Sync theme to <html> data-theme attribute
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100dvh',
        background: 'var(--bg-primary)',
        color: 'var(--text-primary)',
      }}
    >
      {/* Main content */}
      <main style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {activeTab === 'todo' && <TodoView />}
        {activeTab === 'reference' && <ReferenceView />}
        {activeTab === 'notes' && <NotesView />}
        {activeTab === 'settings' && <SettingsView />}
      </main>

      {/* Tab bar */}
      <nav
        style={{
          display: 'flex',
          borderTop: '1px solid var(--border-subtle)',
          background: 'var(--bg-secondary)',
          paddingBottom: 'calc(var(--safe-bottom) + 4px)',
          paddingTop: 4,
          flexShrink: 0,
        }}
      >
        {tabs.map((tab) => {
          const isActive = activeTab === tab.key
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                padding: '4px 0',
                background: 'none',
                border: 'none',
                color: isActive ? 'var(--gold-primary)' : 'var(--text-tertiary)',
                fontSize: 'var(--text-tiny)',
                cursor: 'pointer',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 0,
                transition: 'color var(--duration-sm) var(--ease-out)',
              }}
            >
              <span style={{ fontSize: 22, lineHeight: 1.3 }}>{tab.icon}</span>
              <span style={{ fontSize: 10, marginTop: -1 }}>{tab.label}</span>
            </button>
          )
        })}
      </nav>
    </div>
  )
}
