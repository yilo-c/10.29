import { useState } from 'react'
import { useStore } from '@desk-notes/shared'
import { haptic, touchScale } from '../../../lib/haptic'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'

const THEMES = [
  { value: 'light' as const, label: '浅色', desc: '默认明亮主题',
    preview: { bg: '#FFFFFF', card: '#F2F2F7', text: '#000000', accent: '#007AFF' } },
  { value: 'dark' as const, label: '深色', desc: '适合夜间使用',
    preview: { bg: '#1C1C1E', card: '#2C2C2E', text: '#FFFFFF', accent: '#0A84FF' } },
  { value: 'black' as const, label: '纯黑', desc: 'OLED 省电',
    preview: { bg: '#000000', card: '#1C1C1E', text: '#FFFFFF', accent: '#0A84FF' } },
]

export default function SettingsAppearance() {
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)
  const referenceMode = useStore(s => s.referenceMode)
  const setReferenceMode = useStore(s => s.setReferenceMode)
  const defaultReminder = useStore(s => s.defaultReminder)
  const setDefaultReminder = useStore(s => s.setDefaultReminder)
  const [systemFollow, setSystemFollow] = useState(() => localStorage.getItem('siyi-system-theme') === '1')

  return (
    <>
      <div style={{ marginTop: 28 }}>
        <SectionTitle text="显示" />
        <SettingsCard>
          <SettingsRow label="参考强度">
            <div style={{ display: 'flex', gap: 8 }}>
              {[
                { value: 'simple' as const, label: '精简' },
                { value: 'full' as const, label: '完整' },
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setReferenceMode(opt.value)}
                  style={{
                    flex: 1, padding: '8px 0',
                    borderRadius: 'var(--radius-md)',
                    border: `1.5px solid ${referenceMode === opt.value ? 'var(--accent)' : 'var(--border-default)'}`,
                    background: referenceMode === opt.value ? 'var(--accent-surface)' : 'transparent',
                    color: referenceMode === opt.value ? 'var(--accent)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-footnote)', fontWeight: 500,
                    cursor: 'pointer', transition: 'all 0.15s ease',
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </SettingsRow>
          <SettingsRow label="默认提醒" last>
            <div style={{ display: 'flex', gap: 8 }}>
              {[15, 30, 60].map(min => (
                <button
                  key={min}
                  onClick={() => setDefaultReminder(min)}
                  style={{
                    flex: 1, padding: '8px 0',
                    borderRadius: 'var(--radius-md)',
                    border: `1.5px solid ${defaultReminder === min ? 'var(--accent)' : 'var(--border-default)'}`,
                    background: defaultReminder === min ? 'var(--accent-surface)' : 'transparent',
                    color: defaultReminder === min ? 'var(--accent)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-footnote)', fontWeight: 500,
                    cursor: 'pointer', transition: 'all 0.15s ease',
                  }}
                >
                  {min === 15 ? '15分钟' : min === 30 ? '30分钟' : '1小时'}
                </button>
              ))}
            </div>
          </SettingsRow>
        </SettingsCard>
      </div>

      <div style={{ marginTop: 28 }}>
        <SectionTitle text="主题" />
        <SettingsCard>
          <SettingsRow label="跟随系统主题" last>
            <button
              onClick={() => {
                const next = !systemFollow
                setSystemFollow(next)
                localStorage.setItem('siyi-system-theme', next ? '1' : '')
                if (next) {
                  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches
                  setTheme(dark ? 'dark' : 'light')
                }
              }}
              aria-label="跟随系统主题"
              role="switch"
              aria-checked={systemFollow}
              style={{
                width: 48, height: 28,
                borderRadius: 14,
                border: 'none',
                background: systemFollow ? 'var(--accent)' : 'var(--bg-tertiary)',
                cursor: 'pointer',
                position: 'relative',
                transition: 'background 0.2s ease',
              }}
            >
              <div style={{
                width: 24, height: 24, borderRadius: '50%',
                background: '#fff',
                boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
                position: 'absolute',
                top: 2,
                left: systemFollow ? 22 : 2,
                transition: 'left 0.2s ease',
              }} />
            </button>
          </SettingsRow>
        </SettingsCard>
        {systemFollow && (
          <div style={{
            marginTop: 8, marginBottom: 4,
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
            textAlign: 'center',
          }}>
            当前跟随系统主题 · 手动选择将关闭此选项
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, marginTop: systemFollow ? 4 : 12 }}>
          {THEMES.map(t => (
            <button
              key={t.value}
              onClick={() => {
                haptic.light()
                setTheme(t.value)
                if (systemFollow) {
                  setSystemFollow(false)
                  localStorage.setItem('siyi-system-theme', '')
                }
              }}
              onTouchStart={touchScale.onStart}
              onTouchEnd={touchScale.onEnd}
              style={{
                flex: 1, padding: 0,
                border: `2px solid ${theme === t.value ? 'var(--accent)' : 'var(--border-default)'}`,
                borderRadius: 'var(--radius-lg)', overflow: 'hidden',
                cursor: 'pointer', background: 'none',
                transition: 'border-color 0.15s ease',
              }}
            >
              <div style={{
                background: t.preview.bg, padding: '10px 8px 8px',
                display: 'flex', flexDirection: 'column', gap: 4,
              }}>
                <div style={{ background: t.preview.card, borderRadius: 4, height: 8, width: '80%' }} />
                <div style={{ background: t.preview.card, borderRadius: 4, height: 6, width: '60%' }} />
                <div style={{ background: t.preview.accent, borderRadius: 4, height: 6, width: '40%', marginTop: 2 }} />
              </div>
              <div style={{
                padding: '8px 4px',
                fontSize: 'var(--text-caption-1)',
                fontWeight: theme === t.value ? 600 : 400,
                color: theme === t.value ? 'var(--accent)' : 'var(--text-secondary)',
                textAlign: 'center',
              }}>
                {t.label}
              </div>
            </button>
          ))}
        </div>
      </div>
    </>
  )
}
