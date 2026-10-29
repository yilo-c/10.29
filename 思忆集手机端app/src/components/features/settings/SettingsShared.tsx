import type { ReactNode } from 'react'

export function SectionTitle({ text }: { text: string }) {
  return (
    <div style={{
      fontSize: 'var(--text-caption-1)',
      fontWeight: 600,
      color: 'var(--text-tertiary)',
      marginBottom: 10,
      textTransform: 'uppercase',
      letterSpacing: 0.5,
    }}>
      {text}
    </div>
  )
}

export function SettingsCard({ children }: { children: ReactNode }) {
  return (
    <div style={{
      background: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-lg)',
      overflow: 'hidden',
    }}>
      {children}
    </div>
  )
}

export function SettingsRow({ label, children, last = false }: { label: string; children: ReactNode; last?: boolean }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '12px 16px', minHeight: 44,
      borderBottom: last ? 'none' : '0.5px solid var(--border-subtle)',
      gap: 12,
    }}>
      <div style={{
        fontSize: 'var(--text-footnote)',
        color: 'var(--text-primary)',
        flexShrink: 0,
      }}>
        {label}
      </div>
      <div style={{
        flex: '0 1 auto',
        display: 'flex', justifyContent: 'flex-end',
      }}>
        {children}
      </div>
    </div>
  )
}
