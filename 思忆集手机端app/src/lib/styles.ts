import type React from 'react'

/** Full-width transparent input reset */
export const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 0',
  border: 'none',
  background: 'transparent',
  color: 'var(--text-primary)',
  fontSize: 'var(--text-body)',
  outline: 'none',
}

/** Accent-colored text link button reset */
export const linkBtnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'var(--accent)',
  fontSize: 'var(--text-footnote)',
  cursor: 'pointer',
  padding: '4px 0',
}

/** Mini icon button reset (edit/delete etc) */
export const miniBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none',
  cursor: 'pointer', color: 'var(--text-tertiary)',
  padding: 4, borderRadius: 'var(--radius-sm)',
  display: 'flex', alignItems: 'center', justifyContent: 'center',
  WebkitTapHighlightColor: 'transparent',
}

/** iOS toggle switch container */
export function switchStyle(on: boolean): React.CSSProperties {
  return {
    width: 48, height: 28,
    borderRadius: 14,
    border: 'none',
    background: on ? 'var(--accent)' : 'var(--bg-tertiary)',
    cursor: 'pointer',
    position: 'relative',
    transition: 'background 0.2s ease',
  }
}

/** iOS toggle switch thumb */
export function switchThumbStyle(on: boolean): React.CSSProperties {
  return {
    width: 24, height: 24, borderRadius: '50%',
    background: '#fff',
    boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
    position: 'absolute',
    top: 2,
    left: on ? 22 : 2,
    transition: 'left 0.2s ease',
  }
}

/** Common flex row center with gap */
export function flexRow(gap = 8): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap,
  }
}

/** Glass card surface */
export const cardStyle: React.CSSProperties = {
  background: 'var(--bg-secondary)',
  borderRadius: 'var(--radius-lg)',
  overflow: 'hidden',
}

/** Scroll container reset */
export const scrollContainer: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  WebkitOverflowScrolling: 'touch',
}

/** Section title (iOS-style uppercase) */
export const sectionTitleStyle: React.CSSProperties = {
  fontSize: 'var(--text-caption-1)',
  fontWeight: 600,
  color: 'var(--text-tertiary)',
  marginBottom: 10,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
}
